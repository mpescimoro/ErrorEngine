r"""
ErrorEngine - production entry point (start.bat / start.sh, Windows service, scheduled
task or manual run).

Use this instead of `python app.py`, which starts Flask's development server: meant for
development only, it listens on 127.0.0.1 and is not built for production traffic.

This runs a single process on Waitress (in requirements.txt), so the scheduler starts
exactly once. Give the full path to this file when starting it from another folder:

    C:\path\to\ErrorEngine\venv\Scripts\python.exe C:\path\to\ErrorEngine\serve.py

Logs: printed to the console (when there is one) and written to logs\errorengine.log,
rotated at 10 MB with 5 old files kept.

Optional settings (in .env):
    ERRORENGINE_HOST   default 0.0.0.0 (all interfaces)
    ERRORENGINE_PORT   default 5000
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Services and scheduled tasks start in C:\Windows\System32: work from the project folder.
os.chdir(BASE_DIR)

# Logging is configured before importing the app, so the logging.basicConfig() call in
# app.py becomes a no-op and everything, startup errors included, also lands in the file:
# a scheduled task has no console, so the file is the only place to read it.
LOG_FILE = os.path.join(BASE_DIR, 'logs', 'errorengine.log')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
_handlers = [RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')]
if sys.stderr:  # None under pythonw.exe
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_handlers,
)
logger = logging.getLogger('serve')


def _log_uncaught(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        logger.info('ErrorEngine stopped')
        return
    logger.critical('ErrorEngine stopped by an unexpected error',
                    exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _log_uncaught

# .env is loaded only here: start.bat, start.sh, a service and a scheduled task all run
# this file. This must happen before importing app/config, which read os.environ at import time.
# utf-8-sig also accepts a .env saved by Notepad with a BOM.
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'), encoding='utf-8-sig')

# LOG_LEVEL in .env: DEBUG, INFO (default), WARNING or ERROR
_level = os.environ.get('LOG_LEVEL', 'INFO').strip().upper()
logging.getLogger().setLevel(_level if _level in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL') else 'INFO')

from waitress import serve
from app import create_app


def main():
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        logger.critical('SECRET_KEY is missing: add it to .env (required in production).')
        sys.exit(1)

    # APScheduler logs every run of the 1-minute job at INFO: keep only warnings and errors.
    logging.getLogger('apscheduler').setLevel(logging.WARNING)

    # create_app passes an instance of ProductionConfig: its SECRET_KEY @property is read
    app = create_app('production')

    host = os.environ.get('ERRORENGINE_HOST', '0.0.0.0')
    port = int(os.environ.get('ERRORENGINE_PORT', '5000'))
    try:
        serve(app, host=host, port=port)
    finally:
        # On a clean stop, let the queries still running finish (bounded by their
        # timeouts) and release their locks.
        import scheduler
        scheduler.shutdown(wait=True)
        logger.info('ErrorEngine stopped')


if __name__ == '__main__':
    main()
