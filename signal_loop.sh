#!/bin/bash
cd /home/z/my-project/ppmt
export PYTHONPATH=src
LOG="/home/z/my-project/ppmt/signal_daemon.log"

exec >> "$LOG" 2>&1
echo "[$(date)] === SIGNAL LOOP STARTED ==="

while true; do
    echo "[$(date)] --- Cycle start ---"
    python3 signal_daemon.py --once
    echo "[$(date)] --- Cycle end, sleeping 60s ---"
    sleep 60
done
