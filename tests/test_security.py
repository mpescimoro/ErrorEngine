"""
Security: escaped names and messages, write statements in Test query, secrets kept out of
messages and logs, no scheduler REST API, the production server in the start scripts.
"""
import ast
import io
import json
import logging
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


class TestTestQuery:

    @pytest.fixture
    def connection(self, app, tmp_path):
        import sqlite3
        path = tmp_path / 'source.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE orders (id INTEGER)')
        conn = DatabaseConnection(name='Source', db_type='sqlite', database=str(path))
        db.session.add(conn)
        db.session.commit()
        return conn, path

    def tables(self, path):
        import sqlite3
        with sqlite3.connect(path) as c:
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
