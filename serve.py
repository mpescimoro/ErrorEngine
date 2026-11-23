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

Ctrl+C, or SIGTERM (kill, systemctl stop, docker stop), stops it once the queries still
running have finished.

Optional settings (in .env):
    ERRORENGINE_HOST     default 0.0.0.0 (all interfaces)
    ERRORENGINE_PORT     default 5000
    ERRORENGINE_THREADS  default 4: requests handled at the same time
    ERRORENGINE_TRUSTED_PROXY  address of the reverse proxy, if there is one
"""
import ipaddress
import logging
import os
import shlex
import signal
import socket
import sys
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Services and scheduled tasks start in C:\Windows\System32: work from the project folder.
os.chdir(BASE_DIR)
# The files created from here on (database, its copies, logs) readable only by the account that runs
# ErrorEngine. Linux and macOS: on Windows the permissions of the folder decide
os.umask(0o077)

# Before importing the app (its basicConfig becomes a no-op): startup errors must reach
# the file too, a scheduled task has no console
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

# .env is loaded only here (every way of starting runs this file), before importing
# app/config, which read os.environ. utf-8-sig: also a .env saved by Notepad with a BOM
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'), encoding='utf-8-sig')

# LOG_LEVEL in .env: DEBUG, INFO (default), WARNING or ERROR
_level = os.environ.get('LOG_LEVEL', 'INFO').strip().upper()
logging.getLogger().setLevel(_level if _level in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL') else 'INFO')

from waitress import create_server
from app import create_app
from config import whole_number


def readable_by_others(paths) -> list:
    """Those of these files that other users of this machine can open (Linux, macOS)."""
    if os.name == 'nt':
        return []
    return [path for path in paths if os.path.isfile(path) and os.stat(path).st_mode & 0o077]


def warn_about_permissions(app):
    """The files made before the umask above, or by someone else: the log says how to close them."""
    files = [os.path.join(BASE_DIR, '.env')]
    from models import db
    with app.app_context():
        url = db.engine.url
        if url.get_backend_name() == 'sqlite' and url.database not in (None, '', ':memory:'):
            files += [url.database + suffix for suffix in ('', '-wal', '-shm')]
    open_files = readable_by_others(files)
    if open_files:
        logger.warning(f"Other users of this machine can read {', '.join(open_files)}: the database holds the "
                       "passwords and tokens, encrypted, and .env the keys. Close them with: chmod 600 "
                       + ' '.join(shlex.quote(path) for path in open_files))


def main():
    host = os.environ.get('ERRORENGINE_HOST') or '0.0.0.0'
    port = whole_number('ERRORENGINE_PORT', 5000, between=(1, 65535))
    threads = max(1, whole_number('ERRORENGINE_THREADS', 4))
    proxy = (os.environ.get('ERRORENGINE_TRUSTED_PROXY') or '').strip()
    if proxy not in ('', '*'):
        try:
            # Waitress compares it as text with the address of the peer: ::1, not 0:0:0:0:0:0:0:1
            proxy = str(ipaddress.ip_address(proxy))
        except ValueError:
            logger.critical(f"ERRORENGINE_TRUSTED_PROXY={proxy} in .env is not an IP address: correct it, "
                            "or remove it if there is no reverse proxy")
            sys.exit(1)

    # The port first, our own: a second copy stops here, before it unlocks the first one's queries.
    # Waitress would ask for SO_REUSEADDR, which on Windows lets a second program take a port in use
    try:
        sock = socket.create_server((host, port), family=socket.AF_INET6 if ':' in host else socket.AF_INET)
    except OSError as e:
        logger.critical(f"Cannot listen: {e.strerror or e}. Is ErrorEngine already running? "
                        "Otherwise check ERRORENGINE_HOST and ERRORENGINE_PORT in .env")
        sys.exit(1)

    # APScheduler logs every run of the 1-minute job at INFO: keep only warnings and errors.
    logging.getLogger('apscheduler').setLevel(logging.WARNING)

    app = create_app('production')
    warn_about_permissions(app)
    # From the reverse proxy alone, the client address, host and https of the requests it forwards;
    # the same headers from anyone else are dropped
    from_proxy = {'trusted_proxy': proxy,
                  'trusted_proxy_headers': 'x-forwarded-for x-forwarded-host x-forwarded-proto'} if proxy else {}
    # ident='': the responses do not name the web server
    server = create_server(app, sockets=[sock], threads=threads, ident='', **from_proxy)
    # kill, systemctl stop and docker stop send SIGTERM: the same orderly stop as Ctrl+C
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logger.info(f"Web server: up to {threads} requests at a time")
    if proxy:
        logger.info(f"Trusted reverse proxy: {proxy}")
    try:
        server.print_listen('Serving on http://{}:{}')
        server.run()
    finally:
        # On a clean stop, let the queries still running finish (bounded by their
        # timeouts) and release their locks.
        import scheduler
        scheduler.shutdown(wait=True)
        logger.info('ErrorEngine stopped')


if __name__ == '__main__':
    main()
