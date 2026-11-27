"""
Database drivers: connections closed through the driver, the default port for a connection
saved without one, what a statement changes kept only where writes are allowed, the IBM i JVM
started once, big DECIMAL values kept exact.
"""
import re
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from models import db, DatabaseConnection


class WithoutClose:
    """Like ibm_db.IBM_DBConnection: no close() method."""


class FakeDriver:
    connect_timeout = query_timeout = None

    def __init__(self):
        self.closed = []

    def open(self, *args):
        return WithoutClose()

    def run(self, connection, sql, writes):
        return ['X'], [{'X': '1'}]

    def close(self, connection):
        self.closed.append(connection)


class TestConnectionClosing:

    def test_query_run(self, app):
        driver = FakeDriver()
        connection = DatabaseConnection(name='as400', db_type='as400')
        with patch.object(DatabaseConnection, 'get_driver', return_value=driver):
            assert connection.execute_query('SELECT 1') == (['X'], [{'X': '1'}])
        assert len(driver.closed) == 1

    def test_test_query_from_the_page(self, app, client):
        connection = DatabaseConnection(name='as400', db_type='as400', host='h')
        db.session.add(connection)
        db.session.commit()
        driver = FakeDriver()
        with patch.object(DatabaseConnection, 'get_driver', return_value=driver):
            data = client.post(f'/api/connections/{connection.id}/test-query', json={'sql': 'SELECT 1'}).get_json()
        assert data['valid'] is True and len(driver.closed) == 1


class TestPortLeftEmpty:
    """A connection saved without a port uses the driver's default, as the test in the form does."""

    def connection(self, db_type, port=None):
        return DatabaseConnection(name='ERP', db_type=db_type, host='db', port=port, database='X3',
                                  username='u', password='p')

    def test_oracle(self, app):
        pytest.importorskip('oracledb')
        with patch('db_drivers.oracle.oracledb.connect') as connect:
            assert self.connection('oracle').test_connection()['status'] == 'ok'
            assert connect.call_args.kwargs['dsn'] == 'db:1521/X3'
            self.connection('oracle', port=1600).test_connection()   # a port given is kept
            assert connect.call_args.kwargs['dsn'] == 'db:1600/X3'

    def test_as400_and_its_form(self, app, client):
        pytest.importorskip('ibm_db')
        with patch('db_drivers.as400.ibm_db') as ibm_db:
            assert self.connection('as400').test_connection()['status'] == 'ok'
        # 446: the DRDA port of IBM i, what this driver connects to through DB2 Connect
        assert 'PORT=446;' in ibm_db.connect.call_args.args[0]
        html = client.get('/connections/new').get_data(as_text=True)
        assert re.search(r'value="as400"[^>]*data-port="446"', html)


class NoRowsCursor:
    """The cursor of an INSERT: no description, and psycopg2, pymssql and oracledb fail when asked for rows."""
    description = None

    def execute(self, sql):
        pass

    def fetchall(self):
        raise AssertionError('rows asked to a statement without them')

    __iter__ = fetchall

    def close(self):
        pass


class Transaction:
    def __init__(self):
        self.ends = []

    def cursor(self):
        return NoRowsCursor()

    def set_session(self, readonly):
        """psycopg2: the transaction that follows is BEGIN READ ONLY."""

    def commit(self):
        self.ends.append('commit')

    def rollback(self):
        self.ends.append('rollback')


class TestWrites:
    """What a statement changes is committed where the connection allows writes, rolled back elsewhere."""

    @pytest.mark.parametrize('db_type', ['sqlite', 'postgres', 'mysql', 'sqlserver', 'oracle'])
    def test_committed_only_where_allowed_and_a_statement_without_rows_is_not_an_error(self, db_type):
        from db_drivers import DRIVERS
        if db_type not in DRIVERS:
            pytest.skip(f'{db_type} driver not installed')
        driver, connection = DRIVERS[db_type](), Transaction()
        assert driver.run(connection, 'INSERT INTO t VALUES (1)', writes=True) == ([], [])
        driver.run(connection, 'SELECT writes_something()', writes=False)
        assert connection.ends == ['commit', 'rollback']

    def test_postgres_asks_the_database_to_refuse_changes(self):
        """Where writes are not allowed, BEGIN READ ONLY before the statement: refused also what the text hides."""
        from db_drivers import DRIVERS
        if 'postgres' not in DRIVERS:
            pytest.skip('postgres driver not installed')
        events, connection = [], Transaction()
        connection.set_session = lambda readonly: events.append(('read only', readonly))
        connection.cursor = lambda: types.SimpleNamespace(execute=lambda sql: events.append(('run', sql)),
                                                          description=None, close=lambda: None)
        DRIVERS['postgres']().run(connection, 'SELECT 1', writes=False)
        DRIVERS['postgres']().run(connection, 'INSERT INTO t VALUES (1)', writes=True)
        assert events == [('read only', True), ('run', 'SELECT 1'), ('run', 'INSERT INTO t VALUES (1)')]
        assert connection.ends == ['rollback', 'commit']

    def test_sqlite_file(self, tmp_path):
        import sqlite3
        from contextlib import closing
        from db_drivers.sqlite import SQLiteDriver
        path = tmp_path / 'source.db'
        with closing(sqlite3.connect(path)) as c, c:
            c.execute('CREATE TABLE t (id INTEGER)')
        driver = SQLiteDriver()
        for writes, id_ in ((False, 1), (True, 2)):
            with closing(driver.open(None, None, str(path), None, None)) as connection:
                assert driver.run(connection, f'INSERT INTO t VALUES ({id_}) RETURNING id', writes) == \
                    (['id'], [{'id': str(id_)}])
        with closing(sqlite3.connect(path)) as c:
            assert c.execute('SELECT id FROM t').fetchall() == [(2,)]

    def test_ibm_i_jt400_keeps_autocommit_and_reads_nothing_after_an_insert(self, monkeypatch):
        from db_drivers import ibmi
        monkeypatch.setattr(ibmi, '_attach_as_daemon', lambda: None)

        class Statement:
            def execute(self, sql):
                return False     # an update count, no rows

            def getResultSet(self):
                raise AssertionError('rows asked to a statement without them')

            def close(self):
                pass

        class Connection:
            """Without setAutoCommit, commit and rollback: calling them fails the test."""
            def createStatement(self):
                return Statement()

        driver = ibmi.IBMiDriver()
        for writes in (True, False):
            assert driver.run(Connection(), 'INSERT INTO t VALUES (1)', writes) == ([], [])

    def test_ibm_i_ibm_db_keeps_autocommit_and_reads_nothing_after_an_insert(self):
        pytest.importorskip('ibm_db')
        from db_drivers.as400 import AS400Driver
        with patch('db_drivers.as400.ibm_db') as ibm_db:
            ibm_db.exec_immediate.return_value = 'statement'
            ibm_db.num_fields.return_value = 0
            driver = AS400Driver()
            for writes in (True, False):
                assert driver.run('connection', 'INSERT INTO t VALUES (1)', writes) == ([], [])
            ibm_db.autocommit.assert_not_called()
            ibm_db.commit.assert_not_called()
            ibm_db.rollback.assert_not_called()
            ibm_db.fetch_assoc.assert_not_called()


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
