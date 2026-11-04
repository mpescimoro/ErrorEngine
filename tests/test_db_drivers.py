"""
Database drivers: connections closed through the driver, the IBM i JVM started once,
big DECIMAL values kept exact.
"""
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

from models import db, DatabaseConnection


class WithoutClose:
    """Like ibm_db.IBM_DBConnection: no close() method."""


class FakeDriver:
    connect_timeout = query_timeout = None

    def __init__(self):
        self.closed = []

    def open(self, *args):
        return WithoutClose()

    def execute_query(self, connection, sql):
        return ['X'], [{'X': '1'}]

    def test_query(self, connection, sql, limit=5):
        return {'valid': True, 'columns': ['X'], 'row_count': 1, 'sample_rows': [{'X': '1'}], 'error': None}

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
