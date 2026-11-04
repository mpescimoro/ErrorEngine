"""
Queries that cannot read their source: the alert after MONITOR_FAILURE_THRESHOLD failures
in a row and when they recover, health check and Settings.
"""
from unittest.mock import patch

import pytest

from models import db, MonitoredQuery, NotificationChannel, EmailLog

OK = {'success': True}


def make_query(name='Critical', channels=(), **kwargs):
    query = MonitoredQuery(name=name, sql_query='SELECT 1', key_fields='ID', **kwargs)
    for channel in channels:
        query.notification_channels.append(channel)
    db.session.add(query)
    db.session.commit()
    return query


def make_channel(name, channel_type='webhook'):
    channel = NotificationChannel(name=name, channel_type=channel_type, config='{}', is_active=True)
    db.session.add(channel)
    db.session.commit()
    return channel


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
        stopped = {'enabled': True, 'running': False, 'last_tick_at': None, 'in_flight': 0, 'max_workers': 4}
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
