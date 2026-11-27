"""
The data of an active error follow the source: a row seen again with other values outside the key fields
updates the error, and the pages, the reminders and the retries show them. Its identity, the key fields, stays.
"""
from datetime import timedelta
from unittest.mock import patch

from models import db, ErrorRecord
from monitor_service import monitor_service
from tests.helpers import make_query
from utils import get_utc_now


def check(query, rows, columns=('ID', 'STATUS')):
    """The result of a check that reads these rows; the email mock, to see what was sent."""
    with patch('monitor_service.execute_query_source', return_value=(list(columns), rows)), \
            patch('monitor_service.email_service.send_error_notification',
                  return_value={'success': True, 'recipients': ['ops@example.com']}) as send:
        return monitor_service.check_query(query, force=True), send


def active(query):
    return ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).one()


def test_the_data_follow_the_source(app):
    query = make_query(email_recipients='ops@example.com')
    check(query, [{'ID': '1', 'STATUS': 'WAITING'}])
    first = active(query).id
    result, send = check(query, [{'ID': '1', 'STATUS': 'BLOCKED'}])
    assert (result['new_errors'], result['resolved_errors']) == (0, 0) and not send.called
    error = active(query)
    assert error.id == first and error.occurrence_count == 2   # the same error, not notified again
    assert error.get_error_data() == {'ID': '1', 'STATUS': 'BLOCKED'}


def test_a_column_added_to_the_query_shows_up(app):
    query = make_query(email_recipients='ops@example.com')
    check(query, [{'ID': '1'}], columns=('ID',))
    check(query, [{'ID': '1', 'OWNER': 'Anna'}], columns=('ID', 'OWNER'))
    assert active(query).get_error_data() == {'ID': '1', 'OWNER': 'Anna'}


def test_reminders_carry_the_latest_data(app):
    query = make_query(email_recipients='ops@example.com', reminder_enabled=True, reminder_interval_minutes=15)
    check(query, [{'ID': '1', 'STATUS': 'WAITING'}])
    error = active(query)
    assert error.email_sent
    error.email_sent_at = get_utc_now() - timedelta(hours=2)
    db.session.commit()
    result, send = check(query, [{'ID': '1', 'STATUS': 'BLOCKED'}])
    assert result['reminders_sent'] == 1
    assert send.call_args.kwargs['email_type'] == 'reminder'
    assert send.call_args.kwargs['errors'] == [{'ID': '1', 'STATUS': 'BLOCKED'}]
