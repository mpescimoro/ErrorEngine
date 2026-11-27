"""
The pause of the notifications, for planned work: the checks go on, and nothing is sent, neither the notifications
of the errors found, nor the reminders, nor the alerts about the sources. At the end, what is still open goes out.
"""
from datetime import timedelta

from models import db, ErrorRecord, NotificationPause
from utils import get_utc_now

# The longest pause: the planned work of a long weekend
MAX_PAUSE_MINUTES = 3 * 24 * 60


def held():
    """The errors held by the pause: still open, and not sent."""
    return ErrorRecord.query.filter(ErrorRecord.resolved_at.is_(None), ErrorRecord.notify_pending.is_(True),
                                    ErrorRecord.held_until.isnot(None))


def paused_until():
    """The end of the pause (UTC), None when the notifications are not paused."""
    pause = db.session.get(NotificationPause, 1)
    return pause.until if pause and pause.until > get_utc_now() else None


def pause_notifications(minutes: int):
    """Pauses the notifications for minutes from now, also when they are paused already; returns the end."""
    now = get_utc_now()
    pause = db.session.get(NotificationPause, 1) or NotificationPause(id=1)
    if not (pause.until and pause.until > now):
        pause.started_at = now
    pause.until = now + timedelta(minutes=minutes)
    db.session.add(pause)
    # What it holds already waits for the new end, also when it comes sooner than the old one
    held().update({ErrorRecord.held_until: pause.until, ErrorRecord.notify_retry_at: pause.until})
    db.session.commit()
    return pause.until


def resume_notifications():
    """Ends the pause now: what it held goes out at the next round of the scheduler, not when it was to end."""
    now = get_utc_now()
    pause = db.session.get(NotificationPause, 1)
    if pause and pause.until > now:
        pause.until = now
    held().update({ErrorRecord.notify_retry_at: now})
    db.session.commit()
