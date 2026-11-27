"""
Models of the internal database (SQLite by default).
"""
from datetime import datetime, time, timedelta
from utils import get_utc_now
from encryption import encrypt, decrypt, encrypt_fields, decrypt_fields, unreadable, UnreadableSecret
from flask_sqlalchemy import SQLAlchemy
import hashlib
import json

db = SQLAlchemy()

# The settings the forms show, saved in clear: every other text of the settings is encrypted (encryption.py),
# so that a field nobody listed is not left in clear. Those of an HTTP source:
SOURCE_CLEAR = ('url', 'method', 'auth_type', 'api_key_name', 'api_key_in', 'response_path')
# Those of a channel:
CHANNEL_CLEAR = ('method', 'chat_id')
# The fields of each type of channel that let anyone send to it: never sent back to the browser by its form
CHANNEL_SECRETS = {'webhook': ('url', 'headers'), 'telegram': ('bot_token',), 'teams': ('webhook_url',)}


class MonitoredQuery(db.Model):
    """
    Definition of the queries to monitor.
    Supports multiple sources and conditional routing.
    """
    __tablename__ = 'monitored_queries'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    
    # === DATA SOURCE ===
    source_type = db.Column(db.String(20), default='database')  # database, http
    
    # Database connection (for SQL sources)
    db_connection_id = db.Column(db.Integer, db.ForeignKey('database_connections.id'), nullable=True)
    
    sql_query = db.Column(db.Text)
    
    # HTTP sources, JSON: url, method, headers, auth_type, auth_token, response_path
    source_config = db.Column(db.Text)
    
    # Fields that uniquely identify an error (comma-separated)
    key_fields = db.Column(db.String(500), nullable=False)
    # Checks in a row without an error before it is resolved: more than 1 for data that comes and goes
    # (0, as an upgraded database gets it, is the same as 1)
    resolve_after_checks = db.Column(db.Integer, default=1)
    
    # === EMAIL ===
    email_subject = db.Column(db.String(200), default='[ErrorEngine] New errors: {query_name}')
    email_recipients = db.Column(db.Text)  # Comma-separated emails
    email_template = db.Column(db.Text)  # empty: default; {name}: templates/email/name.html; or HTML
    
    # === SCHEDULE ===
    check_interval_minutes = db.Column(db.Integer, default=15)
    is_active = db.Column(db.Boolean, default=True)
    
    # Time window
    schedule_start_time = db.Column(db.Time)  # e.g. 08:00
    schedule_end_time = db.Column(db.Time)    # e.g. 20:00; before the start: the window crosses midnight
    schedule_days = db.Column(db.String(20))  # ISO weekdays, e.g. "1,2,3,4,5" (Mon-Fri); empty: every day
    schedule_reference_time = db.Column(db.Time)  # Reference time for the runs (e.g. 00:00)
    
    # === REMINDER ===
    reminder_enabled = db.Column(db.Boolean, default=False)
    reminder_interval_minutes = db.Column(db.Integer, default=60)
    reminder_max_count = db.Column(db.Integer, default=5)  # Max reminders per error
    
    # === CONDITIONAL ROUTING ===
    routing_enabled = db.Column(db.Boolean, default=False)
    routing_default_recipients = db.Column(db.Text)  # Fallback when no rule matches
    routing_aggregation = db.Column(db.String(20), default='per_recipient')
    # 'per_recipient' = one email per recipient with all of their errors
    # 'per_error' = one email per error
    routing_no_match_action = db.Column(db.String(20), default='send_default')
    # 'send_default' = send to default_recipients
    # 'skip' = do not send
    
    # === TIMESTAMPS ===
    created_at = db.Column(db.DateTime, default=get_utc_now)
    updated_at = db.Column(db.DateTime, default=get_utc_now, onupdate=get_utc_now)
    last_check_at = db.Column(db.DateTime)
    locked_at = db.Column(db.DateTime, nullable=True)
    last_error_at = db.Column(db.DateTime)
    
    # === STATISTICS ===
    total_errors_found = db.Column(db.Integer, default=0)
    total_emails_sent = db.Column(db.Integer, default=0)

    # === SOURCE STATE ===
    # Failed runs in a row (source unreachable, SQL or API failing)
    consecutive_failures = db.Column(db.Integer, default=0)
    failing_since = db.Column(db.DateTime)        # first failure of the streak
    failure_alerted_at = db.Column(db.DateTime)   # "query failing" alert sent
    last_failure_message = db.Column(db.Text)
    # After failed runs, the next one not before this time: the waits grow while the source keeps failing
    failure_retry_at = db.Column(db.DateTime)

    # === RELATIONSHIPS ===
    errors = db.relationship('ErrorRecord', backref='monitored_query', lazy='dynamic', 
                            cascade='all, delete-orphan')
    logs = db.relationship('QueryLog', backref='monitored_query', lazy='dynamic',
                          cascade='all, delete-orphan')
    routing_rules = db.relationship('RoutingRule', backref='monitored_query',
                                   cascade='all, delete-orphan',
                                   order_by='RoutingRule.priority')
    
    # === NOTIFICATIONS ===
    notification_channels = db.relationship('NotificationChannel', 
                                           secondary='query_notification_channels',
                                           backref='queries')
    
    # === TAGS ===
    tags = db.Column(db.String(500), default='')  # Comma-separated tags

    def get_recipients_list(self):
        if not self.email_recipients:
            return []
        return [r.strip() for r in self.email_recipients.split(',') if r.strip()]
    
    def get_default_routing_recipients(self):
        if not self.routing_default_recipients:
            return []
        return [r.strip() for r in self.routing_default_recipients.split(',') if r.strip()]
    
    def get_key_fields_list(self):
        return [f.strip() for f in self.key_fields.split(',') if f.strip()]
    
    def get_schedule_days_list(self):
        """Returns the list of active days (ISO weekday: 1=Mon, 7=Sun)"""
        if not self.schedule_days:
            return [1, 2, 3, 4, 5, 6, 7]  # Every day if not specified
        # Days saved before the form checked them may be anything: those are left out
        days = (d.strip() for d in self.schedule_days.split(','))
        return [int(d) for d in days if d in ('1', '2', '3', '4', '5', '6', '7')]
    
    def stored_source_config(self) -> dict:
        """The source settings as saved, secrets encrypted: what the form shows, without decrypting them."""
        return json.loads(self.source_config) if self.source_config else {}

    def get_source_config(self):
        """The source settings, secrets in clear."""
        try:
            return decrypt_fields(self.stored_source_config(), SOURCE_CLEAR)
        except UnreadableSecret:
            raise unreadable('A token or header', f'the query {self.name}') from None

    def get_tags_list(self):
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def set_source_config(self, config):
        """Sets the source settings, secrets encrypted (one already encrypted stays as it is)."""
        self.source_config = json.dumps(encrypt_fields(config, SOURCE_CLEAR), ensure_ascii=False)

    def connection_disabled(self) -> bool:
        """A database query whose connection is disabled: it does not run until the connection is enabled."""
        return (self.source_type != 'http' and self.db_connection is not None
                and not self.db_connection.is_active)

    def is_failing(self) -> bool:
        """It cannot read its source. Not while its connection is disabled: then it does not run."""
        return bool(self.is_active and self.consecutive_failures and not self.connection_disabled())
    
    def _get_local_now(self):
        from utils import get_local_now
        return get_local_now()

    def is_in_schedule(self, now=None):
        """Checks whether the query can run right now (local time)."""
        if now is None:
            now = self._get_local_now()
        return self._window_contains(now)

    def _window_contains(self, moment) -> bool:
        """
        Whether a local time falls inside the active days and the time window.

        - Times are compared to the minute: the check of 18:00:20 is still the 18:00 slot.
        - A window that ends before it starts (e.g. 22:00-06:00) crosses midnight; the hours
          after midnight belong to the day the window opened, so a Friday night ends on Saturday.
        """
        current = moment.time().replace(second=0, microsecond=0)
        start, end = self.schedule_start_time, self.schedule_end_time
        day = moment.date()

        if start and end and start > end:
            if current >= start:
                window_day = day
            elif current <= end:
                window_day = day - timedelta(days=1)
            else:
                return False
        else:
            if start and current < start:
                return False
            if end and current > end:
                return False
            window_day = day

        return window_day.isoweekday() in self.get_schedule_days_list()

    def get_next_scheduled_time(self, now=None):
        """
        The current slot and the next one, from reference_time + interval, as local times.

        - Up to an hour the slots are counted in real time from the reference time of the day: the night the
          clock goes back keeps the rhythm through the hour that comes twice, instead of stopping for an hour,
          and the night it goes forward has no slot in the hour that does not exist.
        - Longer intervals keep the local times of the day (a daily query at 08:00 stays at 08:00): in the hour
          that comes twice they run the first time round, and a time the clock skips (02:30, when it jumps from
          02:00 to 03:00) comes an hour later.
        - The next slot is the first one after now, also when the next day begins before the last slot plus the
          interval (every 7 hours, or a day of 23 or 25 hours).

        In the hour that comes twice the local times carry fold, Python's mark for the second time round, which
        local_to_utc reads: compare them in UTC.
        """
        from utils import local_to_utc, utc_to_local
        if now is None:
            now = self._get_local_now()
        
        # Without a reference_time, use midnight
        ref_time = self.schedule_reference_time or time(0, 0)
        interval = timedelta(minutes=self.check_interval_minutes)
        now_utc = local_to_utc(now)

        def first_slot(day):
            """The reference time of a day, in UTC."""
            return local_to_utc(datetime.combine(day, ref_time))

        if interval <= timedelta(hours=1):
            # From the reference time of today, or of yesterday before it
            day = now.date() if now_utc >= first_slot(now.date()) else now.date() - timedelta(days=1)
            start = first_slot(day)
            current_slot = start + (now_utc - start) // interval * interval
            next_slot = min(current_slot + interval, first_slot(day + timedelta(days=1)))
        else:
            # The local times of yesterday, today and tomorrow (at most 24 a day), in UTC
            slots = sorted({local_to_utc(datetime.combine(day, ref_time) + timedelta(minutes=minutes))
                            for day in (now.date() + timedelta(days=offset) for offset in (-1, 0, 1))
                            for minutes in range(0, 24 * 60, self.check_interval_minutes)})
            current_slot = max(slot for slot in slots if slot <= now_utc)
            next_slot = min(slot for slot in slots if slot > now_utc)
        
        return utc_to_local(current_slot), utc_to_local(next_slot)

    def get_next_run_time(self, now=None):
        """Computes when the query will run next (taking the time window into account)."""
        if now is None:
            now = self._get_local_now()
        
        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Which slot is the next one to run: compared in UTC, as in the hour that comes twice the local times
        # do not tell which came first
        from utils import local_to_utc
        if self.last_check_at is not None:
            if self.last_check_at >= local_to_utc(current_slot):
                target_slot = next_slot
            else:
                target_slot = current_slot
        else:
            target_slot = current_slot if local_to_utc(current_slot) >= local_to_utc(now) else next_slot
        
        # A source that keeps failing is tried again when its wait is over (should_run_now)
        if self.failure_retry_at is not None and self.failure_retry_at > local_to_utc(target_slot):
            target_slot = self._utc_to_local(self.failure_retry_at)

        # Move forward until the slot falls inside the active days and the time window
        for _ in range(1000):  # at most 1000 iterations, for safety
            if self._window_contains(target_slot):
                return target_slot
            start = self.schedule_start_time
            if start and target_slot.time() < start:
                # The window opens later today (overnight windows too: e.g. at 12:00 for 22:00-06:00)
                target_slot = datetime.combine(target_slot.date(), start)
            elif start:
                target_slot = datetime.combine(target_slot.date() + timedelta(days=1), start)
            else:
                target_slot = self._next_day_start(target_slot)

        return None  # Should never get here

    def _next_day_start(self, dt):
        """Returns the start of the next day at reference_time."""
        ref_time = self.schedule_reference_time or time(0, 0)
        next_day = dt.date() + timedelta(days=1)
        return datetime.combine(next_day, ref_time)

    def _utc_to_local(self, utc_dt):
        from utils import utc_to_local
        return utc_to_local(utc_dt)

    def should_run_now(self, now=None):
        """Checks whether the query must run now."""
        if now is None:
            now = self._get_local_now()
        
        if not self.is_in_schedule(now):
            return False, "Outside schedule window"
        
        # After an inactive day the first run is at reference_time, not at 00:00
        # (a slot left over from the inactive day would precede "now")
        ref_time = self.schedule_reference_time or time(0, 0)
        prev_day = now.date() - timedelta(days=1)
        prev_day_inactive = prev_day.isoweekday() not in self.get_schedule_days_list()
        if prev_day_inactive and now.time() < ref_time:
            return False, f"Waiting for first run at {ref_time.strftime('%H:%M')}"
        
        # After failed runs the next one waits (MonitorService._mark_failure): no thread for a failing source
        # at every round. Compared in UTC, as in the hour that comes twice local times do not tell which came first
        from utils import local_to_utc
        if self.failure_retry_at is not None and local_to_utc(now) < self.failure_retry_at:
            retry_at = self._utc_to_local(self.failure_retry_at)
            return False, f"Source failing: next attempt at {retry_at.strftime('%H:%M')}"

        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Never run yet: run if we are in the current slot
        if self.last_check_at is None:
            return True, "First run"
        
        # If the last run is before the current slot, run now
        if self.last_check_at < local_to_utc(current_slot):
            return True, "Current slot not run yet"
        
        return False, f"Next run: {next_slot.strftime('%H:%M')}"

    def __repr__(self):
        return f'<MonitoredQuery {self.name}>'


class DatabaseConnection(db.Model):
    """Connection to an external database."""
    __tablename__ = 'database_connections'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    db_type = db.Column(db.String(20), nullable=False)
    host = db.Column(db.String(255))
    port = db.Column(db.Integer)
    database = db.Column(db.String(255))  # database name, or service_name for Oracle
    username = db.Column(db.String(100))
    # Encrypted (the password property): the column keeps its name
    _password = db.Column('password', db.Text)
    is_active = db.Column(db.Boolean, default=True)
    # Statements that change data (INSERT, UPDATE, DELETE, procedures): only where allowed
    allow_writes = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    queries = db.relationship('MonitoredQuery', backref='db_connection', lazy='dynamic')

    @property
    def password(self):
        """The password in clear."""
        try:
            return decrypt(self._password)
        except UnreadableSecret:
            raise unreadable('The password', f'the connection {self.name}') from None

    @password.setter
    def password(self, value):
        self._password = encrypt(value)

    def get_driver(self):
        """Returns the driver for this DB type, with the configured timeouts and row limit."""
        from db_drivers import get_driver, driver_timeouts, row_limit
        return get_driver(self.db_type, max_rows=row_limit(), **driver_timeouts())

    def test_connection(self) -> dict:
        try:
            password = self.password
        except UnreadableSecret as e:
            return {'status': 'error', 'message': str(e)}
        return self.get_driver().test_connection(self.host, self.port, self.database, self.username, password)

    def write_refusal(self, sql: str) -> str:
        """Why this connection does not run the statement, one that can change data; empty when it does."""
        # Here and not at the top: validators imports routing_service, which imports this module
        from validators import write_statement_keyword
        keyword = write_statement_keyword(sql, self.db_type)
        if keyword and not self.allow_writes:
            return (f"The query contains {keyword}, which can change data, and the connection {self.name} does not "
                    "allow it: change the query, or allow statements that change data in the connection")
        return ''

    def execute_query(self, sql: str) -> tuple:
        refusal = self.write_refusal(sql)
        if refusal:
            raise ValueError(refusal)
        driver = self.get_driver()
        conn = driver.open(
            self.host, self.port, self.database, self.username, self.password
        )
        try:
            return driver.run(conn, sql, bool(self.allow_writes))
        finally:
            # driver.close: not every connection has close() (e.g. ibm_db)
            driver.close(conn)

    def __repr__(self):
            return f'<DatabaseConnection {self.name} ({self.db_type})>'


# Association table for query-notification channels (many-to-many)
query_notification_channels = db.Table('query_notification_channels',
    db.Column('query_id', db.Integer, db.ForeignKey('monitored_queries.id'), primary_key=True),
    db.Column('channel_id', db.Integer, db.ForeignKey('notification_channels.id'), primary_key=True)
)


class NotificationChannel(db.Model):
    """
    Configurable notification channel (Webhook, Telegram, Teams).
    """
    __tablename__ = 'notification_channels'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    channel_type = db.Column(db.String(20), nullable=False)  # webhook, telegram, teams
    
    # Type-specific JSON configuration
    config = db.Column(db.Text, nullable=False, default='{}')
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    # Statistics
    total_sent = db.Column(db.Integer, default=0)
    last_sent_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    
    def stored_config(self) -> dict:
        """The settings as saved, secrets encrypted: what the form shows, without decrypting them."""
        return json.loads(self.config) if self.config else {}

    def get_config(self):
        """The settings, secrets in clear."""
        try:
            return decrypt_fields(self.stored_config(), CHANNEL_CLEAR)
        except UnreadableSecret:
            raise unreadable('An address, token or header', f'the channel {self.name}') from None

    def set_config(self, config_dict):
        """Sets the settings, secrets encrypted (one already encrypted stays as it is)."""
        self.config = json.dumps(encrypt_fields(config_dict, CHANNEL_CLEAR), ensure_ascii=False)
    
    def __repr__(self):
        return f'<NotificationChannel {self.name} ({self.channel_type})>'


class RoutingRule(db.Model):
    """
    Routing rule with multiple conditions on arbitrary fields.
    Sends errors to different recipients depending on the data.
    """
    __tablename__ = 'routing_rules'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    # Descriptive name (optional, for the UI)
    name = db.Column(db.String(100))
    
    condition_logic = db.Column(db.String(5), default='AND')  # 'AND' / 'OR'
    
    # Action: recipients on match (comma-separated)
    recipients = db.Column(db.Text, nullable=False)
    
    # Priority (evaluation order, lower = first)
    priority = db.Column(db.Integer, default=0)
    
    # If True, later rules are not evaluated after a match
    stop_on_match = db.Column(db.Boolean, default=False)
    
    is_active = db.Column(db.Boolean, default=True)
    
    conditions = db.relationship('RoutingCondition', backref='rule',
                                cascade='all, delete-orphan',
                                order_by='RoutingCondition.id')
    
    def get_recipients_list(self):
        if not self.recipients:
            return []
        return [r.strip() for r in self.recipients.split(',') if r.strip()]
    
    def __repr__(self):
        return f'<RoutingRule {self.id} "{self.name or "unnamed"}">'


class RoutingCondition(db.Model):
    """
    A single condition of a routing rule.
    The field is fully dynamic (any column of the query).
    """
    __tablename__ = 'routing_conditions'
    
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('routing_rules.id'), nullable=False)
    
    field_name = db.Column(db.String(100), nullable=False)
    operator = db.Column(db.String(20), nullable=False)  # one of routing_service.OPERATORS
    value = db.Column(db.String(500))  # Can be empty for is_empty/is_not_empty
    
    # Case sensitivity for text comparisons
    case_sensitive = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<RoutingCondition {self.field_name} {self.operator} {self.value}>'


class ErrorRecord(db.Model):
    """
    Record of the errors detected by the queries.
    Each error is identified by a unique hash based on the key fields.
    """
    __tablename__ = 'error_records'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    # Unique error hash (computed from the key fields)
    error_hash = db.Column(db.String(64), nullable=False, index=True)
    
    # Error data in JSON format
    error_data = db.Column(db.Text, nullable=False)
    
    # Initial notification state
    email_sent = db.Column(db.Boolean, default=False)
    email_sent_at = db.Column(db.DateTime)
    
    # Recipients who received the notification by email (JSON list)
    notified_to = db.Column(db.Text)

    # Not yet delivered to every recipient and channel: retried later, only towards those missing
    notify_pending = db.Column(db.Boolean, default=False)
    notified_channels = db.Column(db.Text)  # ids of the channels reached (JSON list)
    notify_attempts = db.Column(db.Integer, default=0)  # failed attempts
    last_notify_error = db.Column(db.Text)
    notify_retry_at = db.Column(db.DateTime)  # next attempt not before (waits that grow)
    # Given up: channels that refused it (4xx) and whatever was still missing after
    # the last attempt (JSON: {"channels": [ids], "recipients": [addresses]})
    undeliverable = db.Column(db.Text)

    # Reminder
    last_reminder_at = db.Column(db.DateTime)
    reminder_count = db.Column(db.Integer, default=0)
    
    # Timestamps
    first_seen_at = db.Column(db.DateTime, default=get_utc_now)
    last_seen_at = db.Column(db.DateTime, default=get_utc_now)
    resolved_at = db.Column(db.DateTime)
    
    # Counter: how many times it has been seen
    occurrence_count = db.Column(db.Integer, default=1)
    # Checks in a row that did not return it, while it waits to be resolved (see resolve_after_checks)
    missing_checks = db.Column(db.Integer, default=0)

    # What the person who handles the errors made of it: 'ignored' (out of the lists and counts, nothing sent) or
    # 'assigned' (assigned_to is on it); empty, an open error. Both stop the reminders
    state = db.Column(db.String(20))
    assigned_to = db.Column(db.String(100))
    state_changed_at = db.Column(db.DateTime)
    # Held by a pause of the notifications, ending then: it goes out at the end, if still open
    held_until = db.Column(db.DateTime)
    
    # Composite index for efficient lookups
    __table_args__ = (
        db.Index('ix_error_query_hash', 'query_id', 'error_hash'),
        # The active errors of a query, at every check
        db.Index('ix_error_records_query_active', 'query_id', 'resolved_at'),
        # The active errors of every query (scheduler, dashboard) and the resolved ones to delete
        db.Index('ix_error_records_resolved_at', 'resolved_at'),
        # The statistics and the errors page, by time of detection
        db.Index('ix_error_records_first_seen_at', 'first_seen_at'),
        # One active error per identity; on an upgraded database, migration 1 resolves the copies first
        db.Index('ix_error_records_active_identity', 'query_id', 'error_hash', unique=True,
                 sqlite_where=db.text('resolved_at IS NULL')),
    )
    
    def get_error_data(self):
        return json.loads(self.error_data) if self.error_data else {}
    
    def set_error_data(self, data):
        self.error_data = json.dumps(data, default=str, ensure_ascii=False)

    def get_notified_to(self) -> list:
        """Recipients who received the notification by email."""
        return json.loads(self.notified_to) if self.notified_to else []

    def add_notified_to(self, recipients, sort_key=None):
        """Adds recipients (without duplicates) to those who received the notification."""
        merged = list(dict.fromkeys(self.get_notified_to() + list(recipients)))
        if sort_key:
            merged.sort(key=sort_key)
        self.notified_to = json.dumps(merged)

    def get_notified_channels(self) -> list:
        """Ids of the notification channels the notification reached."""
        return json.loads(self.notified_channels) if self.notified_channels else []

    def add_notified_channels(self, channel_ids):
        """Adds channels (without duplicates) to those reached by the notification."""
        merged = list(dict.fromkeys(self.get_notified_channels() + list(channel_ids)))
        self.notified_channels = json.dumps(merged)

    def get_undeliverable(self) -> dict:
        """Channels and recipients the notification was given up for."""
        data = json.loads(self.undeliverable) if self.undeliverable else {}
        return {'channels': data.get('channels', []), 'recipients': data.get('recipients', [])}

    def undelivered_to(self, channel_names: dict) -> list:
        """Who the notification was given up for: the addresses, then the channels ({id: name})."""
        given = self.get_undeliverable()
        return given['recipients'] + [channel_names.get(c, f'deleted channel {c}') for c in given['channels']]

    def add_undeliverable(self, channels=(), recipients=()):
        """Gives up the notification towards these channels and recipients."""
        data = self.get_undeliverable()
        data['channels'] = list(dict.fromkeys(data['channels'] + list(channels)))
        data['recipients'] = list(dict.fromkeys(data['recipients'] + list(recipients)))
        self.undeliverable = json.dumps(data) if data['channels'] or data['recipients'] else None
    
    def needs_reminder(self, query):
        if not query.reminder_enabled or not self.email_sent or self.resolved_at:
            return False
        # Not returned by the last check: waiting to be resolved, not still active
        if self.missing_checks:
            return False
        # Someone is on it, or it was ignored: nobody needs reminding
        if self.state:
            return False
        if self.reminder_count >= query.reminder_max_count:
            return False
        last_notification = self.last_reminder_at or self.email_sent_at
        if not last_notification:
            return False
        
        elapsed_minutes = (get_utc_now() - last_notification).total_seconds() / 60
        return elapsed_minutes >= query.reminder_interval_minutes
    
    @staticmethod
    def calculate_hash(data: dict, key_fields: list) -> str:
        """
        The identity of an error: the hash of the values of the key fields as a JSON list, so a value with |
        in it stays apart and NULL (or a missing field) is not an empty value. It changes only together with a
        migration of the saved hashes (migrations.py, number 2): otherwise every open error would look new.
        """
        key_values = []
        for field in key_fields:
            # Look up the field case-insensitively
            value = None
            for k, v in data.items():
                if k.upper() == field.upper():
                    value = v
                    break
            key_values.append(str(value) if value is not None else None)
        
        hash_string = json.dumps(key_values, ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(hash_string.encode()).hexdigest()
    
    @classmethod
    def listed(cls):
        """The errors of the lists and counts: all but the ignored ones, which have a view of their own."""
        return db.func.coalesce(cls.state, '') != 'ignored'

    @classmethod
    def active_summary(cls) -> dict:
        """
        Summary for the automatic page refresh: number of active errors, the ignored ones apart,
        and a signature that changes when an error appears, is resolved or ignored
        (not when a known error is just seen again).
        """
        active, last_id, last_resolved = db.session.query(
            db.func.coalesce(db.func.sum(db.case((db.and_(cls.resolved_at.is_(None), cls.listed()), 1), else_=0)), 0),
            db.func.max(cls.id),
            db.func.max(cls.resolved_at),
        ).one()
        return {
            'active': int(active),
            'signature': f"{int(active)}-{last_id or 0}-{last_resolved or ''}",
        }
    
    def __repr__(self):
        return f'<ErrorRecord {self.error_hash[:8]}... query={self.query_id}>'


class QueryLog(db.Model):
    """Run log of the queries, for debugging and monitoring."""
    __tablename__ = 'query_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'), nullable=False)
    
    executed_at = db.Column(db.DateTime, default=get_utc_now)
    
    status = db.Column(db.String(20))  # 'success', 'error', 'skipped'
    
    # Run statistics
    rows_returned = db.Column(db.Integer, default=0)
    new_errors = db.Column(db.Integer, default=0)
    resolved_errors = db.Column(db.Integer, default=0)
    reminders_sent = db.Column(db.Integer, default=0)
    emails_sent = db.Column(db.Integer, default=0)
    execution_time_ms = db.Column(db.Integer)
    
    # Error message or notes, if any
    error_message = db.Column(db.Text)
    
    __table_args__ = (
        # The latest runs of a query (its page, its state)
        db.Index('ix_query_logs_query_time', 'query_id', 'executed_at'),
        # The log page and the cleanup
        db.Index('ix_query_logs_executed_at', 'executed_at'),
    )

    def __repr__(self):
        return f'<QueryLog {self.id} status={self.status}>'


class EmailLog(db.Model):
    """Log of the emails sent, for traceability."""
    __tablename__ = 'email_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey('monitored_queries.id'))
    
    sent_at = db.Column(db.DateTime, default=get_utc_now)
    recipients = db.Column(db.Text)
    subject = db.Column(db.String(200))
    
    email_type = db.Column(db.String(20), default='new_errors')  # new_errors, reminder, monitor_failed, monitor_recovered
    
    # Number of errors in the email
    error_count = db.Column(db.Integer, default=0)
    
    status = db.Column(db.String(20))  # 'sent', 'failed'
    error_message = db.Column(db.Text)
    
    __table_args__ = (
        # The emails of today (dashboard) and the cleanup
        db.Index('ix_email_logs_sent_at', 'sent_at'),
    )

    def __repr__(self):
        return f'<EmailLog {self.id} status={self.status}>'


class NotificationPause(db.Model):
    """The pause of the notifications for planned work, in one row: paused while until is ahead."""
    __tablename__ = 'notification_pause'

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime)
    until = db.Column(db.DateTime, nullable=False)


class SchemaMigration(db.Model):
    """A numbered migration of the database that has run (migrations.py): each runs once."""
    __tablename__ = 'schema_migrations'

    number = db.Column(db.Integer, primary_key=True, autoincrement=False)
    name = db.Column(db.String(100), nullable=False)
    applied_at = db.Column(db.DateTime, default=get_utc_now)


# Columns added after the tables were created, in the order to add them
NEW_COLUMNS = [
    ('error_records', 'notified_to'),
    ('error_records', 'notify_pending'),
    ('error_records', 'notified_channels'),
    ('error_records', 'notify_attempts'),
    ('error_records', 'last_notify_error'),
    ('monitored_queries', 'consecutive_failures'),
    ('monitored_queries', 'failing_since'),
    ('monitored_queries', 'failure_alerted_at'),
    ('monitored_queries', 'last_failure_message'),
    ('error_records', 'notify_retry_at'),
    ('error_records', 'undeliverable'),
    ('database_connections', 'allow_writes'),
    ('monitored_queries', 'failure_retry_at'),
    ('monitored_queries', 'resolve_after_checks'),
    ('error_records', 'missing_checks'),
    ('error_records', 'state'),
    ('error_records', 'assigned_to'),
    ('error_records', 'state_changed_at'),
    ('error_records', 'held_until'),
]


def ensure_schema() -> list:
    """
    Adds the columns introduced after the tables were created (db.create_all() only
    creates missing tables) and returns them. Type and default come from the model.
    """
    from sqlalchemy import inspect, text, false

    added = []
    inspector = inspect(db.engine)
    dialect = db.engine.dialect
    for table, column_name in NEW_COLUMNS:
        existing = {c['name'] for c in inspector.get_columns(table)}
        if column_name in existing:
            continue
        column = db.metadata.tables[table].c[column_name]
        ddl = f'ALTER TABLE {table} ADD COLUMN {column_name} {column.type.compile(dialect=dialect)}'
        # Existing rows start at 0 / false (e.g. no delivery to retry)
        if isinstance(column.type, db.Boolean):
            ddl += f' DEFAULT {false().compile(dialect=dialect)}'
        elif isinstance(column.type, db.Integer):
            ddl += ' DEFAULT 0'
        with db.engine.begin() as connection:
            connection.execute(text(ddl))
        added.append(f'{table}.{column_name}')
    return added


def ensure_indexes() -> list:
    """
    Creates the indexes of the models that an existing database lacks (db.create_all() creates them only with
    new tables) and returns their names. On a large database the first start after the update takes longer, once.
    """
    from sqlalchemy import inspect

    added = []
    inspector = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        existing = {index['name'] for index in inspector.get_indexes(table.name)}
        for index in sorted(table.indexes, key=lambda index: index.name):
            if index.name not in existing:
                index.create(bind=db.engine)
                added.append(index.name)
    return added


def allow_writes_where_queries_write() -> list:
    """
    Once, when allow_writes arrives in a database of 2.4.2 or earlier, off everywhere: the connections
    with a saved query that can change data keep running it. Returns [(connection, query, keyword)].
    """
    from validators import write_statement_keyword
    allowed = []
    for query in MonitoredQuery.query.filter(MonitoredQuery.db_connection_id.isnot(None)).order_by(MonitoredQuery.id):
        connection = query.db_connection
        keyword = query.source_type != 'http' and connection is not None and write_statement_keyword(
            query.sql_query, connection.db_type)
        if keyword and not connection.allow_writes:
            connection.allow_writes = True
            allowed.append((connection.name, query.name, keyword))
    db.session.commit()
    return allowed
