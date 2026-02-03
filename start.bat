@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   ErrorEngine - Monitoring System
echo ========================================
echo.

REM Verifica Python 3 usando py launcher
py -3 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRORE] Python 3 non trovato.
    echo Installa Python 3.9+ da https://python.org
    echo Assicurati che "py launcher" sia installato.
    pause
    exit /b 1
)

REM Mostra versione Python
for /f "tokens=*" %%i in ('py -3 --version') do set PYVER=%%i
echo [OK] Trovato: %PYVER%

REM Crea virtual environment se non esiste
if not exist "venv" (
    echo.
    echo [INFO] Creazione ambiente virtuale...
    py -3 -m venv venv
)

REM Attiva virtual environment
echo [INFO] Attivazione ambiente virtuale...
call venv\Scripts\activate.bat

REM Installa dipendenze
echo [INFO] Verifica dipendenze...
pip install -r requirements.txt --quiet

REM Carica variabili d'ambiente da .env se esiste
if exist ".env" (
    echo [INFO] Caricamento configurazione da .env
    for /f "tokens=*" %%a in (.env) do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            if not "!line!"=="" (
                set "%%a"
            )
        )
    )
)

REM Avvia applicazione
echo.
echo ========================================
echo   Avvio server su http://localhost:5000
echo   Premi CTRL+C per terminare
echo ========================================
echo.

python app.py

pause
