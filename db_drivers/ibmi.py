"""IBM i (AS/400) driver via JT400 JDBC.

Uses JTOpen (open source) to connect to IBM i.
Requires: Java 8+, JPype1==1.5.1, jt400.jar in lib/

Why all this mess? See docs/ibmi-driver.md
"""
import os
import logging
import threading
from .base import DatabaseDriver
from decimal import Decimal

logger = logging.getLogger(__name__)

_jvm_started = False
# Queries run in parallel: only one thread may start the JVM, the others wait for it
_jvm_lock = threading.Lock()


def _ensure_jvm():
    """Starts the JVM if it is not running yet (once, even with threads in parallel)."""
    if _jvm_started:
        return
    with _jvm_lock:
        if not _jvm_started:
            _start_jvm()


def _start_jvm():
    global _jvm_started
    import jpype
    
    if jpype.isJVMStarted():
        _jvm_started = True
        return
    
    # Find jt400.jar
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jar_path = os.path.join(base_dir, 'lib', 'jt400.jar')
    
    if not os.path.exists(jar_path):
        raise FileNotFoundError(
            f"jt400.jar not found at {jar_path}\n"
            "Download it from: https://repo1.maven.org/maven2/net/sf/jt400/jt400/20.0.7/jt400-20.0.7.jar\n"
            "Rename it to jt400.jar and put it in the lib/ folder"
        )
    
    jpype.startJVM(classpath=[jar_path])
    import jpype.imports  # Enables Java imports
    _jvm_started = True
    logger.info("JVM started for the IBM i driver")


def _attach_as_daemon():
    """
    Attaches the current thread to the JVM as a daemon: a stuck JDBC call
    (a host that does not answer) must not keep the process from exiting.
    """
    from java.lang import Thread
    if not Thread.isAttached():
        Thread.attachAsDaemon()


class IBMiDriver(DatabaseDriver):
    """IBM i (AS/400) driver via JT400 JDBC."""
    
    name = "ibmi"
    default_port = 446

    def open(self, host: str, port: int, database: str, username: str, password: str):
        # The JVM starts in the calling thread: the connection thread, which can
        # hang on a silent host, attaches as a daemon and does not keep the process alive
        _ensure_jvm()
        return super().open(host, port, database, username, password)

    def connect(self, host: str, port: int, database: str, username: str, password: str, **kwargs):
        """Connection via JT400 JDBC."""
        _ensure_jvm()
        _attach_as_daemon()
        
        from java.sql import DriverManager
        
        # database = library
        url = f"jdbc:as400://{host}"
        if database:
            url += f";libraries={database}"
        url += ";naming=sql;errors=full;date format=iso"
        if self.connect_timeout:
            url += f";login timeout={int(self.connect_timeout)}"
        limit = max(self.connect_timeout or 0, self.query_timeout or 0)
        if limit:
            # Without a socket timeout JT400 waits forever for a host that does not answer
            url += f";socket timeout={int((limit + 30) * 1000)}"
        if self.query_timeout:
            # Really stops the query when the time is up (the default relies on estimates)
            url += ";query timeout mechanism=cancel"
        
        return DriverManager.getConnection(url, username, password)

    def execute_query(self, connection, sql: str) -> tuple:
        """Runs a query and returns (columns, rows)."""
        _attach_as_daemon()
        stmt = connection.createStatement()
        try:
            if self.query_timeout:
                stmt.setQueryTimeout(int(self.query_timeout))
            rs = stmt.executeQuery(sql)
            try:
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
            finally:
                rs.close()
        finally:
            stmt.close()
        
        return columns, rows

    def _java_to_python(self, val):
        """Converts Java objects to native Python types."""
        if val is None:
            return None
        class_name = val.getClass().getName()

        if class_name == 'java.lang.String':
            return str(val)

        elif class_name in (
            'java.lang.Integer',
            'java.lang.Long',
            'java.lang.Short',
            'java.lang.Byte'
        ):
            return int(val)

        elif class_name in ('java.lang.Float', 'java.lang.Double'):
            return float(val)
        elif class_name == 'java.math.BigDecimal':
            stripped = val.stripTrailingZeros()
            if stripped.scale() <= 0:
                # toBigInteger, not longValue: a long keeps only the low 64 bits
                return int(str(stripped.toBigInteger()))
            return Decimal(str(stripped))
        elif class_name == 'java.lang.Boolean':
            return bool(val)

        return str(val)

    def close(self, connection):
        """Closes the connection."""
        connection.close()

    def test_connection(self, host: str, port: int, database: str, username: str, password: str, **kwargs) -> dict:
        """Connection test."""
        try:
            conn = self.open(host, port, database, username, password)
            
            stmt = conn.createStatement()
            rs = stmt.executeQuery("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            rs.next()
            rs.close()
            stmt.close()
            
            self.close(conn)
            return {'status': 'ok', 'message': 'Connection successful'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}