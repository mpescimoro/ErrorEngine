"""
Utility functions for ErrorEngine.
Centralized timezone handling that works everywhere (with or without Flask context).
"""
import os
from datetime import datetime, timezone, time, timedelta

# Cache per evitare lookup ripetuti
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
    
    # Determina timezone name
    tz_name = None
    
    # Prova Flask context
    try:
        from flask import current_app
        if current_app and current_app.config:
            tz_name = current_app.config.get('TIMEZONE')
    except RuntimeError:
        pass  # Fuori dal context Flask
    
    # Fallback a environment
    if not tz_name:
        tz_name = os.environ.get('TIMEZONE', 'UTC')
    
    # Usa cache se timezone non è cambiata
    if tz_name == _cached_tz_name and _cached_tz is not None:
        return _cached_tz
    
    # Crea ZoneInfo
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
    return datetime.utcnow()


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


# Legacy compatibility - mantiene backward compatibility con codice esistente
def get_local_now_aware(app=None):
    """
    Returns current time in configured timezone as aware datetime.
    Legacy function for backward compatibility.
    
    Args:
        app: Flask app (ignored, kept for compatibility)
    
    Returns:
        datetime: Local time with tzinfo (aware)
    """
    tz = get_configured_timezone()
    return datetime.now(timezone.utc).astimezone(tz)


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