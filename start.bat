@echo off
setlocal

echo ========================================
echo   ErrorEngine - Monitoring System
echo ========================================
echo.

REM Check for Python 3 with the py launcher
py -3 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3 not found.
    echo Install Python 3.11+ from https://python.org
    echo Make sure the "py launcher" is installed.
    pause
    exit /b 1
)

REM Show the Python version
for /f "tokens=*" %%i in ('py -3 --version') do set PYVER=%%i
echo [OK] Found: %PYVER%

REM Create the virtual environment if missing
if not exist "venv" (
    echo.
    echo [INFO] Creating the virtual environment...
    py -3 -m venv venv
)

REM Activate the virtual environment
echo [INFO] Activating the virtual environment...
call venv\Scripts\activate.bat

REM Install the dependencies
echo [INFO] Checking dependencies...
pip install -r requirements.txt --quiet

REM Start the production server (Waitress, a single process): serve.py reads .env
REM by itself and also writes the log to logs\errorengine.log
echo.
echo ========================================
echo   Server starting on http://localhost:5000
echo   Press CTRL+C to stop
echo ========================================
echo.

python serve.py

pause
