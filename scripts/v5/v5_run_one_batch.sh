#!/bin/bash
# Run v5_dl_remaining.py for at most 90 seconds, then exit cleanly
cd /home/z/my-project
timeout 90 /home/z/.venv/bin/python -u scripts/v5_dl_remaining.py \
    >> /home/z/my-project/logs/v5_dl_remaining.log 2>&1
echo "BATCH EXIT=$? ts=$(date +%s)" >> /home/z/my-project/logs/v5_dl_remaining.log
