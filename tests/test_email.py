"""
Email without a fixed provider: any SMTP server (with or without login, with STARTTLS,
SSL or no encryption), Microsoft 365 through Microsoft Graph, or email off.
Templates run in a sandbox.
"""
import email
import json
import logging
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from models import db, MonitoredQuery, ErrorRecord, EmailLog, NotificationChannel

ROOT = Path(__file__).resolve().parent.parent
ROWS = (['ID'], [{'ID': 1}])


def make_query(**kwargs):
    kwargs.setdefault('name', 'Orders')
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    kwargs.setdefault('email_recipients', 'ops@x.it')
    query = MonitoredQuery(**kwargs)
    db.session.add(query)
    db.session.commit()
    return query


def config_from_env(**env):
    """Config values as config.py computes them from these environment variables."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(('MAIL_', 'M365_'))}
    clean.update(env)
    code = ('import json, config; c = config.Config; print(json.dumps({k: getattr(c, k) for k in '
            '("MAIL_SERVER", "MAIL_SECURITY", "MAIL_PORT", "MAIL_DEFAULT_SENDER")}))')
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=clean,
                         capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


# --- Settings in .env ---

class TestSettings:

    def test_no_default_server(self):
        assert config_from_env()['MAIL_SERVER'] == ''

    def test_security_and_port(self):
        assert config_from_env(MAIL_SERVER='mail.x.it') == {
            'MAIL_SERVER': 'mail.x.it', 'MAIL_SECURITY': 'starttls', 'MAIL_PORT': 587, 'MAIL_DEFAULT_SENDER': ''}
        assert config_from_env(MAIL_PORT='465')['MAIL_SECURITY'] == 'ssl'
        assert config_from_env(MAIL_SECURITY='ssl')['MAIL_PORT'] == 465
        assert config_from_env(MAIL_SECURITY='NONE')['MAIL_PORT'] == 25
        assert config_from_env(MAIL_SECURITY='none', MAIL_PORT='2525')['MAIL_PORT'] == 2525
        # MAIL_USE_TLS of older versions is no longer read: without MAIL_SECURITY the port decides
        assert config_from_env(MAIL_USE_TLS='false', MAIL_PORT='587')['MAIL_SECURITY'] == 'starttls'

    def test_mail_use_tls_left_in_env_says_how_to_keep_the_encryption(self, monkeypatch):
        import config
        # Until 2.4.1, without MAIL_SECURITY: MAIL_USE_TLS=true STARTTLS, any other value SSL, whatever the port
        before = {('false', ''): ('ssl', 465), ('false', '587'): ('ssl', 587), ('false', '465'): ('ssl', 465),
                  ('true', ''): ('starttls', 587), ('TRUE', '465'): ('starttls', 465), ('true', '25'): ('starttls', 25)}
        monkeypatch.delenv('MAIL_SECURITY', raising=False)
        for (use_tls, port), was in before.items():
            monkeypatch.setenv('MAIL_USE_TLS', use_tls)
            monkeypatch.setenv('MAIL_PORT', port)
            [warning] = [w for w in config.retired_settings_warnings() if w.startswith('MAIL_USE_TLS')]
            now = config_from_env(MAIL_USE_TLS=use_tls, MAIL_PORT=port)
            # It says whether email goes out as before, and the MAIL_SECURITY that keeps it so
            assert ('until now' in warning) == ((now['MAIL_SECURITY'], now['MAIL_PORT']) != was), warning
            kept = config_from_env(MAIL_SECURITY=re.search(r'MAIL_SECURITY=(\w+)', warning)[1], MAIL_PORT=port)
            assert (kept['MAIL_SECURITY'], kept['MAIL_PORT']) == was
        # With MAIL_SECURITY set, MAIL_USE_TLS was not read before either
        monkeypatch.setenv('MAIL_SECURITY', 'ssl')
        assert 'MAIL_SECURITY decides, as before' in ' '.join(config.retired_settings_warnings())

    def test_sender_defaults_to_the_user_name(self):
        assert config_from_env(MAIL_USERNAME='alerts@x.it')['MAIL_DEFAULT_SENDER'] == 'alerts@x.it'
        assert config_from_env(MAIL_USERNAME='alerts@x.it', MAIL_DEFAULT_SENDER='noreply@x.it')[
            'MAIL_DEFAULT_SENDER'] == 'noreply@x.it'


# --- SMTP ---

class TestSmtp:

    def send(self, app, **config):
        from email_service import email_service
        app.config.update(config)
        with patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            result = email_service.test_email('ops@x.it')
        return result, smtp, smtp_ssl

    def test_starttls_with_login(self, app):
        result, smtp, smtp_ssl = self.send(app, MAIL_SECURITY='starttls', MAIL_PORT=587)
        server = smtp.return_value
        assert result['success'] and not smtp_ssl.called
        assert server.starttls.called and server.login.call_args.args == ('test@test.com', 'test')

    def test_ssl_with_login(self, app):
        result, smtp, smtp_ssl = self.send(app, MAIL_SECURITY='ssl', MAIL_PORT=465)
        assert result['success'] and smtp_ssl.called and not smtp.called
        assert smtp_ssl.return_value.login.called

    def test_relay_without_login(self, app):
        result, smtp, _ = self.send(app, MAIL_SECURITY='starttls', MAIL_USERNAME='', MAIL_PASSWORD='',
                                    MAIL_DEFAULT_SENDER='alerts@x.it')
        server = smtp.return_value
        assert result['success'] and server.starttls.called and not server.login.called
        message = email.message_from_string(server.sendmail.call_args.args[2])
        assert message['From'] == 'alerts@x.it' and server.sendmail.call_args.args[0] == 'alerts@x.it'

    def test_internal_relay_without_encryption(self, app):
        result, smtp, smtp_ssl = self.send(app, MAIL_SECURITY='none', MAIL_PORT=25, MAIL_USERNAME='',
                                           MAIL_DEFAULT_SENDER='alerts@x.it')
        server = smtp.return_value
        assert result['success'] and not smtp_ssl.called
        assert not server.starttls.called and not server.login.called and server.sendmail.called

    def test_never_a_password_in_clear_text(self, app):
        result, smtp, smtp_ssl = self.send(app, MAIL_SECURITY='none')
        assert not result['success'] and 'unencrypted' in result['message']
        assert not smtp.called and not smtp_ssl.called

    def test_server_without_starttls(self, app):
        import smtplib
        app.config.update(MAIL_SECURITY='starttls')
        with patch('email_service.smtplib.SMTP') as smtp:
            smtp.return_value.starttls.side_effect = smtplib.SMTPNotSupportedError(
                'STARTTLS extension not supported by server.')
            from email_service import email_service
            result = email_service.test_email('ops@x.it')
        assert not result['success'] and 'MAIL_SECURITY=none' in result['message']
        assert smtp.return_value.close.called

    def test_unknown_security(self, app):
        result, smtp, _ = self.send(app, MAIL_SECURITY='tls')
        assert not result['success'] and 'starttls, ssl or none' in result['message']

    def test_date_and_message_id(self, app):
        from email.utils import parsedate_to_datetime
        result, _, smtp_ssl = self.send(app, MAIL_DEFAULT_SENDER='alerts@x.it')
        message = email.message_from_string(smtp_ssl.return_value.sendmail.call_args.args[2])
        assert result['success'] and parsedate_to_datetime(message['Date']).tzinfo is not None
        assert message['Message-ID'].endswith('@x.it>')

    def test_sender_falls_back_to_the_user_name(self, app):
        result, _, smtp_ssl = self.send(app, MAIL_DEFAULT_SENDER='', MAIL_USERNAME='alerts@x.it')
        assert result['success'] and smtp_ssl.return_value.sendmail.call_args.args[0] == 'alerts@x.it'

    def test_no_sender_at_all(self, app):
        result, smtp, smtp_ssl = self.send(app, MAIL_DEFAULT_SENDER='', MAIL_USERNAME='')
        assert not result['success'] and 'MAIL_DEFAULT_SENDER' in result['message']
        assert not smtp.called and not smtp_ssl.called

    def test_connection_closed_when_login_fails(self, app):
        from email_service import email_service, EmailUnavailable
        app.config.update(MAIL_SECURITY='ssl')
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            smtp_ssl.return_value.login.side_effect = OSError('535 Authentication failed')
            with pytest.raises(EmailUnavailable):
                email_service._get_smtp_connection()
        assert smtp_ssl.return_value.close.called

    def test_notification_reports_the_server_down(self, app):
        from email_service import email_service
        query = make_query(email_recipients='ops@x.it')
        with patch('email_service.smtplib.SMTP_SSL', side_effect=OSError('Connection refused')):
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
        assert result['success'] is False and result['email_unavailable'] is True

    def test_every_address_refused(self, app):
        import smtplib
        from email_service import email_service
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            smtp_ssl.return_value.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
                {'bad@x.it': (550, b'5.1.1 User unknown')})
            result = email_service.test_email('bad@x.it')
        assert result == {'success': False, 'message': 'Refused by the mail server: bad@x.it: 550 5.1.1 User unknown'}

    def test_service_email_says_who_was_refused(self, app, caplog):
        from email_service import email_service
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, caplog.at_level(logging.WARNING):
            smtp_ssl.return_value.sendmail.return_value = {'bad@x.it': (550, b'5.1.1 User\n unknown')}
            result = email_service.send_system_notification(['ops@x.it', 'bad@x.it'], 'S', 'T', ['line'])
        assert result['success'] is True and result['recipients'] == ['ops@x.it']
        assert EmailLog.query.one().error_message == 'Refused by the mail server: bad@x.it: 550 5.1.1 User unknown'
        assert 'bad@x.it: 550 5.1.1 User unknown' in caplog.text


class TestSmtpTls:

    def send_test(self, app, **config):
        from email_service import email_service
        app.config.update(config)
        with patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            email_service.test_email('ops@x.it')
        return smtp, smtp_ssl

    def test_starttls_checks_the_certificate(self, app):
        smtp, _ = self.send_test(app, MAIL_SECURITY='starttls')
        context = smtp.return_value.starttls.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_ssl_checks_the_certificate(self, app):
        _, smtp_ssl = self.send_test(app, MAIL_SECURITY='ssl')
        context = smtp_ssl.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_internal_ca_file(self, app):
        import certifi
        _, smtp_ssl = self.send_test(app, MAIL_SECURITY='ssl', MAIL_CA_FILE=certifi.where())
        context = smtp_ssl.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        # Python 3.13 strict checks refuse many internal certificates: off for the chosen CA
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT

    def test_public_servers_keep_the_default_checks(self, app):
        _, smtp_ssl = self.send_test(app, MAIL_SECURITY='ssl', MAIL_CA_FILE='')
        assert smtp_ssl.call_args.kwargs['context'].verify_flags == ssl.create_default_context().verify_flags



# --- Subject ---

class TestSubject:

    def test_placeholders(self):
        from email_service import format_subject, unknown_subject_placeholders
        assert format_subject('{query_name}: {error_count} errors', query_name='Orders', error_count=3) == \
            'Orders: 3 errors'
        assert format_subject('{error_count:03d} new', query_name='Orders', error_count=3) == '003 new'
        assert unknown_subject_placeholders('{query} {query_name} {error_count:d} {}') == ['{query}', '{}']
        assert unknown_subject_placeholders(None) == []

    def test_a_typo_is_sent_as_written_and_empty_is_the_default(self, app):
        from email_service import email_service
        query = make_query(email_subject='[ErrorEngine] {query}: {error_count} errors on {query_name} {')
        with patch('email_service.EmailService._deliver', return_value={}) as deliver:
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
            assert result['success']
            assert deliver.call_args.args[1] == '[ErrorEngine] {query}: 1 errors on Orders {'
            query.email_subject = ''
            email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
            assert deliver.call_args.args[1] == '[ErrorEngine] New errors: Orders'


# --- One SMTP connection per check ---

class TestOneConnectionPerCheck:

    def test_one_connection_for_the_emails_of_a_check(self, app):
        from monitor_service import monitor_service
        query = make_query(routing_enabled=True, routing_aggregation='per_error',
                           routing_default_recipients='a@x.it, b@x.it')
        with patch('monitor_service.execute_query_source', return_value=(['ID'], [{'ID': 1}, {'ID': 2}])), \
             patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            monitor_service.check_query(query, force=True)
        server = smtp_ssl.return_value
        assert server.sendmail.call_count == 4  # one email per error and recipient
        assert smtp_ssl.call_count == 1 and server.login.call_count == 1 and server.quit.call_count == 1

    def test_also_for_the_retries_between_checks(self, app):
        from monitor_service import monitor_service
        query = make_query(email_recipients='a@x.it', routing_enabled=True, routing_aggregation='per_error',
                           routing_default_recipients='a@x.it')
        with patch('monitor_service.execute_query_source', return_value=(['ID'], [{'ID': 1}, {'ID': 2}])), \
             patch('email_service.smtplib.SMTP_SSL', side_effect=OSError('Connection refused')):
            monitor_service.check_query(query, force=True)
        for error in ErrorRecord.query.filter_by(query_id=query.id):
            error.notify_retry_at = None
        db.session.commit()
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            monitor_service.deliver_pending(query)
        assert smtp_ssl.return_value.sendmail.call_count == 2 and smtp_ssl.call_count == 1

    def test_outside_a_check_one_per_email_inside_a_dropped_one_opened_again(self, app):
        import smtplib
        from email_service import email_service
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            email_service.test_email('a@x.it')
            email_service.test_email('b@x.it')
        assert smtp_ssl.call_count == 2 and smtp_ssl.return_value.quit.call_count == 2
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            smtp_ssl.return_value.sendmail.side_effect = [smtplib.SMTPServerDisconnected('gone'), None]
            with email_service.batch():
                first = email_service.test_email('a@x.it')
                second = email_service.test_email('b@x.it')
        assert not first['success'] and second['success']
        assert smtp_ssl.call_count == 2


# --- Microsoft 365 through Microsoft Graph ---

def answer(status, body=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode() if body is not None else b''
    return response


class FakeMicrosoft:
    """Token endpoint and sendMail of Microsoft, as requests.post sees them."""

    def __init__(self, send_answers=None, token_answer=None):
        self.token_calls, self.send_calls = [], []
        self.send_answers = list(send_answers or [])
        self.token_answer = token_answer
        self.tokens = 0

    def __call__(self, url, data=None, json=None, headers=None, timeout=None):
        if url.startswith('https://login.microsoftonline.com/'):
            self.token_calls.append({'url': url, 'data': data, 'timeout': timeout})
            if self.token_answer is not None:
                return self.token_answer
            self.tokens += 1
            return answer(200, {'token_type': 'Bearer', 'expires_in': 3599, 'access_token': f'token-{self.tokens}'})
        self.send_calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        return self.send_answers.pop(0) if self.send_answers else answer(202)


@pytest.fixture
def m365(app):
    from email_service import graph_mailer
    graph_mailer._token = None
    app.config.update(M365_TENANT_ID='contoso.onmicrosoft.com', M365_CLIENT_ID='app-id-123',
                      M365_CLIENT_SECRET='s3cret~value', MAIL_DEFAULT_SENDER='alerts@contoso.com',
                      MAIL_TIMEOUT_SECONDS=12)
    yield app
    graph_mailer._token = None


class TestMicrosoft365:

    def test_graph_wins_over_smtp(self, m365):
        from email_service import email_service
        assert m365.config['MAIL_SERVER'] and email_service.method() == 'microsoft365'
        m365.config['M365_CLIENT_SECRET'] = ''
        assert email_service.method() == 'smtp'

    def test_sends_through_graph(self, m365):
        from email_service import email_service
        query = make_query(email_recipients='a@x.it, b@x.it')
        fake = FakeMicrosoft()
        with patch('email_service.requests.post', side_effect=fake), \
             patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
        assert result['success'], result
        assert not smtp.called and not smtp_ssl.called

        token = fake.token_calls[0]
        assert token['url'] == 'https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token'
        assert token['data'] == {'grant_type': 'client_credentials', 'client_id': 'app-id-123',
                                 'client_secret': 's3cret~value', 'scope': 'https://graph.microsoft.com/.default'}
        sent = fake.send_calls[0]
        assert sent['url'] == 'https://graph.microsoft.com/v1.0/users/alerts@contoso.com/sendMail'
        assert sent['headers'] == {'Authorization': 'Bearer token-1'} and sent['timeout'] == 12
        message = sent['json']['message']
        assert message['subject'] == query.email_subject.format(query_name='Orders', error_count=1)
        assert message['body']['contentType'] == 'HTML' and 'Orders' in message['body']['content']
        assert [r['emailAddress']['address'] for r in message['toRecipients']] == ['a@x.it', 'b@x.it']
        assert EmailLog.query.filter_by(status='sent').count() == 1

    def test_token_reused(self, m365):
        from email_service import email_service
        fake = FakeMicrosoft()
        with patch('email_service.requests.post', side_effect=fake):
            assert email_service.test_email('a@x.it')['success']
            assert email_service.test_email('b@x.it')['success']
        assert len(fake.token_calls) == 1 and len(fake.send_calls) == 2

    def test_expired_token_renewed_once(self, m365):
        from email_service import email_service
        fake = FakeMicrosoft(send_answers=[answer(401, {'error': {'code': 'InvalidAuthenticationToken'}})])
        with patch('email_service.requests.post', side_effect=fake):
            assert email_service.test_email('a@x.it')['success']
        assert len(fake.token_calls) == 2
        assert fake.send_calls[1]['headers'] == {'Authorization': 'Bearer token-2'}

    def test_wrong_secret(self, m365):
        from email_service import email_service
        fake = FakeMicrosoft(token_answer=answer(401, {
            'error': 'invalid_client',
            'error_description': 'AADSTS7000215: Invalid client secret provided.\r\nTrace ID: 1234'}))
        query = make_query(email_recipients='a@x.it')
        with patch('email_service.requests.post', side_effect=fake):
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
        assert result['email_unavailable'] is True and not fake.send_calls
        assert result['message'] == 'Microsoft 365 sign-in failed: AADSTS7000215: Invalid client secret provided.'

    def test_missing_permission_stops_the_round(self, m365):
        from email_service import email_service
        fake = FakeMicrosoft(send_answers=[answer(403, {'error': {'code': 'ErrorAccessDenied',
                                                                   'message': 'Access is denied.'}})])
        query = make_query(email_recipients='a@x.it')
        with patch('email_service.requests.post', side_effect=fake):
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
        assert result['email_unavailable'] is True
        assert result['message'] == 'Microsoft 365: HTTP 403 ErrorAccessDenied: Access is denied.'

    def test_bad_address_fails_only_that_email(self, m365):
        from email_service import email_service
        fake = FakeMicrosoft(send_answers=[answer(400, {'error': {'code': 'ErrorInvalidRecipients',
                                                                   'message': 'At least one recipient is not valid.'}})])
        query = make_query(email_recipients='not-an-address')
        with patch('email_service.requests.post', side_effect=fake):
            result = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
        assert result['success'] is False and result['email_unavailable'] is False
        assert 'ErrorInvalidRecipients' in result['message']

    def test_network_error(self, m365):
        from email_service import email_service
        with patch('email_service.requests.post', side_effect=requests.ConnectionError('proxy refused')):
            result = email_service.test_email('a@x.it')
        assert result == {'success': False, 'message': 'Microsoft 365 sign-in: proxy refused'}

    def test_settings_show_it_without_the_secret(self, m365, client):
        html = client.get('/settings').get_data(as_text=True)
        block = html[html.index('id="emailStatus"'):html.index('Database connections')]
        assert 'Microsoft 365 (Graph)' in block and 'app app-id-123' in block and 'alerts@contoso.com' in block
        assert 's3cret' not in html

    def test_partial_settings_warn_at_startup(self, caplog):
        from email_service import EmailService
        fake_app = SimpleNamespace(config={'M365_TENANT_ID': 't', 'M365_CLIENT_ID': 'c', 'MAIL_SERVER': 'x'},
                                   extensions={})
        with caplog.at_level(logging.WARNING, logger='email_service'):
            EmailService().init_app(fake_app)
        assert 'are all needed' in caplog.text


# --- Email off ---

@pytest.fixture
def email_off(app):
    app.config.update(MAIL_SERVER='')
    return app


class TestEmailOff:

    def test_nothing_is_sent_or_logged(self, email_off):
        from email_service import email_service, EMAIL_OFF_MESSAGE
        query = make_query(email_recipients='a@x.it')
        with patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            notification = email_service.send_error_notification(query, [{'ID': 1}], ['ID'])
            service = email_service.send_system_notification(['a@x.it'], 'S', 'T', ['line'], query=query)
            test = email_service.test_email('a@x.it')
        assert notification['email_off'] and service['email_off']
        assert test == {'success': False, 'message': EMAIL_OFF_MESSAGE}
        assert not smtp.called and not smtp_ssl.called and EmailLog.query.count() == 0

    def test_settings_say_so(self, email_off, client):
        html = client.get('/settings').get_data(as_text=True)
        assert 'Email is off' in html and 'id="testEmailBtn"' not in html

    def test_warning_at_startup(self, caplog):
        from email_service import EmailService
        fake_app = SimpleNamespace(config={'MAIL_SERVER': ''}, extensions={})
        with caplog.at_level(logging.WARNING, logger='email_service'):
            EmailService().init_app(fake_app)
        assert 'Email is off' in caplog.text

    def test_errors_reach_the_channels_and_nothing_waits_for_email(self, email_off):
        from monitor_service import monitor_service
        channel = NotificationChannel(name='Ops', channel_type='webhook', config='{"url": "https://x.invalid"}')
        db.session.add(channel)
        query = make_query(email_recipients='a@x.it')
        query.notification_channels.append(channel)
        db.session.commit()
        with patch('monitor_service.execute_query_source', return_value=ROWS), \
             patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, \
             patch('notification_service.notification_service.send_to_channel',
                   return_value={'success': True, 'message': 'OK'}) as to_channel:
            monitor_service.check_query(query, force=True)
        record = ErrorRecord.query.one()
        assert to_channel.call_count == 1 and not smtp.called and not smtp_ssl.called
        assert record.notify_pending is False and record.get_notified_channels() == [channel.id]
        assert EmailLog.query.count() == 0

    def test_without_channels_the_error_is_not_left_pending(self, email_off):
        from monitor_service import monitor_service
        query = make_query(email_recipients='a@x.it')
        with patch('monitor_service.execute_query_source', return_value=ROWS):
            monitor_service.check_query(query, force=True)
        record = ErrorRecord.query.one()
        assert record.notify_pending is False and not record.notify_attempts

    def test_reminders_go_only_to_the_channels(self, email_off):
        from monitor_service import monitor_service
        query = make_query(email_recipients='a@x.it')
        with patch('monitor_service.email_service.send_error_notification') as send:
            monitor_service._send_notifications(query, [{'ID': 1}], ['ID'], email_type='reminder')
        assert not send.called

    def test_failing_query_alert_only_to_the_channels(self, email_off):
        from monitor_service import monitor_service
        query = make_query(email_recipients='a@x.it')
        with patch('monitor_service.email_service.send_system_notification') as send:
            monitor_service._send_monitor_alert(query, 'failed', failures=3, message='boom')
        assert not send.called


# --- Templates ---

ERRORS = [{'ID': 'E-1', 'MSG': '<b>bold</b>'}]


def send_notification(query):
    """Sends the notification with a fake SMTP server; returns (result, HTML of the email sent)."""
    from email_service import email_service
    with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, patch('email_service.smtplib.SMTP') as smtp:
        result = email_service.send_error_notification(query, ERRORS, ['ID', 'MSG'])
    server = (smtp_ssl if smtp_ssl.called else smtp).return_value
    if not server.sendmail.called:
        return result, None
    message = email.message_from_string(server.sendmail.call_args.args[2])
    for part in message.walk():
        if part.get_content_type() == 'text/html':
            return result, part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
    return result, None


class TestEmailTemplate:

    def test_template_cannot_run_python_code(self, app, monkeypatch):
        monkeypatch.setenv('EE_SECRET_MARKER', 'value-read-from-the-server')
        # Inside a tag: a field that starts with { and ends with } is a template name
        query = make_query(email_template="<p>{{ cycler.__init__.__globals__.os.environ['EE_SECRET_MARKER'] }}</p>")
        result, html = send_notification(query)
        # The notification still goes out, with the default template
        assert result['success'] is True
        assert 'value-read-from-the-server' not in html
        assert 'E-1' in html

    def test_template_cannot_see_the_configuration(self, app):
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['MAIL_PASSWORD'] = 'test-smtp-password'
        query = make_query(email_template='<p>{{ config }}|{{ config.SECRET_KEY }}|{{ config.MAIL_PASSWORD }}</p>')
        result, html = send_notification(query)
        assert result['success'] is True
        assert 'test-secret-key' not in html
        assert 'test-smtp-password' not in html

    def test_template_name_cannot_leave_the_email_folder(self, app):
        query = make_query(email_template='{../login}')
        result, html = send_notification(query)
        assert result['success'] is True
        assert 'Sign in' not in html   # text of templates/login.html
        assert 'E-1' in html                # default template

    def test_inline_template_that_starts_and_ends_with_jinja(self, app):
        query = make_query(email_template="{% for e in errors %}<p>Row {{ e['ID'] }}</p>{% endfor %}")
        result, html = send_notification(query)
        assert result['success'] is True and '<p>Row E-1</p>' in html

    def test_custom_template_works_and_data_is_escaped(self, app):
        query = make_query(email_template=(
            "<h1>{{ query_name }}</h1>"
            "{% for e in errors %}<p>{{ e['ID'] }} {{ e['MSG'] }}</p>{% endfor %}"
            "<p>{{ check_time|localtime('%Y') }}</p>"
        ))
        result, html = send_notification(query)
        assert result['success'] is True
        assert '<h1>Orders</h1>' in html
        assert 'E-1 &lt;b&gt;bold&lt;/b&gt;' in html
        assert '<b>bold</b>' not in html

    def test_default_template_works(self, app):
        result, html = send_notification(make_query())
        assert result['success'] is True
        assert 'E-1' in html and '&lt;b&gt;bold&lt;/b&gt;' in html


# --- Sample email from the query form ---

def send_sample(client, **form):
    """Sends the sample email with the given form values; returns (answer, _deliver mock)."""
    from email_service import email_service
    form.setdefault('recipients', 'me@x.it')
    form.setdefault('name', 'Draft')
    with patch.object(email_service, '_deliver', return_value={}) as deliver:
        answer = client.post('/api/test/sample-email', json=form)
    return answer, deliver


class TestSampleEmail:
    """The email of the query form, before saving: its subject, template and sample rows."""

    def test_what_the_form_shows(self, app, client):
        answer, deliver = send_sample(
            client, email_subject='{query_name}: {error_count} rows',
            email_template='<p>{{ query_name }}{% for e in errors %} {{ e.ID }}{% endfor %}</p>',
            columns=['ID'], rows=[{'ID': 'R-1'}, {'ID': 'R-2'}])
        result = answer.get_json()
        assert result['success'] is True and 'Test query' in result['message']
        recipients, subject, html, _ = deliver.call_args.args
        assert recipients == ['me@x.it']
        assert subject == '[Sample] Draft: 2 rows'
        assert '<p>Draft R-1 R-2</p>' in html
        assert EmailLog.query.count() == 0   # not a notification

    def test_rows_without_test_query(self, app, client):
        query = make_query()
        record = ErrorRecord(query_id=query.id, error_hash='h1')
        record.set_error_data({'ID': 'STORED-1'})
        db.session.add(record)
        db.session.commit()
        answer, deliver = send_sample(client, query_id=query.id)
        assert 'latest errors' in answer.get_json()['message']
        assert 'STORED-1' in deliver.call_args.args[2]
        # A new query, without errors yet: one made-up row with the key fields
        answer, deliver = send_sample(client, key_fields='ORDER_NO, ITEM')
        assert 'made-up' in answer.get_json()['message']
        html = deliver.call_args.args[2]
        assert 'ORDER_NO' in html and 'ITEM' in html

    def test_templates_that_cannot_be_used_are_reported_not_hidden(self, app, client):
        answer, deliver = send_sample(client, email_template='<p>{% for e in errors %}</p>')
        result = answer.get_json()
        assert result['success'] is False and 'template' in result['message']
        assert 'default template' in result['message']
        assert not deliver.called
        answer, deliver = send_sample(client, email_template='{no_such_template}')
        result = answer.get_json()
        assert result['success'] is False and 'no_such_template.html' in result['message']
        assert not deliver.called

    def test_unknown_placeholders_warned(self, app, client):
        answer, deliver = send_sample(client, email_subject='Errors for {custmer}')
        result = answer.get_json()
        assert result['success'] is True and '{custmer}' in result['warning']
        assert deliver.call_args.args[1] == '[Sample] Errors for {custmer}'

    def test_refused_addresses_reported(self, app, client):
        with patch('email_service.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.sendmail.return_value = {'bad@x.it': (550, b'5.1.1 User unknown')}
            answer = client.post('/api/test/sample-email', json={'recipients': 'me@x.it, bad@x.it', 'name': 'Draft'})
        result = answer.get_json()
        assert result['success'] is True
        assert 'sent to me@x.it' in result['message'] and 'bad@x.it' not in result['message']
        assert 'bad@x.it: 550 5.1.1 User unknown' in result['warning']

    def test_addresses_checked(self, app, client):
        answer, deliver = send_sample(client, recipients='not-an-address')
        assert answer.status_code == 400 and not deliver.called
        answer, deliver = send_sample(client, recipients='')
        assert answer.status_code == 400 and not deliver.called

    def test_email_off(self, email_off, client):
        from email_service import EMAIL_OFF_MESSAGE
        answer, deliver = send_sample(client)
        assert answer.get_json() == {'success': False, 'message': EMAIL_OFF_MESSAGE}
        assert not deliver.called

    def test_in_the_query_form(self, app, client):
        query = make_query(email_recipients='ops@x.it, it@x.it')
        html = client.get(f'/queries/{query.id}/edit').get_data(as_text=True)
        assert 'Send sample email' in html
        assert 'id="sampleEmailRecipients"' in html and 'value="ops@x.it, it@x.it"' in html
        app.config['MAIL_SERVER'] = ''
        html = client.get('/queries/new').get_data(as_text=True)
        assert 'Send sample email' not in html and 'Email is off' in html
