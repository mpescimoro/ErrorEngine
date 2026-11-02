"""
Tests for cleanup_service: retention-based and manual cleanup.
"""
from models import ErrorRecord, QueryLog, EmailLog


class TestCleanupQueryLogs:
    """Tests for QueryLog cleanup."""
    
    def test_deletes_old_logs(self, app, sample_query, sample_logs_in_db):
        """Deletes logs older than the retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            # Check the setup: 2 logs present
            assert QueryLog.query.count() == 2
            
            deleted = cleanup_service.cleanup_query_logs()
            
            # The old log (60 days) exceeds the default retention (30 days)
            assert deleted == 1
            assert QueryLog.query.count() == 1
    
    def test_keeps_recent_logs(self, app, sample_query, sample_logs_in_db):
        """Does not delete recent logs."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_query_logs()
            
            remaining = QueryLog.query.first()
            assert remaining is not None
            assert remaining.rows_returned == 5  # The recent log


class TestCleanupEmailLogs:
    """Tests for EmailLog cleanup."""
    
    def test_deletes_old_email_logs(self, app, sample_query, sample_logs_in_db):
        """Deletes email logs older than the retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            assert EmailLog.query.count() == 2
            
            deleted = cleanup_service.cleanup_email_logs()
            
            # The old email log (120 days) exceeds the default retention (90 days)
            assert deleted == 1
            assert EmailLog.query.count() == 1
    
    def test_keeps_recent_email_logs(self, app, sample_query, sample_logs_in_db):
        """Does not delete recent email logs."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_email_logs()
            
            remaining = EmailLog.query.first()
            assert remaining is not None
            assert remaining.subject == 'Test'  # The recent email


class TestCleanupResolvedErrors:
    """Tests for the cleanup of resolved errors."""
    
    def test_deletes_old_resolved(self, app, sample_query, sample_errors_in_db):
        """Deletes resolved errors older than the retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            # Fixture: 2 active + 2 resolved (1 recent, 1 old, 100 days)
            total_before = ErrorRecord.query.count()
            assert total_before == 4
            
            deleted = cleanup_service.cleanup_resolved_errors()
            
            # Only the old resolved one (100 days) exceeds the default retention (60 days)
            assert deleted == 1
    
    def test_never_deletes_active_errors(self, app, sample_query, sample_errors_in_db):
        """Never touches active (unresolved) errors."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_resolved_errors()
            
            active = ErrorRecord.query.filter_by(resolved_at=None).count()
            assert active == 2  # Unchanged
    
    def test_keeps_recent_resolved(self, app, sample_query, sample_errors_in_db):
        """Does not delete recently resolved errors."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_resolved_errors()
            
            # The recently resolved one (1 hour ago) is still there
            resolved = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None)
            ).all()
            assert len(resolved) == 1
            assert resolved[0].error_hash == 'hash_resolved_recent'


class TestRunFullCleanup:
    """Tests for the full periodic cleanup."""
    
    def test_cleans_all_types(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """run_full_cleanup cleans every record type."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_full_cleanup()
            
            assert results['query_logs_deleted'] == 1
            assert results['email_logs_deleted'] == 1
            assert results['resolved_errors_deleted'] == 1
            assert 'executed_at' in results


class TestRunManualCleanup:
    """Tests for the manual cleanup (ignores retention)."""
    
    def test_deletes_all_logs(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """The manual cleanup deletes ALL logs."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_manual_cleanup()
            
            # All logs deleted (recent and old)
            assert results['query_logs_deleted'] == 2
            assert results['email_logs_deleted'] == 2
            assert QueryLog.query.count() == 0
            assert EmailLog.query.count() == 0
    
    def test_deletes_all_resolved_errors(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """The manual cleanup deletes ALL resolved errors."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_manual_cleanup()
            
            assert results['resolved_errors_deleted'] == 2
            
            # But the active ones stay
            active = ErrorRecord.query.filter_by(resolved_at=None).count()
            assert active == 2


class TestGetStats:
    """Tests for the cleanup statistics."""
    
    def test_returns_counts(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """get_stats returns the right counts."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert stats['counts']['query_logs'] == 2
            assert stats['counts']['email_logs'] == 2
            assert stats['counts']['total_errors'] == 4
            assert stats['counts']['active_errors'] == 2
            assert stats['counts']['resolved_errors'] == 2
    
    def test_returns_retention_config(self, app):
        """get_stats includes the retention configuration."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert 'retention_config' in stats
            assert 'log_retention_days' in stats['retention_config']
            assert 'email_log_retention_days' in stats['retention_config']
            assert 'resolved_errors_retention_days' in stats['retention_config']
    
    def test_empty_database(self, app):
        """get_stats works with an empty database."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert stats['counts']['query_logs'] == 0
            assert stats['counts']['total_errors'] == 0
            assert stats['oldest_records']['query_log'] is None
