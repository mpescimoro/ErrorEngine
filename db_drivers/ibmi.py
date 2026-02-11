"""IBM i (AS/400) driver via JT400 JDBC.

Uses JTOpen (open source) to connect to IBM i.
Requires: Java 8+, JPype1==1.5.1, jt400.jar in lib/

Why all this mess? See docs/ibmi-driver.md
"""
import os
import logging
from .base import DatabaseDriver
from decimal import Decimal

logger = logging.getLogger(__name__)

_jvm_started = False


def _ensure_jvm():
    """Start the JVM if not already running."""
    global _jvm_started
    if _jvm_started:
        return

    import jpype

    if jpype.isJVMStarted():
        _jvm_started = True
        return

    # Locate jt400.jar
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jar_path = os.path.join(base_dir, 'lib', 'jt400.jar')

    if not os.path.exists(jar_path):
        raise FileNotFoundError(
            f"jt400.jar not found at {jar_path}\n"
            "Download it from: https://repo1.maven.org/maven2/net/sf/jt400/jt400/20.0.7/jt400-20.0.7.jar\n"
            "Rename it to jt400.jar and place it in the lib/ folder"
        )

    jpype.startJVM(classpath=[jar_path])
    import jpype.imports  # Enable Java imports
    _jvm_started = True
    logger.info("JVM started for IBM i driver")


class IBMiDriver(DatabaseDriver):
    """IBM i (AS/400) driver via JT400 JDBC."""

    name = "ibmi"
    default_port = 446

    def connect(self, host: str, port: int, database: str, username: str, password: str, **kwargs):
        """Connect via JT400 JDBC."""
        _ensure_jvm()

        from java.sql import DriverManager

        # database = library
        url = f"jdbc:as400://{host}"
        if database:
            url += f";libraries={database}"
        url += ";naming=sql;errors=full;date format=iso"

        return DriverManager.getConnection(url, username, password)

    def execute_query(self, connection, sql: str) -> tuple:
        """Execute query and return (columns, rows)."""
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
        """Convert Java objects to native Python types."""
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
                return int(stripped.longValue())
            return Decimal(str(stripped))

        elif class_name == 'java.lang.Boolean':
            return bool(val)

        return str(val)

    def close(self, connection):
        """Close the connection."""
        connection.close()

    def test_connection(self, host: str, port: int, database: str, username: str, password: str, **kwargs) -> dict:
        """Test the connection."""
        try:
            conn = self.connect(host, port, database, username, password, **kwargs)

            stmt = conn.createStatement()
            rs = stmt.executeQuery("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            rs.next()
            rs.close()
            stmt.close()

            self.close(conn)
            return {'status': 'ok', 'message': 'Connection successful'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}