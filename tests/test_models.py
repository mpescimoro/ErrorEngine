"""
Model tests: scheduling, hash, needs_reminder, serialization.
"""
from datetime import datetime, time, timedelta
from models import MonitoredQuery, ErrorRecord, DatabaseConnection


class TestErrorHash:
    """Tests for the error hash."""
    
    def test_same_data_same_hash(self):
        """Same data, same hash."""
        data = {'ID': '001', 'CODE': 'ERR001'}
        h1 = ErrorRecord.calculate_hash(data, ['ID', 'CODE'])
        h2 = ErrorRecord.calculate_hash(data, ['ID', 'CODE'])
        assert h1 == h2
    
    def test_different_data_different_hash(self):
        """Different data, different hash."""
        d1 = {'ID': '001', 'CODE': 'ERR001'}
        d2 = {'ID': '002', 'CODE': 'ERR001'}
        h1 = ErrorRecord.calculate_hash(d1, ['ID', 'CODE'])
        h2 = ErrorRecord.calculate_hash(d2, ['ID', 'CODE'])
        assert h1 != h2
    
    def test_hash_uses_only_key_fields(self):
        """The hash depends only on the key fields, not on the others."""
        d1 = {'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error A'}
        d2 = {'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error B'}
        h1 = ErrorRecord.calculate_hash(d1, ['ID', 'CODE'])
        h2 = ErrorRecord.calculate_hash(d2, ['ID', 'CODE'])
        assert h1 == h2
    
    def test_hash_case_insensitive_field_names(self):
        """Field names are case-insensitive."""
        d1 = {'id': '001', 'code': 'ERR001'}
        d2 = {'ID': '001', 'CODE': 'ERR001'}
        h1 = ErrorRecord.calculate_hash(d1, ['ID', 'CODE'])
        h2 = ErrorRecord.calculate_hash(d2, ['ID', 'CODE'])
        assert h1 == h2
    
    def test_hash_missing_field_uses_empty(self):
        """A missing field is treated as an empty string."""
        d1 = {'ID': '001'}
        h1 = ErrorRecord.calculate_hash(d1, ['ID', 'CODE'])
        # Must not crash
        assert len(h1) == 64  # SHA-256 hex
    
    def test_hash_is_sha256(self):
        """The hash is a SHA-256 (64 hex characters)."""
        data = {'ID': '001'}
        h = ErrorRecord.calculate_hash(data, ['ID'])
        assert len(h) == 64
        assert all(c in '0123456789abcdef' for c in h)


class TestErrorDataSerialization:
    """Tests for error data serialization/deserialization."""
    
    def test_roundtrip(self, app):
        """set_error_data and get_error_data keep the data."""
        with app.app_context():
            e = ErrorRecord(query_id=1, error_hash='test')
            data = {'ID': '001', 'VALUE': 'test', 'NUM': 42}
            e.set_error_data(data)
            
            result = e.get_error_data()
            assert result['ID'] == '001'
            assert result['VALUE'] == 'test'
            assert result['NUM'] == 42
    
    def test_empty_data(self, app):
        """get_error_data with empty data returns an empty dict."""
        with app.app_context():
            e = ErrorRecord(query_id=1, error_hash='test')
            e.error_data = None
            assert e.get_error_data() == {}
    
    def test_unicode_data(self, app):
        """Unicode characters are kept."""
        with app.app_context():
            e = ErrorRecord(query_id=1, error_hash='test')
            data = {'NAME': 'Pescimoro à è ì ò ù', 'DESC': '日本語'}
            e.set_error_data(data)
            result = e.get_error_data()
            assert result['NAME'] == 'Pescimoro à è ì ò ù'
            assert result['DESC'] == '日本語'


class TestNeedsReminder:
    """Tests for the needs_reminder logic."""
    
    def test_reminder_disabled(self, app, sample_query):
        """With reminders disabled, returns False."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = False
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=True,
                            email_sent_at=datetime.utcnow() - timedelta(hours=5))
            assert e.needs_reminder(query) is False
    
    def test_email_not_sent_yet(self, app, sample_query):
        """If the first email was not sent, no reminder."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = True
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=False)
            assert e.needs_reminder(query) is False
    
    def test_already_resolved(self, app, sample_query):
        """A resolved error does not get reminders."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = True
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=True,
                            email_sent_at=datetime.utcnow() - timedelta(hours=5),
                            resolved_at=datetime.utcnow())
            assert e.needs_reminder(query) is False
    
    def test_max_reminders_reached(self, app, sample_query):
        """Does not exceed the maximum number of reminders."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = True
            query.reminder_max_count = 3
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=True,
                            email_sent_at=datetime.utcnow() - timedelta(hours=5),
                            reminder_count=3)
            assert e.needs_reminder(query) is False
    
    def test_reminder_needed(self, app, sample_query):
        """Reminder needed when the conditions are met."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = True
            query.reminder_interval_minutes = 60
            query.reminder_max_count = 5
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=True,
                            email_sent_at=datetime.utcnow() - timedelta(hours=2),
                            reminder_count=0)
            assert e.needs_reminder(query) is True
    
    def test_reminder_too_soon(self, app, sample_query):
        """No reminder if the interval has not passed."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.reminder_enabled = True
            query.reminder_interval_minutes = 60
            query.reminder_max_count = 5
            
            e = ErrorRecord(query_id=query.id, error_hash='test', email_sent=True,
                            email_sent_at=datetime.utcnow() - timedelta(minutes=10),
                            reminder_count=0)
            assert e.needs_reminder(query) is False


class TestIsInSchedule:
    """Tests for the time window check."""
    
    def test_no_schedule_always_in(self, app, sample_query):
        """Without restrictions, always in schedule."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = None
            query.schedule_end_time = None
            query.schedule_days = ''
            
            # Monday at 10:00
            now = datetime(2025, 2, 10, 10, 0, 0)
            assert query.is_in_schedule(now) is True
    
    def test_within_time_window(self, app, sample_query):
        """Inside the time window returns True."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(18, 0)
            query.schedule_days = ''
            
            now = datetime(2025, 2, 10, 12, 0, 0)  # Monday 12:00
            assert query.is_in_schedule(now) is True
    
    def test_before_start_time(self, app, sample_query):
        """Before the start time returns False."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(18, 0)
            query.schedule_days = ''
            
            now = datetime(2025, 2, 10, 6, 0, 0)  # Monday 06:00
            assert query.is_in_schedule(now) is False
    
    def test_after_end_time(self, app, sample_query):
        """After the end time returns False."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(18, 0)
            query.schedule_days = ''
            
            now = datetime(2025, 2, 10, 20, 0, 0)  # Monday 20:00
            assert query.is_in_schedule(now) is False
    
    def test_wrong_day(self, app, sample_query):
        """A day not allowed returns False."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_days = '1,2,3,4,5'  # Mon-Fri only
            query.schedule_start_time = None
            query.schedule_end_time = None
            
            # Saturday = isoweekday 6
            now = datetime(2025, 2, 8, 12, 0, 0)
            assert query.is_in_schedule(now) is False
    
    def test_allowed_day(self, app, sample_query):
        """An allowed day returns True."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_days = '1,2,3,4,5'  # Mon-Fri only
            query.schedule_start_time = None
            query.schedule_end_time = None
            
            # Monday = isoweekday 1
            now = datetime(2025, 2, 10, 12, 0, 0)
            assert query.is_in_schedule(now) is True


class TestShouldRunNow:
    """Tests for should_run_now."""
    
    def test_first_execution(self, app, sample_query):
        """First run: always runs if in schedule."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.last_check_at = None
            query.schedule_start_time = None
            query.schedule_end_time = None
            query.schedule_days = ''
            
            now = datetime(2025, 2, 10, 10, 0, 0)
            should_run, reason = query.should_run_now(now)
            assert should_run is True
            assert 'First run' in reason
    
    def test_out_of_schedule_no_run(self, app, sample_query):
        """Outside the time window: do not run."""
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(18, 0)
            
            now = datetime(2025, 2, 10, 22, 0, 0)  # 22:00
            should_run, reason = query.should_run_now(now)
            assert should_run is False


class TestDatabaseConnectionRepr:
    """Tests for DatabaseConnection.__repr__."""
    
    def test_repr(self, app, sample_connection):
        """Checks the __repr__ format."""
        with app.app_context():
            conn = DatabaseConnection.query.get(sample_connection.id)
            r = repr(conn)
            assert 'Test SQLite' in r
            assert 'sqlite' in r


class TestKeyFieldsList:
    """Tests for get_key_fields_list."""
    
    def test_single_field(self, app, sample_query):
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.key_fields = 'ID'
            assert query.get_key_fields_list() == ['ID']
    
    def test_multiple_fields(self, app, sample_query):
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.key_fields = 'ID, CODE, STATUS'
            assert query.get_key_fields_list() == ['ID', 'CODE', 'STATUS']
    
    def test_strips_whitespace(self, app, sample_query):
        with app.app_context():
            query = MonitoredQuery.query.get(sample_query.id)
            query.key_fields = '  ID  ,  CODE  '
            assert query.get_key_fields_list() == ['ID', 'CODE']
