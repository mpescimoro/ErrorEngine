"""
Email delivery through SMTP (Exchange).
Supports conditional routing and reminders.
"""
import re
import smtplib
import ssl
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app
from jinja2.sandbox import ImmutableSandboxedEnvironment
from datetime import datetime, timezone
from models import db, EmailLog
from utils import format_local_now, utc_to_local

logger = logging.getLogger(__name__)

# Names allowed for file templates: {name} -> templates/email/name.html
TEMPLATE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


class SmtpUnavailable(Exception):
    """The SMTP server does not answer or refuses the connection/login."""


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


# Default HTML template for new-error notifications
def load_email_template(template_name=None):
    """
    Loads an email template.

    Args:
        template_name: Template name (without .html) or None for the default

    Returns:
        str: Template content
    """
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
            # Fall back to the default if the custom template is not found
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
    Resolves the content of the template field.
    
    Args:
        template_field: Value of the query's email_template field
        
    Returns:
        str: HTML template to use
        
    Logic:
        - Empty/None → load default.html
        - {template_name} → load template_name.html
        - Anything else → use it as HTML directly
    """
    if not template_field or not template_field.strip():
        return load_email_template('default')
    
    template_field = template_field.strip()
    
    # Is it a template reference: {name}
    if template_field.startswith('{') and template_field.endswith('}'):
        template_name = template_field[1:-1].strip()
        if template_name:
            return load_email_template(template_name)
        else:
            return load_email_template('default')
    
    # Otherwise it is custom HTML
    return template_field

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
    
    @staticmethod
    def _tls_context():
        """
        TLS that checks the server certificate and name (smtplib does not do it by
        itself). MAIL_CA_FILE: the CA, or the certificate, of an internal SMTP server.
        """
        ca_file = current_app.config.get('MAIL_CA_FILE') or None
        context = ssl.create_default_context(cafile=ca_file)
        if ca_file:
            # Python 3.13 adds strict RFC 5280 checks that many certificates of internal
            # CAs fail ("Missing Authority Key Identifier"): the CA chosen here is trusted
            # as it is. The chain and the server name are still checked.
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return context

    def _get_smtp_connection(self):
        """Creates an SMTP connection (with a timeout: a server that does not answer does not block the checks)."""
        timeout = current_app.config.get('MAIL_TIMEOUT_SECONDS', 30)
        try:
            context = self._tls_context()
            if current_app.config['MAIL_USE_TLS']:
                server = smtplib.SMTP(
                    current_app.config['MAIL_SERVER'],
                    current_app.config['MAIL_PORT'],
                    timeout=timeout
                )
                server.starttls(context=context)
            else:
                server = smtplib.SMTP_SSL(
                    current_app.config['MAIL_SERVER'],
                    current_app.config['MAIL_PORT'],
                    timeout=timeout,
                    context=context
                )

            server.login(
                current_app.config['MAIL_USERNAME'],
                current_app.config['MAIL_PASSWORD']
            )
            return server
        except Exception as e:
            logger.error(f"SMTP connection error: {e}")
            raise SmtpUnavailable(str(e)) from e

    def _deliver(self, sender: str, recipients: list, message: str):
        """Opens the connection, sends, and always closes it, even on errors."""
        server = self._get_smtp_connection()
        try:
            server.sendmail(sender, recipients, message)
        finally:
            try:
                server.quit()
            except Exception:
                server.close()
    
    def send_error_notification(self, query, errors: list, columns: list,
                                recipients_override: list = None,
                                email_type: str = 'new_errors') -> dict:
        """
        Sends an email notification for the errors.
        
        Args:
            query: MonitoredQuery object
            errors: List of dictionaries with the error data
            columns: List of column names
            recipients_override: If given, use these recipients instead of the query's
            email_type: 'new_errors' o 'reminder'
            
        Returns:
            dict: {'success': bool, 'message': str, 'recipients': list}
        """
        # Recipients
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
        
        # Template
        template = resolve_template(query.email_template)
        
        # Subject
        subject_template = query.email_subject
        if email_type == 'reminder':
            # REMINDER prefix if not already there
            if 'REMINDER' not in subject_template.upper():
                subject_template = '[REMINDER] ' + subject_template
        
        try:
            # Render the template
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

            # Build the message
            msg = MIMEMultipart('alternative')
            
            # Format the subject with the variables
            subject = subject_template
            if '{' in subject:
                subject = subject.format(
                    query_name=query.name,
                    error_count=len(errors)
                )
            
            msg['Subject'] = subject
            msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
            msg['To'] = ', '.join(recipients)
            
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
            
            msg.attach(MIMEText(plain_text, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))

            # Send
            self._deliver(
                current_app.config['MAIL_DEFAULT_SENDER'],
                recipients,
                msg.as_string()
            )

            # Log the delivery
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
            # Log the error
            email_log = EmailLog(
                query_id=query.id,
                recipients=', '.join(recipients),
                subject=query.email_subject,
                error_count=len(errors),
                email_type=email_type,
                status='failed',
                error_message=str(e)
            )
            db.session.add(email_log)
            db.session.commit()
            
            logger.error(f"Error sending email for {query.name}: {e}")

            return {
                'success': False,
                'message': str(e),
                'recipients': recipients,
                # SMTP server unreachable: no point trying the other emails of this round
                'smtp_unavailable': isinstance(e, SmtpUnavailable)
            }

    def send_system_notification(self, recipients: list, subject: str, title: str,
                                 lines: list, query=None, email_type: str = 'system') -> dict:
        """
        ErrorEngine service email (e.g. query failing or recovered),
        with fixed text: it does not use the queries' custom templates.
        """
        if not recipients:
            return {'success': False, 'message': 'No recipients configured', 'recipients': []}

        from html import escape
        html_lines = ''.join(
            f'<p style="margin:0 0 10px 0;">{escape(line)}</p>' for line in lines)
        html_content = (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#333;">'
            f'<div style="padding:14px 18px;background:#0d9488;color:#fff;font-size:16px;font-weight:bold;">'
            f'{escape(title)}</div><div style="padding:16px 18px;">{html_lines}</div></div>'
        )
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
        msg['To'] = ', '.join(recipients)
        msg.attach(MIMEText('\n\n'.join([title] + list(lines)), 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        try:
            self._deliver(current_app.config['MAIL_DEFAULT_SENDER'], recipients, msg.as_string())
            status, error, result = 'sent', None, {'success': True, 'message': 'Sent', 'recipients': recipients}
        except Exception as e:
            logger.error(f"Error sending service email '{subject}': {e}")
            status, error = 'failed', str(e)
            result = {'success': False, 'message': str(e), 'recipients': recipients}

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
        """
        Sends a test email.
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            msg = MIMEMultipart()
            msg['Subject'] = '[ErrorEngine] Test email'
            msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
            msg['To'] = recipient
            
            html = """
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #5cb85c;">✅ Email connection test successful!</h2>
                <p>The SMTP configuration is correct and emails can be sent.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="color: #666; font-size: 12px;">
                    <strong>Server:</strong> {server}<br>
                    <strong>Port:</strong> {port}<br>
                    <strong>Test date:</strong> {date}
                </p>
            </body>
            </html>
            """.format(
                server=current_app.config['MAIL_SERVER'],
                port=current_app.config['MAIL_PORT'],
                date=format_local_now()
            )
            
            msg.attach(MIMEText(html, 'html'))

            self._deliver(
                current_app.config['MAIL_DEFAULT_SENDER'],
                [recipient],
                msg.as_string()
            )

            return {'success': True, 'message': f'Test email sent to {recipient}'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}


# Singleton
email_service = EmailService()
