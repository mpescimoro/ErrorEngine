"""
Application Factory - ErrorEngine Monitoring System
"""
import os
import logging
from flask import Flask
from config import config

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(config_name=None):
    """Creates and configures the application."""
    
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    app = Flask(__name__)
    # An instance, not the class: this way ProductionConfig's SECRET_KEY @property is read
    app.config.from_object(config[config_name]())

    # SQLite: with the scheduler running in parallel, several threads write to the internal DB,
    # so wait up to 30 s for the lock instead of failing after 5
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        engine_options = app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
        engine_options.setdefault('connect_args', {}).setdefault('timeout', 30)

    # Secrets in URLs (tokens, keys) hidden in every log line
    from utils import install_log_redaction
    install_log_redaction()

    # Extensions
    from models import db
    db.init_app(app)
    
    # Jinja2 filter: UTC to the configured local time
    @app.template_filter('localtime')
    def localtime_filter(dt, fmt='%d/%m/%Y %H:%M'):
        if dt is None:
            return None
        from utils import utc_to_local
        local_dt = utc_to_local(dt)
        return local_dt.strftime(fmt) if local_dt else ''

    from email_service import email_service
    email_service.init_app(app)
    
    from monitor_service import monitor_service
    monitor_service.init_app(app)
    
    from cleanup_service import cleanup_service
    cleanup_service.init_app(app)
    
    from notification_service import notification_service
    notification_service.init_app(app)
    
    # Blueprints
    from routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    
    # Login: protects pages and APIs
    from auth import init_auth
    init_auth(app)

    # Create the database tables (and add new columns to existing ones)
    with app.app_context():
        db.create_all()
        from models import ensure_schema
        for column in ensure_schema():
            logger.info(f"Database updated: added column {column}")
        if db.engine.dialect.name == 'sqlite' and db.engine.url.database not in (None, '', ':memory:'):
            # WAL: reads do not block writes and vice versa
            with db.engine.connect() as connection:
                connection.exec_driver_sql('PRAGMA journal_mode=WAL')
        logger.info("Database initialized")
    
    # Scheduler (not in testing)
    if not app.config.get('TESTING'):
        from scheduler import init_scheduler
        init_scheduler(app)
    
    logger.info(f"App started in {config_name} mode")
    
    return app


# Development only (production: serve.py). Local only, and no reloader: it would start
# a second scheduler. The debugger is on only with FLASK_DEBUG=1.
if __name__ == '__main__':
    app = create_app()
    app.run(host='127.0.0.1', port=5000,
            debug=os.environ.get('FLASK_DEBUG') == '1', use_reloader=False)

