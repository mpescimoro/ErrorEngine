"""
Tests for monitor_service: check_query, deduplication, error resolution.
"""
from unittest.mock import patch
from models import db, MonitoredQuery, ErrorRecord, QueryLog


class TestCheckQueryNewErrors:
    """Tests for new error detection."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_finds_new_errors(self, mock_execute, mock_email, app, sample_query):
        """Finds new errors and saves them in the database."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.return_value = (
                ['ID', 'CODE', 'MESSAGE'],
                [
                    {'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error 1'},
                    {'ID': '002', 'CODE': 'ERR002', 'MESSAGE': 'Error 2'},
                ]
            )
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'success'
            assert result['rows_returned'] == 2
            assert result['new_errors'] == 2
            
            # Check that the errors were saved
            errors = ErrorRecord.query.filter_by(query_id=query.id).all()
            assert len(errors) == 2
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_no_rows_no_errors(self, mock_execute, mock_email, app, sample_query):
        """No rows returned = no new errors."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.return_value = (['ID', 'CODE'], [])
            
            query = MonitoredQuery.query.get(sample_query.id)
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'success'
            assert result['rows_returned'] == 0
            assert result['new_errors'] == 0


class TestCheckQueryDeduplication:
    """Tests for hash-based deduplication."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_duplicate_not_counted(self, mock_execute, mock_email, app, sample_query):
        """An error already present is not counted as new."""
        with app.app_context():
            from monitor_service import monitor_service
            
            rows = [{'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error 1'}]
            mock_execute.return_value = (['ID', 'CODE', 'MESSAGE'], rows)
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # First run: finds 1 new error
            r1 = monitor_service.check_query(query, force=True)
            assert r1['new_errors'] == 1
            
            # Reset the lock to run again
            query.locked_at = None
            db.session.commit()
            
            # Second run with the same data: 0 new
            r2 = monitor_service.check_query(query, force=True)
            assert r2['new_errors'] == 0
            assert r2['rows_returned'] == 1
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_occurrence_count_incremented(self, mock_execute, mock_email, app, sample_query):
        """An error seen again increments the counter."""
        with app.app_context():
            from monitor_service import monitor_service
            
            rows = [{'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error 1'}]
            mock_execute.return_value = (['ID', 'CODE', 'MESSAGE'], rows)
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # First run
            monitor_service.check_query(query, force=True)
            query.locked_at = None
            db.session.commit()
            
            # Second run
            monitor_service.check_query(query, force=True)
            
            error = ErrorRecord.query.filter_by(query_id=query.id).first()
            assert error.occurrence_count == 2


class TestCheckQueryResolution:
    """Tests for the automatic resolution of errors."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_error_resolved_when_missing(self, mock_execute, mock_email, app, sample_query):
        """An error no longer returned by the query is marked as resolved."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # First run: 2 errors
            mock_execute.return_value = (
                ['ID', 'CODE'],
                [
                    {'ID': '001', 'CODE': 'ERR001'},
                    {'ID': '002', 'CODE': 'ERR002'},
                ]
            )
            monitor_service.check_query(query, force=True)
            query.locked_at = None
            db.session.commit()
            
            assert ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).count() == 2
            
            # Second run: only 1 error (002 resolved)
            mock_execute.return_value = (
                ['ID', 'CODE'],
                [{'ID': '001', 'CODE': 'ERR001'}]
            )
            r2 = monitor_service.check_query(query, force=True)
            
            assert r2['resolved_errors'] == 1
            
            active = ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).all()
            assert len(active) == 1
            assert active[0].get_error_data()['ID'] == '001'
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_all_errors_resolved(self, mock_execute, mock_email, app, sample_query):
        """If the query returns no more rows, all errors are resolved."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # Before: 2 errors
            mock_execute.return_value = (
                ['ID', 'CODE'],
                [{'ID': '001', 'CODE': 'ERR001'}, {'ID': '002', 'CODE': 'ERR002'}]
            )
            monitor_service.check_query(query, force=True)
            query.locked_at = None
            db.session.commit()
            
            # After: 0 rows
            mock_execute.return_value = (['ID', 'CODE'], [])
            r2 = monitor_service.check_query(query, force=True)
            
            assert r2['resolved_errors'] == 2
            assert ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).count() == 0


class TestCheckQuerySchedule:
    """Tests for the time window."""
    
    @patch('monitor_service.execute_query_source')
    def test_skips_out_of_schedule(self, mock_execute, app, sample_query):
        """A query outside its time window is skipped unless forced."""
        with app.app_context():
            from monitor_service import monitor_service
            from datetime import time
            
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(10, 0)
            db.session.commit()
            
            # Mock _get_local_now to simulate a time outside the window
            with patch.object(query, '_get_local_now') as mock_now:
                from datetime import datetime as dt
                mock_now.return_value = dt(2025, 2, 10, 22, 0, 0)
                
                result = monitor_service.check_query(query, force=False)
            
            assert result['status'] == 'skipped'
            mock_execute.assert_not_called()
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_force_ignores_schedule(self, mock_execute, mock_email, app, sample_query):
        """With force=True the time window is ignored."""
        with app.app_context():
            from monitor_service import monitor_service
            from datetime import time
            
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(10, 0)
            db.session.commit()
            
            mock_execute.return_value = (['ID'], [])
            
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'success'
            mock_execute.assert_called_once()


class TestCheckQueryErrorHandling:
    """Tests for error handling during the run."""
    
    @patch('monitor_service.execute_query_source')
    def test_data_source_error(self, mock_execute, app, sample_query):
        """An error in the data source is handled and logged."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.side_effect = Exception("Connection refused")
            
            query = MonitoredQuery.query.get(sample_query.id)
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'error'
            assert 'Connection refused' in result['error_message']


class TestCheckQueryStats:
    """Tests for the statistics update."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_updates_query_stats(self, mock_execute, mock_email, app, sample_query):
        """check_query updates last_check_at and the counters."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.return_value = (
                ['ID', 'CODE'],
                [{'ID': '001', 'CODE': 'ERR001'}]
            )
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            assert query.last_check_at is None
            assert query.total_errors_found == 0
            
            monitor_service.check_query(query, force=True)
            
            # Reload from the DB
            db.session.refresh(query)
            assert query.last_check_at is not None
            assert query.total_errors_found == 1
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_creates_execution_log(self, mock_execute, mock_email, app, sample_query):
        """check_query creates a QueryLog for every run."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.return_value = (['ID'], [{'ID': '001'}])
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            monitor_service.check_query(query, force=True)
            
            log = QueryLog.query.filter_by(query_id=query.id).first()
            assert log is not None
            assert log.status == 'success'
            assert log.rows_returned == 1
            assert log.new_errors == 1
            assert log.execution_time_ms >= 0


class TestGetActiveErrors:
    """Tests for get_active_errors."""
    
    def test_returns_only_active(self, app, sample_query, sample_errors_in_db):
        """Returns only unresolved errors."""
        with app.app_context():
            from monitor_service import monitor_service
            
            errors = monitor_service.get_active_errors(sample_query.id)
            assert len(errors) == 2  # 2 active, 2 resolved in the fixture
    
    def test_returns_all_when_no_filter(self, app, sample_query, sample_errors_in_db):
        """Without query_id returns all active errors."""
        with app.app_context():
            from monitor_service import monitor_service
            
            errors = monitor_service.get_active_errors()
            assert len(errors) == 2


class TestGetQueryStatus:
    """Tests for get_query_status."""
    
    def test_returns_status(self, app, sample_query):
        """Returns the query state."""
        with app.app_context():
            from monitor_service import monitor_service
            
            status = monitor_service.get_query_status(sample_query.id)
            assert status['name'] == 'Test Query'
            assert 'active_errors' in status
            assert 'is_active' in status
    
    def test_not_found(self, app):
        """A query that is not found returns an error."""
        with app.app_context():
            from monitor_service import monitor_service
            
            status = monitor_service.get_query_status(99999)
            assert 'error' in status
