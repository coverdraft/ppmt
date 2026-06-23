#!/bin/bash
# Orquestador: descarga todos los chunks de 4 tokens × 1 tf × 1 window
# secuencialmente, esperando entre cada chunk para evitar matar el proceso.

set -e
LOG=/home/z/my-project/logs/v5_orchestrator.log
cd /home/z/my-project
echo "=== Orchestrator started $(date) ===" > $LOG

TOKENS_PER_CHUNK=4
WINDOWS="BULL_2024 RANGE_2025 RECENT_2026 BEAR_2022 RANGE_2023"
# Use shorter lists for BEAR_2022 and RANGE_2023 (PEPE/WIF/BONK don't exist)
ALL_TOKENS="BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT AVAXUSDT LINKUSDT DOGEUSDT SHIBUSDT PEPEUSDT WIFUSDT BONKUSDT"

chunk_tokens() {
  local list=$1
  local out=""
  local n=0
  for t in $list; do
    if [ $n -eq 0 ]; then
      out="$t"
    else
      out="$out $t"
    fi
    n=$((n+1))
    if [ $n -ge $TOKENS_PER_CHUNK ]; then
      echo "$out"
      out=""
      n=0
    fi
  done
  if [ -n "$out" ]; then
    echo "$out"
  fi
}

# Order: 5m and 15m first (small, fast), then 1m (large, slow)
for TF in 5m 15m 1m; do
  for WIN in $WINDOWS; do
    for chunk in $(chunk_tokens "$ALL_TOKENS"); do
      echo "[$(date)] TF=$TF WIN=$WIN tokens='$chunk'" >> $LOG
      # Run in foreground, no parallelism, blocking
      /home/z/.venv/bin/python -u scripts/v5_download_multiex.py \
          --timeframes $TF --windows $WIN --tokens $chunk --workers 1 \
          >> /home/z/my-project/logs/v5_dl_${TF}_${WIN}.log 2>&1
      EC=$?
      echo "[$(date)] exit=$EC" >> $LOG
      # Small pause between chunks to be polite
      sleep 2
    done
  done
done

echo "=== Orchestrator done $(date) ===" >> $LOG
