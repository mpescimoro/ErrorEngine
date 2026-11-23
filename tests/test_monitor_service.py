"""
The check of a query: errors found, seen again and resolved, the time window, a failing source, statistics and
logs, key fields, disabled connections.
"""
from datetime import datetime, time
from unittest.mock import patch

from models import db, ErrorRecord, QueryLog, DatabaseConnection
from monitor_service import monitor_service
from tests.helpers import make_query

ROWS = [{'ID': '001', 'CODE': 'ERR001'}, {'ID': '002', 'CODE': 'ERR002'}]


def check(query, rows, columns=('ID', 'CODE'), force=True):
    """The result of a check that reads these rows; the source mock, to see if it was called."""
    with patch('monitor_service.execute_query_source', return_value=(list(columns), rows)) as source, \
            patch('monitor_service.email_service.send_error_notification', return_value={'success': True}):
        return monitor_service.check_query(query, force=force), source


def counts(result):
    return result['rows_returned'], result['new_errors'], result['resolved_errors']


def test_errors_found_seen_again_and_resolved(app, sample_query):
    result, _ = check(sample_query, [])
    assert result['status'] == 'success' and counts(result) == (0, 0, 0)
    assert counts(check(sample_query, ROWS)[0]) == (2, 2, 0)
    assert ErrorRecord.query.filter_by(query_id=sample_query.id).count() == 2
    # The same error is not new: it is counted again; the one no longer returned is resolved
    assert counts(check(sample_query, ROWS[:1])[0]) == (1, 0, 1)
    active = ErrorRecord.query.filter_by(query_id=sample_query.id, resolved_at=None).all()
    assert [(e.get_error_data()['ID'], e.occurrence_count) for e in active] == [('001', 2)]
    assert counts(check(sample_query, [])[0]) == (0, 0, 1)
    assert ErrorRecord.query.filter_by(query_id=sample_query.id, resolved_at=None).count() == 0


def test_the_time_window_unless_forced(app, sample_query):
    sample_query.schedule_start_time, sample_query.schedule_end_time = time(8, 0), time(10, 0)
    db.session.commit()
    with patch.object(sample_query, '_get_local_now', return_value=datetime(2025, 2, 10, 22, 0)):
        result, source = check(sample_query, [], force=False)
    assert result['status'] == 'skipped' and not source.called
    result, source = check(sample_query, [])
    assert result['status'] == 'success' and source.call_count == 1


def test_a_source_that_fails(app, sample_query):
    with patch('monitor_service.execute_query_source', side_effect=Exception('Connection refused')):
        result = monitor_service.check_query(sample_query, force=True)
    assert result['status'] == 'error' and 'Connection refused' in result['error_message']


def test_statistics_and_log_of_a_run(app, sample_query):
    assert sample_query.last_check_at is None and sample_query.total_errors_found == 0
    check(sample_query, ROWS[:1])
    db.session.refresh(sample_query)
    assert sample_query.last_check_at is not None and sample_query.total_errors_found == 1
    log = QueryLog.query.filter_by(query_id=sample_query.id).one()
    assert (log.status, log.rows_returned, log.new_errors) == ('success', 1, 1) and log.execution_time_ms >= 0


def test_active_errors_and_status(app, sample_query, sample_errors_in_db):
    # The fixture: 2 active errors, 2 resolved
    assert len(monitor_service.get_active_errors(sample_query.id)) == len(monitor_service.get_active_errors()) == 2
    status = monitor_service.get_query_status(sample_query.id)
    assert status['name'] == 'Test Query' and {'active_errors', 'is_active'} <= set(status)
    assert 'error' in monitor_service.get_query_status(99999)


class TestKeyFields:
    """Key fields that are not columns of the results: an error, not rows merged into one."""

    def test_missing_key_field_is_a_failed_run(self, app):
        query = make_query(key_fields='ID, CODE', email_recipients='ops@x.it')
        result, _ = check(query, [{'ID': 1, 'MSG': 'a'}, {'ID': 2, 'MSG': 'b'}], columns=('ID', 'MSG'))
        assert result['status'] == 'error'
        assert 'CODE' in result['error_message'] and 'ID, MSG' in result['error_message']
        assert ErrorRecord.query.count() == 0 and query.consecutive_failures == 1

    def test_names_compared_without_case(self, app):
        query = make_query(key_fields='id', email_recipients='ops@x.it')
        assert check(query, [{'ID': 1}, {'ID': 2}], columns=('ID',))[0]['new_errors'] == 2

    def test_without_rows(self, app):
        api = make_query(name='API', source_type='http', email_recipients='ops@x.it')
        empty = make_query(key_fields=' , ', email_recipients='ops@x.it')
        # Nothing to check against: an HTTP source without rows has no columns either
        assert check(api, [], columns=())[0]['status'] == 'success'
        # No key fields at all fails anyway: any row would be the same error
        result, _ = check(empty, [], columns=('ID',))
        assert result['status'] == 'error' and 'No key fields' in result['error_message']


class TestDisabledConnection:
    """A disabled connection stops its queries: they are skipped, not failing."""

    def make(self):
        connection = DatabaseConnection(name='AS400 prod', db_type='sqlite', database=':memory:', is_active=False)
        db.session.add(connection)
        db.session.flush()
        return make_query(db_connection_id=connection.id, email_recipients='ops@x.it')

    def test_check_skipped(self, app):
        query = self.make()
        result, source = check(query, [])
        assert not source.called and result['status'] == 'skipped'
        assert 'AS400 prod' in result['error_message'] and 'disabled' in result['error_message']
        assert not query.consecutive_failures

    def test_not_started_by_the_scheduler(self, app, scheduler_pool):
        query = self.make()
        assert query.connection_disabled()
        assert scheduler_pool.dispatch_due_queries(app) == []

    def test_shown_in_the_queries_list(self, app, client):
        self.make()
        assert 'Connection disabled' in client.get('/queries').get_data(as_text=True)

    def test_earlier_failures_do_not_count_while_it_is_disabled(self, app, client):
        query = self.make()
        query.consecutive_failures = 3
        db.session.commit()
        assert not query.is_failing()
        assert client.get('/api/health').get_json()['checks']['queries_failing'] == 0
