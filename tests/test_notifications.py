"""
Notification delivery tests: email per recipient, recording who received each
error, channels sent only once and their messages, queries with channels only.
"""
import json
import re
import pytest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from models import db, RoutingRule, ErrorRecord, NotificationChannel
from utils import get_utc_now
from tests.helpers import make_query

CHANNELS_OK = {'total': 1, 'success': 1, 'failed': 0, 'results': []}


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


def make_old_error(query):
    """An error notified 5 hours ago: a reminder is due."""
    error = ErrorRecord(query_id=query.id, email_sent=True,
                        error_hash=ErrorRecord.calculate_hash(ERRORS[0], query.get_key_fields_list()),
                        email_sent_at=get_utc_now() - timedelta(hours=5))
    error.set_error_data(ERRORS[0])
    db.session.add(error)
    db.session.commit()
    return error


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
    """Emails: one for everyone with routing off, one per recipient with routing on, or per error."""

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
            ('anna@x.it, luca@x.it, helpdesk@x.it', [('USER', 'USER01')]),
            ('giulia@x.it, anna@x.it, helpdesk@x.it', [('USER', 'USER01')]),
        ])
        mock_execute.return_value = (['ID', 'USER'], [{'ID': 1, 'USER': 'USER01'}])

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
        query = make_query(channels=[channel], email_recipients='')
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        monitor_service.check_query(query, force=True)

        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        assert error.get_notified_to() == []
        assert error.email_sent is True


class TestChannels:
    """
    Channels (Telegram, Teams, webhook): a single delivery, even without email;
    messages they accept and their answers read correctly.
    """

    def test_channel_sent_once_with_several_recipients(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channels=[channel], **ROUTING_HELPDESK)

        emails, channels, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert (emails, channels) == (4, 1)
        assert send_channels.call_count == 1
        assert send_channels.call_args.args[1] == ERRORS

    def test_channel_only_without_email_recipients(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='')

        emails, channels, _ = monitor_service._send_notifications(query, ERRORS, ['ID'])
        assert (emails, channels) == (0, 1)
        assert send_email.call_count == 0
        assert send_channels.call_count == 1

    @patch('monitor_service.execute_query_source')
    def test_no_delivery_errors_not_notified(self, mock_execute, app, channel, send_email):
        """If the channel fails, the error is not marked as notified."""
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='')
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        with patch('notification_service.notification_service.send_to_all_channels',
                   return_value={'total': 1, 'success': 0, 'failed': 1, 'results': []}):
            monitor_service.check_query(query, force=True)

        assert ErrorRecord.query.filter_by(query_id=query.id).one().email_sent is False

    def test_teams_202_response_is_a_successful_delivery(self, app):
        from notification_service import notification_service
        with patch('notification_service.requests.post') as post:
            post.return_value.status_code = 202
            assert notification_service._post_teams('https://example.invalid/workflow', {})['success'] is True

    def test_telegram_converts_html_characters(self, app):
        from notification_service import notification_service
        query = MagicMock(name='Q')
        query.name = 'Orders <B2B>'
        with patch('notification_service.requests.post') as post:
            post.return_value.json.return_value = {'ok': True}
            notification_service._send_telegram({'bot_token': 'T', 'chat_id': '1'}, query,
                                                [{'NOTE': 'qty < 0 & stock > 5'}])
        message = post.call_args.kwargs['json']['text']
        assert 'Orders &lt;B2B&gt;' in message
        assert 'qty &lt; 0 &amp; stock &gt; 5' in message
        assert message.startswith('<b>ErrorEngine</b>')

    def test_webhook_with_decimal_values(self, app):
        from notification_service import notification_service
        query = MagicMock(id=1, description='')
        query.name = 'IBM i'
        with patch('notification_service.requests.request') as request:
            request.return_value.status_code = 200
            result = notification_service._send_webhook({'url': 'https://example.invalid/hook'}, query,
                                                        [{'AMOUNT': Decimal('12.50')}])
        assert result['success'] is True
        assert json.loads(request.call_args.kwargs['data'])['errors'] == [{'AMOUNT': '12.50'}]

    def test_new_errors_reach_the_channels_with_their_ids(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='')
        with patch('monitor_service.execute_query_source', return_value=(['ID', 'TEAM'], ERRORS[:2])):
            monitor_service.check_query(query, force=True)
        ids = [e.id for e in ErrorRecord.query.filter_by(query_id=query.id).order_by(ErrorRecord.id)]
        assert send_channels.call_args.kwargs['error_ids'] == ids
        assert not send_channels.call_args.kwargs.get('reminder')


CHANNEL_CONFIGS = {
    'webhook': {'url': 'https://example.invalid/hook'},
    'telegram': {'bot_token': 'T', 'chat_id': '1'},
    'teams': {'webhook_url': 'https://example.invalid/workflow'},
}


def received_by(channel_type, query, errors=ERRORS[:2], **kwargs):
    """What the channel receives: the webhook JSON, the Telegram text or the Teams card."""
    from notification_service import notification_service
    channel = NotificationChannel(name=channel_type, channel_type=channel_type,
                                  config=json.dumps(CHANNEL_CONFIGS[channel_type]))
    db.session.add(channel)
    db.session.commit()
    with patch('notification_service.requests.request') as request, \
         patch('notification_service.requests.post') as post:
        request.return_value.status_code = 200
        post.return_value.status_code = 200
        post.return_value.json.return_value = {'ok': True}
        assert notification_service.send_to_channel(channel, query, errors, **kwargs)['success'] is True
    if channel_type == 'webhook':
        return json.loads(request.call_args.kwargs['data'])
    if channel_type == 'telegram':
        return post.call_args.kwargs['json']['text']
    return json.loads(post.call_args.kwargs['data'])


class TestChannelMessages:
    """Webhooks, Telegram and Teams tell reminders from new errors; webhooks carry the error ids."""

    def test_webhook(self, app):
        query = make_query()
        payload = received_by('webhook', query, error_ids=[7, 8])
        assert payload['event'] == 'errors_detected'
        assert payload['errors'] == ERRORS[:2] and payload['error_ids'] == [7, 8]
        assert payload['errors_count'] == 2
        # UTC, and it says so
        assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', payload['timestamp'])
        payload = received_by('webhook', query, reminder=True, error_ids=[7, 8])
        assert payload['event'] == 'errors_reminder' and payload['error_ids'] == [7, 8]
        # The ids follow the errors sent: the first 50
        rows = [{'ID': i} for i in range(60)]
        payload = received_by('webhook', query, errors=rows, error_ids=list(range(100, 160)))
        assert payload['errors_count'] == 60
        assert len(payload['errors']) == len(payload['error_ids']) == 50
        assert payload['error_ids'][-1] == 149

    def test_telegram(self, app):
        query = make_query()
        new = received_by('telegram', query)
        reminder = received_by('telegram', query, reminder=True)
        assert new.splitlines()[0] != reminder.splitlines()[0]
        assert 'reminder' in reminder.splitlines()[0].lower()
        assert '<b>Time:</b>' in new and 'Data:' not in new

    def test_teams(self, app):
        query = make_query()
        new = received_by('teams', query)['sections'][0]
        reminder = received_by('teams', query, reminder=True)['sections'][0]
        assert new['activitySubtitle'] != reminder['activitySubtitle']
        assert 'reminder' in reminder['activitySubtitle'].lower()
        assert [f['name'] for f in new['facts']][:2] == ['New errors', 'Time']
        # Values of the source as text: [text](address) is not a link
        assert new['markdown'] is False and reminder['markdown'] is False

    def test_teams_failing_query_alert_is_text(self, app):
        from notification_service import notification_service
        query = make_query()
        query.notification_channels.append(NotificationChannel(
            name='Teams', channel_type='teams', config=json.dumps(CHANNEL_CONFIGS['teams'])))
        db.session.commit()
        with patch('notification_service.requests.post') as post:
            post.return_value.status_code = 200
            notification_service.send_system_message(query, 'monitor_failed', 'Failing', ['Last error: [x](y)'])
        assert json.loads(post.call_args.kwargs['data'])['sections'][0]['markdown'] is False

    def test_webhook_failing_query_alert_in_utc(self, app):
        from notification_service import notification_service
        query = make_query()
        query.notification_channels.append(NotificationChannel(
            name='Hook', channel_type='webhook', config=json.dumps(CHANNEL_CONFIGS['webhook'])))
        db.session.commit()
        with patch('notification_service.requests.request') as request:
            request.return_value.status_code = 200
            notification_service.send_system_message(query, 'monitor_failed', 'Failing', ['Last error: x'])
        payload = json.loads(request.call_args.kwargs['data'])
        assert payload['event'] == 'monitor_failed'
        assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', payload['timestamp'])

    def test_no_links_to_errorengine(self, app):
        # With a single account, whoever receives the notifications usually cannot sign in
        from email_service import email_service
        query = make_query()
        assert 'url' not in received_by('webhook', query)
        assert 'http' not in received_by('telegram', query)
        assert 'http' not in json.dumps(received_by('teams', query)['sections'])
        with patch.object(email_service, '_deliver') as deliver:
            email_service.send_error_notification(query, ERRORS[:1], ['ID', 'TEAM'], recipients_override=['a@x.it'])
        assert 'href=' not in deliver.call_args.args[2]

    def test_reminders_reach_the_channels_as_reminders(self, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        error = make_old_error(query)
        monitor_service._process_reminders(query, ['ID'])
        assert send_channels.call_args.kwargs['reminder'] is True
        assert send_channels.call_args.kwargs['error_ids'] == [error.id]


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

    def test_channel_only_reminder_updates_the_counters(self, app, channel, send_email, send_channels):
        """Without updating the counters the reminder would go out at every check."""
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        error = make_old_error(query)

        assert monitor_service._process_reminders(query, ['ID']) == (0, 1)
        assert error.reminder_count == 1
        assert error.last_reminder_at is not None

        # Right after, it must not go out again
        assert monitor_service._process_reminders(query, ['ID']) == (0, 0)
        assert send_channels.call_count == 1

    @patch('monitor_service.execute_query_source')
    def test_reminders_counted_in_the_log(self, mock_execute, app, channel, send_email, send_channels):
        from monitor_service import monitor_service
        query = make_query(channels=[channel], email_recipients='a@x.it', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        make_old_error(query)
        mock_execute.return_value = (['ID', 'TEAM'], [ERRORS[0]])

        result = monitor_service.check_query(query, force=True)

        assert result['reminders_sent'] == 2   # 1 email + 1 channel
        assert result['emails_sent'] == 1

    def test_a_refused_recipient_is_not_counted_as_reminded(self, app):
        from monitor_service import monitor_service
        query = make_query(email_recipients='ops@x.it, bad@x.it', reminder_enabled=True,
                           reminder_interval_minutes=60, reminder_max_count=3)
        error = make_old_error(query)
        with patch('email_service.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.sendmail.return_value = {'bad@x.it': (550, b'5.1.1 User unknown')}
            monitor_service._process_reminders(query, ['ID'])
        assert error.get_notified_to() == ['ops@x.it']
