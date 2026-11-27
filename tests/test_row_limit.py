"""
MAX_ROWS: a query or an HTTP source that returns more rows than the limit fails, and creates
or resolves no error; the rows beyond the limit are not even read into dictionaries.
"""
import sqlite3
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest

from models import db, DatabaseConnection, ErrorRecord
from tests import helpers


def source(tmp_path, count, name='source.db'):
    """A SQLite source with `count` rows in t."""
    path = tmp_path / name
    with closing(sqlite3.connect(path)) as c, c:
        c.execute('CREATE TABLE IF NOT EXISTS t (ID INTEGER, STATUS TEXT)')
        c.execute('DELETE FROM t')
        c.executemany('INSERT INTO t VALUES (?, ?)', [(i, 'ERROR') for i in range(count)])
    return path


class Rows:
    """A DB-API cursor over `count` rows, that counts the rows read from it."""
    description = [('ID',)]

    def __init__(self, count):
        self.rows, self.read = [(i,) for i in range(count)], 0

    def execute(self, sql):
        pass

    def fetchmany(self, size):
        self.read += min(size, len(self.rows))
        return self.rows[:size]

    def fetchall(self):
        self.read = len(self.rows)
        return self.rows

    def __iter__(self):   # one row at a time, as the Oracle driver reads them
        for row in self.rows:
            self.read += 1
            yield row

    def close(self):
        pass


class TestDrivers:

    def test_sqlite_reads_up_to_the_limit(self, tmp_path):
        from db_drivers import TooManyRows
        from db_drivers.sqlite import SQLiteDriver
        path = str(source(tmp_path, 30))
        for limit in (30, None):   # exactly the limit is allowed; None: no limit
            driver = SQLiteDriver(max_rows=limit)
            with closing(driver.open(None, None, path, None, None)) as connection:
                assert len(driver.execute_query(connection, 'SELECT * FROM t')[1]) == 30
        driver = SQLiteDriver(max_rows=29)
        with closing(driver.open(None, None, path, None, None)) as connection:
            with pytest.raises(TooManyRows, match='more than 29 rows'):
                driver.execute_query(connection, 'SELECT * FROM t')

    @pytest.mark.parametrize('db_type', ['postgres', 'mysql', 'sqlserver', 'oracle'])
    def test_database_drivers_stop_past_the_limit(self, db_type):
        from db_drivers import DRIVERS, TooManyRows
        if db_type not in DRIVERS:
            pytest.skip(f'{db_type} driver not installed')
        connection = MagicMock()
        connection.cursor.return_value = cursor = Rows(1000)
        with pytest.raises(TooManyRows):
            DRIVERS[db_type](max_rows=10).execute_query(connection, 'SELECT ID FROM T')
        assert cursor.read == 11   # the rest is not read
        connection.cursor.return_value = Rows(10)
        assert len(DRIVERS[db_type](max_rows=10).execute_query(connection, 'SELECT ID FROM T')[1]) == 10
        connection.cursor.return_value = Rows(1000)
        assert len(DRIVERS[db_type]().execute_query(connection, 'SELECT ID FROM T')[1]) == 1000

    def test_ibm_i_stops_past_the_limit(self, monkeypatch):
        from db_drivers import ibmi, TooManyRows
        monkeypatch.setattr(ibmi, '_attach_as_daemon', lambda: None)
        value = MagicMock()
        value.getClass.return_value.getName.return_value = 'java.lang.Integer'

        def connection(count):
            result = MagicMock()
            result.next.side_effect = [True] * count + [False]
            result.getMetaData.return_value.getColumnCount.return_value = 1
            result.getMetaData.return_value.getColumnName.return_value = 'ID'
            result.getObject.return_value = value
            statement = MagicMock()
            statement.execute.return_value = True
            statement.getResultSet.return_value = result
            conn = MagicMock()
            conn.createStatement.return_value = statement
            return conn

        with pytest.raises(TooManyRows):
            ibmi.IBMiDriver(max_rows=3).execute_query(connection(4), 'SELECT ID FROM T')
        assert len(ibmi.IBMiDriver(max_rows=3).execute_query(connection(3), 'SELECT ID FROM T')[1]) == 3

    def test_as400_stops_past_the_limit(self):
        pytest.importorskip('ibm_db')
        from db_drivers import TooManyRows
        from db_drivers.as400 import AS400Driver
        with patch('db_drivers.as400.ibm_db') as ibm_db:
            ibm_db.num_fields.return_value = 1
            ibm_db.field_name.return_value = 'ID'
            ibm_db.fetch_assoc.side_effect = [{'ID': i} for i in range(4)] + [False]
            with pytest.raises(TooManyRows):
                AS400Driver(max_rows=3).execute_query('connection', 'SELECT ID FROM T')
            ibm_db.free_stmt.assert_called_once()   # the statement is released all the same
            ibm_db.fetch_assoc.side_effect = [{'ID': i} for i in range(3)] + [False]
            assert len(AS400Driver(max_rows=3).execute_query('connection', 'SELECT ID FROM T')[1]) == 3


class TestChecks:

    def test_past_the_limit_the_check_fails_and_changes_nothing(self, app, tmp_path):
        from monitor_service import monitor_service
        app.config['MAX_ROWS'] = 20
        path = source(tmp_path, 5)
        connection = DatabaseConnection(name='Source', db_type='sqlite', database=str(path), is_active=True)
        db.session.add(connection)
        db.session.commit()
        query = helpers.make_query(name='Orders', db_connection_id=connection.id,
                                   sql_query='SELECT ID, STATUS FROM t', email_recipients='')
        assert monitor_service.check_query(query, force=True)['new_errors'] == 5

        source(tmp_path, 25)   # a JOIN gone wrong
        result = monitor_service.check_query(query, force=True)
        assert result['status'] == 'error' and 'more than 20 rows' in result['error_message']
        assert 'MAX_ROWS' in result['error_message']
        assert result['new_errors'] == 0 and result['resolved_errors'] == 0
        assert ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).count() == 5
        assert query.consecutive_failures == 1

        app.config['MAX_ROWS'] = 0   # no limit
        assert monitor_service.check_query(query, force=True)['new_errors'] == 20

    def test_the_test_of_the_query_says_so(self, app, client, tmp_path):
        app.config['MAX_ROWS'] = 20
        connection = DatabaseConnection(name='Source', db_type='sqlite', database=str(source(tmp_path, 25)),
                                        is_active=True)
        db.session.add(connection)
        db.session.commit()
        data = client.post(f'/api/connections/{connection.id}/test-query', json={'sql': 'SELECT * FROM t'}).get_json()
        assert data['valid'] is False and 'more than 20 rows' in data['error']

    def test_http_source_past_the_limit(self, app):
        from data_sources import HttpDataSource
        app.config['MAX_ROWS'] = 20
        response = MagicMock(status_code=200, url='https://api.example.com/problems')
        response.json.return_value = [{'id': i} for i in range(25)]
        with patch('data_sources.requests.request', return_value=response):
            result = HttpDataSource().test({'url': 'https://api.example.com/problems'})
            assert result['success'] is False and 'more than 20 rows' in result['message']
            app.config['MAX_ROWS'] = 25
            assert HttpDataSource().test({'url': 'https://api.example.com/problems'})['success'] is True

    def test_the_default_is_ten_thousand(self, app):
        assert app.config['MAX_ROWS'] == 10000
