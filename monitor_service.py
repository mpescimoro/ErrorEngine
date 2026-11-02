"""
Main monitoring service.
Runs the queries periodically, with conditional routing and reminders.
"""
import logging
from datetime import timedelta
from flask import current_app
from utils import get_utc_now, format_local, redact_secrets
import time
from collections import defaultdict
from models import db, MonitoredQuery, ErrorRecord, QueryLog
from data_sources import execute_query_source, test_query_source, get_query_fields
from routing_service import apply_routing_rules
from email_service import email_service

logger = logging.getLogger(__name__)


class MonitorService:
    """
    Orchestrates error monitoring.
    Supports multiple sources, conditional routing and reminders.
    """
    
    def __init__(self, app=None):
        self.app = app
        
    def init_app(self, app):
        """Initializes the Flask extension."""
        self.app = app
        app.extensions['monitor'] = self
    
    def check_query(self, query: MonitoredQuery, force: bool = False) -> dict:
        """
        Runs the full check of a single query.

        1. Checks the time window (unless force)
        2. Runs the query on the configured data source
        3. Computes the hashes of the errors found
        4. Compares them with the internal DB and saves the result right away
        5. Sends the pending notifications (new errors and deliveries that failed
           in previous checks), with conditional routing
        6. Sends reminders for unresolved errors
        7. If the source cannot be read, counts the failures and sends an alert
           (and another one when it works again)

        Args:
            query: MonitoredQuery to run
            force: If True, ignores the time window
            
        Returns:
            dict with the run statistics
        """
        start_time = time.time()
        result = {
            'query_id': query.id,
            'query_name': query.name,
            'status': 'success',
            'rows_returned': 0,
            'new_errors': 0,
            'resolved_errors': 0,
            'reminders_sent': 0,
            'emails_sent': 0,
            'error_message': None
        }
        
        try:
            # 1. Time window
            if not force and not query.is_in_schedule():
                # Skips are not logged: they would only fill the logs
                result['status'] = 'skipped'
                result['error_message'] = 'Outside the configured time window'
                return result

            # ATOMIC LOCK: prevents concurrent runs of the same query
            # The UPDATE is atomic in SQLite - only one process can take the lock
            LOCK_TTL = self._lock_ttl()
            now = get_utc_now()
            lock_expired_before = now - LOCK_TTL
            
            updated = (
                db.session.query(MonitoredQuery)
                .filter(
                    MonitoredQuery.id == query.id,
                    db.or_(
                        MonitoredQuery.locked_at == None,
                        MonitoredQuery.locked_at < lock_expired_before
                    )
                )
                .update(
                    {MonitoredQuery.locked_at: now},
                    synchronize_session=False
                )
            )
            db.session.commit()
            
            if updated == 0:
                # Lock already taken by another process
                result['status'] = 'skipped'
                result['error_message'] = 'Query already running in another process'
                return result

            # 2. Run the query on the configured source
            logger.info(f"Running query: {query.name} (source: {query.source_type})")
            columns, rows = execute_query_source(query)
            result['rows_returned'] = len(rows)
            
            # 3. Key fields
            key_fields = query.get_key_fields_list()
            
            # 4. Hashes of the current errors
            current_errors = {}
            for row in rows:
                error_hash = ErrorRecord.calculate_hash(row, key_fields)
                current_errors[error_hash] = row
            
            # 5. Existing unresolved errors
            existing_errors = {
                e.error_hash: e 
                for e in ErrorRecord.query.filter_by(
                    query_id=query.id, 
                    resolved_at=None
                ).all()
            }
            
            # 6. New, resolved and continuing errors
            new_error_hashes = set(current_errors.keys()) - set(existing_errors.keys())
            resolved_hashes = set(existing_errors.keys()) - set(current_errors.keys())
            continuing_hashes = set(current_errors.keys()) & set(existing_errors.keys())
            
            # 7. New errors (in row order), to notify
            new_records = []
            for hash_val, error_data in current_errors.items():
                if hash_val not in new_error_hashes:
                    continue
                new_record = ErrorRecord(
                    query_id=query.id,
                    error_hash=hash_val,
                    email_sent=False,
                    notify_pending=True
                )
                new_record.set_error_data(error_data)
                db.session.add(new_record)
                new_records.append(new_record)
                result['new_errors'] += 1

            # 8. Mark resolved errors
            for hash_val in resolved_hashes:
                error = existing_errors[hash_val]
                error.resolved_at = get_utc_now()
                error.notify_pending = False
                result['resolved_errors'] += 1

            # 9. Update the existing errors still present
            for hash_val in continuing_hashes:
                error = existing_errors[hash_val]
                error.last_seen_at = get_utc_now()
                error.occurrence_count += 1

            # Result saved before any delivery: the DB does not stay locked while
            # sending, and a notification problem does not lose the errors found
            db.session.commit()

            # 10-11. Pending notifications and reminders: a problem here must not
            # make the source read count as failed
            try:
                # New errors + errors from previous checks not yet delivered to everyone,
                # once their wait before the next attempt is over
                now = get_utc_now()
                pending = new_records + [
                    existing_errors[h] for h in current_errors
                    if h in continuing_hashes and existing_errors[h].notify_pending
                    and (existing_errors[h].notify_retry_at is None or existing_errors[h].notify_retry_at <= now)
                ]
                if pending:
                    emails_sent, _ = self._notify_pending(query, pending, columns)
                    result['emails_sent'] += emails_sent

                if query.reminder_enabled:
                    reminder_emails, reminder_channels = self._process_reminders(
                        query, columns, exclude_ids={e.id for e in pending}
                    )
                    result['reminders_sent'] = reminder_emails + reminder_channels
                    result['emails_sent'] += reminder_emails
            except Exception as e:
                logger.exception(f"Error sending the notifications for {query.name}: {e}")
                db.session.rollback()
                result['error_message'] = f"Notifications not completed: {redact_secrets(str(e))}"

            # 12. Update the query statistics
            recovery = self._mark_success(query)
            query.last_check_at = get_utc_now()
            query.locked_at = None  # Release the lock
            query.total_errors_found += result['new_errors']
            query.total_emails_sent += result['emails_sent']
            if result['new_errors'] > 0:
                query.last_error_at = get_utc_now()

            db.session.commit()

            # The source was failing and the alert had been sent: say it is back
            if recovery:
                try:
                    self._send_monitor_alert(query, 'recovered', **recovery)
                except Exception as e:
                    logger.error(f"Recovery alert not sent for {query.name}: {e}")
                    db.session.rollback()

        except Exception as e:
            result['status'] = 'error'
            result['error_message'] = redact_secrets(str(e))
            logger.error(f"Error while checking {query.name}: {e}")
            db.session.rollback()

            # Release the lock and count the failure
            alert_due = False
            try:
                query.locked_at = None
                alert_due = self._mark_failure(query, result['error_message'])
                db.session.commit()
            except Exception:
                logger.exception(f"Cannot record the failure of {query.name}")
                db.session.rollback()

            # "Query failing" alert (retried at the next failure if it does not go out)
            if alert_due:
                try:
                    if self._send_monitor_alert(query, 'failed',
                                                failures=query.consecutive_failures,
                                                failing_since=query.failing_since,
                                                message=query.last_failure_message):
                        query.failure_alerted_at = get_utc_now()
                        db.session.commit()
                except Exception as alert_error:
                    logger.error(f"Failure alert not sent for {query.name}: {alert_error}")
                    db.session.rollback()

        # 13. Run log
        self._log_execution(query, result, start_time)
        
        logger.info(
            f"Query {query.name} done: "
            f"{result['rows_returned']} rows, "
            f"{result['new_errors']} new errors, "
            f"{result['resolved_errors']} resolved, "
            f"{result['reminders_sent']} reminder"
        )
        
        return result
    
    def _send_notifications(self, query: MonitoredQuery, errors: list, 
                           columns: list, email_type: str = 'new_errors') -> tuple:
        """
        Sends notifications applying conditional routing.
        
        Email: one per recipient (or one per error and recipient with
        'per_error' aggregation); with routing off, one to all recipients.
        Channels (Webhook, Telegram, Teams): a single delivery with all the errors,
        even when the query has no email recipients.
        
        Returns:
            tuple: (emails sent successfully, channels reached successfully,
                    {position of the error in 'errors': recipients who received it})
        """
        if not errors:
            return 0, 0, {}
        
        # Apply routing
        routing_result = apply_routing_rules(query, errors)
        
        if not routing_result and not query.notification_channels:
            logger.warning(f"Query {query.name}: no recipients for {len(errors)} errors")
        
        emails_sent = 0
        delivered = {}  # position of the error -> recipients who received it
        position = {id(error): i for i, error in enumerate(errors)}
        
        # Send an email for each recipient/group
        for recipients, recipient_errors in routing_result.items():
            if not recipient_errors:
                continue
            
            # Normalize recipients to a list
            if isinstance(recipients, tuple):
                recipients_list = list(recipients)
            else:
                recipients_list = [recipients]
            
            # Aggregate or send one by one
            if query.routing_aggregation == 'per_recipient' or not query.routing_enabled:
                # One email with all the errors of the recipient
                email_result = email_service.send_error_notification(
                    query=query,
                    errors=recipient_errors,
                    columns=columns,
                    recipients_override=recipients_list,
                    email_type=email_type
                )
                if email_result.get('success'):
                    emails_sent += 1
                    self._track_delivery(delivered, position, recipient_errors, recipients_list)
            else:
                # One email per error (per_error)
                for error in recipient_errors:
                    email_result = email_service.send_error_notification(
                        query=query,
                        errors=[error],
                        columns=columns,
                        recipients_override=recipients_list,
                        email_type=email_type
                    )
                    if email_result.get('success'):
                        emails_sent += 1
                        self._track_delivery(delivered, position, [error], recipients_list)
        
        # Send to the notification channels (Webhook, Telegram, Teams): only once
        channels_sent = 0
        if query.notification_channels:
            try:
                from notification_service import notification_service
                channels_result = notification_service.send_to_all_channels(query, errors)
                channels_sent = channels_result['success']
                if channels_result['total']:
                    logger.info(
                        f"Query {query.name}: notification sent to "
                        f"{channels_sent}/{channels_result['total']} channels"
                    )
            except Exception as e:
                logger.error(f"Notification channels error: {e}")
        
        sort_key = self._recipients_sort_key(query)
        notified_to = {i: sorted(dict.fromkeys(r), key=sort_key) for i, r in delivered.items()}
        return emails_sent, channels_sent, notified_to
    
    @staticmethod
    def _track_delivery(delivered: dict, position: dict, sent_errors: list, recipients: list):
        """Records which recipients received each error (by position)."""
        for error in sent_errors:
            i = position.get(id(error))
            if i is not None:
                delivered.setdefault(i, []).extend(recipients)
    
    @staticmethod
    def _recipients_sort_key(query: MonitoredQuery):
        """
        Order in which to list the recipients of an error: the configuration's
        order, with those who appear in every rule (e.g. the IT team) at the end,
        so the first addresses are the ones specific to that error.
        """
        if query.routing_enabled:
            rules = sorted((r for r in query.routing_rules if r.is_active), key=lambda r: r.priority)
            lists = [r.get_recipients_list() for r in rules]
            lists.append(query.get_default_routing_recipients())
        else:
            lists = [query.get_recipients_list()]
        lists = [[r.lower() for r in recipients] for recipients in lists if recipients]

        everywhere = set.intersection(*map(set, lists)) if len(lists) > 1 else set()
        first_seen = {}
        for recipients in lists:
            for recipient in recipients:
                first_seen.setdefault(recipient, len(first_seen))

        return lambda r: (r.lower() in everywhere, first_seen.get(r.lower(), len(first_seen)))
    
    # ------------------------------------------------------------------
    # Pending notifications: delivered and retried until they reach everyone
    # ------------------------------------------------------------------

    def _notify_pending(self, query: MonitoredQuery, records: list, columns: list) -> tuple:
        """
        Sends the notifications not yet delivered (new errors or deliveries that
        failed in previous checks), only to the recipients and channels that have
        not received them yet. An error stays pending, and is retried at the
        next check, until it has reached everyone.

        Returns:
            tuple: (emails sent successfully, successful channel deliveries)
        """
        errors = [record.get_error_data() for record in records]
        position = {id(error): i for i, error in enumerate(errors)}
        routing_result = apply_routing_rules(query, errors)

        # Intended recipients for each error, in configuration order
        intended = [[] for _ in errors]
        for recipients, recipient_errors in routing_result.items():
            recipients_list = list(recipients) if isinstance(recipients, tuple) else [recipients]
            for error in recipient_errors:
                i = position.get(id(error))
                if i is None:
                    continue
                for recipient in recipients_list:
                    if recipient not in intended[i]:
                        intended[i].append(recipient)

        # Already reached, or given up (see the end of this method)
        given_up = [record.get_undeliverable() for record in records]
        received = [{r.lower() for r in record.get_notified_to() + given_up[i]['recipients']}
                    for i, record in enumerate(records)]
        missing = [[r for r in intended[i] if r.lower() not in received[i]] for i in range(len(errors))]

        channels = [c for c in query.notification_channels if c.is_active]
        missing_channels = [
            [c.id for c in channels
             if c.id not in record.get_notified_channels() and c.id not in given_up[i]['channels']]
            for i, record in enumerate(records)
        ]

        if not any(intended) and not channels:
            logger.warning(f"Query {query.name}: no recipients for {len(errors)} errors")

        delivered = defaultdict(list)           # error -> recipients reached now
        delivered_channels = defaultdict(list)  # error -> channels reached now
        refused_channels = defaultdict(list)    # error -> channels that refused it for good (4xx)
        failures = defaultdict(list)            # error -> reasons for failed deliveries
        emails_sent = 0

        smtp_down = False
        for recipients_list, idxs in self._plan_email_jobs(query, routing_result, position, missing):
            if smtp_down:
                for i in idxs:
                    failures[i].append(f"{', '.join(recipients_list)}: SMTP server unreachable")
                continue
            email_result = email_service.send_error_notification(
                query=query,
                errors=[errors[i] for i in idxs],
                columns=columns,
                recipients_override=recipients_list,
                email_type='new_errors'
            )
            if email_result.get('success'):
                emails_sent += 1
                for i in idxs:
                    delivered[i].extend(recipients_list)
            else:
                for i in idxs:
                    failures[i].append(f"{', '.join(recipients_list)}: {email_result.get('message', 'delivery failed')}")
                # If the server does not answer, no point trying the other emails of this round
                smtp_down = email_result.get('smtp_unavailable') is True

        channels_sent = 0
        if channels:
            from notification_service import notification_service
            for channel_ids, idxs in self._plan_channel_jobs(channels, missing_channels):
                requested = channel_ids if channel_ids is not None else [c.id for c in channels]
                try:
                    channels_result = notification_service.send_to_all_channels(
                        query, [errors[i] for i in idxs], channel_ids=channel_ids
                    )
                except Exception as e:
                    logger.error(f"Notification channels error: {e}")
                    for i in idxs:
                        failures[i].append(f"channels: {redact_secrets(str(e))}")
                    continue
                channels_sent += channels_result.get('success', 0)
                if channels_result.get('total'):
                    logger.info(
                        f"Query {query.name}: notification sent to "
                        f"{channels_result.get('success', 0)}/{channels_result['total']} channels"
                    )
                reached = self._channels_delivered(channels_result, requested)
                for i in idxs:
                    delivered_channels[i].extend(cid for cid in reached if cid in missing_channels[i])
                    for r in channels_result.get('results') or []:
                        if not r.get('success'):
                            failures[i].append(f"{r.get('channel_name', 'channel')}: {r.get('message', 'delivery failed')}")
                            if r.get('permanent') and r.get('channel_id') is not None:
                                refused_channels[i].append(r['channel_id'])

        # Record what was delivered and what is still missing
        sort_key = self._recipients_sort_key(query)
        now = get_utc_now()
        retry_hours = current_app.config.get('NOTIFY_RETRY_HOURS', 24)
        for i, record in enumerate(records):
            if delivered.get(i):
                record.add_notified_to(sorted(dict.fromkeys(delivered[i]), key=sort_key), sort_key)
            if delivered_channels.get(i):
                record.add_notified_channels(delivered_channels[i])
            if (delivered.get(i) or delivered_channels.get(i)) and not record.email_sent:
                # Delivered at least once: reminder timing starts from here
                record.email_sent = True
                record.email_sent_at = now
            if refused_channels.get(i):
                # The channel answered with a definitive refusal (e.g. 404, 410): no retries
                record.add_undeliverable(channels=refused_channels[i])

            given = record.get_undeliverable()
            got = {r.lower() for r in record.get_notified_to() + given['recipients']}
            got_channels = set(record.get_notified_channels()) | set(given['channels'])
            missing_now = [r for r in intended[i] if r.lower() not in got]
            missing_channels_now = [c for c in channels if c.id not in got_channels]
            still_missing = missing_now + [c.name for c in missing_channels_now]
            reasons = '; '.join(failures.get(i) or [])

            if still_missing:
                record.notify_attempts = (record.notify_attempts or 0) + 1
                if record.first_seen_at and now - record.first_seen_at >= timedelta(hours=retry_hours):
                    # Retried long enough: given up, shown as not delivered
                    record.add_undeliverable(channels=[c.id for c in missing_channels_now], recipients=missing_now)
                    record.notify_pending = False
                    record.notify_retry_at = None
                    record.last_notify_error = (f"Not delivered after {record.notify_attempts} attempts: "
                                                f"{reasons or ', '.join(still_missing)}")[:2000]
                    logger.error(f"Query {query.name}: notification given up for {', '.join(still_missing)} "
                                 f"after {record.notify_attempts} attempts")
                else:
                    # Waits of 1, 2, 4... minutes, at most one hour (and at least the query interval)
                    record.notify_pending = True
                    record.notify_retry_at = now + timedelta(minutes=min(2 ** (record.notify_attempts - 1), 60))
                    record.last_notify_error = (reasons or f"Not yet delivered to: {', '.join(still_missing)}")[:2000]
                    logger.warning(
                        f"Query {query.name}: notification pending for {', '.join(still_missing)} "
                        f"(attempt {record.notify_attempts}), will be retried"
                    )
            else:
                record.notify_pending = False
                record.notify_retry_at = None
                if refused_channels.get(i):
                    record.last_notify_error = f"Not delivered: {reasons}"[:2000]
                elif not record.undeliverable:
                    record.last_notify_error = None

        db.session.commit()
        return emails_sent, channels_sent

    @staticmethod
    def _plan_email_jobs(query: MonitoredQuery, routing_result: dict, position: dict, missing: list) -> list:
        """
        Emails to send, as [(recipients, error positions)]: the usual aggregation
        rules, but only towards those who have not received the error yet.
        """
        jobs = []
        if not query.routing_enabled:
            # One email to all recipients; errors with the same missing recipients together
            groups = {}
            for i, recipients in enumerate(missing):
                if recipients:
                    groups.setdefault(tuple(recipients), []).append(i)
            return [(list(recipients), idxs) for recipients, idxs in groups.items()]

        for recipients, recipient_errors in routing_result.items():
            recipients_list = list(recipients) if isinstance(recipients, tuple) else [recipients]
            idxs = [position[id(e)] for e in recipient_errors if id(e) in position]
            idxs = [i for i in idxs if all(r in missing[i] for r in recipients_list)]
            if not idxs:
                continue
            if query.routing_aggregation == 'per_recipient':
                jobs.append((recipients_list, idxs))
            else:
                jobs.extend((recipients_list, [i]) for i in idxs)
        return jobs

    @staticmethod
    def _plan_channel_jobs(channels: list, missing_channels: list) -> list:
        """
        Channel deliveries as [(channel ids or None = all, error positions)]:
        a single delivery to all channels the first time, then one per missing channel.
        """
        all_ids = [c.id for c in channels]
        idxs = [i for i, ids in enumerate(missing_channels) if ids]
        if not idxs:
            return []
        if all(missing_channels[i] == all_ids for i in range(len(missing_channels))):
            return [(None, idxs)]
        jobs = []
        for channel_id in all_ids:
            channel_idxs = [i for i in idxs if channel_id in missing_channels[i]]
            if channel_idxs:
                jobs.append(([channel_id], channel_idxs))
        return jobs

    @staticmethod
    def _channels_delivered(channels_result: dict, requested_ids: list) -> list:
        """Ids of the channels reached; without per-channel detail it is all or nothing."""
        results = channels_result.get('results') or []
        if results and all('channel_id' in r for r in results):
            return [r['channel_id'] for r in results if r.get('success')]
        total = channels_result.get('total') or 0
        if total and channels_result.get('success') == total:
            return list(requested_ids)
        return []

    # ------------------------------------------------------------------
    # Queries that cannot read their source
    # ------------------------------------------------------------------

    @staticmethod
    def _lock_ttl() -> timedelta:
        """Lock duration: beyond the longest possible check (connection and query timeouts)."""
        cfg = current_app.config
        seconds = cfg.get('DB_CONNECT_TIMEOUT_SECONDS', 15) + cfg.get('DB_QUERY_TIMEOUT_SECONDS', 300) + 600
        return timedelta(seconds=max(300, seconds))

    @staticmethod
    def _failure_threshold(query: MonitoredQuery) -> int:
        """
        Failures in a row before the alert (0 = alerts off).
        After a failure the scheduler retries every minute, whatever the interval,
        so the threshold is roughly the minutes without a successful check.
        """
        configured = int(current_app.config.get('MONITOR_FAILURE_THRESHOLD', 3) or 0)
        return max(configured, 0)

    def _mark_failure(self, query: MonitoredQuery, message: str) -> bool:
        """Counts a failed run. True when it is time to send the alert."""
        query.consecutive_failures = (query.consecutive_failures or 0) + 1
        if not query.failing_since:
            query.failing_since = get_utc_now()
        query.last_failure_message = (message or '')[:2000]
        if query.failure_alerted_at:
            return False  # already alerted for this streak of failures
        threshold = self._failure_threshold(query)
        return threshold > 0 and query.consecutive_failures >= threshold

    @staticmethod
    def _mark_success(query: MonitoredQuery):
        """Resets the failure state. Returns the data for the recovery alert, if an alert was sent."""
        recovery = None
        if query.failure_alerted_at:
            recovery = {'failures': query.consecutive_failures or 0, 'failing_since': query.failing_since}
        if query.consecutive_failures or query.failing_since or query.failure_alerted_at:
            query.consecutive_failures = 0
            query.failing_since = None
            query.failure_alerted_at = None
            query.last_failure_message = None
        return recovery

    @staticmethod
    def _monitor_alert_recipients(query: MonitoredQuery) -> list:
        """ERRORENGINE_ADMIN_EMAILS if configured, otherwise the query's recipients."""
        admins = [r.strip() for r in (current_app.config.get('ADMIN_EMAILS') or '').split(',') if r.strip()]
        if admins:
            return admins
        recipients = query.get_recipients_list()
        if query.routing_enabled:
            recipients += query.get_default_routing_recipients()
        return list(dict.fromkeys(recipients))

    def _send_monitor_alert(self, query: MonitoredQuery, event: str, failures: int = 0,
                            failing_since=None, message: str = None) -> bool:
        """
        Alerts that the query cannot read its source ('failed')
        or that it works again ('recovered'). True if it reached someone.
        """
        since = format_local(failing_since) if failing_since else None
        if event == 'failed':
            subject = f"[ErrorEngine] Query failing: {query.name}"
            title = f"The query \"{query.name}\" cannot read its data"
            lines = [
                f"Failed runs in a row: {failures}" + (f", the first on {since}." if since else "."),
                f"Last error: {message or 'not available'}",
                "While the problem lasts, ErrorEngine cannot detect new errors for this query.",
            ]
            email_type, channel_event = 'monitor_failed', 'monitor_failed'
        else:
            subject = f"[ErrorEngine] Query recovered: {query.name}"
            title = f"The query \"{query.name}\" works again"
            lines = [
                f"The check works again after {failures} failed runs"
                + (f" (failing since {since})." if since else "."),
            ]
            email_type, channel_event = 'monitor_recovered', 'monitor_recovered'

        delivered = False
        recipients = self._monitor_alert_recipients(query)
        if recipients:
            email_result = email_service.send_system_notification(
                recipients, subject, title, lines, query=query, email_type=email_type)
            delivered = bool(email_result.get('success'))
        if query.notification_channels:
            from notification_service import notification_service
            channels_result = notification_service.send_system_message(query, channel_event, title, lines)
            delivered = delivered or (channels_result.get('success') or 0) > 0
        if not recipients and not query.notification_channels:
            logger.warning(f"Query {query.name}: no recipients for the '{event}' alert")

        log = logger.warning if event == 'failed' else logger.info
        log(f"Query {query.name}: '{event}' alert {'sent' if delivered else 'NOT sent'}")
        return delivered

    def _process_reminders(self, query: MonitoredQuery, columns: list, exclude_ids=()) -> tuple:
        """
        Processes the reminders for unresolved errors.

        Args:
            exclude_ids: errors just (re)notified in this check, not to be reminded

        Returns:
            tuple: (reminder emails sent, channels reached)
        """
        # Errors that need a reminder
        errors_needing_reminder = []
        error_records = []

        for error in ErrorRecord.query.filter_by(
            query_id=query.id,
            resolved_at=None,
            email_sent=True
        ).all():
            if error.id in exclude_ids:
                continue
            if error.needs_reminder(query):
                errors_needing_reminder.append(error.get_error_data())
                error_records.append(error)
        
        if not errors_needing_reminder:
            return 0, 0
        
        # Send the reminder
        emails_sent, channels_sent, notified_to = self._send_notifications(
            query, errors_needing_reminder, columns, email_type='reminder'
        )
        
        # Adds any new recipients (e.g. rules changed in the meantime)
        sort_key = self._recipients_sort_key(query)
        for i, error in enumerate(error_records):
            if i in notified_to:
                error.add_notified_to(notified_to[i], sort_key)
        
        # Update the reminder counters (even if it only went to the channels,
        # otherwise the reminder would be repeated at every check)
        if emails_sent + channels_sent > 0:
            for error in error_records:
                error.last_reminder_at = get_utc_now()
                error.reminder_count += 1
            db.session.commit()
        
        return emails_sent, channels_sent
    
    def _log_execution(self, query: MonitoredQuery, result: dict, start_time: float):
        """Records the run in the log."""
        execution_time = int((time.time() - start_time) * 1000)
        
        log_entry = QueryLog(
            query_id=query.id,
            status=result['status'],
            rows_returned=result['rows_returned'],
            new_errors=result['new_errors'],
            resolved_errors=result['resolved_errors'],
            reminders_sent=result.get('reminders_sent', 0),
            emails_sent=result['emails_sent'],
            execution_time_ms=execution_time,
            error_message=result['error_message']
        )
        db.session.add(log_entry)
        
        try:
            db.session.commit()
        except Exception as e:
            logger.error(f"Error saving the log: {e}")
            db.session.rollback()
    
    def get_query_status(self, query_id: int) -> dict:
        """Gets the current state of a query."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return {'error': 'Query not found'}
        
        # Active errors
        active_errors = ErrorRecord.query.filter_by(
            query_id=query_id,
            resolved_at=None
        ).count()
        
        # Errors with pending reminders
        pending_reminders = 0
        if query.reminder_enabled:
            for error in ErrorRecord.query.filter_by(
                query_id=query_id,
                resolved_at=None,
                email_sent=True
            ).all():
                if error.needs_reminder(query):
                    pending_reminders += 1
        
        # Last log
        last_log = QueryLog.query.filter_by(
            query_id=query_id
        ).order_by(QueryLog.executed_at.desc()).first()
        
        return {
            'query_id': query.id,
            'name': query.name,
            'source_type': query.source_type,
            'is_active': query.is_active,
            'is_in_schedule': query.is_in_schedule(),
            'last_check_at': query.last_check_at,
            'last_error_at': query.last_error_at,
            'active_errors': active_errors,
            'pending_reminders': pending_reminders,
            'total_errors_found': query.total_errors_found,
            'total_emails_sent': query.total_emails_sent,
            'routing_enabled': query.routing_enabled,
            'routing_rules_count': len(query.routing_rules),
            'last_execution': {
                'status': last_log.status if last_log else None,
                'rows_returned': last_log.rows_returned if last_log else 0,
                'execution_time_ms': last_log.execution_time_ms if last_log else 0,
                'executed_at': last_log.executed_at if last_log else None
            } if last_log else None
        }
    
    def get_active_errors(self, query_id: int = None, include_data: bool = True) -> list:
        """
        Gets all the active (unresolved) errors.
        
        Args:
            query_id: Optional, filters by a specific query
            include_data: If True, includes the full error data
        """
        errors_query = ErrorRecord.query.filter_by(resolved_at=None)
        
        if query_id:
            errors_query = errors_query.filter_by(query_id=query_id)
        
        errors = errors_query.order_by(ErrorRecord.first_seen_at.desc()).all()
        
        return [{
            'id': e.id,
            'query_id': e.query_id,
            'query_name': e.monitored_query.name,
            'error_hash': e.error_hash[:12] + '...',
            'error_data': e.get_error_data() if include_data else None,
            'first_seen_at': e.first_seen_at,
            'last_seen_at': e.last_seen_at,
            'occurrence_count': e.occurrence_count,
            'email_sent': e.email_sent,
            'email_sent_at': e.email_sent_at,
            'reminder_count': e.reminder_count,
            'last_reminder_at': e.last_reminder_at
        } for e in errors]
    
    def test_query_connection(self, query_id: int) -> dict:
        """Tests the connection/query of a query."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return {'success': False, 'message': 'Query not found'}
        
        return test_query_source(query)
    
    def get_query_available_fields(self, query_id: int) -> list:
        """Gets the fields available for a query (for routing configuration)."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return []
        
        return get_query_fields(query)


# Singleton
monitor_service = MonitorService()
