"""Driver SQL Server."""
import pymssql
from .base import DatabaseDriver


class SQLServerDriver(DatabaseDriver):
    name = "sqlserver"
    default_port = 1433

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        params = dict(
            server=host,
            port=port,
            database=database,
            user=username,
            password=password
        )
        if self.connect_timeout:
            params['login_timeout'] = int(self.connect_timeout)
        if self.query_timeout:
            # A SELECT waiting for a lock is stopped past the limit
            params['timeout'] = int(self.query_timeout)
        return pymssql.connect(**params)

    def execute_query(self, connection, sql: str) -> tuple:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            if not cursor.description:
                return [], []   # nothing to read, as after an INSERT where writes are allowed: some drivers fail on fetch
            columns = [desc[0] for desc in cursor.description]
            rows = []
            for row in self._fetch_rows(cursor):
                row_dict = {col: self._safe_value(row[i]) for i, col in enumerate(columns)}
                rows.append(row_dict)
            return columns, rows
        finally:
            cursor.close()