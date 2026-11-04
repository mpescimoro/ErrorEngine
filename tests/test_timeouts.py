"""
Timeouts of database sources, SMTP and HTTP; a stuck query does not hold up the others.
"""
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

from models import db, MonitoredQuery, DatabaseConnection

OK = {'success': True}


def make_query(name='Critical', **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    query = MonitoredQuery(name=name, key_fields='ID', **kwargs)
    db.session.add(query)
    db.session.commit()
    return query


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

    def test_a_stuck_query_does_not_stop_the_others(self, file_app, pool):
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
            started = pool.dispatch_due_queries(file_app)
            assert sorted(started) == sorted([slow.id, fast.id])
            for _ in range(50):
                if 'Fast' in finished:
                    break
                time.sleep(0.05)
            assert finished == ['Fast']

            # Next round: the slow one is still running and is not started twice
            assert pool.dispatch_due_queries(file_app) == [fast.id]
            assert pool.get_scheduler_status()['in_flight'] >= 1
            release.set()
            pool.shutdown(wait=True)
        assert finished.count('Slow') == 1 and finished.count('Fast') == 2
        assert pool.get_scheduler_status()['last_tick_at'] is not None
