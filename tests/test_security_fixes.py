"""
Tests of the security fixes (bugs 6-9 of the October 2026 review):
sandboxed email templates, scheduler REST API off, start scripts using the
production server, no inline JavaScript with data in delete confirmations.
"""
import ast
import email
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask_apscheduler import APScheduler

from config import Config, DevelopmentConfig, ProductionConfig
from models import db, MonitoredQuery, DatabaseConnection, NotificationChannel

ROOT = Path(__file__).resolve().parent.parent
ERRORS = [{'ID': 'E-1', 'MSG': '<b>bold</b>'}]


def make_query(**kwargs):
    query = MonitoredQuery(name=kwargs.pop('name', 'Orders'), sql_query='SELECT 1', key_fields='ID',
                           email_recipients='ops@x.it', email_subject='[ErrorEngine] {query_name}', **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


def send(query):
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


# ---------------------------------------------------------------------------
# 6. Email templates in a sandbox
# ---------------------------------------------------------------------------

class TestEmailTemplate:

    def test_template_cannot_run_python_code(self, app, monkeypatch):
        monkeypatch.setenv('EE_SECRET_MARKER', 'value-read-from-the-server')
        # Inside a tag: a field that starts with { and ends with } is a template name
        query = make_query(email_template="<p>{{ cycler.__init__.__globals__.os.environ['EE_SECRET_MARKER'] }}</p>")
        result, html = send(query)
        # The notification still goes out, with the default template
        assert result['success'] is True
        assert 'value-read-from-the-server' not in html
        assert 'E-1' in html

    def test_template_cannot_see_the_configuration(self, app):
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['MAIL_PASSWORD'] = 'test-smtp-password'
        query = make_query(email_template='<p>{{ config }}|{{ config.SECRET_KEY }}|{{ config.MAIL_PASSWORD }}</p>')
        result, html = send(query)
        assert result['success'] is True
        assert 'test-secret-key' not in html
        assert 'test-smtp-password' not in html

    def test_template_name_cannot_leave_the_email_folder(self, app):
        query = make_query(email_template='{../login}')
        result, html = send(query)
        assert result['success'] is True
        assert 'Sign in' not in html   # text of templates/login.html
        assert 'E-1' in html                # default template

    def test_custom_template_works_and_data_is_escaped(self, app):
        query = make_query(email_template=(
            "<h1>{{ query_name }}</h1>"
            "{% for e in errors %}<p>{{ e['ID'] }} {{ e['MSG'] }}</p>{% endfor %}"
            "<p>{{ check_time|localtime('%Y') }}</p>"
        ))
        result, html = send(query)
        assert result['success'] is True
        assert '<h1>Orders</h1>' in html
        assert 'E-1 &lt;b&gt;bold&lt;/b&gt;' in html
        assert '<b>bold</b>' not in html

    def test_default_template_works(self, app):
        result, html = send(make_query())
        assert result['success'] is True
        assert 'E-1' in html and '&lt;b&gt;bold&lt;/b&gt;' in html


# ---------------------------------------------------------------------------
# 7. Scheduler REST API off
# ---------------------------------------------------------------------------

class TestApiScheduler:

    @pytest.mark.parametrize('config_class', [Config, DevelopmentConfig, ProductionConfig])
    def test_off_in_every_configuration(self, config_class):
        assert config_class.SCHEDULER_API_ENABLED is False

    def test_no_scheduler_routes_registered(self):
        flask_app = Flask(__name__)
        flask_app.config.from_object(Config())
        APScheduler().init_app(flask_app)   # without start(): no threads
        assert not [r.rule for r in flask_app.url_map.iter_rules() if r.rule.startswith('/scheduler')]


# ---------------------------------------------------------------------------
# 8. Start scripts use the production server
# ---------------------------------------------------------------------------

class TestStartup:

    @pytest.mark.parametrize('script', ['start.bat', 'start.sh'])
    def test_scripts_start_serve_py(self, script):
        content = (ROOT / script).read_text(encoding='utf-8')
        assert 'python serve.py' in content
        assert 'app.py' not in content

    def test_waitress_on_every_platform_and_no_gunicorn(self):
        lines = [l.strip() for l in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                 if l.strip() and not l.strip().startswith('#')]
        assert any(l.startswith('waitress==') and ';' not in l for l in lines)
        assert not any(l.startswith('gunicorn') for l in lines)

    def test_development_server_local_only_without_reloader(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        main = next(node for node in tree.body if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "__name__ == '__main__'")
        run = next(node for node in ast.walk(main) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == 'run')
        kwargs = {k.arg: k.value for k in run.keywords}
        assert ast.literal_eval(kwargs['host']) == '127.0.0.1'
        assert ast.literal_eval(kwargs['use_reloader']) is False
        assert not (isinstance(kwargs['debug'], ast.Constant) and kwargs['debug'].value is True)


# ---------------------------------------------------------------------------
# 9. Delete confirmations and channel test results
# ---------------------------------------------------------------------------

NAME = "Order's errors <script>window.x=1</script>"
NAME_ESCAPED = 'Order&#39;s errors &lt;script&gt;window.x=1&lt;/script&gt;'


class TestConfirmationsAndXss:

    def test_confirmations_without_inline_javascript(self, app, client):
        make_query(name=NAME)
        db.session.add(DatabaseConnection(name=NAME, db_type='sqlite', database=':memory:'))
        db.session.add(NotificationChannel(name=NAME, channel_type='webhook'))
        db.session.commit()

        pages = {
            '/queries': f'data-confirm="Delete query {NAME_ESCAPED}? This cannot be undone."',
            '/connections': f'data-confirm="Delete connection {NAME_ESCAPED}?"',
            '/channels': f'data-confirm="Delete channel {NAME_ESCAPED}?"',
        }
        for url, attribute in pages.items():
            html = client.get(url).get_data(as_text=True)
            assert attribute in html, url
            assert 'return confirm(' not in html, url
            assert '<script>window.x=1</script>' not in html, url

    def test_app_js_asks_confirmation_for_data_confirm(self):
        js = (ROOT / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
        assert "dataset.confirm" in js and "addEventListener('submit'" in js

    def test_channel_test_result_is_escaped(self, app, client):
        channel = NotificationChannel(name='Webhook', channel_type='webhook')
        db.session.add(channel)
        db.session.commit()
        html = client.get(f'/channels/{channel.id}/edit').get_data(as_text=True)
        assert 'escapeHtml(res.message)' in html and 'escapeHtml(e.message)' in html
        assert "+ res.message +" not in html and "+ e.message +" not in html
