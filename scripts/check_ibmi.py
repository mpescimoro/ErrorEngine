r"""
Manual check of the connection to IBM i (AS/400) with the JT400 driver.

Not an automated test: run it by hand. It holds no credentials and reads them
from the environment (or asks for them). From the project folder:

    set IBMI_HOST=192.168.x.x
    set IBMI_USER=USER
    set IBMI_LIBRARY=LIBRARY          (optional)
    venv\Scripts\python.exe scripts\check_ibmi.py

On Linux/macOS: export IBMI_HOST=... and python scripts/check_ibmi.py.
The password can be passed with IBMI_PASSWORD; if missing, it is asked without echo.
"""
import getpass
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def main() -> int:
    host = os.environ.get('IBMI_HOST') or input('IBM i host: ').strip()
    user = os.environ.get('IBMI_USER') or input('User: ').strip()
    password = os.environ.get('IBMI_PASSWORD') or getpass.getpass('Password: ')
    library = os.environ.get('IBMI_LIBRARY', '')

    from db_drivers.ibmi import IBMiDriver
    driver = IBMiDriver(connect_timeout=15, query_timeout=30)
    result = driver.test_connection(host, None, library, user, password)
    print(f"{result['status'].upper()}: {result['message']}")
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
