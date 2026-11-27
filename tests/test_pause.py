"""
The pause of the notifications, for planned work: from the Dashboard or the API, up to 3 days. While it lasts the
checks go on and nothing is sent, neither the notifications of the errors found (each says it was not sent), nor the
reminders, nor the alerts about the sources; at the end the errors still open are sent, not those notified before.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from models import db, ErrorRecord, NotificationPause
from monitor_service import monitor_service
from notification_pause import paused_until, pause_notifications
from scheduler import queries_with_due_retries
from tests.helpers import make_query
from utils import get_utc_now

OK = {'success': True, 'recipients': ['ops@example.com']}


def check(query, rows):
    """A check that reads these rows; the email mock, to see what was sent."""
    with patch('monitor_service.execute_query_source', return_value=(['ID'], rows)), \
            patch('monitor_service.email_service.send_error_notification', return_value=OK) as send:
        return monitor_service.check_query(query, force=True), send


def time_passes(minutes):
    """As if minutes had gone by: the times of the pause, and of what waits for it, that much earlier."""
    shift = timedelta(minutes=minutes)
    pause = db.session.get(NotificationPause, 1)
    pause.started_at, pause.until = pause.started_at - shift, pause.until - shift
    for error in ErrorRecord.query.filter(ErrorRecord.notify_retry_at.isnot(None)):
        error.notify_retry_at -= shift
        error.held_until = error.held_until and error.held_until - shift
    db.session.commit()


def errors_of(query):
    return ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).order_by(ErrorRecord.id).all()


class TestApi:

    def test_pause_change_and_resume(self, app, client):
        assert client.get('/api/notifications/pause').get_json() == {'paused': False, 'until': None}
        answer = client.post('/api/notifications/pause', json={'minutes': 60}).get_json()
        assert answer['paused'] and answer['until'].endswith('Z')
        assert timedelta(minutes=59) < paused_until() - get_utc_now() <= timedelta(minutes=60)
        # Again, while paused: a new end, from now
        client.post('/api/notifications/pause', json={'minutes': 4320})
        assert paused_until() - get_utc_now() > timedelta(days=2, hours=23)
        assert client.delete('/api/notifications/pause').get_json() == {'paused': False, 'until': None}
        assert paused_until() is None

    @pytest.mark.parametrize('body', [{}, {'minutes': 0}, {'minutes': 4321}, {'minutes': '60'}, {'minutes': True},
                                      {'minutes': 1.5}, []])
    def test_refused(self, app, client, body):
        response = client.post('/api/notifications/pause', json=body)
        assert response.status_code == 400 and 'minutes: a whole number from 1 to 4320' in response.get_json()['message']
        assert paused_until() is None

    def test_the_health_check_says_it(self, app, client):
        pause_notifications(30)
        assert client.get('/api/health').get_json()['notifications_paused_until'].endswith('Z')


class TestWhileItLasts:

    def test_errors_found_are_held_and_say_so(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        until = pause_notifications(60)
        result, send = check(query, [{'ID': '1'}, {'ID': '2'}])
        assert result['new_errors'] == 2 and not send.called
        assert [(e.notify_pending, e.held_until, e.notify_attempts) for e in errors_of(query)] == [(True, until, 0)] * 2
        page = client.get('/errors').get_data(as_text=True)
        assert 'Not sent: notifications paused' in page and 'Notifications paused until' in page
        assert queries_with_due_retries() == []   # nothing to retry before the end
        _, send = check(query, [{'ID': '1'}, {'ID': '2'}])
        assert not send.called

    def test_no_reminders(self, app):
        query = make_query(email_recipients='ops@example.com', reminder_enabled=True, reminder_interval_minutes=60)
        check(query, [{'ID': '1'}])
        error = errors_of(query)[0]
        error.email_sent_at = get_utc_now() - timedelta(hours=2)
        db.session.commit()
        pause_notifications(60)
        result, send = check(query, [{'ID': '1'}])
        assert result['reminders_sent'] == 0 and not send.called
        time_passes(61)
        result, send = check(query, [{'ID': '1'}])
        assert result['reminders_sent'] == 1   # due, it goes at the first check after the pause

    def test_no_alerts_about_the_sources(self, app):
        query = make_query(email_recipients='ops@example.com')
        pause_notifications(60)
        with patch('monitor_service.execute_query_source', side_effect=Exception('ORA-12541')), \
                patch('monitor_service.email_service.send_system_notification', return_value=OK) as alert:
            for _ in range(3):
                query.failure_retry_at = None
                monitor_service.check_query(query, force=True)
            assert not alert.called and query.failure_alerted_at is None
            # The source still fails after the pause: the alert goes out at the next failure
            time_passes(61)
            monitor_service.check_query(query, force=True)
            assert alert.call_count == 1 and query.failure_alerted_at is not None

    def test_a_recovery_during_it_is_told_after_it(self, app):
        """Failing and alerted before the pause, working again during it: the last alert must not stay "failing"."""
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(email_recipients='ops@example.com')
        with patch('monitor_service.email_service.send_system_notification', return_value=OK) as alert:
            with patch('monitor_service.execute_query_source', side_effect=Exception('ORA-12541')):
                monitor_service.check_query(query, force=True)
            assert alert.call_count == 1 and 'failing' in alert.call_args.args[1]
            pause_notifications(60)
            check(query, [])
            check(query, [])
            assert alert.call_count == 1 and query.consecutive_failures == 0   # working, on the pages too
            time_passes(61)
            check(query, [])
            assert alert.call_count == 2 and 'recovered' in alert.call_args.args[1]
            assert alert.call_args.args[3] == ['The check works again: it recovered while the notifications were paused.']
            check(query, [])
            assert alert.call_count == 2 and query.failure_alerted_at is None

    def test_the_bar_on_every_page(self, app, client):
        assert 'Notifications paused until' not in client.get('/').get_data(as_text=True)
        pause_notifications(60)
        for path in ('/', '/queries', '/errors', '/logs', '/settings'):
            assert 'Notifications paused until' in client.get(path).get_data(as_text=True), path


class TestAtTheEnd:

    def test_what_is_still_open_goes_out_once(self, app):
        query = make_query(email_recipients='ops@example.com')
        check(query, [{'ID': 'before'}])   # notified before the pause
        pause_notifications(60)
        check(query, [{'ID': 'before'}, {'ID': 'during'}, {'ID': 'gone'}])
        check(query, [{'ID': 'before'}, {'ID': 'during'}])   # 'gone' resolved during the pause
        time_passes(61)
        assert queries_with_due_retries() == [query.id]
        with patch('monitor_service.email_service.send_error_notification', return_value=OK) as send:
            monitor_service.deliver_pending(query)
        sent = [row for call in send.call_args_list for row in call.kwargs['errors']]
        assert sent == [{'ID': 'during'}]
        during = errors_of(query)[1]
        assert (during.notify_pending, during.held_until, during.email_sent) == (False, None, True)
        gone = ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).one()
        assert gone.email_sent is False and gone.held_until is not None   # it says it was never sent

    def test_a_new_end_moves_what_is_held(self, app):
        query = make_query(email_recipients='ops@example.com')
        pause_notifications(30)
        check(query, [{'ID': '1'}])
        pause_notifications(240)   # the work takes longer
        time_passes(31)
        assert queries_with_due_retries() == []
        until = pause_notifications(10)   # or ends sooner
        assert errors_of(query)[0].held_until == until
        time_passes(11)
        assert queries_with_due_retries() == [query.id]

    def test_resume_now_sends_without_waiting_for_the_end(self, app, client):
        query = make_query(email_recipients='ops@example.com')
        pause_notifications(240)
        check(query, [{'ID': '1'}])
        client.delete('/api/notifications/pause')
        assert queries_with_due_retries() == [query.id]
        with patch('monitor_service.email_service.send_error_notification', return_value=OK) as send:
            monitor_service.deliver_pending(query)
        assert send.call_count == 1 and errors_of(query)[0].held_until is None
