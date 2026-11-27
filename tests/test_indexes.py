"""
Indexes of the internal database: a new database gets them from the models, an existing one at start
(ensure_indexes), and the queries of the checks, the scheduler, the pages and the cleanup use them.
"""
import pytest
from sqlalchemy import inspect, text

from models import db, ensure_indexes

NEW_INDEXES = {
    'error_records': {'ix_error_records_query_active', 'ix_error_records_resolved_at', 'ix_error_records_first_seen_at'},
    'query_logs': {'ix_query_logs_query_time', 'ix_query_logs_executed_at'},
    'email_logs': {'ix_email_logs_sent_at'},
}


def indexes(table):
    return {index['name'] for index in inspect(db.engine).get_indexes(table)}


def plan(sql) -> str:
    with db.engine.connect() as connection:
        return ' | '.join(row[-1] for row in connection.execute(text('EXPLAIN QUERY PLAN ' + sql)))


def test_a_new_database_has_them(app):
    for table, names in NEW_INDEXES.items():
        assert names <= indexes(table), table
    assert ensure_indexes() == []


def test_an_existing_database_gets_them_once(app):
    with db.engine.begin() as connection:
        for names in NEW_INDEXES.values():
            for name in names:
                connection.execute(text(f'DROP INDEX {name}'))
        connection.execute(text(
            "INSERT INTO monitored_queries (name, sql_query, key_fields) VALUES ('Old', 'SELECT 1', 'ID')"))
        connection.execute(text(
            "INSERT INTO error_records (query_id, error_hash, error_data) VALUES (1, 'h1', '{\"ID\": 1}')"))
    assert sorted(ensure_indexes()) == sorted(name for names in NEW_INDEXES.values() for name in names)
    for table, names in NEW_INDEXES.items():
        assert names <= indexes(table), table
    assert ensure_indexes() == []
    with db.engine.connect() as connection:
        assert connection.execute(text('SELECT error_hash FROM error_records')).scalar() == 'h1'


@pytest.mark.parametrize('sql, index', [
    # Every check: the active errors of its query
    ("SELECT * FROM error_records WHERE query_id = 1 AND resolved_at IS NULL", 'ix_error_records_query_active'),
    # Every minute, the scheduler: the active errors with a notification to send again
    ("SELECT DISTINCT query_id FROM error_records WHERE resolved_at IS NULL AND notify_pending = 1",
     'ix_error_records_resolved_at'),
    # The cleanup
    ("SELECT count(*) FROM error_records WHERE resolved_at IS NOT NULL AND resolved_at < '2026-01-01'",
     'ix_error_records_resolved_at'),
    ("SELECT count(*) FROM query_logs WHERE executed_at < '2026-01-01'", 'ix_query_logs_executed_at'),
    ("SELECT count(*) FROM email_logs WHERE sent_at < '2026-01-01'", 'ix_email_logs_sent_at'),
    # The pages and the statistics
    ("SELECT * FROM query_logs WHERE query_id = 1 ORDER BY executed_at DESC LIMIT 20", 'ix_query_logs_query_time'),
    ("SELECT * FROM query_logs ORDER BY executed_at DESC LIMIT 100", 'ix_query_logs_executed_at'),
    ("SELECT count(*) FROM error_records WHERE first_seen_at >= '2026-01-01'", 'ix_error_records_first_seen_at'),
    ("SELECT count(*) FROM email_logs WHERE sent_at >= '2026-01-01'", 'ix_email_logs_sent_at'),
])
def test_the_queries_use_them(app, sql, index):
    assert index in plan(sql)
