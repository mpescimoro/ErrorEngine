"""
Fixes from the second code review (November 2026): time windows, SMTP certificate,
Test query, secrets in messages, public health check, failed deliveries, IBM i,
API key, JSON path, regex, deleting queries.
"""
import io
import json
import logging
import ssl
import sys
import threading
import time
import types
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from models import db, MonitoredQuery, ErrorRecord, EmailLog, DatabaseConnection, NotificationChannel, QueryLog
from utils import get_utc_now, redact_secrets, RedactSecretsFilter
from validators import write_statement_keyword, validate_routing_rule

ROOT = Path(__file__).resolve().parent.parent


def make_query(name='Orders', **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    query = MonitoredQuery(name=name, **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


# ---------------------------------------------------------------------------
# F3 · Time windows
# ---------------------------------------------------------------------------

class TestTimeWindows:
    FRIDAY = datetime(2026, 10, 30)          # a Friday

    def at(self, day_offset, hh, mm, ss=0):
        return self.FRIDAY + timedelta(days=day_offset, hours=hh, minutes=mm, seconds=ss)

    def test_window_past_midnight(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0))
        assert q.is_in_schedule(self.at(0, 23, 30))
        assert q.is_in_schedule(self.at(1, 2, 0))
        assert not q.is_in_schedule(self.at(0, 12, 0))

    def test_night_belongs_to_the_day_it_starts(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0),
                       schedule_days='1,2,3,4,5')
        assert q.is_in_schedule(self.at(1, 2, 0))       # Saturday 02:00: Friday night
        assert not q.is_in_schedule(self.at(1, 23, 0))  # Saturday night
        assert not q.is_in_schedule(self.at(3, 2, 0))   # Monday 02:00: Sunday night

    def test_last_slot_of_the_window_runs(self, app):
        q = make_query(schedule_start_time=dtime(8, 0), schedule_end_time=dtime(18, 0),
                       check_interval_minutes=60)
        assert q.is_in_schedule(self.at(0, 18, 0, 20))
        assert not q.is_in_schedule(self.at(0, 18, 1))
        q.last_check_at = self.at(0, 17, 0, 30) - timedelta(hours=1)   # UTC, Rome is +1
        with app.app_context():
            app.config['TIMEZONE'] = 'Europe/Rome'
            should, reason = q.should_run_now(self.at(0, 18, 0, 20))
        assert should, reason

    def test_next_run_of_a_night_window(self, app):
        q = make_query(schedule_start_time=dtime(22, 0), schedule_end_time=dtime(6, 0),
                       check_interval_minutes=60)
        assert q.get_next_run_time(self.at(0, 12, 0)) == self.at(0, 22, 0)

    def test_no_days_means_every_day(self, app):
        assert make_query(schedule_days='').get_schedule_days_list() == [1, 2, 3, 4, 5, 6, 7]

    def test_form_warns_when_no_day_is_selected(self, app, client):
        q = make_query(schedule_days='')
        html = client.get(f'/queries/{q.id}/edit').get_data(as_text=True)
        assert 'id="noDaysModal"' in html and 'scheduleCheck.noDays()' in html
        # An empty value saved earlier shows every day, so it is not taken for "no days"
        assert 'name="schedule_days" id="schedule_days" \n                               value="1,2,3,4,5,6,7"' in html


# ---------------------------------------------------------------------------
# S1 · SMTP certificate
# ---------------------------------------------------------------------------

class TestSmtpTls:

    def send_test(self, app, **config):
        from email_service import email_service
        app.config.update(config)
        with patch('email_service.smtplib.SMTP') as smtp, patch('email_service.smtplib.SMTP_SSL') as smtp_ssl:
            email_service.test_email('ops@x.it')
        return smtp, smtp_ssl

    def test_starttls_checks_the_certificate(self, app):
        smtp, _ = self.send_test(app, MAIL_USE_TLS=True)
        context = smtp.return_value.starttls.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_ssl_checks_the_certificate(self, app):
        _, smtp_ssl = self.send_test(app, MAIL_USE_TLS=False)
        context = smtp_ssl.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_internal_ca_file(self, app):
        import certifi
        _, smtp_ssl = self.send_test(app, MAIL_USE_TLS=False, MAIL_CA_FILE=certifi.where())
        context = smtp_ssl.call_args.kwargs['context']
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        # Python 3.13 strict checks refuse many internal certificates: off for the chosen CA
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT

    def test_public_servers_keep_the_default_checks(self, app):
        _, smtp_ssl = self.send_test(app, MAIL_USE_TLS=False, MAIL_CA_FILE='')
        assert smtp_ssl.call_args.kwargs['context'].verify_flags == ssl.create_default_context().verify_flags


# ---------------------------------------------------------------------------
# S2 · Test query
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# S3 · Secrets out of messages, logs and alerts
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# S8 · Public health check
# ---------------------------------------------------------------------------

def test_public_health_check_shows_only_the_state(app, client):
    app.config.update(LOGIN_DISABLED=False, LOGIN_PASSWORD_HASH='x', SECRET_KEY='a-real-key', API_TOKEN='tok')
    public = client.get('/api/health').get_json()
    assert 'version' not in public and set(public['checks']) == {'database', 'scheduler'}
    detailed = client.get('/api/health', headers={'Authorization': 'Bearer tok'}).get_json()
    assert 'version' in detailed and 'queries_failing' in detailed['checks']


# ---------------------------------------------------------------------------
# F5 · Failed deliveries
# ---------------------------------------------------------------------------

def run_check(query, email_result=None, channel_result=None):
    from monitor_service import monitor_service
    rows = (['ID'], [{'ID': 1}])
    with patch('monitor_service.execute_query_source', return_value=rows), \
         patch('monitor_service.email_service.send_error_notification',
               return_value=email_result or {'success': True}), \
         patch('notification_service.notification_service.send_to_channel',
               return_value=channel_result or {'success': True, 'message': 'OK'}):
        return monitor_service.check_query(query, force=True)


class TestDeliveries:

    def test_http_answers(self):
        from notification_service import NotificationService
        for status in (400, 401, 403, 404, 410):
            assert NotificationService._http_failure(status)['permanent'] is True
        for status in (408, 429, 500, 502, 503):
            assert NotificationService._http_failure(status)['permanent'] is False

    def test_channel_that_refuses_is_not_retried(self, app, client):
        teams = NotificationChannel(name='Old Teams', channel_type='teams', config='{}', is_active=True)
        db.session.add(teams)
        query = make_query(email_recipients='ops@x.it')
        query.notification_channels.append(teams)
        db.session.commit()
        run_check(query, channel_result={'success': False, 'permanent': True, 'message': 'HTTP 410 Gone'})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.notify_pending is False
        assert error.get_undeliverable()['channels'] == [teams.id]
        assert 'HTTP 410' in error.last_notify_error
        html = client.get('/errors').get_data(as_text=True)
        assert 'Not delivered' in html and 'Delivery pending' not in html

    def test_waits_grow_between_attempts(self, app):
        query = make_query(email_recipients='ops@x.it')
        before = get_utc_now()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'smtp_unavailable': True})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.notify_pending and timedelta(seconds=50) < error.notify_retry_at - before < timedelta(minutes=2)
        error.notify_retry_at = get_utc_now() - timedelta(seconds=1)
        db.session.commit()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'smtp_unavailable': True})
        assert error.notify_attempts == 2 and error.notify_retry_at - get_utc_now() > timedelta(seconds=100)

    def test_given_up_after_the_retry_hours(self, app, client):
        query = make_query(email_recipients='ops@x.it')
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'smtp_unavailable': True})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        error.first_seen_at = get_utc_now() - timedelta(hours=25)
        error.notify_retry_at = None
        db.session.commit()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'smtp_unavailable': True})
        assert error.notify_pending is False
        assert error.get_undeliverable()['recipients'] == ['ops@x.it']
        assert error.last_notify_error.startswith('Not delivered after 2 attempts')
        assert 'Not delivered' in client.get('/errors').get_data(as_text=True)


# ---------------------------------------------------------------------------
# F8 · JVM started once · F12 · big DECIMAL
# ---------------------------------------------------------------------------

def test_jvm_started_once_with_threads_in_parallel(monkeypatch):
    from db_drivers import ibmi
    started = []

    def start_jvm(**kwargs):
        if started:
            raise OSError('JVM is already started')
        time.sleep(0.05)
        started.append(True)

    fake = types.ModuleType('jpype')
    fake.isJVMStarted = lambda: bool(started)
    fake.startJVM = start_jvm
    monkeypatch.setitem(sys.modules, 'jpype', fake)
    monkeypatch.setitem(sys.modules, 'jpype.imports', types.ModuleType('jpype.imports'))
    monkeypatch.setattr(ibmi, '_jvm_started', False)
    monkeypatch.setattr(ibmi.os.path, 'exists', lambda path: True)

    errors, barrier = [], threading.Barrier(4)

    def worker():
        barrier.wait()
        try:
            ibmi._ensure_jvm()
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and started == [True]


def test_big_integer_decimal_stays_exact():
    from db_drivers.ibmi import IBMiDriver

    class BigDecimal:
        def __init__(self, text):
            self.text = text

        def getClass(self):
            return MagicMock(getName=lambda: 'java.math.BigDecimal')

        def stripTrailingZeros(self):
            return self

        def scale(self):
            return 0

        def toBigInteger(self):
            return self.text

        def longValue(self):
            return -6101065172474983726      # what a Java long keeps of it

    driver = IBMiDriver.__new__(IBMiDriver)
    assert driver._java_to_python(BigDecimal('12345678901234567890')) == 12345678901234567890


# ---------------------------------------------------------------------------
# F1 · API key · F2 · JSON path
# ---------------------------------------------------------------------------

def http_response(body, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def test_api_key_from_the_form_is_sent(app):
    from data_sources import HttpDataSource
    with patch('data_sources.requests.request', return_value=http_response([{'ID': 1}])) as req:
        HttpDataSource().execute({'url': 'https://api.x.com/items', 'auth_type': 'api_key', 'auth_token': 'K-123'})
    assert req.call_args.kwargs['headers']['X-API-Key'] == 'K-123'


class TestJsonPath:

    def test_missing_step_is_a_source_failure(self, app):
        from data_sources import HttpDataSource
        with patch('data_sources.requests.request',
                   return_value=http_response({'status': 'error', 'message': 'rate limit exceeded'})):
            with pytest.raises(ValueError, match="'data' not found"):
                HttpDataSource().execute({'url': 'https://api.x.com', 'response_path': 'data.items'})

    def test_list_index_in_the_path(self, app):
        from data_sources import HttpDataSource
        body = {'data': [{'rows': [{'ID': 1}, {'ID': 2}]}]}
        with patch('data_sources.requests.request', return_value=http_response(body)):
            columns, rows = HttpDataSource().execute({'url': 'https://api.x.com', 'response_path': 'data.0.rows'})
        assert [r['ID'] for r in rows] == [1, 2]

    def test_error_body_does_not_resolve_the_real_errors(self, app):
        query = make_query(source_type='http', email_recipients='ops@x.it')
        query.set_source_config({'url': 'https://api.x.com', 'response_path': 'data.items'})
        db.session.commit()
        from monitor_service import monitor_service
        good = http_response({'data': {'items': [{'ID': 1}, {'ID': 2}]}})
        bad = http_response({'status': 'error', 'message': 'rate limit exceeded'})
        with patch('monitor_service.email_service.send_error_notification', return_value={'success': True}) as send:
            with patch('data_sources.requests.request', return_value=good):
                monitor_service.check_query(query, force=True)
            with patch('data_sources.requests.request', return_value=bad):
                result = monitor_service.check_query(query, force=True)
            with patch('data_sources.requests.request', return_value=good):
                monitor_service.check_query(query, force=True)
        assert result['status'] == 'error'
        assert ErrorRecord.query.filter_by(query_id=query.id).count() == 2
        assert ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count() == 0
        assert send.call_count == 1


# ---------------------------------------------------------------------------
# F11 · Regex · F13 · Deleting a query with email history
# ---------------------------------------------------------------------------

def test_case_insensitive_regex_keeps_its_meaning(app):
    from models import RoutingCondition
    from routing_service import evaluate_condition
    cond = RoutingCondition(field_name='CODE', operator='regex', value=r'^\D+$', case_sensitive=False)
    assert evaluate_condition({'CODE': 'ABC'}, cond) is True
    assert evaluate_condition({'CODE': '123'}, cond) is False
    cond = RoutingCondition(field_name='CODE', operator='regex', value='^abc$', case_sensitive=False)
    assert evaluate_condition({'CODE': 'ABC'}, cond) is True


def test_invalid_regex_is_refused():
    ok, message = validate_routing_rule({'recipients': 'a@x.it', 'conditions': [
        {'field_name': 'CODE', 'operator': 'regex', 'value': '([a-z'}]})
    assert not ok and 'regular expression' in message


def test_delete_query_with_email_history(app, client):
    from sqlalchemy import event
    engine = db.engine

    @event.listens_for(engine, 'connect')
    def foreign_keys_on(dbapi_connection, record):
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
    try:
        with engine.connect() as c:
            c.exec_driver_sql('PRAGMA foreign_keys=ON')
        query = make_query(email_recipients='ops@x.it')
        db.session.add(EmailLog(query_id=query.id, recipients='ops@x.it', status='sent'))
        db.session.commit()
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        response = client.post(f'/queries/{query.id}/delete')
        assert response.status_code == 302
        assert db.session.get(MonitoredQuery, query.id) is None
        log = EmailLog.query.one()
        assert log.query_id is None
    finally:
        event.remove(engine, 'connect', foreign_keys_on)
