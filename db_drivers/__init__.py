"""Database driver factory with conditional imports.

Each driver is loaded only if its library is installed.
SQLite is always available (Python standard library).
"""
import logging

logger = logging.getLogger(__name__)

DRIVERS = {}
DRIVER_LABELS = {}

# SQLite: always available (standard library)
from .sqlite import SQLiteDriver
DRIVERS['sqlite'] = SQLiteDriver
DRIVER_LABELS['sqlite'] = 'SQLite'

# Oracle (oracledb, thin mode)
try:
    from .oracle import OracleDriver
    DRIVERS['oracle'] = OracleDriver
    DRIVER_LABELS['oracle'] = 'Oracle'
except ImportError:
    logger.debug("Oracle driver not available (pip install oracledb)")

# PostgreSQL (psycopg2)
try:
    from .postgres import PostgresDriver
    DRIVERS['postgres'] = PostgresDriver
    DRIVER_LABELS['postgres'] = 'PostgreSQL'
except ImportError:
    logger.debug("PostgreSQL driver not available (pip install psycopg2-binary)")

# MySQL / MariaDB (pymysql)
try:
    from .mysql import MySQLDriver
    DRIVERS['mysql'] = MySQLDriver
    DRIVER_LABELS['mysql'] = 'MySQL / MariaDB'
except ImportError:
    logger.debug("MySQL driver not available (pip install pymysql)")

# SQL Server (pymssql)
try:
    from .sqlserver import SQLServerDriver
    DRIVERS['sqlserver'] = SQLServerDriver
    DRIVER_LABELS['sqlserver'] = 'SQL Server'
except ImportError:
    logger.debug("SQL Server driver not available (pip install pymssql)")

# AS/400 - DB2 for i (ibm_db)
try:
    from .as400 import AS400Driver
    DRIVERS['as400'] = AS400Driver
    DRIVER_LABELS['as400'] = 'AS/400 (DB2)'
except ImportError:
    logger.debug("AS/400 driver not available (pip install ibm_db)")

# IBM i (AS/400 via JT400 JDBC) - needs Java and jt400.jar
try:
    from .ibmi import IBMiDriver
    DRIVERS['ibmi'] = IBMiDriver
    DRIVER_LABELS['ibmi'] = 'IBM i (AS/400)'
except ImportError as e:
    logger.debug(f"IBM i driver not available: {e}")


def driver_timeouts() -> dict:
    """Connection and query timeouts (seconds) from the app configuration."""
    try:
        from flask import current_app
        cfg = current_app.config
    except RuntimeError:  # outside the Flask context: default values
        cfg = {}
    return {
        'connect_timeout': cfg.get('DB_CONNECT_TIMEOUT_SECONDS', 15),
        'query_timeout': cfg.get('DB_QUERY_TIMEOUT_SECONDS', 300),
    }


def get_driver(db_type: str, connect_timeout: float = None, query_timeout: float = None):
    """Returns an instance of the requested driver."""
    driver_class = DRIVERS.get(db_type.lower())
    if not driver_class:
        available = ', '.join(DRIVERS.keys())
        raise ValueError(
            f"Driver not supported or not installed: {db_type}. "
            f"Available: {available}"
        )
    return driver_class(connect_timeout=connect_timeout, query_timeout=query_timeout)


def get_available_drivers() -> dict:
    """Returns only the drivers that are actually available."""
    return DRIVER_LABELS.copy()
