"""
Error states. Ignored: out of the errors list and of the counts, in a view of its own, and no notification goes out
for it. Assigned: with the name of who is on it. Both stop the reminders; an error that resolves and comes back is a
normal error again. From the pages and from the API, as Resolve.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from models import db, ErrorRecord
from monitor_service import monitor_service
from scheduler import queries_with_due_retries
from tests.helpers import make_query
from utils import get_utc_now

ROWS = [{'ID': str(i)} for i in range(3)]


def check(query, rows):
    """A check that reads these rows; the email mock, to see what was sent."""
    with patch('monitor_service.execute_query_source', return_value=(['ID'], rows)), \
            patch('monitor_service.email_service.send_error_notification',
                  return_value={'success': True, 'recipients': ['ops@example.com']}) as send:
        return monitor_service.check_query(query, force=True), send


def errors_of(query):
    return ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).order_by(ErrorRecord.id).all()


def set_state(client, error, **body):
    return client.post(f'/api/errors/{error.id}/state', json=body)


class TestApi:

    def test_ignore_assign_and_back(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        check(query, ROWS)
        first, second, third = errors_of(query)
        assert set_state(client, first, state='ignored').get_json()['state'] == 'ignored'
        answer = set_state(client, second, state='assigned', assigned_to='  Mario Rossi ').get_json()
        assert (answer['state'], answer['assigned_to']) == ('assigned', 'Mario Rossi')
        listed = {e['id']: e for e in client.get('/api/errors').get_json()}
        assert set(listed) == {second.id, third.id}
        assert (listed[second.id]['state'], listed[second.id]['assigned_to']) == ('assigned', 'Mario Rossi')
        assert [e['id'] for e in client.get('/api/errors?state=ignored').get_json()] == [first.id]
        assert [e['id'] for e in client.get('/api/errors?state=assigned').get_json()] == [second.id]
        assert client.get('/api/errors/summary').get_json()['active'] == 2
        # Back to an open error, without a name
        answer = set_state(client, second, state=None).get_json()
        assert (answer['state'], answer['assigned_to']) == (None, None)
        assert set_state(client, first, state=None).status_code == 200
        assert client.get('/api/errors/summary').get_json()['active'] == 3

    @pytest.mark.parametrize('body, says', [
        ({'state': 'closed'}, 'state: ignored, assigned or null'),
        ({'state': 'assigned'}, 'assigned_to'),
        ({'state': 'assigned', 'assigned_to': '   '}, 'assigned_to'),
        ({'state': 'assigned', 'assigned_to': 'x' * 101}, 'assigned_to'),
        ({'state': 'assigned', 'assigned_to': 5}, 'assigned_to'),
        ({}, 'state: ignored, assigned or null'),
    ])
    def test_refused(self, app, client, body, says):
        query = make_query(email_recipients='ops@example.com')
        check(query, ROWS[:1])
        response = set_state(client, errors_of(query)[0], **body)
        assert response.status_code == 400 and says in response.get_json()['message']

    def test_a_resolved_error_keeps_its_state(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        check(query, ROWS[:1])
        error = errors_of(query)[0]
        check(query, [])
        response = set_state(client, error, state='ignored')
        assert response.status_code == 400 and 'resolved' in response.get_json()['message']
        assert client.post('/api/errors/999999/state', json={'state': 'ignored'}).status_code == 404


class TestNotifications:

    def test_no_reminders_for_ignored_or_assigned_errors(self, app):
        query = make_query(email_recipients='ops@example.com', reminder_enabled=True, reminder_interval_minutes=60)
        check(query, ROWS)
        first, second, third = errors_of(query)
        first.state, second.state, second.assigned_to = 'ignored', 'assigned', 'Mario'
        for error in (first, second, third):
            error.email_sent_at = get_utc_now() - timedelta(hours=2)
        db.session.commit()
        assert [error.needs_reminder(query) for error in (first, second, third)] == [False, False, True]

    def test_an_ignored_error_is_not_delivered(self, app):
        query = make_query(email_recipients='ops@example.com')
        with patch('monitor_service.execute_query_source', return_value=(['ID'], ROWS[:1])), \
                patch('monitor_service.email_service.send_error_notification',
                      return_value={'success': False, 'email_unavailable': True, 'message': 'down'}):
            monitor_service.check_query(query, force=True)
        error = errors_of(query)[0]
        assert error.notify_pending
        error.state, error.notify_retry_at = 'ignored', get_utc_now() - timedelta(minutes=1)
        db.session.commit()
        assert queries_with_due_retries() == []
        _, send = check(query, ROWS[:1])
        assert not send.called
        # The page says so, and its view has no filter on the notifications
        page = app.test_client().get('/errors?state=ignored&delivery=failed').get_data(as_text=True)
        assert 'Not sent: ignored' in page and 'Delivery pending' not in page and 'Not delivered' not in page
        # Not ignored any more: the delivery goes on
        error.state = None
        db.session.commit()
        assert queries_with_due_retries() == [query.id]
        _, send = check(query, ROWS[:1])
        assert send.called

    def test_back_after_it_resolved_it_is_a_normal_error(self, app):
        query = make_query(email_recipients='ops@example.com')
        check(query, ROWS[:1])
        error = errors_of(query)[0]
        error.state = 'ignored'
        db.session.commit()
        check(query, [])
        result, send = check(query, ROWS[:1])
        back = errors_of(query)[0]
        assert result['new_errors'] == 1 and send.called
        assert back.id != error.id and back.state is None


class TestPages:

    def test_the_errors_list_and_its_views(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        check(query, [{'ID': 'open-1'}, {'ID': 'mine-2'}, {'ID': 'noise-3'}])
        opened, mine, noise = errors_of(query)
        mine.state, mine.assigned_to = 'assigned', 'Mario <Rossi>'
        noise.state = 'ignored'
        db.session.commit()
        page = client.get('/errors').get_data(as_text=True)
        assert 'open-1' in page and 'mine-2' in page and 'noise-3' not in page
        assert 'Assigned to Mario &lt;Rossi&gt;' in page and 'badge-danger">2<' in page
        assert 'Ignored (1)' in page and 'Assigned (1)' in page
        ignored = client.get('/errors?state=ignored').get_data(as_text=True)
        assert 'noise-3' in ignored and 'open-1' not in ignored and 'Stop ignoring' in ignored
        assert 'mine-2' in client.get('/errors?state=assigned').get_data(as_text=True)

    def test_counts_and_the_query_page_leave_out_the_ignored(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        check(query, [{'ID': 'open-1'}, {'ID': 'noise-2'}])
        errors_of(query)[1].state = 'ignored'
        db.session.commit()
        detail = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert 'open-1' in detail and 'noise-2' not in detail
        assert f'/errors?query_id={query.id}&amp;state=ignored' in detail
        assert 'id="navErrorCount">1<' in client.get('/').get_data(as_text=True).replace('\n', '').replace(' ', '')
