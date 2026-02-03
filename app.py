"""
Application Factory - ErrorEngine Monitoring System
"""
import os
import logging
from flask import Flask
from config import config

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(config_name=None):
    """Application Factory Pattern"""
    
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Inizializza estensioni
    from models import db
    db.init_app(app)
    
    # Filtro Jinja2 per convertire UTC in ora locale configurata
    @app.template_filter('localtime')
    def localtime_filter(dt, fmt='%d/%m/%Y %H:%M'):
        if dt is None:
            return None
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        
        tz_name = app.config.get('TIMEZONE', 'UTC')
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = ZoneInfo('UTC')
        
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        
        local_dt = dt.astimezone(local_tz)
        return local_dt.strftime(fmt)

    from email_service import email_service
    email_service.init_app(app)
    
    from monitor_service import monitor_service
    monitor_service.init_app(app)
    
    from cleanup_service import cleanup_service
    cleanup_service.init_app(app)
    
    from notification_service import notification_service
    notification_service.init_app(app)
    
    # Registra blueprints
    from routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    
    # Crea tabelle database
    with app.app_context():
        db.create_all()
        logger.info("Database inizializzato")
    
    # Inizializza scheduler (solo se non in testing)
    if not app.config.get('TESTING'):
        from scheduler import init_scheduler
        init_scheduler(app)
    
    logger.info(f"App avviata in modalità: {config_name}")
    
    return app


# Entry point per sviluppo
if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)

