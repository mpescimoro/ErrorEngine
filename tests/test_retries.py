"""
Notifications retried until delivered: every recipient and every channel, with growing
waits, given up after NOTIFY_ATTEMPTS attempts; a channel that refuses for good is not retried.
"""
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import patch

from models import db, MonitoredQuery, ErrorRecord, EmailLog, NotificationChannel
from utils import get_utc_now
from tests import helpers
from tests.helpers import make_channel


make_query = partial(helpers.make_query, name='Critical')

ROWS = (['ID', 'TEAM'], [{'ID': 1, 'TEAM': 'PED'}, {'ID': 2, 'TEAM': 'IT'}])
OK = {'success': True}
DOWN = {'success': False, 'message': 'Connection refused', 'email_unavailable': True}


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

        def outcome(channel, *args, **kwargs):
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
            # The with of sqlite3 commits but does not close: closing() does
            with closing(sqlite3.connect(db.engine.url.database, timeout=1)) as other, other:
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
        assert 'Not delivered</span>' in html and not re.search(r'badge-warning[^>]*>\s*Delivery pending', html)

    def test_attempts_and_waits(self, app, client):
        down = {'success': False, 'message': 'SMTP down', 'email_unavailable': True}

        def until_given_up(name):
            """The waits before each new attempt, minutes; then the error given up."""
            query = make_query(name=name, email_recipients='ops@x.it')
            run_check(query, email_result=down)
            error = ErrorRecord.query.filter_by(query_id=query.id).one()
            waits = []
            for _ in range(10):
                if not error.notify_pending:
                    break
                waits.append(round((error.notify_retry_at - get_utc_now()).total_seconds() / 60))
                error.notify_retry_at = None   # the wait is over
                db.session.commit()
                run_check(query, email_result=down)
            return waits, error

        waits, error = until_given_up('Default')
        assert waits == [1, 5, 15, 60] and error.notify_attempts == 5
        assert error.get_undeliverable()['recipients'] == ['ops@x.it']
        assert error.last_notify_error.startswith('Not delivered after 5 attempts')
        assert 'Not delivered' in client.get('/errors').get_data(as_text=True)
        app.config['NOTIFY_ATTEMPTS'] = 7   # beyond the fifth, one an hour
        assert until_given_up('Seven')[0] == [1, 5, 15, 60, 60, 60]
        app.config['NOTIFY_ATTEMPTS'] = 1   # no retries
        waits, error = until_given_up('One')
        assert waits == [] and error.get_undeliverable()['recipients'] == ['ops@x.it']


def deliver(query, email=None):
    """The scheduler's delivery of the pending notifications: the source is not read again."""
    from monitor_service import monitor_service
    with patch('monitor_service.execute_query_source') as source, \
         patch('monitor_service.email_service.send_error_notification',
               **({'side_effect': email} if callable(email) else {'return_value': email or OK})) as send:
        result = monitor_service.deliver_pending(query)
    assert not source.called
    return result, send


class TestRetriesBetweenChecks:
    """A failed notification does not wait for the query's next check."""

    def test_daily_query_retried_by_the_scheduler(self, app):
        import scheduler
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=1440)
        run(query, email=DOWN)
        assert scheduler.queries_with_due_retries() == []  # the first wait is not over
        wait_for_retry(query)
        assert query.should_run_now()[0] is False
        assert scheduler.queries_with_due_retries() == [query.id]

        result, send = deliver(query)
        assert result['status'] == 'success' and result['emails_sent'] == 1
        assert [e['ID'] for e in send.call_args.kwargs['errors']] == [1, 2]
        assert send.call_args.kwargs['columns'] == ['ID', 'TEAM']
        assert all(not e.notify_pending and e.email_sent for e in records(query).values())
        assert scheduler.queries_with_due_retries() == []
        assert query.total_emails_sent == 1

    def test_still_failing_waits_longer(self, app):
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=1440)
        run(query, email=DOWN)
        wait_for_retry(query)
        deliver(query, email=DOWN)
        error = records(query)[1]
        assert error.notify_pending and error.notify_attempts == 2
        assert timedelta(minutes=4) < error.notify_retry_at - get_utc_now() <= timedelta(minutes=5)

    def test_outside_the_time_window_too(self, app):
        import scheduler
        query = make_query(email_recipients='ops@x.it')
        run(query, email=DOWN)
        tomorrow = (get_utc_now() + timedelta(days=1)).isoweekday()
        query.schedule_days = str(tomorrow)
        db.session.commit()
        wait_for_retry(query)
        assert not query.is_in_schedule()
        assert scheduler.queries_with_due_retries() == [query.id]

    def test_not_for_inactive_queries_or_resolved_errors(self, app):
        import scheduler
        paused = make_query(name='Paused', email_recipients='ops@x.it')
        gone = make_query(name='Gone', email_recipients='ops@x.it')
        run(paused, email=DOWN)
        run(gone, email=DOWN)
        paused.is_active = False
        for error in ErrorRecord.query.filter_by(query_id=gone.id):
            error.resolved_at = get_utc_now()
        db.session.commit()
        wait_for_retry(paused)
        wait_for_retry(gone)
        assert scheduler.queries_with_due_retries() == []

    def test_a_running_check_is_left_alone_and_the_lock_released(self, app):
        query = make_query(email_recipients='ops@x.it')
        run(query, email=DOWN)
        wait_for_retry(query)
        query.locked_at = get_utc_now()
        db.session.commit()
        result, send = deliver(query)
        assert result['status'] == 'skipped' and send.call_count == 0
        assert db.session.get(MonitoredQuery, query.id).locked_at is not None
        query.locked_at = None   # the check is over
        db.session.commit()
        result, send = deliver(query)
        assert send.call_count == 1
        db.session.expire_all()
        assert db.session.get(MonitoredQuery, query.id).locked_at is None

    def test_dispatch_sends_them_in_the_pool(self, file_app, scheduler_pool):
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=1440)
        run(query, email=DOWN)
        wait_for_retry(query)
        with patch('monitor_service.email_service.send_error_notification', return_value=OK) as send:
            assert scheduler_pool.dispatch_due_queries(file_app) == []  # no check is due
            scheduler_pool.shutdown(wait=True)
        assert send.call_count == 1
        db.session.expire_all()
        assert all(not e.notify_pending for e in records(query).values())


def delivery_log(caplog):
    """What the delivery wrote in the log: everything but the start and end lines of the check."""
    return [r for r in caplog.records
            if r.name in ('monitor_service', 'email_service', 'notification_service')
            and r.levelno >= logging.INFO
            and not r.getMessage().startswith('Running query') and ' done: ' not in r.getMessage()]


FIFTY = (['ID'], [{'ID': i} for i in range(1, 51)])


class TestOneLogLinePerAttempt:
    """A delivery attempt writes one line: the errors, what went out, what is still pending and why."""

    def check(self, query, caplog, smtp=None):
        from monitor_service import monitor_service
        caplog.clear()
        with caplog.at_level(logging.INFO), \
             patch('monitor_service.execute_query_source', return_value=FIFTY), \
             patch('email_service.smtplib.SMTP_SSL', **(smtp or {})):
            monitor_service.check_query(query, force=True)
        return delivery_log(caplog)

    def test_email_down_then_delivered_at_the_next_attempt(self, app, caplog):
        from monitor_service import monitor_service
        query = make_query(email_recipients='ops@x.it, it@x.it')
        lines = self.check(query, caplog, smtp={'side_effect': OSError('Connection refused')})
        assert len(lines) == 1 and lines[0].levelno == logging.WARNING
        message = lines[0].getMessage()
        assert 'Critical' in message and '50 errors' in message and 'attempt 1' in message
        assert '50 pending for ops@x.it, it@x.it' in message and 'next attempt in 1 min' in message
        assert 'Connection refused' in message
        wait_for_retry(query)
        caplog.clear()
        with caplog.at_level(logging.INFO), patch('email_service.smtplib.SMTP_SSL'):
            monitor_service.deliver_pending(query)
        lines = delivery_log(caplog)
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        message = lines[0].getMessage()
        assert '50 errors' in message and 'attempt 2' in message and 'delivered to ops@x.it, it@x.it' in message
        assert 'pending' not in message

    def test_given_up(self, app, caplog):
        app.config['NOTIFY_ATTEMPTS'] = 2
        query = make_query(email_recipients='ops@x.it')
        self.check(query, caplog, smtp={'side_effect': OSError('Connection refused')})
        wait_for_retry(query)
        lines = self.check(query, caplog, smtp={'side_effect': OSError('Connection refused')})
        assert len(lines) == 1 and lines[0].levelno == logging.ERROR
        message = lines[0].getMessage()
        assert '50 given up for ops@x.it after 2 attempts' in message and 'Connection refused' in message

    def test_email_delivered_channel_pending(self, app, caplog):
        hook = make_channel('Hook')
        query = make_query(email_recipients='ops@x.it', channels=[hook])
        with patch('notification_service.notification_service.send_to_channel',
                   return_value={'success': False, 'message': 'HTTP 503'}):
            lines = self.check(query, caplog)
        assert len(lines) == 1 and lines[0].levelno == logging.WARNING
        message = lines[0].getMessage()
        assert 'delivered to ops@x.it' in message and '50 pending for Hook' in message
        assert 'Hook: HTTP 503' in message

    def test_nobody_to_notify(self, app, caplog):
        query = make_query(email_recipients='')
        lines = self.check(query, caplog)
        assert len(lines) == 1 and lines[0].levelno == logging.WARNING
        assert 'no recipients' in lines[0].getMessage()


class TestNotDelivered:
    """After its last attempt an error that could not be notified stays active, and it shows."""

    def given_up(self):
        teams = make_channel('Teams IT', channel_type='teams')
        query = make_query(email_recipients='ops@x.it', channels=[teams])
        failing = {'success': False, 'message': 'HTTP 503 Service Unavailable'}
        run_check(query, email_result={'success': False, 'message': 'Connection refused', 'email_unavailable': True},
                  channel_result=failing)
        error = ErrorRecord.query.filter_by(query_id=query.id).one()
        error.notify_attempts = 4   # as if the first four had failed
        error.notify_retry_at = None
        db.session.commit()
        run_check(query, email_result={'success': False, 'message': 'Connection refused', 'email_unavailable': True},
                  channel_result=failing)
        return query, error

    def test_stays_active_and_is_not_retried(self, app):
        query, error = self.given_up()
        assert error.resolved_at is None and error.undeliverable and not error.notify_pending
        names = dict(db.session.query(NotificationChannel.id, NotificationChannel.name))
        assert error.undelivered_to(names) == ['ops@x.it', 'Teams IT']
        run_check(query)   # the row is still there: still active, not sent again
        assert error.resolved_at is None and error.undeliverable and not error.email_sent

    def test_the_cleanup_keeps_it(self, app):
        from cleanup_service import cleanup_service
        _, error = self.given_up()
        cleanup_service.run_full_cleanup()
        assert db.session.get(ErrorRecord, error.id) is not None

    def test_errors_page_says_to_whom_and_why_and_filters_them(self, app, client):
        self.given_up()
        other = make_query(name='Other', email_recipients='ops@x.it')
        run_check(other)   # delivered
        html = client.get('/errors').get_data(as_text=True)
        # Visible text, not only a tooltip
        assert 'Not delivered</span>' in html
        assert re.search(r'>\s*to ops@x\.it, Teams IT\s*<', html)
        assert re.search(r'>\s*Not delivered after 5 attempts: [^<]*Connection refused', html)
        listed = re.compile(r'class="query-name">\s*Other\s*</a>')
        failed = client.get('/errors?delivery=failed').get_data(as_text=True)
        assert re.search(r'class="query-name">\s*Critical\s*</a>', failed) and not listed.search(failed)
        assert listed.search(html) and 'Not delivered (1)' in failed

    def test_query_page_and_dashboard(self, app, client):
        query, _ = self.given_up()
        detail = client.get(f'/queries/{query.id}').get_data(as_text=True)
        assert 'Not delivered</span>' in detail and 'to ops@x.it, Teams IT' in detail
        assert f'href="/errors?query_id={query.id}&amp;delivery=failed"' in detail
        dashboard = client.get('/').get_data(as_text=True)
        assert re.search(r'href="/errors\?delivery=failed"[^>]*>\s*1 not delivered', dashboard)


START = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def five_minute_query(minutes, errors_from, server_up_from, app, pool):
    """
    A query checked every 5 minutes, run by the real scheduler one tick per minute for
    `minutes` minutes. errors_from: {error ID: minute it appears}; the mail server answers
    from server_up_from on. Returns the events: (minute, 'check') and (minute, 'sent' or 'failed', IDs).
    """
    clock = {'now': START}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now'].astimezone(tz) if tz else clock['now'].replace(tzinfo=None)

    def minute():
        return int((clock['now'] - START).total_seconds() // 60)

    events = []

    def source(query):
        events.append((minute(), 'check'))
        return ['ID'], [{'ID': i} for i, since in errors_from.items() if minute() >= since]

    def send(**kwargs):
        up = minute() >= server_up_from
        events.append((minute(), 'sent' if up else 'failed', [e['ID'] for e in kwargs['errors']]))
        return OK if up else DOWN

    with patch('utils.datetime', Clock):
        query = make_query(email_recipients='ops@x.it', check_interval_minutes=5)
        with patch('monitor_service.execute_query_source', side_effect=source), \
             patch('monitor_service.email_service.send_error_notification', side_effect=send):
            for m in range(minutes):
                clock['now'] = START + timedelta(minutes=m)
                pool.dispatch_due_queries(app)
                pool.shutdown(wait=True)   # the next tick finds this one finished
    db.session.expire_all()
    return query, events


class TestQueryEveryFiveMinutes:
    """
    The checks every 5 minutes do not add attempts: an error waits for its own retry,
    sent by the scheduler between the checks or by the check that falls on it.
    """

    def test_server_back_while_an_error_waits(self, file_app, scheduler_pool):
        query, events = five_minute_query(22, errors_from={1: 0, 2: 10}, server_up_from=12,
                                          app=file_app, pool=scheduler_pool)
        assert events == [
            (0, 'check'), (0, 'failed', [1]),     # 1 found, mail server down
            (1, 'failed', [1]),                   # tried again between the checks: after 1, 5, 15 minutes...
            (5, 'check'),                         # the wait of 1 is not over: the check does not send it
            (6, 'failed', [1]),
            (10, 'check'), (10, 'failed', [2]),   # 2 found: its own attempts
            (11, 'failed', [2]),
            (15, 'check'),
            (16, 'sent', [2]),                    # server back at 12
            (20, 'check'),
            (21, 'sent', [1]),                    # 1 at the end of its wait of 15 minutes
        ]
        assert all(not e.notify_pending and e.email_sent for e in records(query).values())

    def test_server_down_for_more_than_an_hour(self, file_app, scheduler_pool):
        query, events = five_minute_query(100, errors_from={1: 0}, server_up_from=999,
                                          app=file_app, pool=scheduler_pool)
        assert [e[0] for e in events if e[1] == 'failed'] == [0, 1, 6, 21, 81]
        assert [e[0] for e in events if e[1] == 'check'] == list(range(0, 100, 5))
        error = records(query)[1]
        assert not error.notify_pending and error.notify_attempts == 5
        assert error.get_undeliverable()['recipients'] == ['ops@x.it'] and error.resolved_at is None


REFUSED = {'bad@x.it': (550, b'5.1.1 User unknown')}


class TestRefusedRecipients:
    """A recipient the mail server refuses has not received the email: the server's reason, address by address."""

    def check(self, query, refused):
        from monitor_service import monitor_service
        with patch('monitor_service.execute_query_source', return_value=(['ID'], [{'ID': 1}])), \
             patch('email_service.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.sendmail.return_value = refused
            monitor_service.check_query(query, force=True)

    def test_tried_again_alone(self, app):
        from monitor_service import monitor_service
        query = make_query(email_recipients='ops@x.it, bad@x.it')
        self.check(query, REFUSED)
        error = records(query)[1]
        assert error.get_notified_to() == ['ops@x.it'] and error.notify_pending
        assert 'bad@x.it: 550 5.1.1 User unknown' in error.last_notify_error
        log = EmailLog.query.one()
        assert log.status == 'sent' and 'bad@x.it' in log.error_message
        wait_for_retry(query)
        with patch('email_service.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.sendmail.return_value = {}
            monitor_service.deliver_pending(query)
        assert smtp.return_value.sendmail.call_args.args[1] == ['bad@x.it']
        error = records(query)[1]
        assert not error.notify_pending and set(error.get_notified_to()) == {'ops@x.it', 'bad@x.it'}

    def test_refused_every_time_ends_not_delivered(self, app):
        import smtplib
        from monitor_service import monitor_service
        app.config['NOTIFY_ATTEMPTS'] = 2
        query = make_query(email_recipients='ops@x.it, bad@x.it')
        self.check(query, REFUSED)
        wait_for_retry(query)
        with patch('email_service.smtplib.SMTP_SSL') as smtp:
            # Alone, it is the whole email that is refused: the same words as before
            smtp.return_value.sendmail.side_effect = smtplib.SMTPRecipientsRefused(REFUSED)
            monitor_service.deliver_pending(query)
        error = records(query)[1]
        assert not error.notify_pending and error.get_notified_to() == ['ops@x.it']
        assert error.get_undeliverable()['recipients'] == ['bad@x.it']
        assert error.last_notify_error == 'Not delivered after 2 attempts: bad@x.it: 550 5.1.1 User unknown'
