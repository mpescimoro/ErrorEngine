"""Driver Oracle (thin mode)."""
import oracledb
from .base import DatabaseDriver


class OracleDriver(DatabaseDriver):
    name = "oracle"
    default_port = 1521

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        dsn = f"{host}:{port}/{database}"
        params = dict(user=username, password=password, dsn=dsn)
        if self.connect_timeout:
            # Bounds only the TCP connection: the whole connection phase
            # is bounded by DatabaseDriver.open()
            params['tcp_connect_timeout'] = float(self.connect_timeout)
        connection = oracledb.connect(**params)
        if self.query_timeout:
            # Milliseconds for each call to the server (execute and fetch)
            connection.call_timeout = int(self.query_timeout * 1000)
        return connection

    def execute_query(self, connection, sql: str) -> tuple:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = []
            for row in cursor:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if isinstance(value, oracledb.LOB):
                        value = value.read()
                    row_dict[col] = self._safe_value(value)
                rows.append(row_dict)
            return columns, rows
        finally:
            cursor.close()