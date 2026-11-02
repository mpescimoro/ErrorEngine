"""Driver SQLite."""
import sqlite3
import os
import time
from .base import DatabaseDriver


class SQLiteDriver(DatabaseDriver):
    name = "sqlite"
    default_port = None  # No port

    def open(self, host: str, port: int, database: str, username: str, password: str):
        # Local file: no network wait to bound, and a SQLite connection
        # must be used in the thread that created it
        return self.connect(host, port, database, username, password)

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        # Maximum wait when the file is locked by another process
        busy_timeout = self.connect_timeout or 5
        # For SQLite: database = file path
        # Empty or ":memory:" means an in-memory database
        if not database or database == ':memory:':
            return sqlite3.connect(':memory:', timeout=busy_timeout)

        # Normalize the path (Windows support)
        db_path = os.path.normpath(database)

        # Check that the file exists
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")

        return sqlite3.connect(db_path, timeout=busy_timeout)

    def execute_query(self, connection, sql: str) -> tuple:
        deadline = time.monotonic() + self.query_timeout if self.query_timeout else None
        if deadline:
            # SQLite has no query timeout: the progress handler stops the query
            connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = []
            for row in cursor.fetchall():
                row_dict = {col: self._safe_value(row[i]) for i, col in enumerate(columns)}
                rows.append(row_dict)
            return columns, rows
        except sqlite3.OperationalError as e:
            if deadline and time.monotonic() > deadline and 'interrupted' in str(e):
                raise TimeoutError(f"Query stopped after {self.query_timeout:g} seconds") from e
            raise
        finally:
            cursor.close()
            if deadline:
                connection.set_progress_handler(None, 0)
