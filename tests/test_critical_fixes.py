"""
Tests of the fixes for the 5 critical problems:
1. failed notifications retried until they reach everyone
2. alert when a query cannot read its source
3. source timeouts and queries isolated in the scheduler
4. connections closed through the driver (AS/400 with ibm_db)
5. no test that connects to real systems (see scripts/check_ibmi.py)
"""
from datetime import timedelta
import json
import sqlite3
import threading
import time
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text

from models import (db, MonitoredQuery, ErrorRecord, RoutingRule, RoutingCondition,
                    NotificationChannel, DatabaseConnection, EmailLog, ensure_schema)

ROWS = (['ID', 'TEAM'], [{'ID': 1, 'TEAM': 'PED'}, {'ID': 2, 'TEAM': 'IT'}])
OK = {'success': True}
DOWN = {'success': False, 'message': 'Connection refused', 'smtp_unavailable': True}


def make_query(name='Critical', rules=(), channels=(), **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    query = MonitoredQuery(name=name, key_fields='ID', **kwargs)
    for channel in channels:
        query.notification_channels.append(channel)
    db.session.add(query)
    db.session.flush()
    for i, (recipients, conditions) in enumerate(rules):
        rule = RoutingRule(query_id=query.id, name=f'Rule {i}', recipients=recipients,
                           priority=i, is_active=True, condition_logic='OR')
        db.session.add(rule)
        db.session.flush()
        for field, value in conditions:
            db.session.add(RoutingCondition(rule_id=rule.id, field_name=field, operator='equals', value=value))
    db.session.commit()
    return query


def make_channel(name, channel_type='webhook'):
    channel = NotificationChannel(name=name, channel_type=channel_type, config='{}', is_active=True)
    db.session.add(channel)
    db.session.commit()
    return channel


def run(query, rows=ROWS, email=None, **email_kwargs):
    """Runs a check with a simulated source and email delivery."""
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source', return_value=rows), \
         patch('monitor_service.email_service.send_error_notification',
               **({'side_effect': email} if callable(email) else {'return_value': email or OK})) as send:
        result = monitor_service.check_query(query, force=True)
    return result, send


def records(query):
    return {e.get_error_data()['ID']: e for e in ErrorRecord.query.filter_by(query_id=query.id)}


def wait_for_retry(query):
    """As if the wait before the next attempt were over."""
    for error in ErrorRecord.query.filter_by(query_id=query.id):
        if error.notify_retry_at:
            error.notify_retry_at = error.notify_retry_at - timedelta(hours=2)
    db.session.commit()


# ---------------------------------------------------------------------------
# 1. Retried notifications
# ---------------------------------------------------------------------------

class TestRetriedNotifications:

    def test_smtp_down_then_up_the_error_is_notified(self, app):
        query = make_query(email_recipients='ops@x.it', reminder_enabled=True)
        _, send = run(query, email=DOWN)
        assert send.call_count == 1
        error = records(query)[1]
        assert error.notify_pending is True and error.email_sent is False
        assert error.notify_attempts == 1 and 'Connection refused' in error.last_notify_error

        # Not before the wait is over
        _, send = run(query, email=OK)
        assert send.call_count == 0

        wait_for_retry(query)
        _, send = run(query, email=OK)
        assert send.call_count == 1
        assert [e['ID'] for e in send.call_args.kwargs['errors']] == [1, 2]
        error = records(query)[1]
        assert error.notify_pending is False and error.email_sent is True
        assert error.get_notified_to() == ['ops@x.it']
        assert error.last_notify_error is None

        # Delivered: it does not go out again at the next round
        _, send = run(query, email=OK)
        assert send.call_count == 0

    def test_smtp_down_no_other_attempt_in_the_same_round(self, app):
        query = make_query(routing_enabled=True, routing_default_recipients='it@x.it',
                           rules=[('a@x.it', [('TEAM', 'PED')]), ('b@x.it', [('TEAM', 'IT')])])
        _, send = run(query, email=DOWN)
        assert send.call_count == 1
        assert all(e.notify_pending for e in records(query).values())

    def test_partial_delivery_retries_only_the_missing_ones(self, app):
        query = make_query(routing_enabled=True, routing_default_recipients='it@x.it',
                           rules=[('user1@x.it, user2@x.it, it@x.it', [('TEAM', 'IT')])])
        rows = (['ID', 'TEAM'], [{'ID': 2, 'TEAM': 'IT'}])

        def user2_rejected(**kwargs):
            if kwargs['recipients_override'] == ['user2@x.it']:
                return {'success': False, 'message': '450 mailbox busy'}
            return OK
        run(query, rows=rows, email=user2_rejected)
        error = records(query)[2]
        assert error.get_notified_to() == ['user1@x.it', 'it@x.it']
        assert error.notify_pending is True and error.email_sent is True

        wait_for_retry(query)
        _, send = run(query, rows=rows, email=OK)
        assert [c.kwargs['recipients_override'] for c in send.call_args_list] == [['user2@x.it']]
        error = records(query)[2]
        assert error.notify_pending is False
        assert error.get_notified_to() == ['user1@x.it', 'user2@x.it', 'it@x.it']

    def test_failed_channel_retried_alone(self, app):
        telegram, webhook = make_channel('Telegram', 'telegram'), make_channel('Webhook')
        query = make_query(email_recipients='ops@x.it', channels=[telegram, webhook])

        def outcome(channel, query, errors):
            return {'success': channel.name == 'Telegram', 'message': 'HTTP 500'}
        with patch('notification_service.notification_service.send_to_channel', side_effect=outcome) as ch:
            _, send = run(query)
        assert sorted(c.args[0].name for c in ch.call_args_list) == ['Telegram', 'Webhook']
        error = records(query)[1]
        assert error.get_notified_channels() == [telegram.id] and error.notify_pending is True

        wait_for_retry(query)
        with patch('notification_service.notification_service.send_to_channel',
                   return_value={'success': True, 'message': 'OK'}) as ch:
            _, send = run(query)
        assert [c.args[0].name for c in ch.call_args_list] == ['Webhook']
        assert send.call_count == 0  # the email had already arrived
        assert all(not e.notify_pending for e in records(query).values())

    def test_error_gone_is_no_longer_retried(self, app):
        query = make_query(email_recipients='ops@x.it')
        run(query, email=DOWN)
        _, send = run(query, rows=(['ID'], []), email=OK)
        assert send.call_count == 0
        assert all(e.resolved_at and not e.notify_pending for e in records(query).values())

    def test_errors_from_before_the_update_are_not_resent(self, app):
        """Rows created before the fix (notify_pending false): no mass delivery."""
        query = make_query(email_recipients='ops@x.it')
        for row in ROWS[1]:
            error = ErrorRecord(query_id=query.id, email_sent=False,
                                error_hash=ErrorRecord.calculate_hash(row, ['ID']))
            error.set_error_data(row)
            db.session.add(error)
        db.session.commit()
        _, send = run(query)
        assert send.call_count == 0

    def test_results_saved_before_sending(self, tmp_path):
        """While sending, the errors found are already in the DB: another connection sees them."""
        import config
        from app import create_app
        db_file = tmp_path / 'ee.db'
        original = config.TestingConfig.SQLALCHEMY_DATABASE_URI
        config.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_file}'
        try:
            app = create_app('testing')
        finally:
            config.TestingConfig.SQLALCHEMY_DATABASE_URI = original
        with app.app_context():
            query = make_query(email_recipients='ops@x.it')
            seen = {}

            def count_from_outside(**kwargs):
                with sqlite3.connect(db_file, timeout=1) as other:
                    seen['rows'] = other.execute('SELECT COUNT(*) FROM error_records').fetchone()[0]
                    # and the DB accepts writes from others while sending
                    other.execute('UPDATE monitored_queries SET description = ? WHERE id = ?', ('x', query.id))
                return OK
            run(query, email=count_from_outside)
            assert seen['rows'] == 2
            with db.engine.connect() as c:
                assert c.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
            db.session.remove()
            db.drop_all()


class TestChannels:
    """Channel fixes needed so that repeated attempts do not repeat forever."""

    def test_teams_202_response_is_a_successful_delivery(self, app):
        from notification_service import notification_service
        with patch('notification_service.requests.post') as post:
            post.return_value.status_code = 202
            assert notification_service._post_teams('https://example.invalid/workflow', {})['success'] is True

    def test_telegram_converts_html_characters(self, app):
        from notification_service import notification_service
        query = MagicMock(name='Q')
        query.name = 'Orders <B2B>'
        with patch('notification_service.requests.post') as post:
            post.return_value.json.return_value = {'ok': True}
            notification_service._send_telegram({'bot_token': 'T', 'chat_id': '1'}, query,
                                                [{'NOTE': 'qty < 0 & stock > 5'}])
        message = post.call_args.kwargs['json']['text']
        assert 'Orders &lt;B2B&gt;' in message
        assert 'qty &lt; 0 &amp; stock &gt; 5' in message
        assert message.startswith('<b>ErrorEngine</b>')

    def test_webhook_with_decimal_values(self, app):
        from notification_service import notification_service
        query = MagicMock(id=1, description='')
        query.name = 'IBM i'
        with patch('notification_service.requests.request') as request:
            request.return_value.status_code = 200
            result = notification_service._send_webhook({'url': 'https://example.invalid/hook'}, query,
                                                        [{'AMOUNT': Decimal('12.50')}])
        assert result['success'] is True
        assert json.loads(request.call_args.kwargs['data'])['errors'] == [{'AMOUNT': '12.50'}]


# ---------------------------------------------------------------------------
# 2. Query that cannot read its source
# ---------------------------------------------------------------------------

def fail(query, message='ORA-12541: TNS:no listener'):
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source', side_effect=Exception(message)):
        return monitor_service.check_query(query, force=True)


class TestFailingSourceAlert:

    @pytest.fixture
    def alerts(self):
        with patch('monitor_service.email_service.send_system_notification', return_value=OK) as send:
            yield send

    def test_alert_after_three_failures_only_once(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        fail(query)
        assert alerts.call_count == 0
        fail(query)
        assert alerts.call_count == 1
        recipients, subject = alerts.call_args.args[0], alerts.call_args.args[1]
        assert recipients == ['ops@x.it'] and 'failing' in subject
        assert any('ORA-12541' in line for line in alerts.call_args.args[3])
        assert query.consecutive_failures == 3 and query.failure_alerted_at is not None
        fail(query)
        assert alerts.call_count == 1

    def test_configurable_threshold_and_off_switch(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        app.config['MONITOR_FAILURE_THRESHOLD'] = 0
        for _ in range(5):
            fail(query)
        assert alerts.call_count == 0 and query.consecutive_failures == 5
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        fail(query)
        assert alerts.call_count == 1

    def test_after_a_failure_the_scheduler_retries_right_away(self, app):
        """A failed daily query is retried at the next scheduler round."""
        from datetime import datetime, timedelta
        from utils import local_to_utc
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=1440,
                           schedule_days='1,2,3,4,5,6,7')
        today = datetime(2026, 10, 30, 8, 0)
        yesterday = local_to_utc(today - timedelta(days=1))
        query.last_check_at = yesterday          # last successful check: yesterday
        db.session.commit()
        fail(query)
        # last_check_at does not move: one minute after today's check it is still due
        assert query.last_check_at == yesterday
        assert query.should_run_now(today + timedelta(minutes=1))[0] is True

    def test_recovery_alert_and_reset(self, app, alerts):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        run(query)
        assert alerts.call_count == 2
        assert 'recovered' in alerts.call_args.args[1]
        assert query.consecutive_failures == 0 and query.failure_alerted_at is None
        assert query.failing_since is None and query.last_failure_message is None

    def test_no_recovery_alert_if_none_was_sent(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        run(query)
        assert alerts.call_count == 0 and query.consecutive_failures == 0

    def test_alert_not_sent_is_retried(self, app):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(email_recipients='ops@x.it')
        with patch('monitor_service.email_service.send_system_notification',
                   return_value={'success': False}) as send:
            fail(query)
        assert send.call_count == 1 and query.failure_alerted_at is None
        with patch('monitor_service.email_service.send_system_notification', return_value=OK) as send:
            fail(query)
        assert send.call_count == 1 and query.failure_alerted_at is not None

    def test_admin_recipients_when_configured(self, app, alerts):
        app.config.update(ADMIN_EMAILS='it@x.it, dba@x.it', MONITOR_FAILURE_THRESHOLD=1)
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        assert alerts.call_args.args[0] == ['it@x.it', 'dba@x.it']

    def test_alert_on_channels_too(self, app):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        channel = make_channel('Teams', 'teams')
        query = make_query(channels=[channel])
        with patch('notification_service.notification_service.send_system_message',
                   return_value={'total': 1, 'success': 1}) as message:
            fail(query)
        assert message.call_args.args[1] == 'monitor_failed'
        assert query.failure_alerted_at is not None

    def test_real_service_email(self, app):
        """send_system_notification builds the email without the user templates and logs it."""
        from email_service import email_service
        query = make_query(email_recipients='ops@x.it')
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, patch('email_service.smtplib.SMTP') as smtp:
            result = email_service.send_system_notification(
                ['ops@x.it'], '[ErrorEngine] Query failing: Critical', 'Title <b>',
                ['Last error: a < b'], query=query, email_type='monitor_failed')
        assert result['success'] is True
        server = (smtp_ssl if smtp_ssl.called else smtp).return_value
        assert 'a &lt; b' in server.sendmail.call_args.args[2] or 'a < b' in server.sendmail.call_args.args[2]
        assert EmailLog.query.filter_by(email_type='monitor_failed', status='sent').count() == 1

    def test_health_degraded_with_failing_queries(self, app, client, alerts):
        query = make_query()
        assert client.get('/api/health').get_json()['status'] == 'healthy'
        fail(query)
        data = client.get('/api/health').get_json()
        assert data['status'] == 'degraded' and data['checks']['queries_failing'] == 1

    def test_health_503_with_scheduler_stopped(self, app, client):
        stopped = {'enabled': True, 'running': False, 'last_tick_at': None, 'in_flight': 0, 'max_workers': 4}
        with patch('scheduler.get_scheduler_status', return_value=stopped):
            response = client.get('/api/health')
        assert response.status_code == 503
        assert response.get_json()['checks']['scheduler'] == 'stopped'

    def test_settings_show_the_real_state(self, app, client, alerts):
        query = make_query()
        fail(query)
        page = client.get('/settings').get_data(as_text=True)
        assert 'Not started in this process' in page
        assert 'Critical (1)' in page


# ---------------------------------------------------------------------------
# 3. Timeouts and isolated queries
# ---------------------------------------------------------------------------

class TestTimeouts:

    def test_postgres(self):
        pytest.importorskip('psycopg2')
        from db_drivers.postgres import PostgresDriver
        with patch('db_drivers.postgres.psycopg2.connect') as connect:
            PostgresDriver(connect_timeout=15, query_timeout=300).connect('h', 5432, 'db', 'u', 'p')
        assert connect.call_args.kwargs['connect_timeout'] == 15
        assert connect.call_args.kwargs['options'] == '-c statement_timeout=300000'

    def test_mysql_also_bounds_the_server_greeting(self):
        pytest.importorskip('pymysql')
        from db_drivers.mysql import MySQLDriver
        with patch('db_drivers.mysql.pymysql.connect') as connect:
            MySQLDriver(connect_timeout=15, query_timeout=300).connect('h', 3306, 'db', 'u', 'p')
        assert connect.call_args.kwargs['connect_timeout'] == 15
        assert connect.call_args.kwargs['read_timeout'] == 300

    def test_sqlserver(self):
        pytest.importorskip('pymssql')
        from db_drivers.sqlserver import SQLServerDriver
        with patch('db_drivers.sqlserver.pymssql.connect') as connect:
            SQLServerDriver(connect_timeout=15, query_timeout=300).connect('h', 1433, 'db', 'u', 'p')
        assert connect.call_args.kwargs['login_timeout'] == 15
        assert connect.call_args.kwargs['timeout'] == 300

    def test_oracle(self):
        pytest.importorskip('oracledb')
        from db_drivers.oracle import OracleDriver
        with patch('db_drivers.oracle.oracledb.connect') as connect:
            connection = OracleDriver(connect_timeout=15, query_timeout=300).connect('h', 1521, 'SVC', 'u', 'p')
        assert connect.call_args.kwargs['tcp_connect_timeout'] == 15.0
        assert connection.call_timeout == 300000

    def test_sqlite_stops_the_long_query(self):
        from db_drivers.sqlite import SQLiteDriver
        driver = SQLiteDriver(connect_timeout=5, query_timeout=1)
        connection = driver.open(None, None, ':memory:', None, None)
        endless = ('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) '
                   'SELECT COUNT(*) FROM n')
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            driver.execute_query(connection, endless)
        assert time.monotonic() - start < 10
        # The connection stays usable
        assert driver.execute_query(connection, 'SELECT 1 AS X') == (['X'], [{'X': '1'}])

    def test_real_sqlite_query_with_timeout(self, app, tmp_path):
        source = tmp_path / 'erp.db'
        with sqlite3.connect(source) as con:
            con.execute('CREATE TABLE errs (ID INTEGER, MSG TEXT)')
            con.execute("INSERT INTO errs VALUES (1, 'stuck')")
        connection = DatabaseConnection(name='ERP', db_type='sqlite', database=str(source))
        db.session.add(connection)
        db.session.flush()
        query = make_query(email_recipients='ops@x.it', db_connection_id=connection.id,
                           sql_query='SELECT ID, MSG FROM errs')
        from monitor_service import monitor_service
        with patch('monitor_service.email_service.send_error_notification', return_value=OK):
            result = monitor_service.check_query(query, force=True)
        assert result['status'] == 'success' and result['new_errors'] == 1

    def test_connection_without_answer_abandoned(self):
        from db_drivers import base
        closed = []

        class SlowDriver(base.DatabaseDriver):
            def connect(self, *args):
                time.sleep(1.5)
                return 'late connection'

            def execute_query(self, connection, sql):
                return [], []

            def close(self, connection):
                closed.append(connection)

        with patch.object(base, 'CONNECT_GRACE_SECONDS', 0):
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                SlowDriver(connect_timeout=0.3).open('h', 1, 'db', 'u', 'p')
            assert time.monotonic() - start < 1.2
        time.sleep(1.6)
        assert closed == ['late connection']

    def test_drivers_get_the_configured_timeouts(self, app):
        app.config.update(DB_CONNECT_TIMEOUT_SECONDS=7, DB_QUERY_TIMEOUT_SECONDS=42)
        driver = DatabaseConnection(name='c', db_type='sqlite', database=':memory:').get_driver()
        assert (driver.connect_timeout, driver.query_timeout) == (7, 42)

    def test_smtp_with_timeout(self, app):
        from email_service import email_service
        app.config['MAIL_TIMEOUT_SECONDS'] = 12
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, patch('email_service.smtplib.SMTP') as smtp:
            email_service.test_email('ops@x.it')
        constructor = smtp_ssl if smtp_ssl.called else smtp
        assert constructor.call_args.kwargs['timeout'] == 12
        assert constructor.return_value.quit.called

    def test_http_uses_the_configured_timeout(self, app):
        from data_sources import HttpDataSource
        app.config['HTTP_TIMEOUT_SECONDS'] = 9
        with patch('data_sources.requests.request') as request:
            request.return_value.json.return_value = []
            HttpDataSource().execute({'url': 'https://example.invalid/errors'})
        assert request.call_args.kwargs['timeout'] == 9


class TestIsolatedScheduler:

    @pytest.fixture
    def pool(self):
        import scheduler
        scheduler._executor = None
        scheduler._in_flight.clear()
        yield scheduler
        scheduler.shutdown(wait=True)

    def test_a_stuck_query_does_not_stop_the_others(self, app, pool):
        slow = make_query(name='Slow')
        fast = make_query(name='Fast')
        release = threading.Event()
        finished = []

        def check(query, force=False):
            if query.name == 'Slow':
                release.wait(10)
            finished.append(query.name)
            return {'status': 'success'}

        with patch.object(MonitoredQuery, 'should_run_now', return_value=(True, 'test')), \
             patch('monitor_service.monitor_service.check_query', side_effect=check):
            started = pool.dispatch_due_queries(app)
            assert sorted(started) == sorted([slow.id, fast.id])
            for _ in range(50):
                if 'Fast' in finished:
                    break
                time.sleep(0.05)
            assert finished == ['Fast']

            # Next round: the slow one is still running and is not started twice
            assert pool.dispatch_due_queries(app) == [fast.id]
            assert pool.get_scheduler_status()['in_flight'] >= 1
            release.set()
            pool.shutdown(wait=True)
        assert finished.count('Slow') == 1 and finished.count('Fast') == 2
        assert pool.get_scheduler_status()['last_tick_at'] is not None


# ---------------------------------------------------------------------------
# 4. Connections closed by the driver (ibm_db has no connection.close())
# ---------------------------------------------------------------------------

class SenzaClose:
    """Like ibm_db.IBM_DBConnection: no close() method."""


class DriverFinto:
    connect_timeout = query_timeout = None

    def __init__(self):
        self.closed = []

    def open(self, *args):
        return SenzaClose()

    def execute_query(self, connection, sql):
        return ['X'], [{'X': '1'}]

    def test_query(self, connection, sql, limit=5):
        return {'valid': True, 'columns': ['X'], 'row_count': 1, 'sample_rows': [{'X': '1'}], 'error': None}

    def close(self, connection):
        self.closed.append(connection)


class TestConnectionClosing:

    def test_query_run(self, app):
        driver = DriverFinto()
        connection = DatabaseConnection(name='as400', db_type='as400')
        with patch.object(DatabaseConnection, 'get_driver', return_value=driver):
            assert connection.execute_query('SELECT 1') == (['X'], [{'X': '1'}])
        assert len(driver.closed) == 1

    def test_test_query_from_the_page(self, app, client):
        connection = DatabaseConnection(name='as400', db_type='as400', host='h')
        db.session.add(connection)
        db.session.commit()
        driver = DriverFinto()
        with patch.object(DatabaseConnection, 'get_driver', return_value=driver):
            data = client.post(f'/api/connections/{connection.id}/test-query', json={'sql': 'SELECT 1'}).get_json()
        assert data['valid'] is True and len(driver.closed) == 1


# ---------------------------------------------------------------------------
# Updating existing databases
# ---------------------------------------------------------------------------

def test_new_columns_added_to_existing_databases(app):
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE error_records DROP COLUMN notify_pending'))
        connection.execute(text('ALTER TABLE monitored_queries DROP COLUMN consecutive_failures'))
        connection.execute(text(
            "INSERT INTO monitored_queries (name, sql_query, key_fields) VALUES ('Old', 'SELECT 1', 'ID')"))
        connection.execute(text(
            "INSERT INTO error_records (query_id, error_hash, error_data) VALUES (1, 'h1', '{\"ID\": 1}')"))

    assert sorted(ensure_schema()) == ['error_records.notify_pending', 'monitored_queries.consecutive_failures']
    with db.engine.connect() as connection:
        assert connection.execute(text('SELECT notify_pending FROM error_records')).scalar() == 0
        assert connection.execute(text('SELECT consecutive_failures FROM monitored_queries')).scalar() == 0
    assert ensure_schema() == []
