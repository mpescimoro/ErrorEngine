"""Factory per driver database con import condizionali.

Ogni driver viene caricato solo se la libreria corrispondente è installata.
SQLite è sempre disponibile (stdlib Python).
"""
import logging

logger = logging.getLogger(__name__)

DRIVERS = {}
DRIVER_LABELS = {}

# SQLite — sempre disponibile (stdlib)
from .sqlite import SQLiteDriver
DRIVERS['sqlite'] = SQLiteDriver
DRIVER_LABELS['sqlite'] = 'SQLite'

# Oracle (oracledb, thin mode)
try:
    from .oracle import OracleDriver
    DRIVERS['oracle'] = OracleDriver
    DRIVER_LABELS['oracle'] = 'Oracle'
except ImportError:
    logger.debug("Driver Oracle non disponibile (pip install oracledb)")

# PostgreSQL (psycopg2)
try:
    from .postgres import PostgresDriver
    DRIVERS['postgres'] = PostgresDriver
    DRIVER_LABELS['postgres'] = 'PostgreSQL'
except ImportError:
    logger.debug("Driver PostgreSQL non disponibile (pip install psycopg2-binary)")

# MySQL / MariaDB (pymysql)
try:
    from .mysql import MySQLDriver
    DRIVERS['mysql'] = MySQLDriver
    DRIVER_LABELS['mysql'] = 'MySQL / MariaDB'
except ImportError:
    logger.debug("Driver MySQL non disponibile (pip install pymysql)")

# SQL Server (pymssql)
try:
    from .sqlserver import SQLServerDriver
    DRIVERS['sqlserver'] = SQLServerDriver
    DRIVER_LABELS['sqlserver'] = 'SQL Server'
except ImportError:
    logger.debug("Driver SQL Server non disponibile (pip install pymssql)")

# AS/400 - DB2 for i (ibm_db)
try:
    from .as400 import AS400Driver
    DRIVERS['as400'] = AS400Driver
    DRIVER_LABELS['as400'] = 'AS/400 (DB2)'
except ImportError:
    logger.debug("Driver AS/400 non disponibile (pip install ibm_db)")


def get_driver(db_type: str):
    """Restituisce un'istanza del driver richiesto."""
    driver_class = DRIVERS.get(db_type.lower())
    if not driver_class:
        available = ', '.join(DRIVERS.keys())
        raise ValueError(
            f"Driver non supportato o non installato: {db_type}. "
            f"Disponibili: {available}"
        )
    return driver_class()


def get_available_drivers() -> dict:
    """Restituisce solo i driver effettivamente disponibili."""
    return DRIVER_LABELS.copy()
