"""
SQLite database models for ErrorEngine monitoring
"""
from datetime import datetime, time, timedelta
from utils import get_utc_now
from flask_sqlalchemy import SQLAlchemy
import hashlib
import json

db = SQLAlchemy()


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
    
    # For databases: SQL query
    sql_query = db.Column(db.Text)
    
    # For HTTP/API: JSON configuration
    # HTTP: {"url": "...", "method": "GET/POST", "headers": {...}, "body": {...}, "response_path": "data.items"}
    # API: {"endpoint": "...", "auth_type": "bearer/basic/api_key", "auth_value": "...", ...}
    source_config = db.Column(db.Text)
    
    # Fields that uniquely identify an error (comma-separated)
    key_fields = db.Column(db.String(500), nullable=False)
    
    # === BASIC EMAIL CONFIGURATION ===
    email_subject = db.Column(db.String(200), default='[ErrorEngine] New errors: {query_name}')
    email_recipients = db.Column(db.Text)  # Comma-separated emails
    email_template = db.Column(db.Text)  # Custom HTML template
    
    # === ADVANCED SCHEDULING ===
    check_interval_minutes = db.Column(db.Integer, default=15)
    is_active = db.Column(db.Boolean, default=True)
    
    # Time window
    schedule_start_time = db.Column(db.Time)  # es. 08:00
    schedule_end_time = db.Column(db.Time)    # es. 20:00
    schedule_days = db.Column(db.String(20))  # es. "1,2,3,4,5" (lun-ven, ISO weekday)
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
        """Returns the list of email recipients (comma-separated)"""
        if not self.email_recipients:
            return []
        return [r.strip() for r in self.email_recipients.split(',') if r.strip()]
    
    def get_default_routing_recipients(self):
        """Returns the fallback recipients for routing"""
        if not self.routing_default_recipients:
            return []
        return [r.strip() for r in self.routing_default_recipients.split(',') if r.strip()]
    
    def get_key_fields_list(self):
        """Returns the list of key fields"""
        return [f.strip() for f in self.key_fields.split(',') if f.strip()]
    
    def get_schedule_days_list(self):
        """Returns the list of active days (ISO weekday: 1=Mon, 7=Sun)"""
        if not self.schedule_days:
            return [1, 2, 3, 4, 5, 6, 7]  # Every day if not specified
        return [int(d.strip()) for d in self.schedule_days.split(',') if d.strip()]
    
    def get_source_config(self):
        """Returns the source configuration as a dict"""
        if not self.source_config:
            return {}
        return json.loads(self.source_config)
        
    def get_tags_list(self):
        """Returns the list of tags."""
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def set_source_config(self, config):
        """Sets the source configuration from a dict"""
        self.source_config = json.dumps(config, ensure_ascii=False)
    
    def _get_local_now(self):
        """Returns the local time in the configured time zone."""
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
        """Computes the next run from reference_time + interval (local time)."""
        if now is None:
            now = self._get_local_now()
        
        # Without a reference_time, use midnight
        ref_time = self.schedule_reference_time or time(0, 0)
        interval = timedelta(minutes=self.check_interval_minutes)
        
        # First slot of today
        today_ref = datetime.combine(now.date(), ref_time)
        
        # Current or previous slot
        if now >= today_ref:
            elapsed = now - today_ref
            intervals_passed = int(elapsed.total_seconds() // interval.total_seconds())
            current_slot = today_ref + (interval * intervals_passed)
            next_slot = current_slot + interval
        else:
            yesterday_ref = today_ref - timedelta(days=1)
            elapsed = now - yesterday_ref
            intervals_passed = int(elapsed.total_seconds() // interval.total_seconds())
            current_slot = yesterday_ref + (interval * intervals_passed)
            next_slot = current_slot + interval
        
        return current_slot, next_slot

    def get_next_run_time(self, now=None):
        """Computes when the query will run next (taking the time window into account)."""
        if now is None:
            now = self._get_local_now()
        
        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Which slot is the next one to run
        if self.last_check_at is not None:
            # last_check_at in local time, for the comparison
            last_check_local = self._utc_to_local(self.last_check_at)
            if last_check_local >= current_slot:
                target_slot = next_slot
            else:
                target_slot = current_slot
        else:
            target_slot = current_slot if current_slot >= now else next_slot
        
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
        """Converts a UTC datetime to local time."""
        from utils import utc_to_local
        return utc_to_local(utc_dt)

    def should_run_now(self, now=None):
        """Checks whether the query must run now."""
        if now is None:
            now = self._get_local_now()
        
        # Check the time window and the day
        if not self.is_in_schedule(now):
            return False, "Outside schedule window"
        
        # After a skipped (inactive) day, the first run of the new active day
        # must fall on reference_time, not on a slot inherited from the alignment of the
        # skipped day. Without this, the scheduler starts at 00:00 (the first minute in which
        # the day becomes active) because a past slot of the skipped day precedes "now".
        ref_time = self.schedule_reference_time or time(0, 0)
        prev_day = now.date() - timedelta(days=1)
        prev_day_inactive = prev_day.isoweekday() not in self.get_schedule_days_list()
        if prev_day_inactive and now.time() < ref_time:
            return False, f"Waiting for first run at {ref_time.strftime('%H:%M')}"
        
        current_slot, next_slot = self.get_next_scheduled_time(now)
        
        # Never run yet: run if we are in the current slot
        if self.last_check_at is None:
            return True, "First run"
        
        # last_check_at in local time
        last_check_local = self._utc_to_local(self.last_check_at)
        
        # If the last run is before the current slot, run now
        if last_check_local < current_slot:
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
    password = db.Column(db.String(255))  # TODO: encrypt
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    # Relationship
    queries = db.relationship('MonitoredQuery', backref='db_connection', lazy='dynamic')
    
    def get_driver(self):
        """Returns the driver for this DB type, with the configured timeouts."""
        from db_drivers import get_driver, driver_timeouts
        return get_driver(self.db_type, **driver_timeouts())

    def test_connection(self) -> dict:
        """Tests the connection."""
        driver = self.get_driver()
        return driver.test_connection(
            self.host, self.port, self.database, self.username, self.password
        )

    def execute_query(self, sql: str) -> tuple:
        """Runs a query on this connection."""
        driver = self.get_driver()
        conn = driver.open(
            self.host, self.port, self.database, self.username, self.password
        )
        try:
            return driver.execute_query(conn, sql)
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
    
    def get_config(self):
        import json
        return json.loads(self.config) if self.config else {}
    
    def set_config(self, config_dict):
        import json
        self.config = json.dumps(config_dict, ensure_ascii=False)
    
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
    
    # Logic between multiple conditions
    condition_logic = db.Column(db.String(5), default='AND')  # 'AND' / 'OR'
    
    # Action: recipients on match (comma-separated)
    recipients = db.Column(db.Text, nullable=False)
    
    # Priority (evaluation order, lower = first)
    priority = db.Column(db.Integer, default=0)
    
    # If True, later rules are not evaluated after a match
    stop_on_match = db.Column(db.Boolean, default=False)
    
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationship with the conditions
    conditions = db.relationship('RoutingCondition', backref='rule',
                                cascade='all, delete-orphan',
                                order_by='RoutingCondition.id')
    
    def get_recipients_list(self):
        """Returns the list of recipients"""
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
    
    # Generic condition on any field
    field_name = db.Column(db.String(100), nullable=False)
    operator = db.Column(db.String(20), nullable=False)
    value = db.Column(db.String(500))  # Can be empty for is_empty/is_not_empty
    
    # Case sensitivity for text comparisons
    case_sensitive = db.Column(db.Boolean, default=False)
    
    # Supported operators (for documentation):
    # equals, not_equals, contains, not_contains, startswith, endswith,
    # in, not_in, gt, gte, lt, lte, is_empty, is_not_empty, regex
    
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

    # Notification not yet delivered to everyone: retried at every check,
    # only towards the missing recipients and channels
    notify_pending = db.Column(db.Boolean, default=False)
    notified_channels = db.Column(db.Text)  # ids of the channels reached (JSON list)
    notify_attempts = db.Column(db.Integer, default=0)  # failed attempts
    last_notify_error = db.Column(db.Text)
    notify_retry_at = db.Column(db.DateTime)  # next attempt not before (waits that grow)
    # Given up: channels that refused it (4xx) and whatever was still missing after
    # NOTIFY_RETRY_HOURS (JSON: {"channels": [ids], "recipients": [addresses]})
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
    
    # Composite index for efficient lookups
    __table_args__ = (
        db.Index('ix_error_query_hash', 'query_id', 'error_hash'),
    )
    
    def get_error_data(self):
        """Deserializes the error data"""
        return json.loads(self.error_data) if self.error_data else {}
    
    def set_error_data(self, data):
        """Serializes the error data"""
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

    def add_undeliverable(self, channels=(), recipients=()):
        """Gives up the notification towards these channels and recipients."""
        data = self.get_undeliverable()
        data['channels'] = list(dict.fromkeys(data['channels'] + list(channels)))
        data['recipients'] = list(dict.fromkeys(data['recipients'] + list(recipients)))
        self.undeliverable = json.dumps(data) if data['channels'] or data['recipients'] else None
    
    def needs_reminder(self, query):
        """Checks whether the error needs a reminder"""
        if not query.reminder_enabled:
            return False
        if not self.email_sent:
            return False  # First email not sent yet
        if self.resolved_at:
            return False  # Already resolved
        if self.reminder_count >= query.reminder_max_count:
            return False  # Reminder limit reached
        
        # Has enough time passed
        last_notification = self.last_reminder_at or self.email_sent_at
        if not last_notification:
            return False
        
        elapsed_minutes = (get_utc_now() - last_notification).total_seconds() / 60
        return elapsed_minutes >= query.reminder_interval_minutes
    
    @staticmethod
    def calculate_hash(data: dict, key_fields: list) -> str:
        """Computes the unique error hash from the key fields."""
        key_values = []
        for field in key_fields:
            # Look up the field case-insensitively
            value = None
            for k, v in data.items():
                if k.upper() == field.upper():
                    value = v
                    break
            key_values.append(str(value) if value is not None else '')
        
        hash_string = '|'.join(key_values)
        return hashlib.sha256(hash_string.encode()).hexdigest()
    
    @classmethod
    def active_summary(cls) -> dict:
        """
        Summary for the automatic page refresh: number of active errors
        and a signature that changes when an error appears or is resolved
        (not when a known error is just seen again).
        """
        active, last_id, last_resolved = db.session.query(
            db.func.coalesce(db.func.sum(db.case((cls.resolved_at.is_(None), 1), else_=0)), 0),
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
    
    # Run result
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
    
    # Email type
    email_type = db.Column(db.String(20), default='new_errors')  # new_errors, reminder, monitor_failed, monitor_recovered
    
    # Number of errors in the email
    error_count = db.Column(db.Integer, default=0)
    
    # Delivery status
    status = db.Column(db.String(20))  # 'sent', 'failed'
    error_message = db.Column(db.Text)
    
    def __repr__(self):
        return f'<EmailLog {self.id} status={self.status}>'


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
]


def ensure_schema() -> list:
    """
    Adds to the existing tables the columns introduced after they were
    created: db.create_all() only creates missing tables, it does not change them.
    Type and default come from the model, written in the database dialect.

    Returns:
        list: columns added (empty if the database was already up to date)
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
