"""
Main monitoring service.
Runs the queries periodically, with conditional routing and reminders.
"""
import logging
from datetime import timedelta
from flask import current_app
from sqlalchemy.exc import IntegrityError
from utils import get_utc_now, format_local, redact_secrets
import time
from collections import defaultdict
from models import db, MonitoredQuery, ErrorRecord, QueryLog
from data_sources import execute_query_source
from routing_service import apply_routing_rules
from email_service import email_service

logger = logging.getLogger(__name__)

# Minutes before trying a notification again, after the first failed attempt, the second...
# (the last one again beyond these)
RETRY_WAITS = (1, 5, 15, 60)
# Longest wait, in minutes, before reading again a source that keeps failing (see _failure_retry_at)
FAILURE_MAX_WAIT = 60


class MonitorService:
    """
    Orchestrates error monitoring.
    Supports multiple sources, conditional routing and reminders.
    """
    
    def __init__(self, app=None):
        self.app = app
        
    def init_app(self, app):
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

        force=True ignores the time window. Returns the run statistics.
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
            # Time window
            if not force and not query.is_in_schedule():
                # Skips are not logged: they would only fill the logs
                result['status'] = 'skipped'
                result['error_message'] = 'Outside the configured time window'
                return result

            # A disabled connection stops its queries: skipped, not a failure of the source
            if query.connection_disabled():
                result['status'] = 'skipped'
                result['error_message'] = f'The database connection "{query.db_connection.name}" is disabled'
                return result

            try:
                locked = self._acquire_lock(query)
            except Exception as e:
                # ErrorEngine's own database, not the source: no failure of the source, and the lock may be another's
                db.session.rollback()
                result['status'] = 'skipped'
                result['error_message'] = f'Not checked: the lock could not be taken ({redact_secrets(str(e))})'
                logger.error(f"{query.name}: {result['error_message']}")
                return result
            if not locked:
                result['status'] = 'skipped'
                result['error_message'] = 'Query already running in another process'
                return result

            # Read the source
            logger.info(f"Running query: {query.name} (source: {query.source_type})")
            columns, rows = execute_query_source(query)
            result['rows_returned'] = len(rows)

            key_fields = query.get_key_fields_list()
            self._check_key_fields(key_fields, columns, rows)

            # Hash of each row: the identity of the error
            current_errors = {}
            for row in rows:
                error_hash = ErrorRecord.calculate_hash(row, key_fields)
                current_errors[error_hash] = row
            
            # Unresolved errors already known
            existing_errors = {
                e.error_hash: e 
                for e in ErrorRecord.query.filter_by(
                    query_id=query.id, 
                    resolved_at=None
                ).all()
            }
            
            # New, missing (resolved, or waiting to be) and continuing errors
            new_error_hashes = set(current_errors.keys()) - set(existing_errors.keys())
            missing_hashes = set(existing_errors.keys()) - set(current_errors.keys())
            continuing_hashes = set(current_errors.keys()) & set(existing_errors.keys())
            
            # New errors (in row order), to notify
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

            # Errors no longer returned: resolved after resolve_after_checks checks in a row without them
            needed = query.resolve_after_checks or 1
            for hash_val in missing_hashes:
                error = existing_errors[hash_val]
                error.missing_checks = (error.missing_checks or 0) + 1
                if error.missing_checks < needed:
                    continue
                error.resolved_at = get_utc_now()
                error.notify_pending = False
                result['resolved_errors'] += 1

            # Errors still present: their data follow the source, the fields outside the key may have changed
            for hash_val in continuing_hashes:
                error = existing_errors[hash_val]
                error.last_seen_at = get_utc_now()
                error.occurrence_count += 1
                error.missing_checks = 0
                error.set_error_data(current_errors[hash_val])

            # Saved before any delivery: the DB is not locked while sending, and a
            # delivery problem does not lose the errors found
            try:
                db.session.commit()
            except IntegrityError:
                # One active error per identity (migration 1): another check of this query, running at the same
                # time after taking over the lock, saved the same errors first
                db.session.rollback()
                raise RuntimeError('Another check of this query saved the same errors at the same time: this one '
                                   'stops, and the next one goes on from there') from None

            # Notifications and reminders: a problem here is not a failed source read
            try:
                # New errors + errors from previous checks not yet delivered to everyone,
                # once their wait before the next attempt is over
                now = get_utc_now()
                pending = new_records + [
                    existing_errors[h] for h in current_errors
                    if h in continuing_hashes and existing_errors[h].notify_pending
                    and (existing_errors[h].notify_retry_at is None or existing_errors[h].notify_retry_at <= now)
                ]
                with email_service.batch():
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

            # Query statistics
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
                           columns: list, email_type: str = 'new_errors', error_ids: list = None) -> tuple:
        """
        Sends notifications applying conditional routing.
        
        Email: one per recipient (or one per error and recipient with
        'per_error' aggregation); with routing off, one to all recipients.
        Channels (Webhook, Telegram, Teams): a single delivery with all the errors,
        even when the query has no email recipients. error_ids: the ids of the errors, in order.

        Returns (emails sent, channels reached, {position of the error: recipients reached}).
        """
        if not errors:
            return 0, 0, {}
        
        routing_result = apply_routing_rules(query, errors)
        
        if not routing_result and not query.notification_channels:
            logger.warning(f"Query {query.name}: no recipients for {len(errors)} errors")
        
        emails_sent = 0
        delivered = {}  # position of the error -> recipients who received it
        position = {id(error): i for i, error in enumerate(errors)}
        
        # Send an email for each recipient/group (none when email is off: channels only)
        for recipients, recipient_errors in (routing_result.items() if email_service.is_enabled() else ()):
            if not recipient_errors:
                continue
            
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
                    self._track_delivery(delivered, position, recipient_errors,
                                         email_result.get('recipients', recipients_list))
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
                        self._track_delivery(delivered, position, [error],
                                             email_result.get('recipients', recipients_list))
        
        # Send to the notification channels (Webhook, Telegram, Teams): only once
        channels_sent = 0
        if query.notification_channels:
            try:
                from notification_service import notification_service
                channels_result = notification_service.send_to_all_channels(
                    query, errors, reminder=email_type == 'reminder', error_ids=error_ids
                )
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
    def _check_key_fields(key_fields: list, columns: list, rows: list):
        """
        Key fields missing from the results, or none at all, would give every row the same
        identity and merge them into one error: a configuration error, reported as a failed run.
        """
        if not key_fields:
            raise ValueError("No key fields: name the columns that tell one error from another")
        if not rows:
            return
        available = {name.upper() for name in columns}
        for row in rows:
            available.update(name.upper() for name in row)
        missing = [field for field in key_fields if field.upper() not in available]
        if missing:
            raise ValueError(f"Key fields not in the results: {', '.join(missing)} "
                             f"(columns: {', '.join(columns)})")

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
    
    # --- Pending notifications: retried until delivered, or given up ---

    def _notify_pending(self, query: MonitoredQuery, records: list, columns: list) -> tuple:
        """
        Sends the notifications not yet delivered (new errors or deliveries that
        failed earlier), only to the recipients and channels still missing. Failed
        ones are tried again after the RETRY_WAITS, NOTIFY_ATTEMPTS times in all, then given up;
        not while the only failures are an email service or a channel that does not answer.
        Returns (emails sent, channel deliveries).
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

        email_off = not email_service.is_enabled()
        if email_off:
            # No way to send emails: only the channels count, nothing waits for an email
            intended = [[] for _ in errors]

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

        delivered = defaultdict(list)           # error -> recipients reached now
        delivered_channels = defaultdict(list)  # error -> channels reached now
        refused_channels = defaultdict(list)    # error -> channels that refused it for good (4xx)
        failures = defaultdict(list)            # error -> reasons for failed deliveries
        # Errors with a failure that is not a service or channel that did not answer (an address refused, an email
        # the service would not take): only these use up their attempts (see the end of this method)
        refusals = set()
        emails_sent = 0

        email_down = False
        for recipients_list, idxs in self._plan_email_jobs(query, routing_result, position, missing):
            if email_down:
                for i in idxs:
                    failures[i].append(f"{', '.join(recipients_list)}: email service unreachable")
                continue
            email_result = email_service.send_error_notification(
                query=query,
                errors=[errors[i] for i in idxs],
                columns=columns,
                recipients_override=recipients_list,
                email_type='new_errors',
                quiet=True
            )
            refused = email_result.get('refused') or {}
            for i in idxs:
                if email_result.get('success'):
                    delivered[i].extend(email_result.get('recipients', recipients_list))
                elif not refused:
                    failures[i].append(f"{', '.join(recipients_list)}: "
                                       f"{email_result.get('message', 'delivery failed')}")
                    if email_result.get('email_unavailable') is not True:
                        refusals.add(i)
                # Whether the others got it or not, the server's reason for each address it refused
                failures[i].extend(f"{address}: {reason}" for address, reason in refused.items())
                if refused:
                    refusals.add(i)
            if email_result.get('success'):
                emails_sent += 1
            else:
                # If the service does not answer, no point trying the other emails of this round
                email_down = email_result.get('email_unavailable') is True

        channels_sent = 0
        if channels:
            from notification_service import notification_service
            for channel_ids, idxs in self._plan_channel_jobs(channels, missing_channels):
                requested = channel_ids if channel_ids is not None else [c.id for c in channels]
                try:
                    channels_result = notification_service.send_to_all_channels(
                        query, [errors[i] for i in idxs], channel_ids=channel_ids,
                        error_ids=[records[i].id for i in idxs]
                    )
                except Exception as e:
                    logger.error(f"Notification channels error: {e}")
                    for i in idxs:
                        failures[i].append(f"channels: {redact_secrets(str(e))}")
                        refusals.add(i)
                    continue
                channels_sent += channels_result.get('success', 0)
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
        max_attempts = max(1, current_app.config.get('NOTIFY_ATTEMPTS', 5))
        attempts = [(record.notify_attempts or 0) + 1 for record in records]
        pending, given_up = {}, {}  # error -> recipients and channels still missing
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
                # A service that did not answer has not refused the notification: if nothing else went wrong it
                # waits, one attempt an hour, instead of being given up for good (with 1 attempt, no retries)
                waits_for_the_service = max_attempts > 1 and i not in refusals
                if record.notify_attempts >= max_attempts and not waits_for_the_service:
                    # Tried enough: given up, shown as not delivered
                    record.add_undeliverable(channels=[c.id for c in missing_channels_now], recipients=missing_now)
                    record.notify_pending = False
                    record.notify_retry_at = None
                    record.last_notify_error = (f"Not delivered after {record.notify_attempts} attempts: "
                                                f"{reasons or ', '.join(still_missing)}")[:2000]
                    given_up[i] = still_missing
                else:
                    # The scheduler sends it again when the wait is over, not at the query's next check
                    wait = RETRY_WAITS[min(record.notify_attempts, len(RETRY_WAITS)) - 1]
                    record.notify_pending = True
                    record.notify_retry_at = now + timedelta(minutes=wait)
                    record.last_notify_error = (reasons or f"Not yet delivered to: {', '.join(still_missing)}")[:2000]
                    pending[i] = still_missing
            else:
                record.notify_pending = False
                record.notify_retry_at = None
                if refused_channels.get(i):
                    record.last_notify_error = f"Not delivered: {reasons}"[:2000]
                elif not record.undeliverable:
                    record.last_notify_error = None

        db.session.commit()

        names = {c.id: c.name for c in channels}
        self._log_attempt(
            query, records, attempts,
            reached=[r for i in delivered for r in delivered[i]]
            + [names[c] for i in delivered_channels for c in delivered_channels[i]],
            pending=pending, given_up=given_up,
            refused=[names.get(c, str(c)) for i in refused_channels for c in refused_channels[i]],
            reasons=[reason for i in sorted(failures) for reason in failures[i]],
            nobody=not any(intended) and not channels, email_off=email_off,
        )
        return emails_sent, channels_sent

    @staticmethod
    def _log_attempt(query: MonitoredQuery, records: list, attempts: list, reached: list, pending: dict,
                     given_up: dict, refused: list, reasons: list, nobody: bool, email_off: bool):
        """
        One line for a delivery attempt, whatever the number of errors: what went out, what is
        still pending and when it is tried again, what was given up, and why.
        """
        def names(items, limit=10, sep=', '):
            items = list(dict.fromkeys(items))
            more = f" and {len(items) - limit} more" if len(items) > limit else ''
            return sep.join(str(item) for item in items[:limit]) + more

        low, high = min(attempts), max(attempts)
        parts = []
        if nobody:
            parts.append("no recipients" + (" (email is off)" if email_off else ""))
        if reached:
            parts.append(f"delivered to {names(reached)}")
        if pending:
            waits = [records[i].notify_retry_at - get_utc_now() for i in pending]
            minutes = max(1, round(min(waits).total_seconds() / 60))
            parts.append(f"{len(pending)} pending for {names(r for missing in pending.values() for r in missing)}, "
                         f"next attempt in {minutes} min")
        if given_up:
            parts.append(f"{len(given_up)} given up for {names(r for missing in given_up.values() for r in missing)} "
                         f"after {max(records[i].notify_attempts for i in given_up)} attempts")
        if refused:
            parts.append(f"refused for good by {names(refused)}")
        if reasons:
            # Reasons hold lists of recipients themselves: another separator
            parts.append(f"why: {names((reason[:200] for reason in reasons), limit=5, sep=' / ')}")

        level = logging.ERROR if given_up or refused else logging.WARNING if pending or nobody else logging.INFO
        errors = f"{len(records)} error{'s' if len(records) != 1 else ''}"
        attempt = f"attempt {high}" if low == high else f"attempts {low}-{high}"
        logger.log(level, f"Query {query.name}: notifications for {errors}, {attempt}: {'; '.join(parts)}")

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

    # --- Queries that cannot read their source ---

    @staticmethod
    def _lock_ttl() -> timedelta:
        """Lock duration: beyond the longest possible check (connection and query timeouts)."""
        cfg = current_app.config
        seconds = cfg.get('DB_CONNECT_TIMEOUT_SECONDS', 15) + cfg.get('DB_QUERY_TIMEOUT_SECONDS', 300) + 600
        return timedelta(seconds=max(300, seconds))

    def _acquire_lock(self, query: MonitoredQuery) -> bool:
        """
        One check or delivery at a time per query, also across processes (the conditional
        UPDATE is atomic). A lock older than the longest possible check is taken over.
        """
        now = get_utc_now()
        updated = (
            db.session.query(MonitoredQuery)
            .filter(MonitoredQuery.id == query.id,
                    db.or_(MonitoredQuery.locked_at.is_(None),
                           MonitoredQuery.locked_at < now - self._lock_ttl()))
            .update({MonitoredQuery.locked_at: now}, synchronize_session=False)
        )
        db.session.commit()
        return updated > 0

    def deliver_pending(self, query: MonitoredQuery) -> dict:
        """
        Sends again the notifications whose wait is over, without reading the source.
        The scheduler calls it every minute: a failed notification of a daily query
        does not wait for the next day. Returns {'status', 'emails_sent'}.
        """
        result = {'status': 'success', 'emails_sent': 0}
        if not self._acquire_lock(query):
            result['status'] = 'skipped'
            return result
        try:
            now = get_utc_now()
            records = (
                ErrorRecord.query
                .filter(ErrorRecord.query_id == query.id,
                        ErrorRecord.resolved_at.is_(None),
                        ErrorRecord.notify_pending.is_(True),
                        db.or_(ErrorRecord.notify_retry_at.is_(None), ErrorRecord.notify_retry_at <= now))
                .order_by(ErrorRecord.id)
                .all()
            )
            if records:
                # The columns of the rows as they were saved, in the query's order
                columns = list(dict.fromkeys(key for record in records for key in record.get_error_data()))
                with email_service.batch():
                    result['emails_sent'], _ = self._notify_pending(query, records, columns)
                query.total_emails_sent = (query.total_emails_sent or 0) + result['emails_sent']
        except Exception as e:
            logger.exception(f"Pending notifications of {query.name} not sent: {e}")
            db.session.rollback()
            result['status'] = 'error'
        finally:
            query.locked_at = None
            db.session.commit()
        return result

    @staticmethod
    def _failure_threshold(query: MonitoredQuery) -> int:
        """
        Failures in a row before the alert (0 = alerts off).
        The first retries come at the next round and after 1 and 2 minutes (_failure_retry_at),
        so with 3 the alert comes about 2 minutes after the first failure, whatever the interval.
        """
        configured = int(current_app.config.get('MONITOR_FAILURE_THRESHOLD', 3) or 0)
        return max(configured, 0)

    @staticmethod
    def _failure_retry_at(query: MonitoredQuery):
        """
        When to read a failing source again: at the next round after the first failure, then after 1, 2, 4,
        8... minutes, never less often than the query's interval and at least once every FAILURE_MAX_WAIT
        minutes. None: at the next round.
        """
        failures = query.consecutive_failures or 0
        if failures < 2:
            return None
        wait = min(2 ** (failures - 2), query.check_interval_minutes or FAILURE_MAX_WAIT, FAILURE_MAX_WAIT)
        # From the minute of the failure: the scheduler works in whole minutes
        return get_utc_now().replace(second=0, microsecond=0) + timedelta(minutes=wait)

    def _mark_failure(self, query: MonitoredQuery, message: str) -> bool:
        """Counts a failed run and sets the wait before the next one. True when it is time to send the alert."""
        query.consecutive_failures = (query.consecutive_failures or 0) + 1
        if not query.failing_since:
            query.failing_since = get_utc_now()
        query.last_failure_message = (message or '')[:2000]
        query.failure_retry_at = self._failure_retry_at(query)
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
        if query.consecutive_failures or query.failing_since or query.failure_alerted_at or query.failure_retry_at:
            query.consecutive_failures = 0
            query.failing_since = None
            query.failure_alerted_at = None
            query.last_failure_message = None
            query.failure_retry_at = None
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
        recipients = self._monitor_alert_recipients(query) if email_service.is_enabled() else []
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
        Reminders for unresolved errors, except those just (re)notified (exclude_ids).
        Returns (reminder emails sent, channels reached).
        """
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
        
        emails_sent, channels_sent, notified_to = self._send_notifications(
            query, errors_needing_reminder, columns, email_type='reminder',
            error_ids=[error.id for error in error_records]
        )
        
        # Adds any new recipients (e.g. rules changed in the meantime)
        sort_key = self._recipients_sort_key(query)
        for i, error in enumerate(error_records):
            if i in notified_to:
                error.add_notified_to(notified_to[i], sort_key)
        
        # Counted even if it only reached the channels: otherwise it would repeat at every check
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
        query = db.session.get(MonitoredQuery, query_id)
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
        Active (unresolved) errors, of one query if query_id is given;
        include_data=False leaves out the rows.
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
    
    def get_query_available_fields(self, query_id: int) -> list:
        """
        Fields for the routing conditions, from the last error found. Opening the form must
        not run the query: it can be slow and it reads production data (Test query in the
        form suggests the fields too).
        """
        last = ErrorRecord.query.filter_by(query_id=query_id).order_by(ErrorRecord.id.desc()).first()
        if last is None:
            return []
        return [{'name': name,
                 'type': 'number' if isinstance(value, (int, float)) and not isinstance(value, bool) else 'text',
                 'sample': str(value)[:100] if value is not None else None}
                for name, value in last.get_error_data().items()]


monitor_service = MonitorService()
