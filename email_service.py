"""
Email delivery: any SMTP server (with or without login) or Microsoft 365 through
Microsoft Graph. With neither configured, email is off and the notifications go
only to the channels.
Supports conditional routing and reminders.
"""
import re
import smtplib
import ssl
import logging
import os
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import quote
import requests
from flask import current_app
from jinja2.sandbox import ImmutableSandboxedEnvironment
from datetime import datetime, timezone
from models import db, EmailLog
from utils import format_local_now, utc_to_local, redact_secrets

logger = logging.getLogger(__name__)

# Names allowed for file templates: {name} -> templates/email/name.html
TEMPLATE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


EMAIL_OFF_MESSAGE = 'Email is off: set MAIL_SERVER, or the Microsoft 365 settings, in .env'
SECURITY_LABELS = {'starttls': 'STARTTLS', 'ssl': 'SSL/TLS', 'none': 'no encryption'}


class EmailUnavailable(Exception):
    """The email service does not answer or refuses the sign-in: no email can go out now."""


class EmailOff(Exception):
    """Neither an SMTP server nor Microsoft 365 is configured."""


def _localtime_filter(dt, fmt='%d/%m/%Y %H:%M'):
    if dt is None:
        return None
    local_dt = utc_to_local(dt)
    return local_dt.strftime(fmt) if local_dt else ''


# Email templates can also be written in the query form: they run in a
# sandbox, with no access to Python objects, configuration or server files
_email_env = ImmutableSandboxedEnvironment(autoescape=True)
_email_env.filters['localtime'] = _localtime_filter


def render_email_template(template: str, **context) -> str:
    """Renders an email template in the sandbox."""
    return _email_env.from_string(template).render(**context)


def load_email_template(template_name=None):
    """Content of templates/email/<template_name>.html (default.html when None)."""
    if template_name is None:
        template_name = 'default'

    if not TEMPLATE_NAME_RE.match(template_name):
        # No paths: only files in templates/email
        logger.warning(f"Invalid email template name: {template_name!r}, using the default")
        template_name = 'default'

    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'email', f'{template_name}.html')
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Email template not found: {template_path}")
        if template_name != 'default':
            logger.info("Falling back to the default template")
            return load_email_template('default')
        # Inline fallback if the default is missing too
    return """
<table cellpadding="5" cellspacing="0" border="1" style="border-collapse:collapse;font-family:Arial;font-size:12px;">
<tr style="background:#0d9488;color:#fff;">
    <th colspan="{{ columns|length }}" align="left">{{ query_name }}</th>
</tr>
<tr>
    <td colspan="{{ columns|length }}">
        {{ check_time|localtime('%d/%m/%Y %H:%M:%S') }} · Errors: {{ error_count }}
    </td>
</tr>
<tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr>
{% for e in errors %}
<tr>{% for c in columns %}<td>{{ e[c] or '' }}</td>{% endfor %}</tr>
{% endfor %}
</table>
"""


def resolve_template(template_field):
    """
    Template of a query's email_template field: empty → default.html,
    {name} → templates/email/name.html, anything else is the HTML itself.
    """
    if not template_field or not template_field.strip():
        return load_email_template('default')
    
    template_field = template_field.strip()
    
    if template_field.startswith('{') and template_field.endswith('}'):
        template_name = template_field[1:-1].strip()
        if template_name:
            return load_email_template(template_name)
        else:
            return load_email_template('default')
    
    return template_field


def _json(response) -> dict:
    """Body of an HTTP answer as a dict ({} when it is not JSON)."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class GraphMailer:
    """
    Microsoft 365 through Microsoft Graph, with an app registered in Entra ID that has
    the Mail.Send application permission (client credentials: no user signs in).
    Needs neither SMTP AUTH nor a password of the mailbox.
    """

    TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    SEND_URL = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'

    def __init__(self):
        # Queries run in parallel: one token at a time, reused until shortly before it expires
        self._lock = threading.Lock()
        self._token = None
        self._token_for = None
        self._expires_at = 0.0

    def _access_token(self, tenant, client_id, secret, timeout, renew=False):
        key = (tenant, client_id, secret)
        with self._lock:
            if not renew and self._token and self._token_for == key and time.monotonic() < self._expires_at:
                return self._token
            try:
                response = requests.post(
                    self.TOKEN_URL.format(tenant=quote(tenant, safe='')),
                    data={'grant_type': 'client_credentials', 'client_id': client_id,
                          'client_secret': secret, 'scope': 'https://graph.microsoft.com/.default'},
                    timeout=timeout)
            except requests.RequestException as e:
                raise EmailUnavailable(f"Microsoft 365 sign-in: {redact_secrets(str(e))}") from e
            data = _json(response)
            if response.status_code != 200 or not data.get('access_token'):
                # e.g. "AADSTS7000215: Invalid client secret provided..." (first line only)
                detail = str(data.get('error_description') or data.get('error') or f'HTTP {response.status_code}')
                raise EmailUnavailable(f"Microsoft 365 sign-in failed: {detail.splitlines()[0]}")
            self._token, self._token_for = data['access_token'], key
            self._expires_at = time.monotonic() + max(int(data.get('expires_in') or 3600) - 300, 60)
            return self._token

    def send(self, config, sender: str, recipients: list, subject: str, html: str):
        """Sends one email; raises EmailUnavailable when no email can go out now."""
        timeout = config.get('MAIL_TIMEOUT_SECONDS', 30)
        credentials = (config['M365_TENANT_ID'], config['M365_CLIENT_ID'], config['M365_CLIENT_SECRET'], timeout)
        payload = {'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html},
            'toRecipients': [{'emailAddress': {'address': address}} for address in recipients],
        }}
        url = self.SEND_URL.format(sender=quote(sender, safe='@'))
        for attempt in (1, 2):
            token = self._access_token(*credentials, renew=attempt == 2)
            try:
                response = requests.post(url, json=payload, timeout=timeout,
                                         headers={'Authorization': f'Bearer {token}'})
            except requests.RequestException as e:
                raise EmailUnavailable(f"Microsoft 365: {redact_secrets(str(e))}") from e
            if response.status_code == 202:
                return
            if response.status_code != 401:
                break
            # 401: token expired or revoked, one more try with a new one

        error = _json(response).get('error') or {}
        detail = f"HTTP {response.status_code}"
        if isinstance(error, dict):
            detail += ''.join(part for part in (f" {error.get('code')}" if error.get('code') else '',
                                                f": {error.get('message')}" if error.get('message') else ''))
        if response.status_code in (400, 413):
            # This email only, e.g. an invalid recipient address or a message too large
            raise RuntimeError(f"Microsoft 365 refused the email: {detail}")
        # Permission missing (403), unknown sender mailbox (404), throttling (429), outage (5xx)
        raise EmailUnavailable(f"Microsoft 365: {detail}")


graph_mailer = GraphMailer()


class EmailService:
    """
    Sends notification emails.
    Supports conditional routing through recipients_override.
    """

    def __init__(self, app=None):
        self.app = app

    def init_app(self, app):
        """Initializes the Flask extension."""
        self.app = app
        app.extensions['email'] = self
        cfg = app.config
        m365 = [name for name in ('M365_TENANT_ID', 'M365_CLIENT_ID', 'M365_CLIENT_SECRET') if cfg.get(name)]
        if m365 and len(m365) < 3:
            logger.warning("Microsoft 365: M365_TENANT_ID, M365_CLIENT_ID and M365_CLIENT_SECRET are all "
                           "needed; until then emails go through MAIL_SERVER, if it is set")
        if self._method_for(cfg) is None and not cfg.get('TESTING'):
            logger.warning("Email is off (MAIL_SERVER is empty): notifications go only to the channels")

    @staticmethod
    def _method_for(config):
        if config.get('M365_TENANT_ID') and config.get('M365_CLIENT_ID') and config.get('M365_CLIENT_SECRET'):
            return 'microsoft365'
        if config.get('MAIL_SERVER'):
            return 'smtp'
        return None

    def method(self):
        """'microsoft365', 'smtp' or None when email is off."""
        return self._method_for(current_app.config)

    def is_enabled(self) -> bool:
        return self.method() is not None

    @staticmethod
    def sender() -> str:
        """Address the emails are sent from."""
        cfg = current_app.config
        return cfg.get('MAIL_DEFAULT_SENDER') or cfg.get('MAIL_USERNAME') or ''

    def status(self):
        """How emails go out, for the Settings page (never passwords or secrets); None when off."""
        cfg = current_app.config
        method = self.method()
        if method == 'microsoft365':
            return {'method': 'Microsoft 365 (Graph)', 'server': 'graph.microsoft.com',
                    'security': 'HTTPS', 'login': 'app ' + cfg['M365_CLIENT_ID'], 'sender': self.sender()}
        if method == 'smtp':
            security = (cfg.get('MAIL_SECURITY') or 'starttls').lower()
            return {'method': 'SMTP', 'server': f"{cfg['MAIL_SERVER']}:{cfg['MAIL_PORT']}",
                    'security': SECURITY_LABELS.get(security, security),
                    'login': cfg.get('MAIL_USERNAME') or 'none (relay)', 'sender': self.sender()}
        return None

    @staticmethod
    def _tls_context():
        """
        TLS that checks the server certificate and name (smtplib does not do it by
        itself). MAIL_CA_FILE: the CA, or the certificate, of an internal SMTP server.
        """
        ca_file = current_app.config.get('MAIL_CA_FILE') or None
        context = ssl.create_default_context(cafile=ca_file)
        if ca_file:
            # Python 3.13's strict RFC 5280 checks refuse many internal CAs ("Missing Authority
            # Key Identifier"): this CA is trusted as it is, chain and name are still checked
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return context

    def _get_smtp_connection(self):
        """
        Connects to the SMTP server (with a timeout: a server that does not answer does
        not block the checks) and signs in when MAIL_USERNAME is set.
        """
        cfg = current_app.config
        timeout = cfg.get('MAIL_TIMEOUT_SECONDS', 30)
        security = (cfg.get('MAIL_SECURITY') or 'starttls').lower()
        username = cfg.get('MAIL_USERNAME') or ''
        server = None
        try:
            if security not in SECURITY_LABELS:
                raise ValueError(f"MAIL_SECURITY must be starttls, ssl or none, not '{security}'")
            if security == 'none' and username:
                raise ValueError("MAIL_SECURITY=none would send the password unencrypted: use starttls "
                                 "or ssl, or leave MAIL_USERNAME empty for a relay without login")
            if security == 'ssl':
                server = smtplib.SMTP_SSL(cfg['MAIL_SERVER'], cfg['MAIL_PORT'], timeout=timeout,
                                          context=self._tls_context())
            else:
                server = smtplib.SMTP(cfg['MAIL_SERVER'], cfg['MAIL_PORT'], timeout=timeout)
                if security == 'starttls':
                    try:
                        server.starttls(context=self._tls_context())
                    except smtplib.SMTPNotSupportedError:
                        raise ValueError("The server does not support STARTTLS: MAIL_SECURITY=ssl if it "
                                         "uses SSL (usually port 465), or MAIL_SECURITY=none for a relay "
                                         "without encryption, only without login") from None
            if username:
                server.login(username, cfg.get('MAIL_PASSWORD') or '')
            return server
        except Exception as e:
            if server is not None:
                server.close()
            logger.error(f"SMTP connection error: {e}")
            raise EmailUnavailable(str(e)) from e

    def _deliver(self, recipients: list, subject: str, html: str, text: str = None):
        """
        Sends one email with the configured method. Raises EmailOff, EmailUnavailable
        (nothing can go out now) or another error for this email only.
        """
        method = self.method()
        if method is None:
            raise EmailOff(EMAIL_OFF_MESSAGE)
        sender = self.sender()
        if not sender:
            raise EmailUnavailable('MAIL_DEFAULT_SENDER is missing: the address the emails are sent from')
        if method == 'microsoft365':
            graph_mailer.send(current_app.config, sender, recipients, subject, html)
            return

        message = MIMEMultipart('alternative')
        message['Subject'] = subject
        message['From'] = sender
        message['To'] = ', '.join(recipients)
        if text:
            message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        # Opens the connection, sends, and always closes it, even on errors
        server = self._get_smtp_connection()
        try:
            server.sendmail(sender, recipients, message.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                server.close()
    
    def send_error_notification(self, query, errors: list, columns: list,
                                recipients_override: list = None,
                                email_type: str = 'new_errors') -> dict:
        """
        Notification email for these errors, to recipients_override or else the query's
        recipients. email_type: 'new_errors' or 'reminder'.
        Returns {'success', 'message', 'recipients'} (plus 'email_off' / 'email_unavailable').
        """
        if recipients_override:
            recipients = recipients_override
        else:
            recipients = query.get_recipients_list()
        
        if not recipients:
            return {
                'success': False,
                'message': 'No recipients configured',
                'recipients': []
            }
        
        if not errors:
            return {
                'success': True,
                'message': 'No errors to notify',
                'recipients': []
            }

        if not self.is_enabled():
            # Not a failure to record: there is no way to send emails
            return {'success': False, 'message': EMAIL_OFF_MESSAGE, 'recipients': recipients, 'email_off': True}
        
        template = resolve_template(query.email_template)
        
        subject_template = query.email_subject
        if email_type == 'reminder':
            # REMINDER prefix if not already there
            if 'REMINDER' not in subject_template.upper():
                subject_template = '[REMINDER] ' + subject_template
        
        try:
            context = dict(
                query_name=query.name,
                query_description=query.description,
                check_time=datetime.now(timezone.utc),
                error_count=len(errors),
                errors=errors,
                columns=columns,
                email_type=email_type
            )
            try:
                html_content = render_email_template(template, **context)
            except Exception as template_error:
                # A broken custom template must not stop the notification
                logger.warning(
                    f"Email template not usable for {query.name} ({template_error}): "
                    f"using the default"
                )
                html_content = render_email_template(load_email_template('default'), **context)

            subject = subject_template
            if '{' in subject:
                subject = subject.format(
                    query_name=query.name,
                    error_count=len(errors)
                )
            
            # Plain-text version
            error_type_text = 'Errors still active (REMINDER)' if email_type == 'reminder' else 'New errors'
            plain_text = f"""
{'[REMINDER] ' if email_type == 'reminder' else ''}Errors detected - {query.name}

Query: {query.name}
Description: {query.description or 'N/A'}
Check time: {format_local_now()}
{error_type_text}: {len(errors)}

{'⚠️ This is a reminder - these errors have not been resolved yet.' if email_type == 'reminder' else ''}

Open the dashboard to see the details.
            """
            
            self._deliver(recipients, subject, html_content, plain_text)

            email_log = EmailLog(
                query_id=query.id,
                recipients=', '.join(recipients),
                subject=subject,
                error_count=len(errors),
                email_type=email_type,
                status='sent'
            )
            db.session.add(email_log)
            db.session.commit()
            
            logger.info(
                f"Email {email_type} sent for {query.name} to {len(recipients)} recipients"
            )
            
            return {
                'success': True,
                'message': f'Email sent to {len(recipients)} recipients',
                'recipients': recipients
            }
            
        except Exception as e:
            message = redact_secrets(str(e))
            email_log = EmailLog(
                query_id=query.id,
                recipients=', '.join(recipients),
                subject=query.email_subject,
                error_count=len(errors),
                email_type=email_type,
                status='failed',
                error_message=message
            )
            db.session.add(email_log)
            db.session.commit()
            
            logger.error(f"Error sending email for {query.name}: {message}")

            return {
                'success': False,
                'message': message,
                'recipients': recipients,
                # Email service unreachable: no point trying the other emails of this round
                'email_unavailable': isinstance(e, EmailUnavailable)
            }

    def send_system_notification(self, recipients: list, subject: str, title: str,
                                 lines: list, query=None, email_type: str = 'system') -> dict:
        """
        ErrorEngine service email (e.g. query failing or recovered),
        with fixed text: it does not use the queries' custom templates.
        """
        if not recipients:
            return {'success': False, 'message': 'No recipients configured', 'recipients': []}
        if not self.is_enabled():
            return {'success': False, 'message': EMAIL_OFF_MESSAGE, 'recipients': recipients, 'email_off': True}

        from html import escape
        html_lines = ''.join(
            f'<p style="margin:0 0 10px 0;">{escape(line)}</p>' for line in lines)
        html_content = (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#333;">'
            f'<div style="padding:14px 18px;background:#0d9488;color:#fff;font-size:16px;font-weight:bold;">'
            f'{escape(title)}</div><div style="padding:16px 18px;">{html_lines}</div></div>'
        )
        try:
            self._deliver(recipients, subject, html_content, '\n\n'.join([title] + list(lines)))
            status, error, result = 'sent', None, {'success': True, 'message': 'Sent', 'recipients': recipients}
        except Exception as e:
            error = redact_secrets(str(e))
            logger.error(f"Error sending service email '{subject}': {error}")
            status = 'failed'
            result = {'success': False, 'message': error, 'recipients': recipients}

        db.session.add(EmailLog(
            query_id=query.id if query is not None else None,
            recipients=', '.join(recipients),
            subject=subject[:200],
            error_count=0,
            email_type=email_type,
            status=status,
            error_message=error
        ))
        db.session.commit()
        return result
    
    def test_email(self, recipient: str) -> dict:
        """Sends a test email; returns {'success', 'message'}."""
        status = self.status()
        if status is None:
            return {'success': False, 'message': EMAIL_OFF_MESSAGE}
        from html import escape
        html = """
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #0d9488;">Email test successful</h2>
                <p>The email configuration of ErrorEngine works.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="color: #666; font-size: 12px;">
                    <strong>Sent through:</strong> {method}, {server} ({security})<br>
                    <strong>Test date:</strong> {date}
                </p>
            </body>
            </html>
            """.format(method=escape(status['method']), server=escape(status['server']),
                       security=escape(status['security']), date=format_local_now())
        text = f"Email test successful: sent through {status['method']}, {status['server']}."
        try:
            self._deliver([recipient], '[ErrorEngine] Test email', html, text)
            return {'success': True, 'message': f'Test email sent to {recipient}'}
        except Exception as e:
            return {'success': False, 'message': redact_secrets(str(e))}


email_service = EmailService()
