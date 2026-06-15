#!/bin/bash
# PPMT Terminal - Fresh Setup Script
# Run this after git pull to get a clean install

set -e

echo "=== PPMT Terminal - Fresh Setup ==="
echo ""

# Clean old build artifacts
echo "[1/5] Cleaning old build artifacts..."
rm -rf src/*.egg-info
rm -rf build/ dist/ *.egg
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

# Pull latest
echo "[2/5] Pulling latest from GitHub..."
git pull origin main

# Install/upgrade pip
echo "[3/5] Upgrading pip..."
pip install --upgrade pip

# Install PPMT Terminal
echo "[4/5] Installing PPMT Terminal..."
pip install -e . --force-reinstall --no-deps
pip install -e .

# Optional: exchange support
echo "[5/5] Installing exchange support (optional)..."
pip install ccxt>=4.0.0 2>/dev/null || echo "  (ccxt not installed - that's OK for paper trading)"

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Quick Start:"
echo "  ppmt init"
echo "  ppmt ingest -s BTC/USDT -t 1h -d 30"
echo "  ppmt build -s BTC/USDT -t 1h"
echo "  ppmt run -s BTC/USDT --replay"
echo "  ppmt run -s BTC/USDT              # Live WebSocket"
echo "  ppmt terminal                      # Web dashboard"
echo "  ppmt scan                          # Find assets"
echo "  ppmt portfolio                     # Portfolio overview"
echo ""
echo "Version: $(ppmt --version 2>/dev/null || echo 'unknown')"
