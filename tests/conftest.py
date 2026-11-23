"""
Shared fixtures: the app on an in-memory database (or a file), a sample query with its errors and logs.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from app import create_app
from models import (db, MonitoredQuery, RoutingRule, RoutingCondition,
                    ErrorRecord, QueryLog, EmailLog, DatabaseConnection)
from utils import get_utc_now


@pytest.fixture(scope='function')
def app():
    """The app on an in-memory SQLite database, new for each test."""
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def file_app(tmp_path, monkeypatch):
    """
    App on a SQLite file, as in production: each thread gets its own connection.
    For tests where other threads use the database: the in-memory one is a single
    connection shared by all threads, and sqlite3 fails when two use it at once.
    """
    import config
    monkeypatch.setattr(config.TestingConfig, 'SQLALCHEMY_DATABASE_URI',
                        f"sqlite:///{(tmp_path / 'errorengine.db').as_posix()}")
    app = create_app('testing')
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def scheduler_pool():
    """The scheduler module with a new thread pool, shut down at the end."""
    import scheduler
    scheduler._executor = None
    scheduler._in_flight.clear()
    yield scheduler
    scheduler.shutdown(wait=True)


@pytest.fixture(scope='function')
def client(app):
    return app.test_client()


@pytest.fixture(scope='function')
def sample_query(app):
    """'Test Query': keyed on ID,CODE, to test@example.com, every 15 minutes."""
    with app.app_context():
        query = MonitoredQuery(
            name='Test Query',
            description='Test query',
            source_type='oracle',
            sql_query='SELECT * FROM TEST_TABLE WHERE STATUS = \'ERROR\'',
            key_fields='ID,CODE',
            email_subject='[Test] Errors: {query_name}',
            email_recipients='test@example.com',
            check_interval_minutes=15,
            is_active=True
        )
        db.session.add(query)
        db.session.commit()
        db.session.refresh(query)
        yield query


@pytest.fixture(scope='function')
def sample_query_with_routing(app, sample_query):
    """sample_query with routing: CRITICAL to critical@ (the rules stop there), WARNING to warning@, the others
    to default@example.com."""
    with app.app_context():
        query = db.session.get(MonitoredQuery, sample_query.id)
        query.routing_enabled = True
        query.routing_default_recipients = 'default@example.com'
        
        rule1 = RoutingRule(
            query_id=query.id,
            name='Critical errors',
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


@pytest.fixture(scope='function')
def sample_errors_in_db(app, sample_query):
    """Errors of sample_query: 2 active (one notified), one resolved an hour ago, one 100 days ago."""
    with app.app_context():
        now = get_utc_now()
        
        e1 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_active_001',
            email_sent=True,
            email_sent_at=now - timedelta(hours=2),
        )
        e1.set_error_data({'ID': '001', 'CODE': 'ERR001', 'SEVERITY': 'CRITICAL'})
        
        e2 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_active_002',
            email_sent=False,
        )
        e2.set_error_data({'ID': '002', 'CODE': 'ERR002', 'SEVERITY': 'WARNING'})
        
        e3 = ErrorRecord(
            query_id=sample_query.id,
            error_hash='hash_resolved_recent',
            email_sent=True,
            resolved_at=now - timedelta(hours=1),
        )
        e3.set_error_data({'ID': '003', 'CODE': 'ERR003', 'SEVERITY': 'INFO'})
        
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
    """A run log and an email of 15 minutes ago, and older ones: a run of 60 days ago, an email of 120."""
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
    """'Test SQLite': a database in memory, so the queries on it really run."""
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
