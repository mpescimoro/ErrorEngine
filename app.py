"""
Application Factory - ErrorEngine Monitoring System
"""
import os
import logging
from flask import Flask, request, session
from flask_babel import Babel
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

    # Inizializza Babel per i18n
    babel = Babel(app)

    @babel.localeselector
    def get_locale():
        """Determine the best locale for the user."""
        # 1. Check URL parameter (?lang=en)
        lang = request.args.get('lang')
        if lang in app.config['BABEL_SUPPORTED_LOCALES']:
            session['lang'] = lang
            return lang

        # 2. Check session['lang']
        if 'lang' in session and session['lang'] in app.config['BABEL_SUPPORTED_LOCALES']:
            return session['lang']

        # 3. Check browser Accept-Language header
        best_match = request.accept_languages.best_match(app.config['BABEL_SUPPORTED_LOCALES'])
        if best_match:
            return best_match

        # 4. Fallback to default
        return app.config['BABEL_DEFAULT_LOCALE']

    # Add Jinja2 i18n extension
    app.jinja_env.add_extension('jinja2.ext.i18n')

    # Make get_locale available in templates
    app.jinja_env.globals.update(get_locale=get_locale)

    # Filtro Jinja2 per convertire UTC in ora locale configurata
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

