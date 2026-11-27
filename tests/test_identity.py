"""
The identity of an error, version 2: the hash of the key values as a JSON list. A value containing | no longer
merges two errors, NULL is told apart from an empty value, two columns with the same name are refused instead
of being reduced to one. Migration 2 recomputes the hashes of the open errors, so that an upgrade notifies
nothing again.
"""
import hashlib
import sqlite3
from unittest.mock import patch

import pytest

import migrations
from models import db, DatabaseConnection, ErrorRecord, SchemaMigration
from tests.helpers import make_query
from utils import get_utc_now


def h(data, keys=('ID', 'CODE')):
    return ErrorRecord.calculate_hash(data, list(keys))


def v1(data, keys):
    """The formula of 2.5.1, as the hashes of a database of before were computed."""
    values = []
    for field in keys:
        value = next((v for k, v in data.items() if k.upper() == field.upper()), None)
        values.append(str(value) if value is not None else '')
    return hashlib.sha256('|'.join(values).encode()).hexdigest()


class TestTheHash:

    def test_a_value_with_the_separator_stays_apart(self):
        assert h({'ID': 'a|b', 'CODE': 'c'}) != h({'ID': 'a', 'CODE': 'b|c'})
        assert v1({'ID': 'a|b', 'CODE': 'c'}, ['ID', 'CODE']) == v1({'ID': 'a', 'CODE': 'b|c'}, ['ID', 'CODE'])

    def test_null_is_not_an_empty_value(self):
        assert h({'ID': '1', 'CODE': None}) != h({'ID': '1', 'CODE': ''})
        assert h({'ID': '1', 'CODE': None}) == h({'ID': '1'})       # missing: as NULL

    def test_the_same_from_the_row_and_from_the_saved_data(self):
        """Migration 2 computes the hashes from the saved data: they must be those of the rows."""
        rows = [{'ID': 'ORD-1', 'CODE': None}, {'ID': 7, 'CODE': 2.5}, {'ID': True, 'CODE': 'Pescimoro à 日本'},
                {'ID': {'a': 1, 'b': [1, 2]}, 'CODE': ''}, {'id': '9', 'code': 'x', 'OTHER': 'y'}]
        for row in rows:
            saved = ErrorRecord(error_hash='x')
            saved.set_error_data(row)
            assert h(saved.get_error_data()) == h(row), row

    def test_the_migration_computes_it_as_the_model(self):
        for row in ({'ID': '1', 'CODE': None}, {'ID': 'a|b', 'CODE': ''}, {'ID': 3}):
            assert migrations._identity_v2(row, ['ID', 'CODE']) == h(row)


class TestTheMigration:

    def database_of_before(self, query, rows):
        """Active errors with the hashes of 2.5.1, and migration 2 not yet run."""
        for row in rows:
            error = ErrorRecord(query_id=query.id, error_hash=v1(row, query.get_key_fields_list()))
            error.set_error_data(row)
            db.session.add(error)
        db.session.query(SchemaMigration).filter(SchemaMigration.number >= 2).delete()
        db.session.commit()

    def check(self, query, rows):
        from monitor_service import monitor_service
        with patch('monitor_service.execute_query_source', return_value=(['ID', 'CODE'], rows)), \
                patch('monitor_service.email_service.send_error_notification',
                      return_value={'success': True, 'recipients': ['ops@example.com']}) as send:
            return monitor_service.check_query(query, force=True), send

    def test_an_upgrade_notifies_nothing_again(self, app):
        query = make_query(email_recipients='ops@example.com', key_fields='ID,CODE')
        rows = [{'ID': '1', 'CODE': 'E1'}, {'ID': '2', 'CODE': None}, {'ID': '3', 'CODE': ''}]
        self.database_of_before(query, rows)
        resolved = ErrorRecord(query_id=query.id, error_hash='old', resolved_at=get_utc_now())
        resolved.set_error_data({'ID': '0', 'CODE': 'E0'})
        db.session.add(resolved)
        db.session.commit()
        assert migrations.run_migrations() == ['002 error identity v2']
        db.session.expire_all()
        assert sorted(e.error_hash for e in ErrorRecord.query.filter_by(resolved_at=None)) == sorted(map(h, rows))
        assert db.session.get(ErrorRecord, resolved.id).error_hash == 'old'    # history as it was
        result, send = self.check(query, rows)
        assert (result['new_errors'], result['resolved_errors']) == (0, 0) and not send.called

    def test_key_fields_changed_before_the_upgrade(self, app):
        """Open errors saved with other key fields can share the new identity: the oldest stays, the others
        are resolved, and ErrorEngine still starts."""
        query = make_query(email_recipients='ops@example.com', key_fields='ID,CODE')
        self.database_of_before(query, [{'ID': '1', 'CODE': 'a'}, {'ID': '1', 'CODE': 'b'}])
        query.key_fields = 'ID'
        db.session.commit()
        assert migrations.run_migrations() == ['002 error identity v2']
        active = ErrorRecord.query.filter_by(resolved_at=None).all()
        assert [error.get_error_data() for error in active] == [{'ID': '1', 'CODE': 'a'}]
        result, _ = self.check(query, [{'ID': '1', 'CODE': 'c'}])
        assert (result['new_errors'], result['resolved_errors']) == (0, 0)

    def test_an_error_hidden_by_the_old_hash_shows_up(self, app):
        """('a|b', 'c') and ('a', 'b|c') were one error: after the upgrade the second is an error of its own."""
        query = make_query(email_recipients='ops@example.com', key_fields='ID,CODE')
        self.database_of_before(query, [{'ID': 'a|b', 'CODE': 'c'}])
        migrations.run_migrations()
        result, _ = self.check(query, [{'ID': 'a|b', 'CODE': 'c'}, {'ID': 'a', 'CODE': 'b|c'}])
        assert (result['new_errors'], result['resolved_errors']) == (1, 0)


class TestColumnsWithTheSameName:

    def test_refused_instead_of_reduced_to_one(self, app, tmp_path):
        path = tmp_path / 'source.db'
        sqlite3.connect(path).close()
        connection = DatabaseConnection(name='Source', db_type='sqlite', database=str(path), is_active=True)
        db.session.add(connection)
        db.session.commit()
        with pytest.raises(ValueError, match='more than one column named ID: give each its own name'):
            connection.execute_query('SELECT 1 AS ID, 2 AS ID')
        assert connection.execute_query('SELECT 1 AS ID, 2 AS id')[0] == ['ID', 'id']   # names told apart by case

    def test_refused_by_jt400_too(self, monkeypatch):
        """The IBM i drivers save each statement as it runs: the check holds for them too."""
        from db_drivers import ibmi
        monkeypatch.setattr(ibmi, '_attach_as_daemon', lambda: None)

        class Columns:
            def getColumnCount(self):
                return 2

            def getColumnName(self, i):
                return 'ID'

        class Rows:
            left = 1

            def getMetaData(self):
                return Columns()

            def next(self):
                self.left -= 1
                return self.left >= 0

            def getObject(self, i):
                return None

            def close(self):
                pass

        class Statement:
            def execute(self, sql):
                return True

            def getResultSet(self):
                return Rows()

            def close(self):
                pass

        class Connection:
            def createStatement(self):
                return Statement()

        with pytest.raises(ValueError, match='more than one column named ID'):
            ibmi.IBMiDriver().run(Connection(), 'SELECT 1 AS ID, 2 AS ID FROM SYSIBM.SYSDUMMY1', False)

    def test_refused_by_ibm_db_too(self):
        pytest.importorskip('ibm_db')
        from db_drivers.as400 import AS400Driver
        with patch('db_drivers.as400.ibm_db') as ibm_db:
            ibm_db.exec_immediate.return_value = 'statement'
            ibm_db.num_fields.return_value = 2
            ibm_db.field_name.side_effect = lambda statement, i: 'ID'
            ibm_db.fetch_assoc.side_effect = [{'ID': 2}, False]
            with pytest.raises(ValueError, match='more than one column named ID'):
                AS400Driver().run('connection', 'SELECT 1 AS ID, 2 AS ID FROM SYSIBM.SYSDUMMY1', False)

    def test_the_check_says_so(self, app, tmp_path):
        from monitor_service import monitor_service
        path = tmp_path / 'source.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE a (ID INTEGER)')
            c.execute('CREATE TABLE b (ID INTEGER)')
            c.execute('INSERT INTO a VALUES (1)')
            c.execute('INSERT INTO b VALUES (2)')
        c.close()
        connection = DatabaseConnection(name='Source', db_type='sqlite', database=str(path), is_active=True)
        db.session.add(connection)
        db.session.commit()
        query = make_query(db_connection_id=connection.id, sql_query='SELECT * FROM a, b', email_recipients='')
        result = monitor_service.check_query(query, force=True)
        assert result['status'] == 'error' and 'more than one column named ID' in result['error_message']
        assert ErrorRecord.query.count() == 0
