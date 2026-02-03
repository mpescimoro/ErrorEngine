"""Driver SQLite."""
import sqlite3
import os
from .base import DatabaseDriver


class SQLiteDriver(DatabaseDriver):
    name = "sqlite"
    default_port = None  # Non usa porta
    
    def connect(self, host: str, port: int, database: str, username: str, password: str):
        # Per SQLite: database = path del file
        # Se vuoto o ":memory:", usa database in memoria
        if not database or database == ':memory:':
            return sqlite3.connect(':memory:')
        
        # Normalizza il path (supporto Windows)
        db_path = os.path.normpath(database)
        
        # Verifica che il file esista
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database non trovato: {db_path}")
        
        return sqlite3.connect(db_path)
    
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
