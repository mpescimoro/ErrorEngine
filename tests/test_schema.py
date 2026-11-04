"""
Tests for updating the schema of existing databases (ensure_schema).
"""
from sqlalchemy import inspect, text
from models import db, ensure_schema


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
