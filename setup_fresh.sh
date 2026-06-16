#!/bin/bash
# PPMT Terminal v0.32.0 - Fresh Setup Script (macOS + Linux compatible)
# Run this after git pull to get a clean install with the FastAPI dashboard.
#
# Usage:
#   git pull origin main
#   bash setup_fresh.sh
#
# After setup, start the dashboard with:
#   ppmt terminal
#   → open http://localhost:8420

set -e

echo "=== PPMT Terminal v0.32.0 - Fresh Setup ==="
echo ""

# ------------------------------------------------------------------
# Detect Python (macOS usually has python3, not python)
# ------------------------------------------------------------------
if command -v python3 &> /dev/null; then
    PY=python3
elif command -v python &> /dev/null; then
    PY=python
else
    echo "ERROR: Python 3 is not installed."
    echo "  macOS:  brew install python3      (or install Xcode Command Line Tools: xcode-select --install)"
    echo "  Linux:  sudo apt install python3 python3-pip python3-venv"
    exit 1
fi
echo "Using Python: $($PY --version)"

# ------------------------------------------------------------------
# Detect pip — prefer python -m pip (works everywhere)
# ------------------------------------------------------------------
PIP="$PY -m pip"
if ! $PIP --version &> /dev/null; then
    echo "ERROR: pip is not installed for $($PY --version)."
    echo "  macOS:  curl https://bootstrap.pypa.io/get-pip.py | $PY -"
    echo "  Linux:  sudo apt install python3-pip"
    exit 1
fi

# ------------------------------------------------------------------
# Step 1 — Clean old build artifacts
# ------------------------------------------------------------------
echo ""
echo "[1/6] Cleaning old build artifacts..."
rm -rf src/*.egg-info 2>/dev/null || true
rm -rf build/ dist/ *.egg 2>/dev/null || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

# ------------------------------------------------------------------
# Step 2 — Pull latest from GitHub
# ------------------------------------------------------------------
echo ""
echo "[2/6] Pulling latest from GitHub..."
git pull origin main

# ------------------------------------------------------------------
# Step 3 — Upgrade pip
# ------------------------------------------------------------------
echo ""
echo "[3/6] Upgrading pip..."
$PIP install --upgrade pip

# ------------------------------------------------------------------
# Step 4 — Install PPMT Python engine
# ------------------------------------------------------------------
echo ""
echo "[4/6] Installing PPMT Terminal (Python engine)..."
$PIP install -e .

# Optional: exchange support for live data
echo "  Installing exchange support (ccxt)..."
$PIP install "ccxt>=4.0.0" 2>/dev/null || echo "  (ccxt not installed — OK for paper trading)"

# ------------------------------------------------------------------
# Step 5 — Initialize database
# ------------------------------------------------------------------
echo ""
echo "[5/6] Initializing database..."
PYTHONPATH=src $PY -m ppmt.cli.main init || echo "  (DB already initialized)"

# ------------------------------------------------------------------
# Step 6 — Verify installation
# ------------------------------------------------------------------
echo ""
echo "[6/6] Verifying installation..."
PPMT_VER=$($PY -c "import sys; sys.path.insert(0,'src'); import ppmt; print('OK')" 2>/dev/null || echo "FAIL")
echo "  PPMT import: $PPMT_VER"

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Quick Start:"
echo "  ppmt terminal                                   # Start dashboard → http://localhost:8420"
echo "  ppmt terminal --open-browser                    # Start + open browser automatically"
echo ""
echo "  ppmt init                                       # Initialize database"
echo "  ppmt ingest --symbol BTC/USDT --timeframe 1h --days 30    # Fetch historical data"
echo "  ppmt build --symbol BTC/USDT --timeframe 1h    # Build Trie"
echo "  ppmt predict --symbol BTC/USDT --timeframe 1h  # See current prediction"
echo "  ppmt list                                       # List tracked assets"
echo ""
echo "Or open the dashboard and use 'PREPARAR Y VALIDAR' button:"
echo "  ppmt terminal --open-browser"
echo "  → http://localhost:8420"
