#!/bin/bash
cd /home/z/my-project
/home/z/.venv/bin/python -u scripts/v5_download_multiex.py \
    --timeframes 5m 15m 1m --workers 1 \
    > /home/z/my-project/logs/v5_full_download.log 2>&1
EC=$?
echo "EXIT_CODE=$EC" >> /home/z/my-project/logs/v5_full_download.log
# Capture signal if killed
trap 'echo "KILLED BY SIGNAL $?" >> /home/z/my-project/logs/v5_full_download.log' TERM INT KILL
