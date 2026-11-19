"""
ErrorEngine application configuration
"""
import logging
import os
from datetime import timedelta

logger = logging.getLogger(__name__)

# Key used only when SECRET_KEY is missing
DEFAULT_SECRET_KEY = 'secret-key-to-change-in-production'
# Publicly known keys (defaults and the .env.example placeholder): with these, login stays blocked
INSECURE_SECRET_KEYS = {DEFAULT_SECRET_KEY, 'chiave-segreta-da-cambiare-in-produzione',
                        'change-this-to-a-random-string'}

# Settings of older versions, no longer read: still in an .env, they get a warning at startup
# (retired_settings_warnings) instead of being ignored without a word
RETIRED_SETTINGS = ('MAIL_USE_TLS', 'NOTIFY_RETRY_HOURS')


def whole_number(name: str, default: int, between: tuple = None) -> int:
    """
    A whole number from .env, the default when the setting is empty. Anything else stops
    ErrorEngine with a line that names the setting, instead of a traceback.
    """
    text = (os.environ.get(name) or '').strip()
    if not text:
        return default
    try:
        value = int(text)
        if between is None or between[0] <= value <= between[1]:
            return value
    except ValueError:
        pass
    span = f' from {between[0]} to {between[1]}' if between else ''
    logger.critical(f"{name}={text} in .env is not a whole number{span}: "
                    f"correct it, or remove it for the default ({default})")
    raise SystemExit(1)


def flag(name: str) -> bool:
    """
    true or false from .env, false when the setting is empty. Anything else stops ErrorEngine
    with a line that names the setting, as whole_number does.
    """
    text = (os.environ.get(name) or '').strip()
    if text.lower() in ('', 'false'):
        return False
    if text.lower() == 'true':
        return True
    logger.critical(f"{name}={text} in .env is not true or false: "
                    "correct it, or remove it for the default (false)")
    raise SystemExit(1)


def _mail_security():
    """starttls, ssl or none; without MAIL_SECURITY, ssl on port 465 and starttls on the others."""
    value = (os.environ.get('MAIL_SECURITY') or '').strip().lower()
    if value:
        return value
    return 'ssl' if (os.environ.get('MAIL_PORT') or '').strip() == '465' else 'starttls'


def _mail_port(security: str) -> int:
    """MAIL_PORT, or the usual port of that encryption."""
    return whole_number('MAIL_PORT', {'ssl': 465, 'none': 25}.get(security, 587))


def retired_settings_warnings() -> list:
    """
    What the log says at startup about the settings of older versions still in .env. Up to 2.4.1,
    MAIL_USE_TLS=true meant STARTTLS and any other value SSL, whatever the port: where today's rule
    gives the other one, the warning says so, with the MAIL_SECURITY that keeps email as it was.
    """
    warnings = []
    use_tls = (os.environ.get('MAIL_USE_TLS') or '').strip().lower()
    if use_tls and (os.environ.get('MAIL_SECURITY') or '').strip():
        warnings.append("MAIL_USE_TLS is no longer used, and MAIL_SECURITY decides, as before: remove it from .env")
    elif use_tls:
        before = 'starttls' if use_tls == 'true' else 'ssl'
        now = _mail_security()
        if now == before:
            warnings.append(f"MAIL_USE_TLS is no longer used: replace it with MAIL_SECURITY={before} in .env. "
                            f"Email goes out as before, with {before} on port {_mail_port(before)}")
        else:
            warnings.append(f"MAIL_USE_TLS is no longer used: email now goes out with {now} on port "
                            f"{_mail_port(now)}, until now with {before} on port {_mail_port(before)}. "
                            f"To keep it as it was, replace MAIL_USE_TLS with MAIL_SECURITY={before} in .env")
    if (os.environ.get('NOTIFY_RETRY_HOURS') or '').strip():
        warnings.append("NOTIFY_RETRY_HOURS is no longer used: a notification that cannot be delivered gets "
                        "NOTIFY_ATTEMPTS attempts (5 by default), then it is shown as not delivered. "
                        "Remove it from .env")
    return warnings


class Config:
    """Base configuration."""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEFAULT_SECRET_KEY

    # Login (single user, see auth.py)
    LOGIN_USERNAME = os.environ.get('ERRORENGINE_USER') or 'admin'
    LOGIN_PASSWORD_HASH = os.environ.get('ERRORENGINE_PASSWORD_HASH', '')
    API_TOKEN = os.environ.get('ERRORENGINE_API_TOKEN', '')  # optional, for scripts
    LOGIN_DISABLED = False
    CSRF_DISABLED = False
    # The session expires after N hours of inactivity
    PERMANENT_SESSION_LIFETIME = timedelta(hours=whole_number('ERRORENGINE_SESSION_HOURS', 12))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # The cookie goes out again only when the session changes (something the user does): the requests
    # a page makes by itself do not move its expiry forward
    SESSION_REFRESH_EACH_REQUEST = False
    # Users open ErrorEngine with https://, through a reverse proxy: the session cookie only over HTTPS
    HTTPS = flag('ERRORENGINE_HTTPS')
    SESSION_COOKIE_SECURE = HTTPS
    
    # Internal database (SQLite unless DATABASE_URL is set)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///errorengine.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Email through any SMTP server. MAIL_SERVER empty: email is off and the
    # notifications go only to the channels
    MAIL_SERVER = (os.environ.get('MAIL_SERVER') or '').strip()
    # starttls, ssl or none (none: only for a relay on the internal network, without login)
    MAIL_SECURITY = _mail_security()
    MAIL_PORT = _mail_port(MAIL_SECURITY)
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
    MAIL_TIMEOUT_SECONDS = whole_number('MAIL_TIMEOUT_SECONDS', 30)
    # CA (or certificate) of an internal SMTP server; empty: the system certificates
    MAIL_CA_FILE = os.environ.get('MAIL_CA_FILE', '')


    # Scheduler: the Flask-APScheduler REST API stays off (it would allow registering
    # jobs with arbitrary Python functions); the app has its own APIs in routes/api.py
    SCHEDULER_API_ENABLED = False
    # Queries run in parallel: a slow source does not block the others
    SCHEDULER_MAX_WORKERS = whole_number('SCHEDULER_MAX_WORKERS', 4)

    # HTTP/API timeout for external sources
    HTTP_TIMEOUT_SECONDS = whole_number('HTTP_TIMEOUT_SECONDS', 30)

    # Database source timeouts (seconds)
    DB_CONNECT_TIMEOUT_SECONDS = whole_number('DB_CONNECT_TIMEOUT_SECONDS', 15)
    DB_QUERY_TIMEOUT_SECONDS = whole_number('DB_QUERY_TIMEOUT_SECONDS', 300)

    # Attempts to deliver a notification: the first, then after 1, 5, 15 and 60 minutes, and every
    # 60 minutes beyond the fifth. Then it is marked as not delivered; 1 = no retries
    NOTIFY_ATTEMPTS = whole_number('NOTIFY_ATTEMPTS', 5)

    # Alert when a query cannot read its source N times in a row
    # (after a failure the scheduler retries every minute); 0 = no alert
    MONITOR_FAILURE_THRESHOLD = whole_number('MONITOR_FAILURE_THRESHOLD', 3)
    # Recipients of these alerts (comma-separated); if empty, the query's recipients
    ADMIN_EMAILS = os.environ.get('ERRORENGINE_ADMIN_EMAILS', '')
    
    # Log retention (days)
    LOG_RETENTION_DAYS = whole_number('LOG_RETENTION_DAYS', 30)
    
    # Email retention (days)
    EMAIL_LOG_RETENTION_DAYS = whole_number('EMAIL_LOG_RETENTION_DAYS', 90)
    
    # Resolved errors retention (days)
    RESOLVED_ERRORS_RETENTION_DAYS = whole_number('RESOLVED_ERRORS_RETENTION_DAYS', 60)
    
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
    # The CSRF check too (tests/test_csrf.py turns it back on)
    CSRF_DISABLED = True

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
