"""Driver PostgreSQL."""
import psycopg2
from .base import DatabaseDriver


class PostgresDriver(DatabaseDriver):
    name = "postgres"
    default_port = 5432

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        params = dict(
            host=host,
            port=port,
            dbname=database,
            user=username,
            password=password
        )
        if self.connect_timeout:
            # libpq accepts at least 2 seconds
            params['connect_timeout'] = max(2, int(self.connect_timeout))
        if self.query_timeout:
            # The server stops the query past the limit
            params['options'] = f'-c statement_timeout={int(self.query_timeout * 1000)}'
        return psycopg2.connect(**params)

    def execute_query(self, connection, sql: str) -> tuple:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = []
            for row in cursor.fetchall():
                row_dict = {col: self._safe_value(row[i]) for i, col in enumerate(columns)}
                rows.append(row_dict)
            return columns, rows
        finally:
            cursor.close()