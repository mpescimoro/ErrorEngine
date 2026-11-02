"""
Utility functions for ErrorEngine.
Centralized timezone handling that works everywhere (with or without Flask context).
"""
import logging
import os
import re
from datetime import datetime, timezone, time, timedelta
from urllib.parse import urlsplit, urlunsplit

# Cache to avoid repeated lookups
_cached_tz = None
_cached_tz_name = None


def get_configured_timezone():
    """
    Returns the configured ZoneInfo timezone.
    Works both inside and outside Flask context.
    
    Priority:
    1. Flask current_app.config['TIMEZONE']
    2. os.environ['TIMEZONE']
    3. 'UTC' (fallback)
    """
    global _cached_tz, _cached_tz_name
    
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    
    # Time zone name
    tz_name = None
    
    # Try the Flask context
    try:
        from flask import current_app
        if current_app and current_app.config:
            tz_name = current_app.config.get('TIMEZONE')
    except RuntimeError:
        pass  # Outside the Flask context
    
    # Fall back to the environment
    if not tz_name:
        tz_name = os.environ.get('TIMEZONE', 'UTC')
    
    # Use the cache if the time zone has not changed
    if tz_name == _cached_tz_name and _cached_tz is not None:
        return _cached_tz
    
    # Build the ZoneInfo
    try:
        _cached_tz = ZoneInfo(tz_name)
        _cached_tz_name = tz_name
    except Exception:
        _cached_tz = ZoneInfo('UTC')
        _cached_tz_name = 'UTC'
    
    return _cached_tz


def get_local_now():
    """
    Returns current time in configured timezone as naive datetime.
    Use for scheduling calculations and comparisons with time windows.
    
    Returns:
        datetime: Local time without tzinfo (naive local)
    """
    tz = get_configured_timezone()
    return datetime.now(timezone.utc).astimezone(tz).replace(tzinfo=None)


def get_utc_now():
    """
    Returns current UTC time as naive datetime.
    Use for database storage.
    
    Returns:
        datetime: UTC time without tzinfo (naive UTC)
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_to_local(utc_dt):
    """
    Converts naive UTC datetime to naive local datetime.
    
    Args:
        utc_dt: datetime in UTC (naive, assumed UTC)
    
    Returns:
        datetime: Local time without tzinfo (naive local)
    """
    if utc_dt is None:
        return None
    
    tz = get_configured_timezone()
    
    # Assume input is naive UTC, make it aware
    utc_aware = utc_dt.replace(tzinfo=timezone.utc)
    
    # Convert to local and strip tzinfo
    return utc_aware.astimezone(tz).replace(tzinfo=None)


def local_to_utc(local_dt):
    """
    Converts naive local datetime to naive UTC datetime.
    
    Args:
        local_dt: datetime in local time (naive, assumed local)
    
    Returns:
        datetime: UTC time without tzinfo (naive UTC)
    """
    if local_dt is None:
        return None
    
    tz = get_configured_timezone()
    
    # Assume input is naive local, make it aware
    local_aware = local_dt.replace(tzinfo=tz)
    
    # Convert to UTC and strip tzinfo
    return local_aware.astimezone(timezone.utc).replace(tzinfo=None)


def local_day_start_utc(days_ago=0):
    """
    Start of a local day (midnight in the configured time zone), as naive UTC.

    Args:
        days_ago: 0 = today, 1 = yesterday, ...

    Returns:
        datetime: UTC time without tzinfo, to compare with the stored dates
    """
    day = get_local_now().date() - timedelta(days=days_ago)
    return local_to_utc(datetime.combine(day, time.min))


def format_local(utc_dt, fmt='%d/%m/%Y %H:%M'):
    """
    Formats a UTC datetime as local time string.
    
    Args:
        utc_dt: datetime in UTC
        fmt: strftime format string
    
    Returns:
        str: Formatted local time
    """
    if utc_dt is None:
        return ''
    
    local_dt = utc_to_local(utc_dt)
    return local_dt.strftime(fmt)


def format_local_now(fmt='%d/%m/%Y %H:%M:%S', app=None):
    """
    Returns current local time formatted as string.
    
    Args:
        fmt: strftime format
        app: Flask app (ignored, kept for compatibility)
    
    Returns:
        str: Formatted local time
    """
    return get_local_now().strftime(fmt)


# ---------------------------------------------------------------------------
# Secrets out of messages: error texts end up in logs, pages and alerts
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.I)


def _looks_like_a_secret(segment: str) -> bool:
    """A long path segment of letters and digits (a token, a key, a signature)."""
    return (len(segment) >= 24 and re.fullmatch(r'[A-Za-z0-9._~%@:-]+', segment) is not None
            and any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment))


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return '***'
    netloc = parts.netloc
    if '@' in netloc:
        netloc = '***@' + netloc.rsplit('@', 1)[1]
    path = re.sub(r'/bot[^/]+', '/bot***', parts.path)       # Telegram token
    path = '/'.join('***' if _looks_like_a_secret(s) else s for s in path.split('/'))
    query = '&'.join(p.split('=', 1)[0] + '=***' if '=' in p else p for p in parts.query.split('&')) \
        if parts.query else ''
    return urlunsplit((parts.scheme, netloc, path, query, '***' if parts.fragment else ''))


def redact_secrets(text):
    """
    Hides what may be a secret in the URLs inside a text: query string values,
    user and password, the Telegram bot token, long tokens in the path.
    The host and the rest of the path stay, so the message still says what failed.
    """
    if not text:
        return text
    text = _URL_RE.sub(lambda m: _redact_url(m.group(0)), str(text))
    # Paths without the host, as in "Max retries exceeded with url: /path?key=..."
    text = re.sub(r"(url: )(/[^\s'\"]+)", lambda m: m.group(1) + _redact_url(m.group(2)), text)
    # Telegram bot token anywhere (123456789:AA...)
    return re.sub(r'\bbot\d+:[\w-]+', 'bot***', text)


class RedactSecretsFilter(logging.Filter):
    """Logging filter: the same hiding for every log line, tracebacks included."""

    def filter(self, record):
        message = redact_secrets(record.getMessage())
        if message != record.getMessage():
            record.msg, record.args = message, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_secrets(logging.Formatter().formatException(record.exc_info))
        return True


def install_log_redaction():
    """Adds the filter to every handler of the root logger (idempotent)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
            handler.addFilter(RedactSecretsFilter())


# ---------------------------------------------------------------------------
# Server log file (set up by serve.py), shown in Settings
# ---------------------------------------------------------------------------

def format_size(size) -> str:
    """Bytes in a readable form: 512 B, 1.5 KB, 244 KB, 10 MB."""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            break
        value /= 1024
    if unit == 'B' or value >= 10:
        return f"{value:.0f} {unit}"
    return f"{value:.1f} {unit}"


def server_log_info():
    """
    The log file this process writes, with its rotated copies: path, number of files,
    total size and rotation rule. None when the log goes only to the console
    (development server).
    """
    null_device = os.path.abspath(os.devnull)   # e.g. the one pytest attaches: not a log file
    handler = next((h for h in logging.getLogger().handlers
                    if isinstance(h, logging.FileHandler) and h.baseFilename != null_device), None)
    if handler is None:
        return None
    path = handler.baseFilename
    max_bytes = getattr(handler, 'maxBytes', 0)
    backups = getattr(handler, 'backupCount', 0)
    sizes = []
    for name in [path] + [f"{path}.{i}" for i in range(1, backups + 1)]:
        try:
            sizes.append(os.path.getsize(name))
        except OSError:  # not created yet, or not rotated that many times
            pass
    return {
        'path': path,
        'files': len(sizes),
        'size': format_size(sum(sizes)),
        # RotatingFileHandler: no rotation when either value is 0
        'rotation': (f"new file every {format_size(max_bytes)}, the last {backups} kept"
                     if max_bytes and backups else "no rotation, the file keeps growing"),
    }
