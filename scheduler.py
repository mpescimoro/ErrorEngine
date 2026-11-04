"""
Scheduler for the periodic checks.
Uses APScheduler integrated with Flask.
Time windows are handled by MonitorService.

Every minute the job picks the queries to run and hands them to a thread
pool: a slow or stuck source takes a single thread and does not stop the others.
A query that is still running is not started a second time.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from flask_apscheduler import APScheduler
from models import db, MonitoredQuery
from utils import get_utc_now

logger = logging.getLogger(__name__)
scheduler = APScheduler()

_executor = None
_in_flight = set()          # ids of the queries currently running
_lock = threading.Lock()
_state = {'last_tick_at': None, 'max_workers': None}


def _get_executor(app) -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            workers = max(1, int(app.config.get('SCHEDULER_MAX_WORKERS', 4)))
            _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='query')
            _state['max_workers'] = workers
        return _executor


def _run_query(app, query_id: int):
    """Runs a query in its own thread, with its own application context."""
    try:
        with app.app_context():
            from monitor_service import monitor_service
            query = db.session.get(MonitoredQuery, query_id)
            if query is None or not query.is_active:
                return
            result = monitor_service.check_query(query)
            if result['status'] == 'skipped':
                logger.debug(f"Scheduler: {query.name} skipped - {result.get('error_message', '')}")
            elif result['status'] == 'error':
                logger.error(f"Scheduler: error in {query.name} - {result.get('error_message', '')}")
    except Exception as e:
        logger.exception(f"Scheduler: exception in query {query_id}: {e}")
    finally:
        with _lock:
            _in_flight.discard(query_id)


def dispatch_due_queries(app) -> list:
    """Starts the queries that are due in the pool; returns their ids."""
    started = []
    with app.app_context():
        _state['last_tick_at'] = get_utc_now()
        due = []
        for query in MonitoredQuery.query.filter_by(is_active=True).all():
            try:
                should_run, reason = query.should_run_now()
            except Exception as e:
                logger.error(f"Scheduler: schedule calculation failed for {query.name}: {e}")
                continue
            if should_run:
                logger.debug(f"Scheduler: starting check {query.name} ({reason})")
                due.append((query.id, query.name))
            else:
                logger.debug(f"Scheduler: {query.name} not due - {reason}")

    executor = _get_executor(app)
    for query_id, name in due:
        with _lock:
            if query_id in _in_flight:
                logger.warning(f"Scheduler: {name} is still running from the previous round, skipping")
                continue
            _in_flight.add(query_id)
        executor.submit(_run_query, app, query_id)
        started.append(query_id)
    return started


def get_scheduler_status() -> dict:
    """Actual scheduler state, for the health check and the Settings page."""
    with _lock:
        in_flight = len(_in_flight)
    return {
        'enabled': scheduler.app is not None,
        'running': bool(scheduler.app is not None and scheduler.running),
        'last_tick_at': _state['last_tick_at'],
        'in_flight': in_flight,
        'max_workers': _state['max_workers'],
    }


def init_scheduler(app):
    """
    Initializes the scheduler with the Flask app.
    """
    # Avoid initializing twice
    if scheduler.running:
        logger.info("Scheduler: already running, skip")
        return

    scheduler.init_app(app)

    @scheduler.task('interval', id='check_queries', minutes=1, misfire_grace_time=60)
    def check_due_queries():
        """
        Every minute starts the queries that are due (the time window is
        checked by MonitorService) without waiting for them to finish.
        """
        dispatch_due_queries(app)

    @scheduler.task('cron', id='cleanup_old_records', hour=3, minute=0, misfire_grace_time=3600)
    def cleanup_old_records():
        """
        Every day at 3:00, cleans up old records.
        """
        with app.app_context():
            from cleanup_service import cleanup_service

            try:
                result = cleanup_service.run_full_cleanup()
                logger.info(f"Scheduled cleanup done: {result}")
            except Exception as e:
                logger.error(f"Scheduler: cleanup error: {e}")

    scheduler.start()
    _get_executor(app)
    logger.info(
        f"Scheduler started (check_queries every 1 min on {_state['max_workers']} threads, cleanup at 03:00)"
    )

    return scheduler


def shutdown(wait: bool = True):
    """Stops the scheduler and waits for running queries (bounded by their timeouts)."""
    global _executor
    if scheduler.running:
        scheduler.shutdown(wait=wait)
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=wait)
