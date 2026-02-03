"""
Configurazione pytest e fixtures condivise.
"""
import pytest
import sys
import os

# Aggiungi la directory root al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db, MonitoredQuery, RoutingRule, RoutingCondition, ErrorRecord


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
