"""
Configurazione dell'applicazione ErrorEngine
"""
import os
from datetime import timedelta


class Config:
    """Configurazione base."""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'chiave-segreta-da-cambiare-in-produzione'
    
    # SQLite Database (DB di appoggio)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///errorengine.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Configurazione Email (Exchange/SMTP)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.office365.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', '')

    
    # Scheduler
    SCHEDULER_API_ENABLED = True
    
    # HTTP/API timeout per sorgenti esterne
    HTTP_TIMEOUT_SECONDS = int(os.environ.get('HTTP_TIMEOUT_SECONDS') or 30)
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'
    
    # Log retention (giorni)
    LOG_RETENTION_DAYS = int(os.environ.get('LOG_RETENTION_DAYS') or 30)
    
    # Email retention (giorni)
    EMAIL_LOG_RETENTION_DAYS = int(os.environ.get('EMAIL_LOG_RETENTION_DAYS') or 90)
    
    # Resolved errors retention (giorni) - errori risolti vengono eliminati dopo questo periodo
    RESOLVED_ERRORS_RETENTION_DAYS = int(os.environ.get('RESOLVED_ERRORS_RETENTION_DAYS') or 60)
    
    # Timezone per visualizzazione date (default: UTC)
    TIMEZONE = os.environ.get('TIMEZONE', 'UTC')

class DevelopmentConfig(Config):
    """Configurazione per sviluppo."""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Configurazione per produzione."""
    DEBUG = False
    LOG_LEVEL = 'WARNING'
    
    # In produzione la SECRET_KEY deve essere impostata via env
    @property
    def SECRET_KEY(self):
        key = os.environ.get('SECRET_KEY')
        if not key:
            raise ValueError("SECRET_KEY deve essere impostata in produzione")
        return key


class TestingConfig(Config):
    """Configurazione per testing."""
    TESTING = True
    DEBUG = True
    
    # Database in memoria per test veloci
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    
    # Disabilita scheduler durante i test
    SCHEDULER_API_ENABLED = False
    
    # Email mock
    MAIL_SERVER = 'localhost'
    MAIL_PORT = 25
    MAIL_USE_TLS = False
    MAIL_USERNAME = 'test@test.com'
    MAIL_PASSWORD = 'test'
    MAIL_DEFAULT_SENDER = 'test@test.com'
    
    # Retention breve per test
    LOG_RETENTION_DAYS = 1
    EMAIL_LOG_RETENTION_DAYS = 1
    RESOLVED_ERRORS_RETENTION_DAYS = 1


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
