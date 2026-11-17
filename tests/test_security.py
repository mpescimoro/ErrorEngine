"""
Security: escaped names and messages, write statements in Test query, secrets kept out of
messages and logs, no scheduler REST API, the production server (start scripts, settings
checked at startup, one copy per port, orderly stop).
"""
import ast
import http.client
import io
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from flask import Flask
from flask_apscheduler import APScheduler

from config import Config, DevelopmentConfig, ProductionConfig
from models import db, MonitoredQuery, DatabaseConnection, NotificationChannel, QueryLog
from utils import redact_secrets, RedactSecretsFilter
from validators import write_statement_keyword

ROOT = Path(__file__).resolve().parent.parent


def project_copy(tmp_path):
    """A copy of the project, a free port and the settings: serve.py writes its log next to itself
    and reads the .env there."""
    project = tmp_path / 'ErrorEngine'
    shutil.copytree(ROOT, project, ignore=shutil.ignore_patterns(
        '.git', 'venv', '.venv', 'logs', 'instance', 'lib', 'docs', 'tests', '__pycache__', '*.db', '.env'))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT') if name in os.environ}
    env.update(SECRET_KEY='s' * 32, ERRORENGINE_HOST='127.0.0.1', ERRORENGINE_PORT=str(port),
               DATABASE_URL=f"sqlite:///{(tmp_path / 'ee.db').as_posix()}")
    return project, port, env


def start(project, port, env):
    """serve.py, once its port answers."""
    server = subprocess.Popen([sys.executable, str(project / 'serve.py')], env=env, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while True:
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            return server
        except OSError:
            assert server.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)


def make_query(name='Orders', **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    query = MonitoredQuery(name=name, **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


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

    def test_escape_html_also_escapes_quotes(self):
        # The page scripts put values inside attributes too: value="..." of a routing condition
        node = shutil.which('node')
        if not node:
            pytest.skip('Node.js not installed')
        js = (ROOT / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
        function = re.search(r'^function escapeHtml\(text\) \{.*?^\}', js, re.S | re.M).group(0)
        script = function + "\nprocess.stdout.write(escapeHtml(`<a title=\"x\">Tom's & co</a>`));"
        out = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=60)
        assert out.stdout == '&lt;a title=&quot;x&quot;&gt;Tom&#39;s &amp; co&lt;/a&gt;', out.stderr


class TestTestQuery:

    @pytest.fixture
    def connection(self, app, tmp_path):
        import sqlite3
        path = tmp_path / 'source.db'
        # The with of sqlite3 commits but does not close: closing() does
        with closing(sqlite3.connect(path)) as c, c:
            c.execute('CREATE TABLE orders (id INTEGER)')
        conn = DatabaseConnection(name='Source', db_type='sqlite', database=str(path))
        db.session.add(conn)
        db.session.commit()
        return conn, path

    def tables(self, path):
        import sqlite3
        with closing(sqlite3.connect(path)) as c:
            return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def test_write_statement_refused_without_confirmation(self, app, client, connection):
        conn, path = connection
        response = client.post(f'/api/connections/{conn.id}/test-query', json={'sql': 'DROP TABLE orders'})
        assert response.status_code == 400
        assert 'DROP' in response.get_json()['error']
        assert self.tables(path) == ['orders']

    def test_select_still_works(self, app, client, connection):
        conn, _ = connection
        result = client.post(f'/api/connections/{conn.id}/test-query', json={'sql': 'SELECT * FROM orders'}).get_json()
        assert result['valid'] is True

    def test_keyword_check_like_the_form(self):
        assert write_statement_keyword('select 1') is None
        assert write_statement_keyword('  WITH x AS (SELECT 1) SELECT * FROM x') is None
        assert write_statement_keyword('/* note */ -- line\n delete from orders') == 'DELETE'
        assert write_statement_keyword('EXEC proc') == 'EXEC'

    def test_button_asks_like_the_save(self):
        js = (ROOT / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
        test = js[js.index('async function testSqlQuery'):]
        test = test[:test.index('\n}\n')]
        assert 'sqlSafety.confirm(keyword)' in test and 'confirm_write: confirmWrite' in test


class TestSecrets:

    def test_redact(self):
        assert redact_secrets('for url: https://api.x.com/v1/items?apikey=TOPSECRET&page=2') == \
            'for url: https://api.x.com/v1/items?apikey=***&page=***'
        assert 'pa55' not in redact_secrets('https://user:pa55@host.x.com/a')
        assert redact_secrets('with url: /bot123456:AAE-xyz_abcdefghijklmnop/sendMessage') == \
            'with url: /bot***/sendMessage'
        assert 'sig=***' in redact_secrets('https://prod.logic.azure.com/workflows/0123456789abcdef0123456789abcdef/x?sig=Abc')
        assert redact_secrets('no such table: shipments') == 'no such table: shipments'

    def test_http_source_failure_does_not_spread_the_key(self, app):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(source_type='http', email_recipients='ops@x.it')
        query.set_source_config({'url': 'https://api.example.com/items?apikey=TOPSECRET-123'})
        db.session.commit()
        response = requests.Response()
        response.status_code, response.url = 401, 'https://api.example.com/items?apikey=TOPSECRET-123'
        from monitor_service import monitor_service
        with patch('data_sources.requests.request', return_value=response), \
             patch('monitor_service.email_service.send_system_notification',
                   return_value={'success': True}) as alert:
            monitor_service.check_query(query, force=True)
        log = QueryLog.query.filter_by(query_id=query.id).first()
        assert '401' in log.error_message and 'TOPSECRET' not in log.error_message
        assert 'TOPSECRET' not in (query.last_failure_message or '')
        assert alert.called and 'TOPSECRET' not in json.dumps(alert.call_args.args, default=str)

    def test_telegram_error_does_not_show_the_token(self, app):
        from notification_service import notification_service
        error = requests.ConnectionError("HTTPSConnectionPool(host='api.telegram.org', port=443): "
                                         "Max retries exceeded with url: /bot123456:SECRETtokenSECRETtoken123/sendMessage")
        with patch('notification_service.requests.post', side_effect=error):
            result = notification_service._post_telegram('123456:SECRETtokenSECRETtoken123', '42', 'hi')
        assert result['success'] is False and 'SECRET' not in result['message']

    def test_log_lines_hidden(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactSecretsFilter())
        logger = logging.getLogger('test_redaction')
        logger.addHandler(handler)
        try:
            logger.error('failed: %s', 'https://h.x.com/a?token=TOPSECRET')
            try:
                raise RuntimeError('for url: https://h.x.com/b?key=TOPSECRET2')
            except RuntimeError:
                logger.exception('boom')
        finally:
            logger.removeHandler(handler)
        assert 'TOPSECRET' not in stream.getvalue() and 'token=***' in stream.getvalue()


class TestApiScheduler:

    @pytest.mark.parametrize('config_class', [Config, DevelopmentConfig, ProductionConfig])
    def test_off_in_every_configuration(self, config_class):
        assert config_class.SCHEDULER_API_ENABLED is False

    def test_no_scheduler_routes_registered(self):
        flask_app = Flask(__name__)
        flask_app.config.from_object(Config())
        APScheduler().init_app(flask_app)   # without start(): no threads
        assert not [r.rule for r in flask_app.url_map.iter_rules() if r.rule.startswith('/scheduler')]



class TestHeaders:
    """What the responses tell the browser."""

    def test_what_every_response_tells_the_browser(self, app, client):
        assert 'Secure' not in client.get('/').headers['Set-Cookie']
        for url in ('/', '/api/queries', '/static/css/app.css'):
            with client.get(url) as response:
                assert response.status_code == 200, url
                assert (response.headers['X-Content-Type-Options'], response.headers['X-Frame-Options'],
                        response.headers['Content-Security-Policy'], response.headers['Referrer-Policy']) == \
                    ('nosniff', 'DENY', "frame-ancestors 'none'", 'same-origin'), url
                assert 'Strict-Transport-Security' not in response.headers
        # Behind HTTPS: the cookie only over HTTPS, and the browser told to use HTTPS only
        app.config.update(HTTPS=True, SESSION_COOKIE_SECURE=True)
        response = app.test_client().get('/')
        assert 'Secure' in response.headers['Set-Cookie']
        assert response.headers['Strict-Transport-Security'] == 'max-age=31536000'

    def test_scripts_and_styles_keep_their_type_whatever_the_windows_registry_says(self):
        import mimetypes
        from app import create_app
        # What some Windows registries say, and Python reads it from there
        mimetypes.add_type('text/plain', '.js')
        mimetypes.add_type('text/plain', '.css')
        app = create_app('testing')
        client = app.test_client()
        with client.get('/static/js/app.js') as js, client.get('/static/css/app.css') as css:
            assert (js.mimetype, css.mimetype) == ('text/javascript', 'text/css')
        with app.app_context():
            db.engine.dispose()

class TestStartup:

    @pytest.mark.parametrize('script', ['start.bat', 'start.sh'])
    def test_scripts_start_serve_py(self, script):
        content = (ROOT / script).read_text(encoding='utf-8')
        assert 'python serve.py' in content
        assert 'app.py' not in content

    def test_settings_that_do_not_work_are_named_at_startup(self, caplog, monkeypatch):
        import config
        from app import create_app
        warnings = ("is not a time zone", "MAIL_USE_TLS is no longer used", "NOTIFY_RETRY_HOURS is no longer used")
        for name in ('MAIL_USE_TLS', 'NOTIFY_RETRY_HOURS'):
            monkeypatch.delenv(name, raising=False)
        with caplog.at_level(logging.WARNING, logger='app'):
            app = create_app('testing')
        with app.app_context():
            db.engine.dispose()
        assert not any(w in caplog.text for w in warnings)
        monkeypatch.setattr(config.TestingConfig, 'TIMEZONE', 'Europe/Rom')
        monkeypatch.setenv('MAIL_USE_TLS', 'true')
        monkeypatch.setenv('NOTIFY_RETRY_HOURS', '24')
        with caplog.at_level(logging.WARNING, logger='app'):
            app = create_app('testing')
        with app.app_context():
            db.engine.dispose()
        assert all(w in caplog.text for w in warnings)

    def test_a_setting_with_a_wrong_value_is_named(self, monkeypatch, caplog):
        from config import flag, whole_number
        for value, expected in (('', 4), (' 12 ', 12), ('0', 0)):
            monkeypatch.setenv('ERRORENGINE_THREADS', value)
            assert whole_number('ERRORENGINE_THREADS', 4) == expected
        for value, between in (('abc', None), ('2.5', None), ('0', (1, 65535)), ('65536', (1, 65535))):
            monkeypatch.setenv('ERRORENGINE_PORT', value)
            caplog.clear()
            with pytest.raises(SystemExit) as stop:
                whole_number('ERRORENGINE_PORT', 5000, between)
            assert stop.value.code == 1
            assert f'ERRORENGINE_PORT={value} in .env is not a whole number' in caplog.text
        for value, expected in (('', False), ('false', False), (' TRUE ', True)):
            monkeypatch.setenv('ERRORENGINE_HTTPS', value)
            assert flag('ERRORENGINE_HTTPS') is expected
        monkeypatch.setenv('ERRORENGINE_HTTPS', 'yes')
        with pytest.raises(SystemExit):
            flag('ERRORENGINE_HTTPS')
        assert 'ERRORENGINE_HTTPS=yes in .env is not true or false' in caplog.text

    @pytest.mark.skipif(sys.platform == 'win32', reason='SIGTERM: Windows ends a process without a signal')
    def test_the_server_takes_its_port_before_the_scheduler_and_stops_in_order(self, tmp_path):
        project, port, env = project_copy(tmp_path)
        serve = [sys.executable, str(project / 'serve.py')]

        def refused(**settings):
            out = subprocess.run(serve, env=dict(env, **settings), capture_output=True, text=True, timeout=60)
            assert out.returncode == 1, out.stderr
            # Stopped before the database and the scheduler, the locks of the first copy included
            assert 'Database' not in out.stderr and 'Scheduler' not in out.stderr, out.stderr
            assert 'Traceback' not in out.stderr, out.stderr
            return out.stderr

        assert 'ERRORENGINE_THREADS=abc in .env' in refused(ERRORENGINE_THREADS='abc')
        assert 'MAIL_PORT=2.5 in .env' in refused(MAIL_PORT='2.5')
        assert 'ERRORENGINE_HTTPS=yes in .env' in refused(ERRORENGINE_HTTPS='yes')
        assert 'ERRORENGINE_TRUSTED_PROXY=proxy.local in .env' in refused(ERRORENGINE_TRUSTED_PROXY='proxy.local')

        first = start(project, port, env)
        try:
            assert 'Is ErrorEngine already running?' in refused()
            health = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
            health.request('GET', '/api/health')
            assert health.getresponse().status == 200
            health.close()
            first.send_signal(signal.SIGTERM)
            assert first.wait(timeout=60) == 0
        finally:
            first.kill()
        log = (project / 'logs' / 'errorengine.log').read_text(encoding='utf-8')
        assert log.count('Scheduler started') == 1 and log.count('ErrorEngine stopped') == 1, log
        assert 'unexpected error' not in log

    def test_from_the_trusted_proxy_alone_the_client_and_the_host_come_from_its_headers(self, tmp_path):
        project, port, env = project_copy(tmp_path)
        forwarded = {'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Host': 'errorengine.example.com',
                     'X-Forwarded-Proto': 'https', 'Referer': 'https://errorengine.example.com/queries'}
        answers = []
        # No proxy; a proxy that is not who connects (written in full: it becomes ::1); the proxy that connects
        for proxy in ('', '0:0:0:0:0:0:0:1', '127.0.0.1'):
            server = start(project, port, dict(env, ERRORENGINE_TRUSTED_PROXY=proxy))
            try:
                # A form without its CSRF token: refused, back to its page if the host is the one of the form
                request = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
                request.request('POST', '/login', body='', headers=forwarded)
                response = request.getresponse()
                answers.append((response.getheader('Location'), response.getheader('Server')))
                request.close()
            finally:
                server.kill()
                server.wait()
        assert answers == [('/', None), ('/', None), ('/queries', None)]
        log = (project / 'logs' / 'errorengine.log').read_text(encoding='utf-8')
        assert 'Trusted reverse proxy: ::1' in log
        assert log.count('Refused POST /login from 127.0.0.1') == 2 and log.count('from 203.0.113.9') == 1

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
