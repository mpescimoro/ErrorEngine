"""Driver Oracle (thin mode)."""
import oracledb
from .base import DatabaseDriver


class OracleDriver(DatabaseDriver):
    name = "oracle"
    default_port = 1521
    
    def connect(self, host: str, port: int, database: str, username: str, password: str):
        dsn = f"{host}:{port}/{database}"
        return oracledb.connect(user=username, password=password, dsn=dsn)
    
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