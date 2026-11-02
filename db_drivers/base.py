"""Base class for all database drivers."""
from abc import ABC, abstractmethod
import logging
import threading

from utils import redact_secrets

logger = logging.getLogger(__name__)

# Margin beyond the driver's connection timeout before giving up on the attempt
CONNECT_GRACE_SECONDS = 5


def call_with_deadline(fn, seconds: float, what: str = 'Operation', on_late_result=None):
    """
    Runs fn() and waits at most `seconds` seconds.

    For libraries that do not bound the whole connection phase (for example
    oracledb thin mode only bounds the TCP connection): past the limit the
    attempt is abandoned with TimeoutError and, if fn() finishes later,
    its result is passed to on_late_result (to close the connection).
    """
    outcome = {}
    done = threading.Event()
    lock = threading.Lock()
    state = {'abandoned': False}

    def run():
        try:
            value, error = fn(), None
        except BaseException as exc:  # reported to the caller
            value, error = None, exc
        with lock:
            if state['abandoned']:
                if value is not None and on_late_result:
                    try:
                        on_late_result(value)
                    except Exception:
                        pass
                return
            outcome['value'], outcome['error'] = value, error
            done.set()

    worker = threading.Thread(target=run, name=f'deadline-{what}', daemon=True)
    worker.start()
    if not done.wait(seconds):
        with lock:
            if not done.is_set():
                state['abandoned'] = True
                raise TimeoutError(f"{what}: no response within {seconds:g} seconds")
    if outcome['error'] is not None:
        raise outcome['error']
    return outcome['value']


class DatabaseDriver(ABC):
    """Common interface for all databases."""

    name = "base"
    default_port = None

    def __init__(self, connect_timeout: float = None, query_timeout: float = None):
        # Seconds; None = no limit (the library's behaviour)
        self.connect_timeout = connect_timeout or None
        self.query_timeout = query_timeout or None

    @abstractmethod
    def connect(self, host: str, port: int, database: str, username: str, password: str):
        """Creates and returns a connection."""
        pass

    @abstractmethod
    def execute_query(self, connection, sql: str) -> tuple:
        """
        Runs a query and returns (columns, rows).
        rows is a list of dictionaries.
        """
        pass

    def open(self, host: str, port: int, database: str, username: str, password: str):
        """connect() with a guaranteed time limit for the whole connection phase."""
        if not self.connect_timeout:
            return self.connect(host, port, database, username, password)
        return call_with_deadline(
            lambda: self.connect(host, port, database, username, password),
            self.connect_timeout + CONNECT_GRACE_SECONDS,
            what=f'Connection {self.name}',
            on_late_result=self.close,
        )

    def close(self, connection):
        """Closes a connection. Override if the driver needs it."""
        try:
            connection.close()
        except AttributeError:
            # Some drivers (e.g. ibm_db) have no connection.close()
            pass

    def test_connection(self, host: str, port: int, database: str, username: str, password: str) -> dict:
        """Tests the connection, whatever the driver."""
        try:
            conn = self.open(host, port, database, username, password)
            self.close(conn)
            return {'status': 'ok', 'message': 'Connection successful'}
        except Exception as e:
            return {'status': 'error', 'message': redact_secrets(str(e))}

    def test_query(self, connection, sql: str, limit: int = 5) -> dict:
        """Tests a query and returns a sample."""
        try:
            columns, rows = self.execute_query(connection, sql)
            return {
                'valid': True,
                'columns': columns,
                'row_count': len(rows),
                'sample_rows': rows[:limit],
                'error': None
            }
        except Exception as e:
            return {
                'valid': False,
                'columns': [],
                'row_count': 0,
                'sample_rows': [],
                'error': redact_secrets(str(e))
            }

    def _safe_value(self, value):
        """Converts a value to a JSON-safe type."""
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return '<binary>'
        return str(value)
