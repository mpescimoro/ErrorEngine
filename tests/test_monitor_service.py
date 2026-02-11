"""
Test per il monitor_service: check_query, deduplicazione, risoluzione errori.
"""
import pytest
from unittest.mock import patch, MagicMock
from models import db, MonitoredQuery, ErrorRecord, QueryLog


class TestCheckQueryNewErrors:
    """Test per il rilevamento di nuovi errori."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_finds_new_errors(self, mock_execute, mock_email, app, sample_query):
        """Trova nuovi errori e li salva nel database."""
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
            
            # Verifica che gli errori siano stati salvati
            errors = ErrorRecord.query.filter_by(query_id=query.id).all()
            assert len(errors) == 2
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_no_rows_no_errors(self, mock_execute, mock_email, app, sample_query):
        """Nessuna riga restituita = nessun errore nuovo."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.return_value = (['ID', 'CODE'], [])
            
            query = MonitoredQuery.query.get(sample_query.id)
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'success'
            assert result['rows_returned'] == 0
            assert result['new_errors'] == 0


class TestCheckQueryDeduplication:
    """Test per la deduplicazione tramite hash."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_duplicate_not_counted(self, mock_execute, mock_email, app, sample_query):
        """Errore già presente non viene contato come nuovo."""
        with app.app_context():
            from monitor_service import monitor_service
            
            rows = [{'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error 1'}]
            mock_execute.return_value = (['ID', 'CODE', 'MESSAGE'], rows)
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # Prima esecuzione: trova 1 errore nuovo
            r1 = monitor_service.check_query(query, force=True)
            assert r1['new_errors'] == 1
            
            # Reset lock per rieseguire
            query.locked_at = None
            db.session.commit()
            
            # Seconda esecuzione con stessi dati: 0 nuovi
            r2 = monitor_service.check_query(query, force=True)
            assert r2['new_errors'] == 0
            assert r2['rows_returned'] == 1
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_occurrence_count_incremented(self, mock_execute, mock_email, app, sample_query):
        """Errore visto di nuovo incrementa il contatore."""
        with app.app_context():
            from monitor_service import monitor_service
            
            rows = [{'ID': '001', 'CODE': 'ERR001', 'MESSAGE': 'Error 1'}]
            mock_execute.return_value = (['ID', 'CODE', 'MESSAGE'], rows)
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # Prima esecuzione
            monitor_service.check_query(query, force=True)
            query.locked_at = None
            db.session.commit()
            
            # Seconda esecuzione
            monitor_service.check_query(query, force=True)
            
            error = ErrorRecord.query.filter_by(query_id=query.id).first()
            assert error.occurrence_count == 2


class TestCheckQueryResolution:
    """Test per la risoluzione automatica degli errori."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_error_resolved_when_missing(self, mock_execute, mock_email, app, sample_query):
        """Errore non più presente nella query viene marcato come risolto."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # Prima esecuzione: 2 errori
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
            
            # Seconda esecuzione: solo 1 errore (002 risolto)
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
        """Se la query non restituisce più righe, tutti gli errori vengono risolti."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_email.send_error_notification.return_value = {'success': True}
            
            query = MonitoredQuery.query.get(sample_query.id)
            
            # Prima: 2 errori
            mock_execute.return_value = (
                ['ID', 'CODE'],
                [{'ID': '001', 'CODE': 'ERR001'}, {'ID': '002', 'CODE': 'ERR002'}]
            )
            monitor_service.check_query(query, force=True)
            query.locked_at = None
            db.session.commit()
            
            # Dopo: 0 righe
            mock_execute.return_value = (['ID', 'CODE'], [])
            r2 = monitor_service.check_query(query, force=True)
            
            assert r2['resolved_errors'] == 2
            assert ErrorRecord.query.filter_by(query_id=query.id, resolved_at=None).count() == 0


class TestCheckQuerySchedule:
    """Test per il rispetto della fascia oraria."""
    
    @patch('monitor_service.execute_query_source')
    def test_skips_out_of_schedule(self, mock_execute, app, sample_query):
        """Query fuori fascia oraria viene skippata se non force."""
        with app.app_context():
            from monitor_service import monitor_service
            from datetime import time
            
            query = MonitoredQuery.query.get(sample_query.id)
            query.schedule_start_time = time(8, 0)
            query.schedule_end_time = time(10, 0)
            db.session.commit()
            
            # Mock _get_local_now per simulare orario fuori fascia
            with patch.object(query, '_get_local_now') as mock_now:
                from datetime import datetime as dt
                mock_now.return_value = dt(2025, 2, 10, 22, 0, 0)
                
                result = monitor_service.check_query(query, force=False)
            
            assert result['status'] == 'skipped'
            mock_execute.assert_not_called()
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_force_ignores_schedule(self, mock_execute, mock_email, app, sample_query):
        """Con force=True la fascia oraria viene ignorata."""
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
    """Test per la gestione errori durante l'esecuzione."""
    
    @patch('monitor_service.execute_query_source')
    def test_data_source_error(self, mock_execute, app, sample_query):
        """Errore nella sorgente dati viene gestito e loggato."""
        with app.app_context():
            from monitor_service import monitor_service
            
            mock_execute.side_effect = Exception("Connection refused")
            
            query = MonitoredQuery.query.get(sample_query.id)
            result = monitor_service.check_query(query, force=True)
            
            assert result['status'] == 'error'
            assert 'Connection refused' in result['error_message']


class TestCheckQueryStats:
    """Test per l'aggiornamento delle statistiche."""
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_updates_query_stats(self, mock_execute, mock_email, app, sample_query):
        """check_query aggiorna last_check_at e contatori."""
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
            
            # Ricarica dal DB
            db.session.refresh(query)
            assert query.last_check_at is not None
            assert query.total_errors_found == 1
    
    @patch('monitor_service.email_service')
    @patch('monitor_service.execute_query_source')
    def test_creates_execution_log(self, mock_execute, mock_email, app, sample_query):
        """check_query crea un QueryLog per ogni esecuzione."""
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
    """Test per get_active_errors."""
    
    def test_returns_only_active(self, app, sample_query, sample_errors_in_db):
        """Restituisce solo errori non risolti."""
        with app.app_context():
            from monitor_service import monitor_service
            
            errors = monitor_service.get_active_errors(sample_query.id)
            assert len(errors) == 2  # 2 attivi, 2 risolti nella fixture
    
    def test_returns_all_when_no_filter(self, app, sample_query, sample_errors_in_db):
        """Senza query_id restituisce tutti gli errori attivi."""
        with app.app_context():
            from monitor_service import monitor_service
            
            errors = monitor_service.get_active_errors()
            assert len(errors) == 2


class TestGetQueryStatus:
    """Test per get_query_status."""
    
    def test_returns_status(self, app, sample_query):
        """Restituisce lo stato della query."""
        with app.app_context():
            from monitor_service import monitor_service
            
            status = monitor_service.get_query_status(sample_query.id)
            assert status['name'] == 'Test Query'
            assert 'active_errors' in status
            assert 'is_active' in status
    
    def test_not_found(self, app):
        """Query non trovata restituisce errore."""
        with app.app_context():
            from monitor_service import monitor_service
            
            status = monitor_service.get_query_status(99999)
            assert 'error' in status
