#!/bin/bash

echo "========================================"
echo "  ErrorEngine - Monitoring System"
echo "========================================"
echo

# Verifica Python 3
if ! command -v python3 &> /dev/null; then
    echo "[ERRORE] Python 3 non trovato."
    echo "Installa Python 3.9+ con: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PYVER=$(python3 --version)
echo "[OK] Trovato: $PYVER"

# Crea virtual environment se non esiste
if [ ! -d "venv" ]; then
    echo
    echo "[INFO] Creazione ambiente virtuale..."
    python3 -m venv venv
fi

# Attiva virtual environment
echo "[INFO] Attivazione ambiente virtuale..."
source venv/bin/activate

# Installa dipendenze
echo "[INFO] Verifica dipendenze..."
pip install -r requirements.txt --quiet

# Carica variabili d'ambiente da .env se esiste
if [ -f ".env" ]; then
    echo "[INFO] Caricamento configurazione da .env"
    export $(grep -v '^#' .env | xargs)
fi

# Avvia applicazione
echo
echo "========================================"
echo "  Avvio server su http://localhost:5000"
echo "  Premi CTRL+C per terminare"
echo "========================================"
echo

python app.py
