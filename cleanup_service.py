"""
Cleanup Service - Pulizia periodica di log ed errori risolti.
Gestisce la retention configurata in config.py.
"""
import logging
from datetime import datetime, timedelta
from flask import current_app
from models import db, QueryLog, EmailLog, ErrorRecord

logger = logging.getLogger(__name__)


class CleanupService:
    """
    Servizio per la pulizia periodica del database.
    Elimina record vecchi in base alla retention configurata.
    """
    
    def __init__(self, app=None):
        self.app = app
    
    def init_app(self, app):
        """Inizializza l'estensione Flask."""
        self.app = app
        app.extensions['cleanup'] = self
    
    def cleanup_query_logs(self) -> int:
        """
        Elimina i log delle query più vecchi della retention configurata.
        
        Returns:
            int: Numero di record eliminati
        """
        retention_days = current_app.config.get('LOG_RETENTION_DAYS', 30)
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
        
        try:
            count = QueryLog.query.filter(
                QueryLog.executed_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: eliminati {count} QueryLog più vecchi di {retention_days} giorni")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"Errore cleanup QueryLog: {e}")
            return 0
    
    def cleanup_email_logs(self) -> int:
        """
        Elimina i log delle email più vecchi della retention configurata.
        
        Returns:
            int: Numero di record eliminati
        """
        retention_days = current_app.config.get('EMAIL_LOG_RETENTION_DAYS', 90)
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
        
        try:
            count = EmailLog.query.filter(
                EmailLog.sent_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: eliminati {count} EmailLog più vecchi di {retention_days} giorni")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"Errore cleanup EmailLog: {e}")
            return 0
    
    def cleanup_resolved_errors(self) -> int:
        """
        Elimina gli errori risolti più vecchi della retention configurata.
        
        Returns:
            int: Numero di record eliminati
        """
        retention_days = current_app.config.get('RESOLVED_ERRORS_RETENTION_DAYS', 60)
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
        
        try:
            count = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None),
                ErrorRecord.resolved_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: eliminati {count} ErrorRecord risolti più vecchi di {retention_days} giorni")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"Errore cleanup ErrorRecord: {e}")
            return 0
    
    def run_full_cleanup(self) -> dict:
        """
        Esegue la pulizia completa di tutti i tipi di record.
        
        Returns:
            dict: Statistiche della pulizia
        """
        logger.info("Avvio pulizia periodica database")
        
        results = {
            'query_logs_deleted': self.cleanup_query_logs(),
            'email_logs_deleted': self.cleanup_email_logs(),
            'resolved_errors_deleted': self.cleanup_resolved_errors(),
            'executed_at': datetime.utcnow().isoformat()
        }
        
        total = sum([
            results['query_logs_deleted'],
            results['email_logs_deleted'],
            results['resolved_errors_deleted']
        ])
        
        if total > 0:
            logger.info(f"Pulizia completata: {total} record totali eliminati")
        else:
            logger.debug("Pulizia completata: nessun record da eliminare")
        
        return results
    
    def run_manual_cleanup(self) -> dict:
        """
        Esegue pulizia COMPLETA per uso manuale.
        Elimina TUTTI i log e gli errori risolti (non rispetta retention).
        Gli errori attivi NON vengono toccati.
        
        Returns:
            dict: Statistiche della pulizia
        """
        logger.info("Avvio pulizia manuale COMPLETA")
        
        results = {
            'query_logs_deleted': 0,
            'email_logs_deleted': 0,
            'resolved_errors_deleted': 0,
            'executed_at': datetime.utcnow().isoformat()
        }
        
        try:
            # Elimina TUTTI i QueryLog
            results['query_logs_deleted'] = QueryLog.query.delete(synchronize_session=False)
            
            # Elimina TUTTI gli EmailLog
            results['email_logs_deleted'] = EmailLog.query.delete(synchronize_session=False)
            
            # Elimina TUTTI gli errori risolti (ma NON quelli attivi!)
            results['resolved_errors_deleted'] = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None)
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            total = sum([
                results['query_logs_deleted'],
                results['email_logs_deleted'],
                results['resolved_errors_deleted']
            ])
            
            logger.info(f"Pulizia manuale completata: {total} record totali eliminati")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Errore pulizia manuale: {e}")
            raise
        
        return results
    
    def get_stats(self) -> dict:
        """
        Restituisce statistiche sui record nel database.
        
        Returns:
            dict: Conteggi e date
        """
        retention_config = {
            'log_retention_days': current_app.config.get('LOG_RETENTION_DAYS', 30),
            'email_log_retention_days': current_app.config.get('EMAIL_LOG_RETENTION_DAYS', 90),
            'resolved_errors_retention_days': current_app.config.get('RESOLVED_ERRORS_RETENTION_DAYS', 60)
        }
        
        # Conteggi totali
        total_query_logs = QueryLog.query.count()
        total_email_logs = EmailLog.query.count()
        total_errors = ErrorRecord.query.count()
        resolved_errors = ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count()
        active_errors = total_errors - resolved_errors
        
        # Record più vecchi
        oldest_query_log = QueryLog.query.order_by(QueryLog.executed_at.asc()).first()
        oldest_email_log = EmailLog.query.order_by(EmailLog.sent_at.asc()).first()
        
        return {
            'retention_config': retention_config,
            'counts': {
                'query_logs': total_query_logs,
                'email_logs': total_email_logs,
                'total_errors': total_errors,
                'active_errors': active_errors,
                'resolved_errors': resolved_errors
            },
            'oldest_records': {
                'query_log': oldest_query_log.executed_at.isoformat() if oldest_query_log else None,
                'email_log': oldest_email_log.sent_at.isoformat() if oldest_email_log else None
            }
        }


# Singleton
cleanup_service = CleanupService()
