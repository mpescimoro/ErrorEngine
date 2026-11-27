"""
Resolved after: an error the query no longer returns is resolved after that many checks in a row without it
(1, the default, at the first one as before). Back before then, it is the same error: not new, not notified again.
"""
import re
from datetime import timedelta
from unittest.mock import patch

from models import db, DatabaseConnection, ErrorRecord, MonitoredQuery
from monitor_service import monitor_service
from tests.helpers import make_query
from utils import get_utc_now

ROWS = [{'ID': str(i)} for i in range(5)]


def check(query, rows):
    """The result of a check that reads these rows; the email mock, to see what was sent."""
    with patch('monitor_service.execute_query_source', return_value=(['ID'], rows)), \
            patch('monitor_service.email_service.send_error_notification',
                  return_value={'success': True, 'recipients': ['ops@example.com']}) as send:
        return monitor_service.check_query(query, force=True), send


def counts(result):
    return result['new_errors'], result['resolved_errors']


def active(query):
    return ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None)


def test_by_default_resolved_at_the_first_check_without_it(app):
    query = make_query(email_recipients='ops@example.com')
    assert query.resolve_after_checks == 1
    check(query, ROWS)
    assert counts(check(query, [])[0]) == (0, 5)
    assert counts(check(query, ROWS)[0]) == (5, 0)   # back: new errors, as before


def test_back_before_then_it_is_the_same_error(app):
    query = make_query(email_recipients='ops@example.com', resolve_after_checks=3)
    check(query, ROWS)
    ids = sorted(error.id for error in active(query))
    assert counts(check(query, [])[0]) == (0, 0)
    assert counts(check(query, ROWS[:2])[0]) == (0, 0)
    result, send = check(query, ROWS)
    assert counts(result) == (0, 0) and not send.called   # nothing new, nothing notified again
    assert sorted(error.id for error in active(query)) == ids
    assert [error.missing_checks for error in active(query)] == [0] * 5


def test_resolved_after_that_many_checks_in_a_row(app):
    query = make_query(email_recipients='ops@example.com', resolve_after_checks=3)
    check(query, ROWS)
    assert [counts(check(query, ROWS[:1])[0]) for _ in range(3)] == [(0, 0), (0, 0), (0, 4)]
    assert [error.get_error_data() for error in active(query)] == [{'ID': '0'}]


def test_a_check_that_fails_does_not_count(app):
    query = make_query(email_recipients='ops@example.com', resolve_after_checks=2)
    check(query, ROWS)
    check(query, [])
    with patch('monitor_service.execute_query_source', side_effect=ConnectionError('down')):
        assert monitor_service.check_query(query, force=True)['status'] == 'error'
    assert active(query).count() == 5
    assert counts(check(query, [])[0]) == (0, 5)


def test_a_query_saved_before_resolves_as_before(app):
    """A database of 2.5.1 gets the setting at 0: the same as 1."""
    query = make_query(email_recipients='ops@example.com', resolve_after_checks=0)
    check(query, ROWS)
    assert counts(check(query, [])[0]) == (0, 5)


def test_not_reminded_while_missing(app):
    query = make_query(email_recipients='ops@example.com', reminder_enabled=True, reminder_interval_minutes=15,
                       resolve_after_checks=2)
    error = ErrorRecord(query_id=query.id, error_hash='h1', email_sent=True,
                        email_sent_at=get_utc_now() - timedelta(hours=2))
    error.set_error_data({'ID': '1'})
    db.session.add(error)
    db.session.commit()
    assert error.needs_reminder(query)
    error.missing_checks = 1   # not returned by the last check: maybe resolved
    assert not error.needs_reminder(query)


def test_the_form(app, client):
    connection = DatabaseConnection(name='ERP', db_type='sqlite', database=':memory:')
    db.session.add(connection)
    db.session.commit()
    form = {'name': 'Orders', 'source_type': 'database', 'db_connection_id': str(connection.id),
            'sql_query': 'SELECT id AS ID FROM orders', 'key_fields': 'ID', 'check_interval_value': '15',
            'check_interval_unit': 'minutes', 'reminder_interval_minutes': '60', 'reminder_max_count': '5'}
    for wrong in ('0', '101', 'x'):
        html = client.post('/queries/new', data={**form, 'resolve_after_checks': wrong}).get_data(as_text=True)
        assert 'Resolved after: a whole number of checks, from 1 to 100' in html
    assert client.post('/queries/new', data={**form, 'resolve_after_checks': '3'}).status_code == 302
    query = MonitoredQuery.query.one()
    assert query.resolve_after_checks == 3
    html = client.get(f'/queries/{query.id}/edit').get_data(as_text=True)
    assert re.search(r'id="resolve_after_checks"[^>]*value="3"', html)
    # A form without the field keeps the saved value
    assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
    db.session.expire_all()
    assert query.resolve_after_checks == 3
    assert re.search(r'id="resolve_after_checks"[^>]*value="1"', client.get('/queries/new').get_data(as_text=True))
