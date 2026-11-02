"""Driver MySQL/MariaDB."""
import pymysql
from .base import DatabaseDriver


class MySQLDriver(DatabaseDriver):
    name = "mysql"
    default_port = 3306

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        params = dict(
            host=host,
            port=port,
            database=database,
            user=username,
            password=password
        )
        if self.connect_timeout:
            params['connect_timeout'] = self.connect_timeout
        # connect_timeout covers only the TCP connection: without read_timeout
        # pymysql waits forever, even for the server greeting
        read_timeout = self.query_timeout or self.connect_timeout
        if read_timeout:
            params['read_timeout'] = read_timeout
            params['write_timeout'] = read_timeout
        return pymysql.connect(**params)

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