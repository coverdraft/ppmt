#!/bin/bash
# Run download in small per-window chunks to avoid sandbox CPU/wall-clock limits
cd /home/z/my-project
LOG=/home/z/my-project/logs/v5_chunked_download.log
echo "=== Chunked download started $(date) ===" > $LOG

# Order: smaller TFs first (5m, 15m), then 1m in separate chunks per window
WINDOWS="BULL_2024 RANGE_2025 RECENT_2026 BEAR_2022 RANGE_2023"

for WIN in $WINDOWS; do
  echo "" >> $LOG
  echo "=== Window: $WIN (5m + 15m) ===" >> $LOG
  /home/z/.venv/bin/python -u scripts/v5_download_multiex.py \
      --timeframes 5m 15m --windows $WIN --workers 1 >> $LOG 2>&1
  echo "EXIT=$?" >> $LOG
done

# Then 1m, one window at a time (largest memory/CPU)
for WIN in $WINDOWS; do
  echo "" >> $LOG
  echo "=== Window: $WIN (1m) ===" >> $LOG
  /home/z/.venv/bin/python -u scripts/v5_download_multiex.py \
      --timeframes 1m --windows $WIN --workers 1 >> $LOG 2>&1
  echo "EXIT=$?" >> $LOG
done

echo "" >> $LOG
echo "=== ALL DONE $(date) ===" >> $LOG
