"""
Cleanup Service - Periodic cleanup of logs and resolved errors.
Applies the retention configured in config.py.
"""
import logging
from datetime import timedelta
from utils import get_utc_now
from flask import current_app
from models import db, QueryLog, EmailLog, ErrorRecord

logger = logging.getLogger(__name__)


class CleanupService:
    """
    Periodic database cleanup service.
    Deletes old records according to the configured retention.
    """
    
    def __init__(self, app=None):
        self.app = app
    
    def init_app(self, app):
        """Initializes the Flask extension."""
        self.app = app
        app.extensions['cleanup'] = self
    
    def cleanup_query_logs(self) -> int:
        """
        Deletes query logs older than the configured retention.
        
        Returns:
            int: Number of deleted records
        """
        retention_days = current_app.config.get('LOG_RETENTION_DAYS', 30)
        cutoff_date = get_utc_now() - timedelta(days=retention_days)
        
        try:
            count = QueryLog.query.filter(
                QueryLog.executed_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: deleted {count} QueryLog older than {retention_days} days")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"QueryLog cleanup error: {e}")
            return 0
    
    def cleanup_email_logs(self) -> int:
        """
        Deletes email logs older than the configured retention.
        
        Returns:
            int: Number of deleted records
        """
        retention_days = current_app.config.get('EMAIL_LOG_RETENTION_DAYS', 90)
        cutoff_date = get_utc_now() - timedelta(days=retention_days)
        
        try:
            count = EmailLog.query.filter(
                EmailLog.sent_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: deleted {count} EmailLog older than {retention_days} days")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"EmailLog cleanup error: {e}")
            return 0
    
    def cleanup_resolved_errors(self) -> int:
        """
        Deletes resolved errors older than the configured retention.
        
        Returns:
            int: Number of deleted records
        """
        retention_days = current_app.config.get('RESOLVED_ERRORS_RETENTION_DAYS', 60)
        cutoff_date = get_utc_now() - timedelta(days=retention_days)
        
        try:
            count = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None),
                ErrorRecord.resolved_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            if count > 0:
                logger.info(f"Cleanup: deleted {count} resolved ErrorRecord older than {retention_days} days")
            
            return count
        except Exception as e:
            db.session.rollback()
            logger.error(f"ErrorRecord cleanup error: {e}")
            return 0
    
    def run_full_cleanup(self) -> dict:
        """
        Runs the full cleanup of every record type.
        
        Returns:
            dict: Cleanup statistics
        """
        logger.info("Starting periodic database cleanup")
        
        results = {
            'query_logs_deleted': self.cleanup_query_logs(),
            'email_logs_deleted': self.cleanup_email_logs(),
            'resolved_errors_deleted': self.cleanup_resolved_errors(),
            'executed_at': get_utc_now().isoformat()
        }
        
        total = sum([
            results['query_logs_deleted'],
            results['email_logs_deleted'],
            results['resolved_errors_deleted']
        ])
        
        if total > 0:
            logger.info(f"Cleanup done: {total} records deleted in total")
        else:
            logger.debug("Cleanup done: nothing to delete")
        
        return results
    
    def run_manual_cleanup(self) -> dict:
        """
        Runs a FULL cleanup, for manual use.
        Deletes ALL logs and resolved errors (ignores retention).
        Active errors are NOT touched.
        
        Returns:
            dict: Cleanup statistics
        """
        logger.info("Starting FULL manual cleanup")
        
        results = {
            'query_logs_deleted': 0,
            'email_logs_deleted': 0,
            'resolved_errors_deleted': 0,
            'executed_at': get_utc_now().isoformat()
        }
        
        try:
            # Delete ALL QueryLog
            results['query_logs_deleted'] = QueryLog.query.delete(synchronize_session=False)
            
            # Delete ALL EmailLog
            results['email_logs_deleted'] = EmailLog.query.delete(synchronize_session=False)
            
            # Delete ALL resolved errors (but NOT the active ones!)
            results['resolved_errors_deleted'] = ErrorRecord.query.filter(
                ErrorRecord.resolved_at.isnot(None)
            ).delete(synchronize_session=False)
            
            db.session.commit()
            
            total = sum([
                results['query_logs_deleted'],
                results['email_logs_deleted'],
                results['resolved_errors_deleted']
            ])
            
            logger.info(f"Manual cleanup done: {total} records deleted in total")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Manual cleanup error: {e}")
            raise
        
        return results
    
    def get_stats(self) -> dict:
        """
        Returns statistics about the records in the database.
        
        Returns:
            dict: Counts and dates
        """
        retention_config = {
            'log_retention_days': current_app.config.get('LOG_RETENTION_DAYS', 30),
            'email_log_retention_days': current_app.config.get('EMAIL_LOG_RETENTION_DAYS', 90),
            'resolved_errors_retention_days': current_app.config.get('RESOLVED_ERRORS_RETENTION_DAYS', 60)
        }
        
        # Total counts
        total_query_logs = QueryLog.query.count()
        total_email_logs = EmailLog.query.count()
        total_errors = ErrorRecord.query.count()
        resolved_errors = ErrorRecord.query.filter(ErrorRecord.resolved_at.isnot(None)).count()
        active_errors = total_errors - resolved_errors
        
        # Oldest records
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
