"""
ErrorEngine application configuration
"""
import os
from datetime import timedelta

# Key used only when SECRET_KEY is missing
DEFAULT_SECRET_KEY = 'secret-key-to-change-in-production'
# Publicly known keys (defaults and the .env.example placeholder): with these, login stays blocked
INSECURE_SECRET_KEYS = {DEFAULT_SECRET_KEY, 'chiave-segreta-da-cambiare-in-produzione',
                        'change-this-to-a-random-string'}


def _mail_security():
    """
    starttls, ssl or none. Without MAIL_SECURITY: MAIL_USE_TLS of older .env files
    (true: starttls, false: ssl), otherwise ssl on port 465 and starttls on the others.
    """
    value = (os.environ.get('MAIL_SECURITY') or '').strip().lower()
    if value:
        return value
    legacy = (os.environ.get('MAIL_USE_TLS') or '').strip().lower()
    if legacy:
        return 'starttls' if legacy == 'true' else 'ssl'
    return 'ssl' if (os.environ.get('MAIL_PORT') or '').strip() == '465' else 'starttls'


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
    
    # Internal database (SQLite unless DATABASE_URL is set)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///errorengine.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Email through any SMTP server. MAIL_SERVER empty: email is off and the
    # notifications go only to the channels
    MAIL_SERVER = (os.environ.get('MAIL_SERVER') or '').strip()
    # starttls, ssl or none (none: only for a relay on the internal network, without login)
    MAIL_SECURITY = _mail_security()
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or {'ssl': 465, 'none': 25}.get(MAIL_SECURITY, 587))
    # Without a user name there is no login: a relay that accepts this server by its address
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    # Address the emails are sent from; empty: MAIL_USERNAME
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('MAIL_USERNAME', '')
    # Microsoft 365 through Graph (an Entra ID app with Mail.Send): with these three set,
    # emails go out from MAIL_DEFAULT_SENDER through Graph instead of SMTP
    M365_TENANT_ID = (os.environ.get('M365_TENANT_ID') or '').strip()
    M365_CLIENT_ID = (os.environ.get('M365_CLIENT_ID') or '').strip()
    M365_CLIENT_SECRET = os.environ.get('M365_CLIENT_SECRET', '')
    # Maximum wait for the email server (connection, login, sending)
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
    
    # Resolved errors retention (days)
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
    MAIL_SECURITY = 'ssl'
    M365_TENANT_ID = M365_CLIENT_ID = M365_CLIENT_SECRET = ''
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
