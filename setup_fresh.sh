#!/bin/bash
# PPMT Terminal v0.14.1 - Fresh Setup Script
# Run this after git pull to get a clean install with both Python engine AND Next.js dashboard

set -e

echo "=== PPMT Terminal v0.14.1 - Fresh Setup ==="
echo ""

# Clean old build artifacts
echo "[1/7] Cleaning old build artifacts..."
rm -rf src/*.egg-info
rm -rf build/ dist/ *.egg
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

# Pull latest
echo "[2/7] Pulling latest from GitHub..."
git pull origin main

# Install/upgrade pip
echo "[3/7] Upgrading pip..."
pip install --upgrade pip

# Install PPMT Terminal (Python engine)
echo "[4/7] Installing PPMT Terminal (Python engine)..."
pip install -e . --force-reinstall --no-deps
pip install -e .

# Optional: exchange support
echo "[5/7] Installing exchange support (optional)..."
pip install "ccxt>=4.0.0" 2>/dev/null || echo "  (ccxt not installed - OK for paper trading)"

# Install Next.js dashboard dependencies
echo "[6/7] Installing Next.js dashboard..."
if command -v node &> /dev/null; then
    npm install
    # Create .env if not exists
    if [ ! -f .env ]; then
        echo 'DATABASE_URL="file:./prisma/dev.db"' > .env
    fi
    # Initialize Prisma database
    npx prisma db push --schema=./prisma/schema.prisma
    echo "  Next.js dashboard ready!"
else
    echo "  Node.js not found. Install it from https://nodejs.org"
    echo "  Or use: ppmt terminal --lite (FastAPI dashboard)"
fi

# Verify installation
echo "[7/7] Verifying installation..."
PPMT_VER=$(ppmt --version 2>/dev/null || echo "unknown")
echo "  PPMT version: $PPMT_VER"

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Quick Start:"
echo "  ppmt init                          # Initialize database"
echo "  ppmt ingest -s BTC/USDT -t 1h -d 30  # Fetch data"
echo "  ppmt build -s BTC/USDT -t 1h       # Build Trie"
echo "  ppmt run -s BTC/USDT --replay       # Test with historical data"
echo "  ppmt run -s BTC/USDT                # Live WebSocket"
echo "  ppmt terminal                       # Next.js dashboard (port 3000)"
echo "  ppmt terminal --lite                # FastAPI dashboard (port 8420)"
echo "  ppmt scan                           # Find assets"
echo "  ppmt portfolio                      # Portfolio overview"
