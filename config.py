"""
ErrorEngine application configuration
"""
import os
from datetime import timedelta

# Key used only when SECRET_KEY is missing
DEFAULT_SECRET_KEY = 'secret-key-to-change-in-production'
# Public keys (the default, the old default of the Italian versions and the
# .env.example placeholder): with these, login stays blocked
INSECURE_SECRET_KEYS = {DEFAULT_SECRET_KEY, 'chiave-segreta-da-cambiare-in-produzione',
                        'change-this-to-a-random-string'}


class Config:
    """Base configuration."""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEFAULT_SECRET_KEY

    # Login (single user, see auth.py)
    LOGIN_USERNAME = os.environ.get('ERRORENGINE_USER') or 'admin'
    LOGIN_PASSWORD_HASH = os.environ.get('ERRORENGINE_PASSWORD_HASH', '')
    API_TOKEN = os.environ.get('ERRORENGINE_API_TOKEN', '')  # optional, for scripts
    LOGIN_DISABLED = False
    # The session expires after N hours of inactivity
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get('ERRORENGINE_SESSION_HOURS') or 12))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # SQLite database (internal DB)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///errorengine.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Email configuration (Exchange/SMTP)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.office365.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', '')
    # Maximum wait for the SMTP server (connection, login, sending)
    MAIL_TIMEOUT_SECONDS = int(os.environ.get('MAIL_TIMEOUT_SECONDS') or 30)
    # CA (or certificate) of an internal SMTP server; empty: the system certificates
    MAIL_CA_FILE = os.environ.get('MAIL_CA_FILE', '')


    # Scheduler: the Flask-APScheduler REST API stays off (it would allow registering
    # jobs with arbitrary Python functions); the app has its own APIs in routes/api.py
    SCHEDULER_API_ENABLED = False
    # Queries run in parallel: a slow source does not block the others
    SCHEDULER_MAX_WORKERS = int(os.environ.get('SCHEDULER_MAX_WORKERS') or 4)

    # HTTP/API timeout for external sources
    HTTP_TIMEOUT_SECONDS = int(os.environ.get('HTTP_TIMEOUT_SECONDS') or 30)

    # Database source timeouts (seconds)
    DB_CONNECT_TIMEOUT_SECONDS = int(os.environ.get('DB_CONNECT_TIMEOUT_SECONDS') or 15)
    DB_QUERY_TIMEOUT_SECONDS = int(os.environ.get('DB_QUERY_TIMEOUT_SECONDS') or 300)

    # Notifications that fail are retried, with longer and longer waits, for this many
    # hours from when the error appeared; then they are marked as not delivered
    NOTIFY_RETRY_HOURS = int(os.environ.get('NOTIFY_RETRY_HOURS') or 24)

    # Alert when a query cannot read its source N times in a row
    # (after a failure the scheduler retries every minute); 0 = no alert
    MONITOR_FAILURE_THRESHOLD = int(os.environ.get('MONITOR_FAILURE_THRESHOLD') or 3)
    # Recipients of these alerts (comma-separated); if empty, the query's recipients
    ADMIN_EMAILS = os.environ.get('ERRORENGINE_ADMIN_EMAILS', '')
    
    # Log retention (days)
    LOG_RETENTION_DAYS = int(os.environ.get('LOG_RETENTION_DAYS') or 30)
    
    # Email retention (days)
    EMAIL_LOG_RETENTION_DAYS = int(os.environ.get('EMAIL_LOG_RETENTION_DAYS') or 90)
    
    # Resolved errors retention (days): resolved errors are deleted after this period
    RESOLVED_ERRORS_RETENTION_DAYS = int(os.environ.get('RESOLVED_ERRORS_RETENTION_DAYS') or 60)
    
    # Time zone for displayed dates (default: UTC)
    TIMEZONE = os.environ.get('TIMEZONE', 'UTC')

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    
    # In production SECRET_KEY must come from the environment
    @property
    def SECRET_KEY(self):
        key = os.environ.get('SECRET_KEY')
        if not key:
            raise ValueError("SECRET_KEY must be set in production")
        return key


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True
    
    # In-memory database for fast tests (the scheduler does not start with TESTING)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

    # Login disabled in tests (tests/test_auth.py turns it back on)
    LOGIN_DISABLED = True

    # Email mock
    MAIL_SERVER = 'localhost'
    MAIL_PORT = 25
    MAIL_USE_TLS = False
    MAIL_USERNAME = 'test@test.com'
    MAIL_PASSWORD = 'test'
    MAIL_DEFAULT_SENDER = 'test@test.com'
    
    # Short retention for tests
    LOG_RETENTION_DAYS = 1
    EMAIL_LOG_RETENTION_DAYS = 1
    RESOLVED_ERRORS_RETENTION_DAYS = 1


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
