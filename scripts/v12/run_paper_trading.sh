#!/usr/bin/env bash
# ============================================================
# run_paper_trading.sh — Quick launcher for V12 paper trading
#
# Usage:
#   bash scripts/v12/run_paper_trading.sh                    # All symbols, balanced
#   bash scripts/v12/run_paper_trading.sh SOL                # Single symbol
#   bash scripts/v12/run_paper_trading.sh SOL conservative   # Conservative profile
#   bash scripts/v12/run_paper_trading.sh --once             # Single cycle (smoke test)
#   bash scripts/v12/run_paper_trading.sh --status           # Show current status
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

# Activate venv if available
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Parse args
SYMBOL="${1:-}"
PROFILE="${2:-balanced}"
EXTRA_ARGS=()

if [ "$SYMBOL" = "--once" ] || [ "$PROFILE" = "--once" ]; then
    EXTRA_ARGS+=("--once")
    if [ "$SYMBOL" = "--once" ]; then SYMBOL=""; fi
    if [ "$PROFILE" = "--once" ]; then PROFILE="balanced"; fi
fi

if [ "$SYMBOL" = "--status" ] || [ "$PROFILE" = "--status" ]; then
    EXTRA_ARGS+=("--status")
    if [ "$SYMBOL" = "--status" ]; then SYMBOL=""; fi
    if [ "$PROFILE" = "--status" ]; then PROFILE="balanced"; fi
fi

# Build command
CMD="python -m scripts.v12.paper_trader"
if [ -n "$SYMBOL" ] && [[ ! "$SYMBOL" =~ ^-- ]]; then
    CMD="$CMD --symbol $SYMBOL"
else
    CMD="$CMD --all"
fi
CMD="$CMD --profile $PROFILE"
CMD="$CMD ${EXTRA_ARGS[*]}"

echo "Running: $CMD"
echo ""
eval $CMD
