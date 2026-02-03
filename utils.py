"""
Funzioni utility condivise per ErrorEngine.
"""
from datetime import datetime, timezone


def get_local_now(app=None):
    """
    Restituisce la data/ora corrente nella timezone configurata.
    Usa per le date di DISPLAY (email, notifiche, messaggi).
    Per il database, usare sempre datetime.utcnow() / datetime.now(timezone.utc).
    
    Args:
        app: Flask app instance (opzionale, usa current_app se non specificato)
    
    Returns:
        datetime: data/ora locale con tzinfo
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    
    if app is None:
        from flask import current_app
        app = current_app._get_current_object()
    
    tz_name = app.config.get('TIMEZONE', 'UTC')
    
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo('UTC')
    
    return datetime.now(timezone.utc).astimezone(local_tz)


def format_local_now(fmt='%d/%m/%Y %H:%M:%S', app=None):
    """
    Restituisce la data/ora corrente formattata nella timezone configurata.
    
    Args:
        fmt: Formato strftime
        app: Flask app instance (opzionale)
    
    Returns:
        str: data/ora formattata
    """
    return get_local_now(app).strftime(fmt)
