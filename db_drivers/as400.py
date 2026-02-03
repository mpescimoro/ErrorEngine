"""Driver AS/400 (DB2 for i)."""
import os
import sys
import site

# Aggiunge clidriver/bin al path DLL prima di importare ibm_db
# pip install ibm_db scarica il clidriver dentro site-packages
# Codice brutto per un problema brutto ma scritto bene
if sys.platform == 'win32':
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        clidriver_bin = os.path.join(sp, 'clidriver', 'bin')
        if os.path.isdir(clidriver_bin):
            os.add_dll_directory(clidriver_bin)
            os.environ['PATH'] = clidriver_bin + ';' + os.environ.get('PATH', '')
            break

import ibm_db
from .base import DatabaseDriver


class AS400Driver(DatabaseDriver):
    name = "as400"
    default_port = 50000

    def connect(self, host: str, port: int, database: str, username: str, password: str):
        """Crea e restituisce una connessione ibm_db."""
        conn_str = (
            f"DATABASE={database};"
            f"HOSTNAME={host};"
            f"PORT={port};"
            f"PROTOCOL=TCPIP;"
            f"UID={username};"
            f"PWD={password};"
        )
        conn = ibm_db.connect(conn_str, "", "")
        if not conn:
            raise ConnectionError("Impossibile connettersi al database AS/400")
        return conn

    def execute_query(self, connection, sql: str) -> tuple:
        """Esegue una query e restituisce (columns, rows) come lista di dizionari."""
        stmt = ibm_db.exec_immediate(connection, sql)
        if not stmt:
            raise RuntimeError("Errore nell'esecuzione della query")

        # Ottieni nomi colonne
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
        """Chiude la connessione ibm_db."""
        ibm_db.close(connection)

    def test_connection(self, host: str, port: int, database: str, username: str, password: str) -> dict:
        """Override per usare ibm_db.close correttamente."""
        try:
            conn = self.connect(host, port, database, username, password)
            self.close(conn)
            return {'status': 'ok', 'message': 'Connessione riuscita'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
