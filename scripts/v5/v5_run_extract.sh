#!/bin/bash
cd /home/z/my-project
timeout 90 /home/z/.venv/bin/python -u scripts/v5_extract_features.py --timeframes 5m 15m --skip-done \
    >> /home/z/my-project/logs/v5_extract.log 2>&1
echo "BATCH EXIT=$? ts=$(date +%s)" >> /home/z/my-project/logs/v5_extract.log
