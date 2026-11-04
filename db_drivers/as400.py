"""Driver AS/400 (DB2 for i)."""
import logging
import os
import sys
import site

# Adds clidriver/bin to the DLL path before importing ibm_db
# pip install ibm_db downloads the clidriver into site-packages
# Ugly code for an ugly problem.
if sys.platform == 'win32':
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        clidriver_bin = os.path.join(sp, 'clidriver', 'bin')
        if os.path.isdir(clidriver_bin):
            os.add_dll_directory(clidriver_bin)
            os.environ['PATH'] = clidriver_bin + ';' + os.environ.get('PATH', '')
            break

import ibm_db
from .base import DatabaseDriver

logger = logging.getLogger(__name__)


class AS400Driver(DatabaseDriver):
    name = "as400"
    default_port = 50000

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        """Creates and returns an ibm_db connection."""
        conn_str = (
            f"DATABASE={database};"
            f"HOSTNAME={host};"
            f"PORT={port};"
            f"PROTOCOL=TCPIP;"
            f"UID={username};"
            f"PWD={password};"
        )
        if self.connect_timeout:
            # CLI ConnectTimeout keyword: maximum wait for the connection
            conn_str += f"CONNECTTIMEOUT={int(self.connect_timeout)};"
        conn = ibm_db.connect(conn_str, "", "")
        if not conn:
            raise ConnectionError("Cannot connect to the AS/400 database")
        return conn

    def execute_query(self, connection, sql: str) -> tuple:
        """Runs a query and returns (columns, rows) as a list of dictionaries."""
        if self.query_timeout:
            stmt = ibm_db.prepare(connection, sql)
            if not stmt:
                raise RuntimeError("Error preparing the query")
            try:
                ibm_db.set_option(stmt, {ibm_db.SQL_ATTR_QUERY_TIMEOUT: int(self.query_timeout)}, 0)
            except Exception as e:
                logger.warning(f"AS/400: query timeout not set ({e})")
            if not ibm_db.execute(stmt):
                raise RuntimeError("Error running the query")
        else:
            stmt = ibm_db.exec_immediate(connection, sql)
        if not stmt:
            raise RuntimeError("Error running the query")

        # Column names
        num_fields = ibm_db.num_fields(stmt)
        columns = [ibm_db.field_name(stmt, i) for i in range(num_fields)]

        rows = []
        try:
            row = ibm_db.fetch_assoc(stmt)
            while row:
                row_dict = {col: self._safe_value(row.get(col)) for col in columns}
                rows.append(row_dict)
                row = ibm_db.fetch_assoc(stmt)
        finally:
            ibm_db.free_stmt(stmt)

        return columns, rows

    def close(self, connection):
        """Closes the ibm_db connection."""
        ibm_db.close(connection)

    def test_connection(self, host: str, port: int, database: str, username: str, password: str) -> dict:
        """Override to use ibm_db.close correctly."""
        try:
            conn = self.open(host, port, database, username, password)
            self.close(conn)
            return {'status': 'ok', 'message': 'Connection successful'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
