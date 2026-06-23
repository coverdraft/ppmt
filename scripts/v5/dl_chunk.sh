#!/bin/bash
# Download a small chunk: argv1=tf argv2=window argv3=space-sep tokens
TF=$1; WIN=$2; TOKENS=$3
LOG=/home/z/my-project/logs/v5_dl_${TF}_${WIN}_chunk.log
cd /home/z/my-project
/home/z/.venv/bin/python -u scripts/v5_download_multiex.py \
    --timeframes $TF --windows $WIN --tokens $TOKENS --workers 1 >> $LOG 2>&1
echo "EXIT=$? ts=$(date +%s)" >> $LOG
