#!/usr/bin/env bash
# ============================================================
# setup_paper_trading.sh — One-time setup for V12 paper trading
#
# Run this on your machine to:
#   1. Clone the repo (if not already cloned)
#   2. Install Python dependencies
#   3. Download 1m OHLCV data from Bybit
#   4. Train V11 models (needed by V12 paper trader)
#   5. Verify everything works with a smoke test
#
# Usage:
#   bash scripts/v12/setup_paper_trading.sh
#
# Prerequisites:
#   - Python 3.9+
#   - Internet connection (for Bybit API + pip)
#   - ~2GB disk space (data + models)
#
# Time estimate: ~20-30 minutes (mostly data download + training)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/coverdraft/ppmt.git"
PROJECT_DIR="$HOME/ppmt"

echo "=========================================="
echo " PPMT V12 Paper Trading — Setup"
echo "=========================================="
echo ""

# --- 1. Clone repo if needed ---
if [ -d "$PROJECT_DIR" ]; then
    echo "[1/5] Repo already exists at $PROJECT_DIR — pulling latest..."
    cd "$PROJECT_DIR"
    git pull origin main 2>/dev/null || echo "  (pull failed, continuing with local state)"
else
    echo "[1/5] Cloning repo..."
    cd "$HOME"
    git clone "$REPO_URL"
    cd "$PROJECT_DIR"
fi

# --- 2. Create virtual environment ---
if [ ! -d ".venv" ]; then
    echo "[2/5] Creating Python virtual environment..."
    python3 -m venv .venv
else
    echo "[2/5] Virtual environment already exists."
fi

source .venv/bin/activate

# --- 3. Install dependencies ---
echo "[3/5] Installing dependencies..."
pip install --upgrade pip --quiet 2>/dev/null
pip install numpy pandas scipy lightgbm ccxt pyyaml pyarrow --quiet 2>/dev/null

echo "  Installed:"
python -c "import numpy; print(f'    numpy {numpy.__version__}')" 2>/dev/null || echo "    numpy: MISSING"
python -c "import pandas; print(f'    pandas {pandas.__version__}')" 2>/dev/null || echo "    pandas: MISSING"
python -c "import lightgbm; print(f'    lightgbm {lightgbm.__version__}')" 2>/dev/null || echo "    lightgbm: MISSING"
python -c "import ccxt; print(f'    ccxt {ccxt.__version__}')" 2>/dev/null || echo "    ccxt: MISSING"

# --- 4. Download 1m data ---
echo ""
echo "[4a/5] Checking 1m OHLCV data cache..."

CACHE_DIR="data/v10/ohlcv_cache"
mkdir -p "$CACHE_DIR"

NEED_DOWNLOAD=false
for SYM in SOL DOGE AVAX BTC ETH; do
    if [ ! -f "$CACHE_DIR/${SYM}_1m.parquet" ]; then
        NEED_DOWNLOAD=true
        break
    fi
done

if [ "$NEED_DOWNLOAD" = true ]; then
    echo "  Downloading 1m data from Bybit (~10-15 min)..."
    python scripts/v12/download_1m_data.py --days 365
else
    echo "  1m data cache exists:"
    ls -lh "$CACHE_DIR"/*_1m.parquet 2>/dev/null | awk '{print "    " $NF, $5}'
fi

# --- 4b. Train V11 models ---
echo ""
echo "[4b/5] Checking V11 models (required by V12 paper trader)..."

MODELS_EXIST=true
for SYM in SOL DOGE AVAX; do
    if [ ! -f "data/v11/models/v11_clf_${SYM}_h12.txt" ]; then
        MODELS_EXIST=false
        break
    fi
done

if [ "$MODELS_EXIST" = true ]; then
    echo "  V11 models already exist — skipping training."
    echo "  Models:"
    ls -lh data/v11/models/v11_clf_*_h12.txt 2>/dev/null | awk '{print "    " $NF, $5}'
else
    echo "  Building V11 dataset..."
    python scripts/v11/v11_build_dataset.py

    echo "  Training V11 models for H=12..."
    python scripts/v11/v11_train.py --horizon 12

    echo "  Models trained:"
    ls -lh data/v11/models/v11_clf_*_h12.txt 2>/dev/null | awk '{print "    " $NF, $5}'
fi

# --- 5. Smoke test ---
echo ""
echo "[5/5] Running smoke test (single cycle, SOL)..."
python -m scripts.v12.paper_trader --symbol SOL --once
SMOKE_EXIT=$?

echo ""
echo "=========================================="
if [ $SMOKE_EXIT -eq 0 ]; then
    echo " ✓ Setup COMPLETE!"
    echo ""
    echo " === How to run paper trading ==="
    echo ""
    echo " Single symbol (foreground):"
    echo "   cd $PROJECT_DIR && source .venv/bin/activate"
    echo "   python -m scripts.v12.paper_trader --symbol SOL"
    echo ""
    echo " All symbols (background):"
    echo "   nohup python -m scripts.v12.paper_trader --all > /tmp/v12_all.log 2>&1 &"
    echo ""
    echo " Quick launcher:"
    echo "   bash scripts/v12/run_paper_trading.sh SOL"
    echo "   bash scripts/v12/run_paper_trading.sh --all conservative"
    echo ""
    echo " Cron mode (every 5 minutes):"
    echo "   */5 * * * * sleep 30 && cd $PROJECT_DIR && source .venv/bin/activate && \\"
    echo "     python -m scripts.v12.paper_trader --symbol SOL --once >> /tmp/v12_SOL.cron.log 2>&1"
    echo ""
    echo " Check status:"
    echo "   python -m scripts.v12.paper_trader --status --symbol SOL"
    echo ""
    echo " Review performance:"
    echo "   cat data/paper_trading/v12_logs/signals_v12_SOL.csv | tail -5"
    echo ""
    echo " Ship criteria (2-4 weeks):"
    echo "   Sharpe > 0.3, MaxDD > -15%, WR > 55%, N trades >= 30"
else
    echo " ⚠ Smoke test returned exit code $SMOKE_EXIT"
    echo " Check the error messages above."
    echo " Common fixes:"
    echo "   - Missing deps: pip install lightgbm ccxt numpy pandas"
    echo "   - API timeout: retry in a few minutes"
    echo "   - No data: python scripts/v12/download_1m_data.py"
fi
echo "=========================================="
