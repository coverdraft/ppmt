#!/usr/bin/env bash
# ============================================================
# rebuild_and_retrain.sh — Rebuild V11 dataset + retrain models
#
# Fixes the timestamp/merge bug that caused SOL and AVAX to have
# 0 rows in the dataset. Uses v2 build script with:
#   - Robust timestamp normalization (auto-detect ms/seconds/datetime)
#   - merge_asof instead of exact merge (prevents row duplication)
#   - Deduplication + validation after every step
#
# Usage:
#   cd ~/ppmt && source .venv/bin/activate
#   bash scripts/v12/rebuild_and_retrain.sh
#
# This will:
#   1. Pull latest code from GitHub
#   2. Verify all 1m data files exist (download if missing)
#   3. Rebuild the V11 dataset with the fixed script
#   4. Retrain V11 models (H=12 only, for paper trading)
#   5. Show summary
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " V11 Rebuild + Retrain (fix v2)"
echo "=========================================="
echo " Project: $PROJECT_DIR"
echo ""

# --- 1. Pull latest code ---
echo "[1/5] Pulling latest code..."
git pull origin main 2>/dev/null || echo "  (pull failed, continuing with local state)"

# --- 2. Activate venv ---
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "ERROR: No .venv found. Run setup_paper_trading.sh first."
    exit 1
fi

# --- 3. Verify 1m data exists for all symbols ---
echo ""
echo "[2/5] Checking 1m data files..."
MISSING=()
for SYM in SOL DOGE AVAX BTC ETH; do
    FPATH="data/v10/ohlcv_cache/${SYM}_1m.parquet"
    if [ -f "$FPATH" ]; then
        ROWS=$(python -c "import pandas as pd; print(len(pd.read_parquet('$FPATH')))" 2>/dev/null || echo "0")
        echo "  $SYM: $ROWS rows ✓"
    else
        echo "  $SYM: MISSING ✗"
        MISSING+=("$SYM")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "  Downloading missing symbols: ${MISSING[*]}"
    python scripts/v12/download_1m_data.py --symbols "$(IFS=,; echo "${MISSING[*]}")" --days 365
fi

# --- 4. Quick diagnostic on 1m data ---
echo ""
echo "[3/5] Quick diagnostic on 1m data..."
python -c "
import pandas as pd
import numpy as np
from pathlib import Path

cache = Path('data/v10/ohlcv_cache')
for sym in ['SOL', 'DOGE', 'AVAX', 'BTC']:
    p = cache / f'{sym}_1m.parquet'
    if p.exists():
        df = pd.read_parquet(p, columns=['timestamp'])
        ts = df['timestamp'].values
        median_ts = np.median(ts)
        if median_ts > 1e12:
            fmt = 'ms'
            span_days = (ts.max() - ts.min()) / (1000 * 86400)
        elif median_ts > 1e9:
            fmt = 'seconds'
            span_days = (ts.max() - ts.min()) / 86400
        else:
            fmt = 'UNKNOWN (corrupted?)'
            span_days = 0
        print(f'  {sym}: {len(df):,} rows, format={fmt}, span={span_days:.0f} days')
    else:
        print(f'  {sym}: file not found')
"

# --- 5. Rebuild dataset ---
echo ""
echo "[4/5] Rebuilding V11 dataset (with v2 fixes)..."
# Remove old dataset to force rebuild
if [ -f "data/v11/v11_dataset.parquet" ]; then
    OLD_ROWS=$(python -c "import pandas as pd; print(len(pd.read_parquet('data/v11/v11_dataset.parquet')))" 2>/dev/null || echo "?")
    echo "  Old dataset: $OLD_ROWS rows — removing..."
    rm data/v11/v11_dataset.parquet
fi

python scripts/v11/v11_build_dataset.py

# Validate the new dataset
echo ""
echo "  Validating new dataset..."
python -c "
import pandas as pd
import numpy as np

df = pd.read_parquet('data/v11/v11_dataset.parquet')
ts = df['timestamp'].values
span_days = (ts.max() - ts.min()) / (1000 * 86400)
symbols = df['symbol'].unique().tolist()

print(f'  Total rows: {len(df):,}')
print(f'  Symbols: {symbols}')
print(f'  Timestamp span: {span_days:.1f} days')
print(f'  Timestamp range: {ts.min()} to {ts.max()}')
for sym in symbols:
    n = len(df[df['symbol'] == sym])
    print(f'    {sym}: {n:,} rows')

# Check for duplicates
dupes = df.duplicated(subset=['timestamp', 'symbol']).sum()
if dupes > 0:
    print(f'  WARNING: {dupes} duplicate (timestamp, symbol) pairs!')
else:
    print(f'  No duplicates ✓')
"

# --- 6. Retrain models ---
echo ""
echo "[5/5] Retraining V11 models (H=12 for paper trading)..."
python scripts/v11/v11_train.py --horizon 12

echo ""
echo "=========================================="
echo " Done! Check results above."
echo ""
echo " Models saved in: data/v11/models/"
echo " Results JSON: data/v11/models/v11_results.json"
echo ""
echo " Next: Start paper trading"
echo "   bash scripts/v12/run_paper_trading.sh SOL"
echo "=========================================="
