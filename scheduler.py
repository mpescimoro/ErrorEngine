"""
Scheduler per l'esecuzione periodica dei controlli.
Usa APScheduler integrato con Flask.
La logica di fascia oraria è gestita dal MonitorService.
"""
import logging
from flask_apscheduler import APScheduler
from models import MonitoredQuery

logger = logging.getLogger(__name__)
scheduler = APScheduler()


def init_scheduler(app):
    """
    Inizializza lo scheduler con l'app Flask.
    """
    # Evita doppia inizializzazione
    if scheduler.running:
        logger.info("Scheduler: già in esecuzione, skip")
        return
    
    scheduler.init_app(app)
    
    @scheduler.task('interval', id='check_queries', minutes=1, misfire_grace_time=60)
    def check_due_queries():
        """
        Ogni minuto, controlla quali query sono "scadute" e le esegue.
        La fascia oraria viene verificata dal MonitorService.
        """
        with app.app_context():
            from monitor_service import monitor_service
            
            queries = MonitoredQuery.query.filter_by(is_active=True).all()
            
            for query in queries:
                try:
                    should_run, reason = query.should_run_now()
                    
                    if should_run:
                        logger.debug(f"Scheduler: avvio controllo {query.name} ({reason})")
                        result = monitor_service.check_query(query)
                        
                        if result['status'] == 'skipped':
                            logger.debug(
                                f"Scheduler: {query.name} skipped - {result.get('error_message', '')}"
                            )
                        elif result['status'] == 'error':
                            logger.error(
                                f"Scheduler: errore in {query.name} - {result.get('error_message', '')}"
                            )
                    else:
                        logger.debug(f"Scheduler: {query.name} non dovuta - {reason}")
                            
                except Exception as e:
                    logger.error(f"Scheduler: eccezione in {query.name}: {e}")

    @scheduler.task('cron', id='cleanup_old_records', hour=3, minute=0, misfire_grace_time=3600)
    def cleanup_old_records():
        """
        Ogni giorno alle 3:00, esegue la pulizia dei record vecchi.
        """
        with app.app_context():
            from cleanup_service import cleanup_service
            
            try:
                result = cleanup_service.run_full_cleanup()
                logger.info(f"Cleanup schedulato completato: {result}")
            except Exception as e:
                logger.error(f"Scheduler: errore nel cleanup: {e}")
    
    scheduler.start()
    logger.info("Scheduler avviato (check_queries ogni 1 min, cleanup alle 03:00)")
    
    return scheduler


def trigger_immediate_check(query_id: int):
    """
    Esegue immediatamente il controllo di una query specifica (ignora fascia oraria).
    """
    from flask import current_app
    
    with current_app.app_context():
        from monitor_service import monitor_service
        query = MonitoredQuery.query.get(query_id)
        if query:
            return monitor_service.check_query(query, force=True)
        return {'error': 'Query non trovata'}
