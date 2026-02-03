"""Classe base per tutti i driver database."""
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class DatabaseDriver(ABC):
    """Interfaccia comune per tutti i database."""

    name = "base"
    default_port = None

    @abstractmethod
    def connect(self, host: str, port: int, database: str, username: str, password: str):
        """Crea e restituisce una connessione."""
        pass

    @abstractmethod
    def execute_query(self, connection, sql: str) -> tuple:
        """
        Esegue una query e restituisce (columns, rows).
        rows è una lista di dizionari.
        """
        pass

    def close(self, connection):
        """Chiude una connessione. Override se il driver lo richiede."""
        try:
            connection.close()
        except AttributeError:
            # Alcuni driver (es. ibm_db) non hanno connection.close()
            pass

    def test_connection(self, host: str, port: int, database: str, username: str, password: str) -> dict:
        """Testa la connessione in modo driver-agnostico."""
        try:
            conn = self.connect(host, port, database, username, password)
            self.close(conn)
            return {'status': 'ok', 'message': 'Connessione riuscita'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def test_query(self, connection, sql: str, limit: int = 5) -> dict:
        """Testa una query restituendo un sample."""
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
                'error': str(e)
            }

    def _safe_value(self, value):
        """Converte valore in tipo JSON-safe."""
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return '<binary>'
        return str(value)
