#!/bin/bash

echo "========================================"
echo "  ErrorEngine - Monitoring System"
echo "========================================"
echo

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 not found."
    echo "Install Python 3.11+ with: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PYVER=$(python3 --version)
echo "[OK] Found: $PYVER"

# Create the virtual environment if missing
if [ ! -d "venv" ]; then
    echo
    echo "[INFO] Creating the virtual environment..."
    python3 -m venv venv
fi

# Activate the virtual environment
echo "[INFO] Activating the virtual environment..."
source venv/bin/activate

# Install the dependencies
echo "[INFO] Checking dependencies..."
pip install -r requirements.txt --quiet

# Start the production server (Waitress, a single process): serve.py reads .env
# by itself and also writes the log to logs/errorengine.log
echo
echo "========================================"
echo "  Server starting on http://localhost:5000"
echo "  Press CTRL+C to stop"
echo "========================================"
echo

python serve.py
