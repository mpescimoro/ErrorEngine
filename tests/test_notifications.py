"""
Notification delivery tests: email per recipient, recording who received each
error, channels sent only once, queries with channels only.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch
from models import db, MonitoredQuery, RoutingRule, RoutingCondition, ErrorRecord, NotificationChannel
from utils import get_utc_now

CHANNELS_OK = {'total': 1, 'success': 1, 'failed': 0, 'results': []}


def make_query(channel=None, rules=(), **kwargs):
    query = MonitoredQuery(name='Notifications', sql_query='SELECT 1', key_fields='ID', **kwargs)
    if channel:
        query.notification_channels.append(channel)
    db.session.add(query)
    db.session.flush()
    for i, (recipients, conditions) in enumerate(rules):
        rule = RoutingRule(query_id=query.id, name=f'Rule {i}', recipients=recipients,
                           priority=i, is_active=True, condition_logic='OR')
        db.session.add(rule)
        db.session.flush()
        for field, value in conditions:
            db.session.add(RoutingCondition(rule_id=rule.id, field_name=field,
                                            operator='equals', value=value))
    db.session.commit()
    return query


def emails_sent(mock_send):
    """[(recipients, error IDs)] for each email sent, in order."""
    return [(tuple(c.kwargs['recipients_override']), [e['ID'] for e in c.kwargs['errors']])
            for c in mock_send.call_args_list]


ERRORS = [
    {'ID': 1, 'TEAM': 'PED'},
    {'ID': 2, 'TEAM': 'IT'},
    {'ID': 3, 'TEAM': 'PED'},
]

# As in production: the helpdesk is in every rule and in the default recipients
ROUTING_HELPDESK = dict(
    routing_enabled=True, routing_no_match_action='send_default', routing_default_recipients='helpdesk@x.it',
    rules=[('user.ped@x.it, helpdesk@x.it', [('TEAM', 'PED')]),
           ('user1.it@x.it, user2.it@x.it, helpdesk@x.it', [('TEAM', 'IT')])],
)


@pytest.fixture
def channel(app):
    channel = NotificationChannel(name='Telegram', channel_type='telegram', config='{}')
    db.session.add(channel)
    db.session.commit()
    return channel


@pytest.fixture
def send_email():
    with patch('monitor_service.email_service.send_error_notification',
               return_value={'success': True}) as mock_send:
        yield mock_send


@pytest.fixture
def send_channels():
    with patch('notification_service.notification_service.send_to_all_channels',
               return_value=CHANNELS_OK) as mock_send:
        yield mock_send


class TestEmailDelivery:
    """Original delivery: one email per recipient when routing is on."""

    def test_routing_off_one_email_to_everyone(self, app, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(email_recipients='a@x.it, b@x.it')

        emails, channels, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert (emails, channels) == (1, 0)
        assert emails_sent(send_email) == [(('a@x.it', 'b@x.it'), [1, 2, 3])]

    def test_routing_one_email_per_recipient(self, app, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(**ROUTING_HELPDESK)

        emails, _, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert emails == 4
        assert sorted(emails_sent(send_email)) == [
            (('helpdesk@x.it',), [1, 2, 3]),
            (('user.ped@x.it',), [1, 3]),
            (('user1.it@x.it',), [2]),
            (('user2.it@x.it',), [2]),
        ]

    def test_per_error_one_email_per_error_and_recipient(self, app, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(routing_aggregation='per_error', **ROUTING_HELPDESK)

        emails, _, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert emails == 7   # helpdesk 3 + PED user 2 + 2 IT users x 1


class TestRecordedRecipients:
    """For each error, who received the notification is recorded."""

    @patch('monitor_service.execute_query_source')
    def test_each_error_has_its_recipients(self, mock_execute, app, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(**ROUTING_HELPDESK)
        mock_execute.return_value = (['ID', 'TEAM'], ERRORS)

        monitor_service.check_query(query, force=True)

        notified = {e.get_error_data()['ID']: e.get_notified_to()
                    for e in ErrorRecord.query.filter_by(query_id=query.id)}
        # Specific recipients first, the helpdesk (present in every rule) last
        assert notified == {
            1: ['user.ped@x.it', 'helpdesk@x.it'],
            2: ['user1.it@x.it', 'user2.it@x.it', 'helpdesk@x.it'],
            3: ['user.ped@x.it', 'helpdesk@x.it'],
        }

    @patch('monitor_service.execute_query_source')
    def test_error_of_several_teams_in_configuration_order(self, mock_execute, app, send_email, send_channels):
        """An error that matches two rules: addresses in configuration order, the helpdesk last."""
        from monitor_service import monitor_service
        query = make_query(routing_enabled=True, routing_default_recipients='helpdesk@x.it', rules=[
            ('anna@x.it, luca@x.it, helpdesk@x.it', [('USER', 'RCISANA')]),
            ('giulia@x.it, anna@x.it, helpdesk@x.it', [('USER', 'RCISANA')]),
        ])
        mock_execute.return_value = (['ID', 'USER'], [{'ID': 1, 'USER': 'RCISANA'}])

        monitor_service.check_query(query, force=True)

        assert ErrorRecord.query.filter_by(query_id=query.id).one().get_notified_to() == \
            ['anna@x.it', 'luca@x.it', 'giulia@x.it', 'helpdesk@x.it']

    @patch('monitor_service.execute_query_source')
    def test_routing_off_all_recipients_in_configuration_order(self, mock_execute, app, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(email_recipients='b@x.it, a@x.it, c@x.it')
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        monitor_service.check_query(query, force=True)

        assert ErrorRecord.query.filter_by(query_id=query.id).one().get_notified_to() == \
            ['b@x.it', 'a@x.it', 'c@x.it']

    @patch('monitor_service.execute_query_source')
    def test_failed_email_recipient_not_recorded(self, mock_execute, app, send_channels):
        from monitor_service import monitor_service
        query = make_query(**ROUTING_HELPDESK)
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[1]])

        def send(**kwargs):
            return {'success': kwargs['recipients_override'] != ['user2.it@x.it']}

        with patch('monitor_service.email_service.send_error_notification', side_effect=send):
            monitor_service.check_query(query, force=True)

        assert ErrorRecord.query.filter_by(query_id=query.id).one().get_notified_to() == \
            ['user1.it@x.it', 'helpdesk@x.it']

    @patch('monitor_service.execute_query_source')
    def test_reminder_adds_the_new_recipients(self, mock_execute, app, send_email, send_channels):
        """If the rules change, whoever receives the reminder is added to the list."""
        from monitor_service import monitor_service
        query = make_query(reminder_enabled=True, reminder_interval_minutes=60, reminder_max_count=3,
                           **ROUTING_HELPDESK)
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])
        monitor_service.check_query(query, force=True)
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.get_notified_to() == ['user.ped@x.it', 'helpdesk@x.it']

        # New recipient in the PED rule, and the reminder is due
        rule = RoutingRule.query.filter_by(query_id=query.id, priority=0).one()
        rule.recipients = 'new.ped@x.it, user.ped@x.it, helpdesk@x.it'
        error.email_sent_at = get_utc_now() - timedelta(hours=2)
        db.session.commit()
        query.locked_at = None
        monitor_service.check_query(query, force=True)

        assert error.reminder_count == 1
        assert error.get_notified_to() == ['new.ped@x.it', 'user.ped@x.it', 'helpdesk@x.it']

    @patch('monitor_service.execute_query_source')
    def test_channel_only_no_address(self, mock_execute, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channel=channel, email_recipients='')
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        monitor_service.check_query(query, force=True)

        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.get_notified_to() == []
        assert error.email_sent is True


class TestChannels:
    """Channels (Telegram, Teams, webhook): a single delivery, even without email."""

    def test_channel_sent_once_with_several_recipients(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channel=channel, **ROUTING_HELPDESK)

        emails, channels, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert (emails, channels) == (4, 1)
        assert send_channels.call_count == 1
        assert send_channels.call_args.args[1] == ERRORS

    def test_channel_only_without_email_recipients(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channel=channel, email_recipients='')

        emails, channels, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert (emails, channels) == (0, 1)
        assert send_email.call_count == 0
        assert send_channels.call_count == 1

    @patch('monitor_service.execute_query_source')
    def test_no_delivery_errors_not_notified(self, mock_execute, app, channel, send_email):
        """If the channel fails, the error is not marked as notified."""
        from monitor_service import monitor_service
        query = make_query(channel=channel, email_recipients='')
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        with patch('notification_service.notification_service.send_to_all_channels',
                   return_value={'total': 1, 'success': 0, 'failed': 1, 'results': []}):
            monitor_service.check_query(query, force=True)

        assert ErrorRecord.query.filter_by(query_id=query.id).one().email_sent is False


class TestEmailFromTheScheduler:
    """Emails are created by the scheduler, outside a web request."""

    def test_email_created_outside_a_request(self, app):
        """Template context processors (e.g. the signed-in user) must not block emails."""
        from flask import has_request_context
        from email_service import email_service
        query = make_query(email_recipients='a@x.it, b@x.it')
        assert not has_request_context()

        with patch('email_service.smtplib.SMTP_SSL') as smtp_ssl, patch('email_service.smtplib.SMTP') as smtp:
            result = email_service.send_error_notification(
                query=query, errors=ERRORS, columns=['ID', 'TEAM'],
                recipients_override=['a@x.it', 'b@x.it'])

        assert result['success'], result.get('message')
        server = (smtp_ssl if smtp_ssl.called else smtp).return_value
        assert server.sendmail.call_args.args[1] == ['a@x.it', 'b@x.it']


class TestReminders:
    """Reminders to channels only."""

    def make_old_error(self, query):
        error = ErrorRecord(query_id=query.id, email_sent=True,
                            error_hash=ErrorRecord.calculate_hash(ERRORS[0], query.get_key_fields_list()),
                            email_sent_at=get_utc_now() - timedelta(hours=5))
        error.set_error_data(ERRORS[0])
        db.session.add(error)
        db.session.commit()
        return error

    def test_channel_only_reminder_updates_the_counters(self, app, channel, send_email, send_channels):
        """Without updating the counters the reminder would go out at every check."""
        from monitor_service import monitor_service
        query = make_query(channel=channel, email_recipients='', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        error = self.make_old_error(query)

        assert monitor_service._process_reminders(query, ['ID']) == (0, 1)
        assert error.reminder_count == 1
        assert error.last_reminder_at is not None

        # Right after, it must not go out again
        assert monitor_service._process_reminders(query, ['ID']) == (0, 0)
        assert send_channels.call_count == 1

    @patch('monitor_service.execute_query_source')
    def test_reminders_counted_in_the_log(self, mock_execute, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channel=channel, email_recipients='a@x.it', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        self.make_old_error(query)
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        result = monitor_service.check_query(query, force=True)

        assert result['reminders_sent'] == 2   # 1 email + 1 channel
        assert result['emails_sent'] == 1
