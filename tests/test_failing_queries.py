"""
Queries that cannot read their source: the alert after MONITOR_FAILURE_THRESHOLD failures
in a row and when they recover, health check and Settings.
"""
from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import patch

import pytest

from models import db, EmailLog
from tests import helpers
from tests.helpers import make_channel


make_query = partial(helpers.make_query, name='Critical')

OK = {'success': True}


def fail(query, message='ORA-12541: TNS:no listener'):
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source', side_effect=Exception(message)):
        return monitor_service.check_query(query, force=True)


def succeed(query):
    """A check that reads the source again."""
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source', return_value=(['ID'], [{'ID': 1}])), \
         patch('monitor_service.email_service.send_error_notification', return_value=OK):
        return monitor_service.check_query(query, force=True)


class TestFailingSourceAlert:

    @pytest.fixture
    def alerts(self):
        with patch('monitor_service.email_service.send_system_notification', return_value=OK) as send:
            yield send

    def test_alert_after_three_failures_only_once(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        fail(query)
        assert alerts.call_count == 0
        fail(query)
        assert alerts.call_count == 1
        recipients, subject = alerts.call_args.args[0], alerts.call_args.args[1]
        assert recipients == ['ops@x.it'] and 'failing' in subject
        assert any('ORA-12541' in line for line in alerts.call_args.args[3])
        assert query.consecutive_failures == 3 and query.failure_alerted_at is not None
        fail(query)
        assert alerts.call_count == 1

    def test_configurable_threshold_and_off_switch(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        app.config['MONITOR_FAILURE_THRESHOLD'] = 0
        for _ in range(5):
            fail(query)
        assert alerts.call_count == 0 and query.consecutive_failures == 5
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        fail(query)
        assert alerts.call_count == 1

    def test_after_a_failure_the_scheduler_retries_right_away(self, app):
        """A failed daily query is retried at the next scheduler round."""
        from datetime import datetime, timedelta
        from utils import local_to_utc
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=1440,
                           schedule_days='1,2,3,4,5,6,7')
        today = datetime(2026, 10, 30, 8, 0)
        yesterday = local_to_utc(today - timedelta(days=1))
        query.last_check_at = yesterday          # last successful check: yesterday
        db.session.commit()
        fail(query)
        # last_check_at does not move: one minute after today's check it is still due
        assert query.last_check_at == yesterday
        assert query.should_run_now(today + timedelta(minutes=1))[0] is True

    def test_recovery_alert_and_reset(self, app, alerts):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        succeed(query)
        assert alerts.call_count == 2
        assert 'recovered' in alerts.call_args.args[1]
        assert query.consecutive_failures == 0 and query.failure_alerted_at is None
        assert query.failing_since is None and query.last_failure_message is None

    def test_no_recovery_alert_if_none_was_sent(self, app, alerts):
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        succeed(query)
        assert alerts.call_count == 0 and query.consecutive_failures == 0

    def test_alert_not_sent_is_retried(self, app):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        query = make_query(email_recipients='ops@x.it')
        with patch('monitor_service.email_service.send_system_notification',
                   return_value={'success': False}) as send:
            fail(query)
        assert send.call_count == 1 and query.failure_alerted_at is None
        with patch('monitor_service.email_service.send_system_notification', return_value=OK) as send:
            fail(query)
        assert send.call_count == 1 and query.failure_alerted_at is not None

    def test_admin_recipients_when_configured(self, app, alerts):
        app.config.update(ADMIN_EMAILS='it@x.it, dba@x.it', MONITOR_FAILURE_THRESHOLD=1)
        query = make_query(email_recipients='ops@x.it')
        fail(query)
        assert alerts.call_args.args[0] == ['it@x.it', 'dba@x.it']

    def test_alert_on_channels_too(self, app):
        app.config['MONITOR_FAILURE_THRESHOLD'] = 1
        channel = make_channel('Teams', 'teams')
        query = make_query(channels=[channel])
        with patch('notification_service.notification_service.send_system_message',
                   return_value={'total': 1, 'success': 1}) as message:
            fail(query)
        assert message.call_args.args[1] == 'monitor_failed'
        assert query.failure_alerted_at is not None

    def test_real_service_email(self, app):
        """send_system_notification builds the email without the user templates and logs it."""
        from email_service import email_service
        query = make_query(email_recipients='ops@x.it')
        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, patch('email_service.smtplib.SMTP') as smtp:
            result = email_service.send_system_notification(
                ['ops@x.it'], '[ErrorEngine] Query failing: Critical', 'Title <b>',
                ['Last error: a < b'], query=query, email_type='monitor_failed')
        assert result['success'] is True
        server = (smtp_ssl if smtp_ssl.called else smtp).return_value
        assert 'a &lt; b' in server.sendmail.call_args.args[2] or 'a < b' in server.sendmail.call_args.args[2]
        assert EmailLog.query.filter_by(email_type='monitor_failed', status='sent').count() == 1

    def test_health_degraded_with_failing_queries(self, app, client, alerts):
        query = make_query()
        assert client.get('/api/health').get_json()['status'] == 'healthy'
        fail(query)
        data = client.get('/api/health').get_json()
        assert data['status'] == 'degraded' and data['checks']['queries_failing'] == 1

    def test_health_503_with_scheduler_stopped(self, app, client):
        stopped = {'enabled': True, 'running': False, 'last_tick_at': None, 'in_flight': 0, 'waiting': 0,
                   'busy': False, 'max_workers': 4}
        with patch('scheduler.get_scheduler_status', return_value=stopped):
            response = client.get('/api/health')
        assert response.status_code == 503
        assert response.get_json()['checks']['scheduler'] == 'stopped'

    def test_settings_show_the_real_state(self, app, client, alerts):
        query = make_query()
        fail(query)
        page = client.get('/settings').get_data(as_text=True)
        assert 'Not started in this process' in page
        assert 'Critical (1)' in page


FIXED = datetime(2026, 11, 16, 9, 0, 20)   # UTC, 20 seconds into the minute


def fail_at(query, now=FIXED):
    """A failed check at a fixed time (UTC)."""
    with patch('monitor_service.get_utc_now', return_value=now):
        return fail(query)


def retry_in_minutes(query, now=FIXED):
    """Minutes from the minute of the failure to the next attempt; None: at the next round."""
    if query.failure_retry_at is None:
        return None
    return round((query.failure_retry_at - now.replace(second=0)).total_seconds() / 60)


class TestRetriesSlowDown:
    """A source that keeps failing is tried again after waits that grow, not at every round."""

    def test_waits_grow_up_to_an_hour(self, app):
        query = make_query(check_interval_minutes=1440)
        waits = []
        for _ in range(9):
            fail_at(query)
            waits.append(retry_in_minutes(query))
        assert waits == [None, 1, 2, 4, 8, 16, 32, 60, 60]

    def test_never_less_often_than_the_interval(self, app):
        query = make_query(check_interval_minutes=5)
        waits = []
        for _ in range(6):
            fail_at(query)
            waits.append(retry_in_minutes(query))
        assert waits == [None, 1, 2, 4, 5, 5]

    def test_not_due_before_the_wait_is_over(self, app):
        from utils import utc_to_local
        query = make_query(check_interval_minutes=1440)
        query.last_check_at = FIXED - timedelta(days=1)   # it worked yesterday: today's run is due
        for _ in range(3):
            fail_at(query)
        local = utc_to_local(FIXED.replace(second=0))
        due, reason = query.should_run_now(local + timedelta(minutes=1))
        assert due is False and 'next attempt at' in reason
        assert query.should_run_now(local + timedelta(minutes=2))[0] is True
        assert query.get_next_run_time(local + timedelta(minutes=1)) == local + timedelta(minutes=2)

    def test_a_success_or_a_save_of_the_form_resets_the_waits(self, app, client):
        query = make_query(check_interval_minutes=1440)
        for _ in range(4):
            fail_at(query)
        succeed(query)   # Run now does not wait
        assert query.failure_retry_at is None and query.consecutive_failures == 0
        for _ in range(4):
            fail_at(query)
        assert query.failure_retry_at is not None
        form = {'name': 'Critical', 'source_type': 'database', 'sql_query': 'SELECT 1', 'key_fields': 'ID',
                'check_interval_value': '1', 'check_interval_unit': 'hours', 'is_active': 'on'}
        assert client.post(f'/queries/{query.id}/edit', data=form).status_code == 302
        db.session.expire_all()
        assert db.session.get(type(query), query.id).failure_retry_at is None   # fixed: tried at the next round

    def test_the_scheduler_tries_a_failing_daily_query_less_and_less(self, file_app, scheduler_pool):
        """The real scheduler, one round a minute for 70 minutes, a source that never answers."""
        clock = {'now': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)}
        start = clock['now']

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock['now'].astimezone(tz) if tz else clock['now'].replace(tzinfo=None)

        tried = []

        def source(query):
            tried.append(int((clock['now'] - start).total_seconds() // 60))
            raise ConnectionError('ORA-12170: TNS:Connect timeout occurred')

        with patch('utils.datetime', Clock):
            make_query(check_interval_minutes=1440)
            with patch('monitor_service.execute_query_source', side_effect=source):
                for minute in range(70):
                    clock['now'] = start + timedelta(minutes=minute)
                    scheduler_pool.dispatch_due_queries(file_app)
                    scheduler_pool.shutdown(wait=True)
        # Before: every minute, 70 times
        assert tried == [0, 1, 2, 4, 8, 16, 32, 64]
