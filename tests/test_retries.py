"""
Notifications retried until delivered: every recipient and every channel, with growing
waits, given up after NOTIFY_RETRY_HOURS; a channel that refuses for good is not retried.
"""
import sqlite3
from datetime import timedelta
from unittest.mock import patch

from models import db, MonitoredQuery, ErrorRecord, RoutingRule, RoutingCondition, NotificationChannel
from utils import get_utc_now

ROWS = (['ID', 'TEAM'], [{'ID': 1, 'TEAM': 'PED'}, {'ID': 2, 'TEAM': 'IT'}])
OK = {'success': True}
DOWN = {'success': False, 'message': 'Connection refused', 'email_unavailable': True}


def make_query(name='Critical', rules=(), channels=(), **kwargs):
    kwargs.setdefault('sql_query', 'SELECT 1')
    query = MonitoredQuery(name=name, key_fields='ID', **kwargs)
    for channel in channels:
        query.notification_channels.append(channel)
    db.session.add(query)
    db.session.flush()
    for i, (recipients, conditions) in enumerate(rules):
        rule = RoutingRule(query_id=query.id, name=f'Rule {i}', recipients=recipients,
                           priority=i, is_active=True, condition_logic='OR')
        db.session.add(rule)
        db.session.flush()
        for field, value in conditions:
            db.session.add(RoutingCondition(rule_id=rule.id, field_name=field, operator='equals', value=value))
    db.session.commit()
    return query


def make_channel(name, channel_type='webhook'):
    channel = NotificationChannel(name=name, channel_type=channel_type, config='{}', is_active=True)
    db.session.add(channel)
    db.session.commit()
    return channel


def run(query, rows=ROWS, email=None):
    """Runs a check with a simulated source and email delivery."""
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source', return_value=rows), \
         patch('monitor_service.email_service.send_error_notification',
               **({'side_effect': email} if callable(email) else {'return_value': email or OK})) as send:
        result = monitor_service.check_query(query, force=True)
    return result, send


def records(query):
    return {e.get_error_data()['ID']: e for e in ErrorRecord.query.filter_by(query_id=query.id)}


def wait_for_retry(query):
    """As if the wait before the next attempt were over."""
    for error in ErrorRecord.query.filter_by(query_id=query.id):
        if error.notify_retry_at:
            error.notify_retry_at = error.notify_retry_at - timedelta(hours=2)
    db.session.commit()


class TestRetriedNotifications:

    def test_smtp_down_then_up_the_error_is_notified(self, app):
        query = make_query(email_recipients='ops@x.it', reminder_enabled=True)
        _, send = run(query, email=DOWN)
        assert send.call_count == 1
        error = records(query)[1]
        assert error.notify_pending is True and error.email_sent is False
        assert error.notify_attempts == 1 and 'Connection refused' in error.last_notify_error

        # Not before the wait is over
        _, send = run(query, email=OK)
        assert send.call_count == 0

        wait_for_retry(query)
        _, send = run(query, email=OK)
        assert send.call_count == 1
        assert [e['ID'] for e in send.call_args.kwargs['errors']] == [1, 2]
        error = records(query)[1]
        assert error.notify_pending is False and error.email_sent is True
        assert error.get_notified_to() == ['ops@x.it']
        assert error.last_notify_error is None

        # Delivered: it does not go out again at the next round
        _, send = run(query, email=OK)
        assert send.call_count == 0

    def test_smtp_down_no_other_attempt_in_the_same_round(self, app):
        query = make_query(routing_enabled=True, routing_default_recipients='it@x.it',
                           rules=[('a@x.it', [('TEAM', 'PED')]), ('b@x.it', [('TEAM', 'IT')])])
        _, send = run(query, email=DOWN)
        assert send.call_count == 1
        assert all(e.notify_pending for e in records(query).values())

    def test_partial_delivery_retries_only_the_missing_ones(self, app):
        query = make_query(routing_enabled=True, routing_default_recipients='it@x.it',
                           rules=[('user1@x.it, user2@x.it, it@x.it', [('TEAM', 'IT')])])
        rows = (['ID', 'TEAM'], [{'ID': 2, 'TEAM': 'IT'}])

        def user2_rejected(**kwargs):
            if kwargs['recipients_override'] == ['user2@x.it']:
                return {'success': False, 'message': '450 mailbox busy'}
            return OK
        run(query, rows=rows, email=user2_rejected)
        error = records(query)[2]
        assert error.get_notified_to() == ['user1@x.it', 'it@x.it']
        assert error.notify_pending is True and error.email_sent is True

        wait_for_retry(query)
        _, send = run(query, rows=rows, email=OK)
        assert [c.kwargs['recipients_override'] for c in send.call_args_list] == [['user2@x.it']]
        error = records(query)[2]
        assert error.notify_pending is False
        assert error.get_notified_to() == ['user1@x.it', 'user2@x.it', 'it@x.it']

    def test_failed_channel_retried_alone(self, app):
        telegram, webhook = make_channel('Telegram', 'telegram'), make_channel('Webhook')
        query = make_query(email_recipients='ops@x.it', channels=[telegram, webhook])

        def outcome(channel, query, errors):
            return {'success': channel.name == 'Telegram', 'message': 'HTTP 500'}
        with patch('notification_service.notification_service.send_to_channel', side_effect=outcome) as ch:
            _, send = run(query)
        assert sorted(c.args[0].name for c in ch.call_args_list) == ['Telegram', 'Webhook']
        error = records(query)[1]
        assert error.get_notified_channels() == [telegram.id] and error.notify_pending is True

        wait_for_retry(query)
        with patch('notification_service.notification_service.send_to_channel',
                   return_value={'success': True, 'message': 'OK'}) as ch:
            _, send = run(query)
        assert [c.args[0].name for c in ch.call_args_list] == ['Webhook']
        assert send.call_count == 0  # the email had already arrived
        assert all(not e.notify_pending for e in records(query).values())

    def test_error_gone_is_no_longer_retried(self, app):
        query = make_query(email_recipients='ops@x.it')
        run(query, email=DOWN)
        _, send = run(query, rows=(['ID'], []), email=OK)
        assert send.call_count == 0
        assert all(e.resolved_at and not e.notify_pending for e in records(query).values())

    def test_errors_from_before_the_update_are_not_resent(self, app):
        """Rows from older versions (notify_pending false): no mass delivery."""
        query = make_query(email_recipients='ops@x.it')
        for row in ROWS[1]:
            error = ErrorRecord(query_id=query.id, email_sent=False,
                                error_hash=ErrorRecord.calculate_hash(row, ['ID']))
            error.set_error_data(row)
            db.session.add(error)
        db.session.commit()
        _, send = run(query)
        assert send.call_count == 0

    def test_results_saved_before_sending(self, file_app):
        """While sending, the errors found are already in the DB: another connection sees them."""
        query = make_query(email_recipients='ops@x.it')
        seen = {}

        def count_from_outside(**kwargs):
            with sqlite3.connect(db.engine.url.database, timeout=1) as other:
                seen['rows'] = other.execute('SELECT COUNT(*) FROM error_records').fetchone()[0]
                # and the DB accepts writes from others while sending
                other.execute('UPDATE monitored_queries SET description = ? WHERE id = ?', ('x', query.id))
            return OK
        run(query, email=count_from_outside)
        assert seen['rows'] == 2
        with db.engine.connect() as c:
            assert c.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'


def run_check(query, email_result=None, channel_result=None):
    """A check that finds one error; email and channels answer as given."""
    from monitor_service import monitor_service
    rows = (['ID'], [{'ID': 1}])
    with patch('monitor_service.execute_query_source', return_value=rows), \
         patch('monitor_service.email_service.send_error_notification',
               return_value=email_result or {'success': True}), \
         patch('notification_service.notification_service.send_to_channel',
               return_value=channel_result or {'success': True, 'message': 'OK'}):
        return monitor_service.check_query(query, force=True)


class TestDeliveries:

    def test_http_answers(self):
        from notification_service import NotificationService
        for status in (400, 401, 403, 404, 410):
            assert NotificationService._http_failure(status)['permanent'] is True
        for status in (408, 429, 500, 502, 503):
            assert NotificationService._http_failure(status)['permanent'] is False

    def test_channel_that_refuses_is_not_retried(self, app, client):
        teams = NotificationChannel(name='Old Teams', channel_type='teams', config='{}', is_active=True)
        db.session.add(teams)
        query = make_query(email_recipients='ops@x.it')
        query.notification_channels.append(teams)
        db.session.commit()
        run_check(query, channel_result={'success': False, 'permanent': True, 'message': 'HTTP 410 Gone'})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.notify_pending is False
        assert error.get_undeliverable()['channels'] == [teams.id]
        assert 'HTTP 410' in error.last_notify_error
        html = client.get('/errors').get_data(as_text=True)
        assert 'Not delivered' in html and 'Delivery pending' not in html

    def test_waits_grow_between_attempts(self, app):
        query = make_query(email_recipients='ops@x.it')
        before = get_utc_now()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'email_unavailable': True})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.notify_pending and timedelta(seconds=50) < error.notify_retry_at - before < timedelta(minutes=2)
        error.notify_retry_at = get_utc_now() - timedelta(seconds=1)
        db.session.commit()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'email_unavailable': True})
        assert error.notify_attempts == 2 and error.notify_retry_at - get_utc_now() > timedelta(seconds=100)

    def test_given_up_after_the_retry_hours(self, app, client):
        query = make_query(email_recipients='ops@x.it')
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'email_unavailable': True})
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        error.first_seen_at = get_utc_now() - timedelta(hours=25)
        error.notify_retry_at = None
        db.session.commit()
        run_check(query, email_result={'success': False, 'message': 'SMTP down', 'email_unavailable': True})
        assert error.notify_pending is False
        assert error.get_undeliverable()['recipients'] == ['ops@x.it']
        assert error.last_notify_error.startswith('Not delivered after 2 attempts')
        assert 'Not delivered' in client.get('/errors').get_data(as_text=True)
