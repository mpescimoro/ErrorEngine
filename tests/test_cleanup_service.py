"""
Test per il cleanup_service: pulizia basata su retention e manuale.
"""
import pytest
from models import db, ErrorRecord, QueryLog, EmailLog


class TestCleanupQueryLogs:
    """Test per la pulizia dei QueryLog."""
    
    def test_deletes_old_logs(self, app, sample_query, sample_logs_in_db):
        """Elimina i log più vecchi della retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            # Verifica setup: 2 log presenti
            assert QueryLog.query.count() == 2
            
            deleted = cleanup_service.cleanup_query_logs()
            
            # Il log vecchio (60 giorni) supera la retention default (30 giorni)
            assert deleted == 1
            assert QueryLog.query.count() == 1
    
    def test_keeps_recent_logs(self, app, sample_query, sample_logs_in_db):
        """Non elimina i log recenti."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_query_logs()
            
            remaining = QueryLog.query.first()
            assert remaining is not None
            assert remaining.rows_returned == 5  # Il log recente


class TestCleanupEmailLogs:
    """Test per la pulizia degli EmailLog."""
    
    def test_deletes_old_email_logs(self, app, sample_query, sample_logs_in_db):
        """Elimina gli email log più vecchi della retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            assert EmailLog.query.count() == 2
            
            deleted = cleanup_service.cleanup_email_logs()
            
            # Email log vecchio (120 giorni) supera retention default (90 giorni)
            assert deleted == 1
            assert EmailLog.query.count() == 1
    
    def test_keeps_recent_email_logs(self, app, sample_query, sample_logs_in_db):
        """Non elimina gli email log recenti."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_email_logs()
            
            remaining = EmailLog.query.first()
            assert remaining is not None
            assert remaining.subject == 'Test'  # L'email recente


class TestCleanupResolvedErrors:
    """Test per la pulizia degli errori risolti."""
    
    def test_deletes_old_resolved(self, app, sample_query, sample_errors_in_db):
        """Elimina errori risolti più vecchi della retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            # Fixture: 2 attivi + 2 risolti (1 recente, 1 vecchio 100 giorni)
            total_before = ErrorRecord.query.count()
            assert total_before == 4
            
            deleted = cleanup_service.cleanup_resolved_errors()
            
            # Solo il risolto vecchio (100 giorni) supera retention default (60 giorni)
            assert deleted == 1
    
    def test_never_deletes_active_errors(self, app, sample_query, sample_errors_in_db):
        """Non tocca mai gli errori attivi (non risolti)."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_resolved_errors()
            
            active = ErrorRecord.query.filter_by(resolved_at=None).count()
            assert active == 2  # Invariato
    
    def test_keeps_recent_resolved(self, app, sample_query, sample_errors_in_db):
        """Non elimina errori risolti di recente."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            cleanup_service.cleanup_resolved_errors()
            
            # Il risolto recente (1 ora fa) è ancora lì
            resolved = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None)
            ).all()
            assert len(resolved) == 1
            assert resolved[0].error_hash == 'hash_resolved_recent'


class TestRunFullCleanup:
    """Test per la pulizia completa periodica."""
    
    def test_cleans_all_types(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """run_full_cleanup pulisce tutti i tipi di record."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_full_cleanup()
            
            assert results['query_logs_deleted'] == 1
            assert results['email_logs_deleted'] == 1
            assert results['resolved_errors_deleted'] == 1
            assert 'executed_at' in results


class TestRunManualCleanup:
    """Test per la pulizia manuale (ignora retention)."""
    
    def test_deletes_all_logs(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """La pulizia manuale elimina TUTTI i log."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_manual_cleanup()
            
            # Tutti i log eliminati (sia recenti che vecchi)
            assert results['query_logs_deleted'] == 2
            assert results['email_logs_deleted'] == 2
            assert QueryLog.query.count() == 0
            assert EmailLog.query.count() == 0
    
    def test_deletes_all_resolved_errors(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """La pulizia manuale elimina TUTTI gli errori risolti."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            results = cleanup_service.run_manual_cleanup()
            
            assert results['resolved_errors_deleted'] == 2
            
            # Ma gli attivi restano
            active = ErrorRecord.query.filter_by(resolved_at=None).count()
            assert active == 2


class TestGetStats:
    """Test per le statistiche di cleanup."""
    
    def test_returns_counts(self, app, sample_query, sample_errors_in_db, sample_logs_in_db):
        """get_stats restituisce i conteggi corretti."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert stats['counts']['query_logs'] == 2
            assert stats['counts']['email_logs'] == 2
            assert stats['counts']['total_errors'] == 4
            assert stats['counts']['active_errors'] == 2
            assert stats['counts']['resolved_errors'] == 2
    
    def test_returns_retention_config(self, app):
        """get_stats include la configurazione retention."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert 'retention_config' in stats
            assert 'log_retention_days' in stats['retention_config']
            assert 'email_log_retention_days' in stats['retention_config']
            assert 'resolved_errors_retention_days' in stats['retention_config']
    
    def test_empty_database(self, app):
        """get_stats funziona con database vuoto."""
        with app.app_context():
            from cleanup_service import cleanup_service
            
            stats = cleanup_service.get_stats()
            
            assert stats['counts']['query_logs'] == 0
            assert stats['counts']['total_errors'] == 0
            assert stats['oldest_records']['query_log'] is None
