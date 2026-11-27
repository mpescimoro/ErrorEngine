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


class TooManyRows(Exception):
    """A source returned more rows than MAX_ROWS: the check fails, and creates or resolves no error."""

    def __init__(self, limit: int):
        super().__init__(f"The source returned more than {limit} rows, the most MAX_ROWS allows: no error was "
                         "created or resolved. Narrow the query, or raise MAX_ROWS in .env")
        self.limit = limit


class DatabaseDriver(ABC):
    """Common interface for all databases."""

    name = "base"
    default_port = None
    # True where the connection saves each statement as it runs: then run has nothing to commit or roll back
    autocommit = False

    def __init__(self, connect_timeout: float = None, query_timeout: float = None, max_rows: int = None):
        # Seconds; None = no limit (the library's behaviour)
        self.connect_timeout = connect_timeout or None
        self.query_timeout = query_timeout or None
        # Most rows a query may return (MAX_ROWS); None = no limit
        self.max_rows = max_rows or None

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

    def run(self, connection, sql: str, writes: bool) -> tuple:
        """
        execute_query, then commit where the connection allows writes; elsewhere roll back, for what gets
        past the check of the text (a function that writes, called from a SELECT), after asking the database
        to refuse it where the driver can (read_only). Every driver goes through here, autocommit ones too.
        """
        if not writes:
            self.read_only(connection)
        columns, rows = self.execute_query(connection, sql)
        # Two columns with the same name would be one in the rows, with the value of the last: say it instead
        repeated = next((name for i, name in enumerate(columns) if name in columns[:i]), None)
        if repeated is not None:
            raise ValueError(f"The query returns more than one column named {repeated}: give each its own name, "
                             f"with AS")
        if self.autocommit:
            return columns, rows
        if writes:
            connection.commit()
        else:
            connection.rollback()
        return columns, rows

    def read_only(self, connection):
        """
        Before the query of a connection that does not allow writes: makes the database refuse what changes
        data, where the driver can. Here nothing, and the rollback of run undoes what gets through.
        """

    def open(self, host: str, port: int, database: str, username: str, password: str):
        """connect() with a guaranteed time limit for the whole connection phase."""
        # A connection saved without a port uses the driver's, as the test in the form does
        port = port or self.default_port
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

    def _fetch_rows(self, cursor) -> list:
        """The rows of a DB-API cursor: past max_rows TooManyRows, without reading the rest."""
        if not self.max_rows:
            return cursor.fetchall()
        rows = cursor.fetchmany(self.max_rows + 1)
        self._check_row_count(len(rows))
        return rows

    def _check_row_count(self, count: int):
        """For the drivers that read the rows one at a time: TooManyRows once count is past max_rows."""
        if self.max_rows and count > self.max_rows:
            raise TooManyRows(self.max_rows)

    def _safe_value(self, value):
        """Converts a value to a JSON-safe type."""
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return '<binary>'
        return str(value)
