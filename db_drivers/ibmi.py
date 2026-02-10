"""Driver IBM i (AS/400) via JT400 JDBC.

Usa JTOpen (open source) per connettersi a IBM i.
Richiede: Java 8+, JPype1==1.5.1, jt400.jar in lib/

Perché questo casino? Vedi docs/ibmi-driver.md
"""
import os
import logging
from .base import DatabaseDriver

logger = logging.getLogger(__name__)

_jvm_started = False


def _ensure_jvm():
    """Avvia la JVM se non già attiva."""
    global _jvm_started
    if _jvm_started:
        return
    
    import jpype
    
    if jpype.isJVMStarted():
        _jvm_started = True
        return
    
    # Trova jt400.jar
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jar_path = os.path.join(base_dir, 'lib', 'jt400.jar')
    
    if not os.path.exists(jar_path):
        raise FileNotFoundError(
            f"jt400.jar non trovato in {jar_path}\n"
            "Scaricalo da: https://repo1.maven.org/maven2/net/sf/jt400/jt400/20.0.7/jt400-20.0.7.jar\n"
            "Rinominalo jt400.jar e mettilo nella cartella lib/"
        )
    
    jpype.startJVM(classpath=[jar_path])
    import jpype.imports  # Abilita import da Java
    _jvm_started = True
    logger.info("JVM avviata per driver IBM i")


class IBMiDriver(DatabaseDriver):
    """Driver per IBM i (AS/400) via JDBC JT400."""
    
    name = "ibmi"
    default_port = 446

    def connect(self, host: str, port: int, database: str, username: str, password: str, **kwargs):
        """Connessione via JT400 JDBC."""
        _ensure_jvm()
        
        from java.sql import DriverManager
        
        # database = library
        url = f"jdbc:as400://{host}"
        if database:
            url += f";libraries={database}"
        url += ";naming=sql;errors=full;date format=iso"
        
        return DriverManager.getConnection(url, username, password)

    def execute_query(self, connection, sql: str) -> tuple:
        """Esegue query e restituisce (columns, rows)."""
        stmt = connection.createStatement()
        rs = stmt.executeQuery(sql)
        
        meta = rs.getMetaData()
        col_count = meta.getColumnCount()
        columns = [str(meta.getColumnName(i + 1)) for i in range(col_count)]
        
        rows = []
        while rs.next():
            row = {}
            for i, col in enumerate(columns):
                val = rs.getObject(i + 1)
                row[col] = self._java_to_python(val)
            rows.append(row)
        
        rs.close()
        stmt.close()
        
        return columns, rows

    def _java_to_python(self, val):
        """Converte oggetti Java in tipi Python nativi."""
        if val is None:
            return None
        
        class_name = val.getClass().getName()
        
        if class_name == 'java.lang.String':
            return str(val)
        elif class_name in ('java.lang.Integer', 'java.lang.Long', 'java.lang.Short', 'java.lang.Byte'):
            return int(val)
        elif class_name in ('java.lang.Float', 'java.lang.Double'):
            return float(val)
        elif class_name == 'java.lang.Boolean':
            return bool(val)

    def close(self, connection):
        """Chiude la connessione."""
        connection.close()

    def test_connection(self, host: str, port: int, database: str, username: str, password: str, **kwargs) -> dict:
        """Test connessione."""
        try:
            conn = self.connect(host, port, database, username, password, **kwargs)
            
            stmt = conn.createStatement()
            rs = stmt.executeQuery("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            rs.next()
            rs.close()
            stmt.close()
            
            self.close(conn)
            return {'status': 'ok', 'message': 'Connessione riuscita'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}