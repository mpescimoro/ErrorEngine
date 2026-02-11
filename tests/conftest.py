"""
Configurazione pytest e fixtures condivise.
"""
import pytest
import sys
import os

# Aggiungi la directory root al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from app import create_app
from models import (db, MonitoredQuery, RoutingRule, RoutingCondition,
                    ErrorRecord, QueryLog, EmailLog, DatabaseConnection,
                    NotificationChannel)
from utils import get_utc_now


@pytest.fixture(scope='function')
def app():
    """Crea un'istanza dell'applicazione per i test."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope='function')
def client(app):
    """Client di test per le richieste HTTP."""
    return app.test_client()


@pytest.fixture(scope='function')
def runner(app):
    """Runner per i comandi CLI."""
    return app.test_cli_runner()


@pytest.fixture(scope='function')
def sample_query(app):
    """Crea una query di esempio per i test."""
    with app.app_context():
        query = MonitoredQuery(
            name='Test Query',
            description='Query di test',
            source_type='oracle',
            sql_query='SELECT * FROM TEST_TABLE WHERE STATUS = \'ERROR\'',
            key_fields='ID,CODE',
            email_subject='[Test] Errori: {query_name}',
            email_recipients='test@example.com',
            check_interval_minutes=15,
            is_active=True
        )
        db.session.add(query)
        db.session.commit()
        
        # Refresh per avere l'ID
        db.session.refresh(query)
        yield query


@pytest.fixture(scope='function')
def sample_query_with_routing(app, sample_query):
    """Crea una query con regole di routing."""
    with app.app_context():
        # Ricarica la query nel contesto corrente
        query = MonitoredQuery.query.get(sample_query.id)
        query.routing_enabled = True
        query.routing_default_recipients = 'default@example.com'
        
        # Regola 1: errori critici
        rule1 = RoutingRule(
            query_id=query.id,
            name='Errori Critici',
            condition_logic='AND',
            recipients='critical@example.com',
            priority=0,
            stop_on_match=True,
            is_active=True
        )
        db.session.add(rule1)
        db.session.flush()
        
        cond1 = RoutingCondition(
            rule_id=rule1.id,
            field_name='SEVERITY',
            operator='equals',
            value='CRITICAL',
            case_sensitive=False
        )
        db.session.add(cond1)
        
        # Regola 2: errori warning
        rule2 = RoutingRule(
            query_id=query.id,
            name='Warning',
            condition_logic='AND',
            recipients='warning@example.com',
            priority=1,
            is_active=True
        )
        db.session.add(rule2)
        db.session.flush()
        
        cond2 = RoutingCondition(
            rule_id=rule2.id,
            field_name='SEVERITY',
            operator='equals',
            value='WARNING',
            case_sensitive=False
        )
        db.session.add(cond2)
        
        db.session.commit()
        db.session.refresh(query)
        yield query


@pytest.fixture
def sample_error_data():
    """Dati di esempio per errori."""
    return [
        {'ID': '001', 'CODE': 'ERR001', 'SEVERITY': 'CRITICAL', 'MESSAGE': 'Critical error'},
        {'ID': '002', 'CODE': 'ERR002', 'SEVERITY': 'WARNING', 'MESSAGE': 'Warning message'},
        {'ID': '003', 'CODE': 'ERR003', 'SEVERITY': 'INFO', 'MESSAGE': 'Info message'},
    ]


@pytest.fixture(scope='function')
def sample_errors_in_db(app, sample_query):
    """Crea errori di esempio nel database (attivi + risolti)."""
    with app.app_context():
        now = get_utc_now()
        
        # Errore attivo
        e1 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_active_001',
            email_sent=True,
            email_sent_at=now - timedelta(hours=2),
        )
        e1.set_error_data({'ID': '001', 'CODE': 'ERR001', 'SEVERITY': 'CRITICAL'})
        
        # Errore attivo senza email
        e2 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_active_002',
            email_sent=False,
        )
        e2.set_error_data({'ID': '002', 'CODE': 'ERR002', 'SEVERITY': 'WARNING'})
        
        # Errore risolto di recente
        e3 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_resolved_recent',
            email_sent=True,
            resolved_at=now - timedelta(hours=1),
        )
        e3.set_error_data({'ID': '003', 'CODE': 'ERR003', 'SEVERITY': 'INFO'})
        
        # Errore risolto vecchio (100 giorni fa)
        e4 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_resolved_old',
            email_sent=True,
            resolved_at=now - timedelta(days=100),
        )
        e4.set_error_data({'ID': '004', 'CODE': 'ERR004', 'SEVERITY': 'INFO'})
        
        db.session.add_all([e1, e2, e3, e4])
        db.session.commit()
        yield {'active': [e1, e2], 'resolved': [e3, e4]}


@pytest.fixture(scope='function')
def sample_logs_in_db(app, sample_query):
    """Crea log di esempio nel database (recenti + vecchi)."""
    with app.app_context():
        now = get_utc_now()
        
        log_recent = QueryLog(
            query_id=sample_query.id,
            status='success',
            rows_returned=5,
            new_errors=2,
            resolved_errors=0,
            emails_sent=1,
            execution_time_ms=150,
        )
        # Force timestamp recente
        db.session.add(log_recent)
        db.session.flush()
        log_recent.executed_at = now - timedelta(minutes=15)
        
        log_old = QueryLog(
            query_id=sample_query.id,
            status='success',
            rows_returned=3,
            new_errors=1,
            resolved_errors=1,
            emails_sent=1,
            execution_time_ms=200,
        )
        db.session.add(log_old)
        db.session.flush()
        log_old.executed_at = now - timedelta(days=60)
        
        email_recent = EmailLog(
            query_id=sample_query.id,
            recipients='test@example.com',
            subject='Test',
            email_type='new_errors',
            error_count=2,
            status='sent',
        )
        db.session.add(email_recent)
        db.session.flush()
        email_recent.sent_at = now - timedelta(minutes=15)
        
        email_old = EmailLog(
            query_id=sample_query.id,
            recipients='test@example.com',
            subject='Test Old',
            email_type='new_errors',
            error_count=1,
            status='sent',
        )
        db.session.add(email_old)
        db.session.flush()
        email_old.sent_at = now - timedelta(days=120)
        
        db.session.commit()
        yield {
            'logs': [log_recent, log_old],
            'emails': [email_recent, email_old]
        }


@pytest.fixture(scope='function')
def sample_connection(app):
    """Crea una connessione database di esempio."""
    with app.app_context():
        conn = DatabaseConnection(
            name='Test SQLite',
            db_type='sqlite',
            host='',
            database=':memory:',
            is_active=True
        )
        db.session.add(conn)
        db.session.commit()
        db.session.refresh(conn)
        yield conn
