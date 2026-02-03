"""
Servizio principale di monitoraggio.
Gestisce l'esecuzione periodica delle query, routing condizionale e reminder.
"""
import logging
from datetime import datetime, timedelta
import time
from collections import defaultdict
from models import db, MonitoredQuery, ErrorRecord, QueryLog
from data_sources import execute_query_source, test_query_source, get_query_fields
from routing_service import apply_routing_rules, get_routing_summary
from email_service import email_service

logger = logging.getLogger(__name__)


class MonitorService:
    """
    Orchestrazione del monitoraggio errori.
    Supporta sorgenti multiple, routing condizionale e reminder.
    """
    
    def __init__(self, app=None):
        self.app = app
        
    def init_app(self, app):
        """Inizializza l'estensione Flask."""
        self.app = app
        app.extensions['monitor'] = self
    
    def check_query(self, query: MonitoredQuery, force: bool = False) -> dict:
        """
        Esegue il controllo completo per una singola consultazione.
        
        1. Verifica se è nella fascia oraria (se non force)
        2. Esegue la query sulla sorgente dati configurata
        3. Calcola gli hash degli errori trovati
        4. Confronta con il DB di appoggio
        5. Applica routing condizionale
        6. Invia email per nuovi errori
        7. Invia reminder per errori non risolti
        
        Args:
            query: MonitoredQuery da eseguire
            force: Se True, ignora la fascia oraria
            
        Returns:
            dict con statistiche dell'esecuzione
        """
        start_time = time.time()
        result = {
            'query_id': query.id,
            'query_name': query.name,
            'status': 'success',
            'rows_returned': 0,
            'new_errors': 0,
            'resolved_errors': 0,
            'reminders_sent': 0,
            'emails_sent': 0,
            'error_message': None
        }
        
        try:
            # 1. Verifica fascia oraria
            if not force and not query.is_in_schedule():
                # Non logghiamo gli skip - riempirebbe inutilmente i log
                result['status'] = 'skipped'
                result['error_message'] = 'Fuori dalla fascia oraria configurata'
                return result

            # LOCK ATOMICO: evita esecuzioni concorrenti della stessa query
            # L'UPDATE è atomico in SQLite - solo un processo può acquisire il lock
            LOCK_TTL = timedelta(minutes=5)
            now = datetime.utcnow()
            lock_expired_before = now - LOCK_TTL
            
            updated = (
                db.session.query(MonitoredQuery)
                .filter(
                    MonitoredQuery.id == query.id,
                    db.or_(
                        MonitoredQuery.locked_at == None,
                        MonitoredQuery.locked_at < lock_expired_before
                    )
                )
                .update(
                    {MonitoredQuery.locked_at: now},
                    synchronize_session=False
                )
            )
            db.session.commit()
            
            if updated == 0:
                # Lock già preso da un altro processo
                result['status'] = 'skipped'
                result['error_message'] = 'Query già in esecuzione da altro processo'
                return result

            # 2. Esegui la query sulla sorgente configurata
            logger.info(f"Esecuzione query: {query.name} (source: {query.source_type})")
            columns, rows = execute_query_source(query)
            result['rows_returned'] = len(rows)
            
            # 3. Ottieni i campi chiave
            key_fields = query.get_key_fields_list()
            
            # 4. Calcola gli hash degli errori attuali
            current_errors = {}
            for row in rows:
                error_hash = ErrorRecord.calculate_hash(row, key_fields)
                current_errors[error_hash] = row
            
            # 5. Recupera errori esistenti non risolti
            existing_errors = {
                e.error_hash: e 
                for e in ErrorRecord.query.filter_by(
                    query_id=query.id, 
                    resolved_at=None
                ).all()
            }
            
            # 6. Trova nuovi, risolti, continuano
            new_error_hashes = set(current_errors.keys()) - set(existing_errors.keys())
            resolved_hashes = set(existing_errors.keys()) - set(current_errors.keys())
            continuing_hashes = set(current_errors.keys()) & set(existing_errors.keys())
            
            # 7. Gestisci nuovi errori
            new_errors_data = []
            for hash_val in new_error_hashes:
                error_data = current_errors[hash_val]
                new_record = ErrorRecord(
                    query_id=query.id,
                    error_hash=hash_val,
                    email_sent=False
                )
                new_record.set_error_data(error_data)
                db.session.add(new_record)
                new_errors_data.append(error_data)
                result['new_errors'] += 1
            
            # 8. Marca errori risolti
            for hash_val in resolved_hashes:
                error = existing_errors[hash_val]
                error.resolved_at = datetime.utcnow()
                result['resolved_errors'] += 1
            
            # 9. Aggiorna errori esistenti ancora presenti
            for hash_val in continuing_hashes:
                error = existing_errors[hash_val]
                error.last_seen_at = datetime.utcnow()
                error.occurrence_count += 1
            
            # Commit parziale per avere gli ID
            db.session.flush()
            
            # 10. Invia notifiche per nuovi errori
            if new_errors_data:
                emails_sent = self._send_notifications(
                    query, new_errors_data, columns, email_type='new_errors'
                )
                result['emails_sent'] += emails_sent
                
                # Marca errori come notificati
                if emails_sent > 0:
                    for hash_val in new_error_hashes:
                        new_error = ErrorRecord.query.filter_by(
                            query_id=query.id,
                            error_hash=hash_val
                        ).first()
                        if new_error:
                            new_error.email_sent = True
                            new_error.email_sent_at = datetime.utcnow()
            
            # 11. Gestisci reminder per errori non risolti
            if query.reminder_enabled:
                reminders_sent = self._process_reminders(query, columns)
                result['reminders_sent'] = reminders_sent
                result['emails_sent'] += reminders_sent

            # 12. Aggiorna statistiche query
            query.last_check_at = datetime.utcnow()
            query.locked_at = None  # Rilascia il lock
            query.total_errors_found += result['new_errors']
            query.total_emails_sent += result['emails_sent']
            if result['new_errors'] > 0:
                query.last_error_at = datetime.utcnow()
            
            db.session.commit()

        except Exception as e:
            result['status'] = 'error'
            result['error_message'] = str(e)
            logger.error(f"Errore durante il controllo di {query.name}: {e}")
            db.session.rollback()
            
            # Rilascia il lock anche in caso di errore
            try:
                query.locked_at = None
                db.session.commit()
            except:
                pass
        
        # 13. Log dell'esecuzione
        self._log_execution(query, result, start_time)
        
        logger.info(
            f"Query {query.name} completata: "
            f"{result['rows_returned']} righe, "
            f"{result['new_errors']} nuovi errori, "
            f"{result['resolved_errors']} risolti, "
            f"{result['reminders_sent']} reminder"
        )
        
        return result
    
    def _send_notifications(self, query: MonitoredQuery, errors: list, 
                           columns: list, email_type: str = 'new_errors') -> int:
        """
        Invia notifiche applicando il routing condizionale.
        
        Returns:
            int: Numero di email inviate con successo
        """
        if not errors:
            return 0
        
        # Applica routing
        routing_result = apply_routing_rules(query, errors)
        
        if not routing_result:
            logger.warning(f"Query {query.name}: nessun destinatario per {len(errors)} errori")
            return 0
        
        emails_sent = 0
        
        # Invia email per ogni destinatario/gruppo
        for recipients, recipient_errors in routing_result.items():
            if not recipient_errors:
                continue
            
            # Normalizza recipients a lista
            if isinstance(recipients, tuple):
                recipients_list = list(recipients)
            else:
                recipients_list = [recipients]
            
            # Determina se aggregare o inviare singole
            if query.routing_aggregation == 'per_recipient' or not query.routing_enabled:
                # Una email con tutti gli errori del destinatario
                email_result = email_service.send_error_notification(
                    query=query,
                    errors=recipient_errors,
                    columns=columns,
                    recipients_override=recipients_list,
                    email_type=email_type
                )
                if email_result.get('success'):
                    emails_sent += 1
            else:
                # Una email per errore (per_error)
                for error in recipient_errors:
                    email_result = email_service.send_error_notification(
                        query=query,
                        errors=[error],
                        columns=columns,
                        recipients_override=recipients_list,
                        email_type=email_type
                    )
                    if email_result.get('success'):
                        emails_sent += 1
        
            # Invia a canali notifica (Webhook, Telegram, Teams)
            if query.notification_channels:
                try:
                    from notification_service import notification_service
                    notification_service.send_to_all_channels(query, errors)
                except Exception as e:
                    logger.error(f"Errore notification channels: {e}")
        
        return emails_sent
    
    def _process_reminders(self, query: MonitoredQuery, columns: list) -> int:
        """
        Processa i reminder per errori non risolti.
        
        Returns:
            int: Numero di reminder inviati
        """
        # Trova errori che necessitano reminder
        errors_needing_reminder = []
        error_records = []
        
        for error in ErrorRecord.query.filter_by(
            query_id=query.id,
            resolved_at=None,
            email_sent=True
        ).all():
            if error.needs_reminder(query):
                errors_needing_reminder.append(error.get_error_data())
                error_records.append(error)
        
        if not errors_needing_reminder:
            return 0
        
        # Invia reminder
        emails_sent = self._send_notifications(
            query, errors_needing_reminder, columns, email_type='reminder'
        )
        
        # Aggiorna contatori reminder
        if emails_sent > 0:
            for error in error_records:
                error.last_reminder_at = datetime.utcnow()
                error.reminder_count += 1
            db.session.commit()
        
        return emails_sent
    
    def _log_execution(self, query: MonitoredQuery, result: dict, start_time: float):
        """Registra l'esecuzione nel log."""
        execution_time = int((time.time() - start_time) * 1000)
        
        log_entry = QueryLog(
            query_id=query.id,
            status=result['status'],
            rows_returned=result['rows_returned'],
            new_errors=result['new_errors'],
            resolved_errors=result['resolved_errors'],
            reminders_sent=result.get('reminders_sent', 0),
            emails_sent=result['emails_sent'],
            execution_time_ms=execution_time,
            error_message=result['error_message']
        )
        db.session.add(log_entry)
        
        try:
            db.session.commit()
        except Exception as e:
            logger.error(f"Errore salvataggio log: {e}")
            db.session.rollback()
    
    def check_all_active_queries(self) -> list:
        """
        Esegue il controllo per tutte le query attive.
        
        Returns:
            list di dict con i risultati di ogni query
        """
        results = []
        active_queries = MonitoredQuery.query.filter_by(is_active=True).all()
        
        logger.info(f"Avvio controllo di {len(active_queries)} query attive")
        
        for query in active_queries:
            result = self.check_query(query)
            results.append(result)
        
        return results
    
    def get_query_status(self, query_id: int) -> dict:
        """Ottiene lo stato attuale di una query."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return {'error': 'Query non trovata'}
        
        # Conta errori attivi
        active_errors = ErrorRecord.query.filter_by(
            query_id=query_id,
            resolved_at=None
        ).count()
        
        # Conta errori con reminder pendenti
        pending_reminders = 0
        if query.reminder_enabled:
            for error in ErrorRecord.query.filter_by(
                query_id=query_id,
                resolved_at=None,
                email_sent=True
            ).all():
                if error.needs_reminder(query):
                    pending_reminders += 1
        
        # Ultimo log
        last_log = QueryLog.query.filter_by(
            query_id=query_id
        ).order_by(QueryLog.executed_at.desc()).first()
        
        return {
            'query_id': query.id,
            'name': query.name,
            'source_type': query.source_type,
            'is_active': query.is_active,
            'is_in_schedule': query.is_in_schedule(),
            'last_check_at': query.last_check_at,
            'last_error_at': query.last_error_at,
            'active_errors': active_errors,
            'pending_reminders': pending_reminders,
            'total_errors_found': query.total_errors_found,
            'total_emails_sent': query.total_emails_sent,
            'routing_enabled': query.routing_enabled,
            'routing_rules_count': len(query.routing_rules),
            'last_execution': {
                'status': last_log.status if last_log else None,
                'rows_returned': last_log.rows_returned if last_log else 0,
                'execution_time_ms': last_log.execution_time_ms if last_log else 0,
                'executed_at': last_log.executed_at if last_log else None
            } if last_log else None
        }
    
    def get_active_errors(self, query_id: int = None, include_data: bool = True) -> list:
        """
        Ottiene tutti gli errori attivi (non risolti).
        
        Args:
            query_id: Opzionale, filtra per query specifica
            include_data: Se True, include i dati completi dell'errore
        """
        errors_query = ErrorRecord.query.filter_by(resolved_at=None)
        
        if query_id:
            errors_query = errors_query.filter_by(query_id=query_id)
        
        errors = errors_query.order_by(ErrorRecord.first_seen_at.desc()).all()
        
        return [{
            'id': e.id,
            'query_id': e.query_id,
            'query_name': e.monitored_query.name,
            'error_hash': e.error_hash[:12] + '...',
            'error_data': e.get_error_data() if include_data else None,
            'first_seen_at': e.first_seen_at,
            'last_seen_at': e.last_seen_at,
            'occurrence_count': e.occurrence_count,
            'email_sent': e.email_sent,
            'email_sent_at': e.email_sent_at,
            'reminder_count': e.reminder_count,
            'last_reminder_at': e.last_reminder_at
        } for e in errors]
    
    def test_query_connection(self, query_id: int) -> dict:
        """Testa la connessione/query di una consultazione."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return {'success': False, 'message': 'Query non trovata'}
        
        return test_query_source(query)
    
    def get_query_available_fields(self, query_id: int) -> list:
        """Ottiene i campi disponibili per una query (per configurazione routing)."""
        query = MonitoredQuery.query.get(query_id)
        if not query:
            return []
        
        return get_query_fields(query)


# Singleton
monitor_service = MonitorService()
