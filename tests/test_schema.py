"""
Tests for updating the schema of existing databases (ensure_schema).
"""
from sqlalchemy import inspect, text
from models import db, ensure_schema, allow_writes_where_queries_write


def columns():
    return {c['name'] for c in inspect(db.engine).get_columns('error_records')}


def test_up_to_date_database_is_not_changed(app):
    assert 'notified_to' in columns()
    assert ensure_schema() == []


def test_adds_the_column_to_existing_databases(app):
    """A database created before the update gets the column, without losing data."""
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE error_records DROP COLUMN notified_to'))
        connection.execute(text(
            "INSERT INTO monitored_queries (name, sql_query, key_fields) VALUES ('Q', 'SELECT 1', 'ID')"))
        connection.execute(text(
            "INSERT INTO error_records (query_id, error_hash, error_data) VALUES (1, 'h1', '{\"ID\": 1}')"))
    assert 'notified_to' not in columns()

    assert ensure_schema() == ['error_records.notified_to']

    assert 'notified_to' in columns()
    with db.engine.connect() as connection:
        row = connection.execute(text('SELECT error_hash, notified_to FROM error_records')).one()
    assert tuple(row) == ('h1', None)
    assert ensure_schema() == []


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


def test_connections_whose_queries_change_data_keep_running_them(app):
    """Upgrade from 2.4.2: the setting arrives off, and is turned on only where a query needs it."""
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE database_connections DROP COLUMN allow_writes'))
        connection.execute(text("INSERT INTO database_connections (id, name, db_type) "
                                "VALUES (1, 'Reads', 'sqlite'), (2, 'Writes', 'sqlite'), (3, 'Dollars', 'postgres')"))
        # The DELETE of Q3 is code for PostgreSQL, after its $$ text
        connection.execute(text("INSERT INTO monitored_queries (name, sql_query, key_fields, db_connection_id) "
                                "VALUES ('Q1', 'SELECT 1', 'ID', 1), ('Q2', 'EXEC refresh_orders', 'ID', 2), "
                                "('Q3', :hidden, 'ID', 3)"), {'hidden': "SELECT $$'$$; DELETE FROM t; --'"})

    assert ensure_schema() == ['database_connections.allow_writes']
    assert allow_writes_where_queries_write() == [('Writes', 'Q2', 'EXEC'), ('Dollars', 'Q3', 'DELETE')]
    with db.engine.connect() as connection:
        rows = connection.execute(text('SELECT name, allow_writes FROM database_connections ORDER BY id')).all()
    assert [tuple(row) for row in rows] == [('Reads', 0), ('Writes', 1), ('Dollars', 1)]


def test_failure_retry_at_added_to_existing_databases(app):
    """2.5.1 and earlier: the wait after a failed run arrives empty, the query runs at the next round."""
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE monitored_queries DROP COLUMN failure_retry_at'))
        connection.execute(text(
            "INSERT INTO monitored_queries (name, sql_query, key_fields) VALUES ('Old', 'SELECT 1', 'ID')"))
    assert ensure_schema() == ['monitored_queries.failure_retry_at']
    with db.engine.connect() as connection:
        assert connection.execute(text('SELECT failure_retry_at FROM monitored_queries')).scalar() is None
    assert ensure_schema() == []


def test_resolve_after_checks_and_missing_checks_added_to_existing_databases(app):
    """2.5.1 and earlier: the setting arrives at 0, which resolves at the first check without the error, as before."""
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE monitored_queries DROP COLUMN resolve_after_checks'))
        connection.execute(text('ALTER TABLE error_records DROP COLUMN missing_checks'))
        connection.execute(text(
            "INSERT INTO monitored_queries (name, sql_query, key_fields) VALUES ('Old', 'SELECT 1', 'ID')"))
        connection.execute(text(
            "INSERT INTO error_records (query_id, error_hash, error_data) VALUES (1, 'h1', '{\"ID\": 1}')"))
    assert ensure_schema() == ['monitored_queries.resolve_after_checks', 'error_records.missing_checks']
    with db.engine.connect() as connection:
        assert connection.execute(text('SELECT resolve_after_checks FROM monitored_queries')).scalar() == 0
        assert connection.execute(text('SELECT missing_checks FROM error_records')).scalar() == 0
    assert ensure_schema() == []
