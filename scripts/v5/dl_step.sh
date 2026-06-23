#!/bin/bash
# Single-step download: argv1=tf, argv2=window, argv3=optional tokens csv
TF=$1
WIN=$2
TOKENS=$3
LOG=/home/z/my-project/logs/v5_dl_${TF}_${WIN}.log
cd /home/z/my-project
ARGS="--timeframes $TF --windows $WIN --workers 1"
if [ -n "$TOKENS" ]; then
  ARGS="$ARGS --tokens $TOKENS"
fi
/home/z/.venv/bin/python -u scripts/v5_download_multiex.py $ARGS > $LOG 2>&1
echo "EXIT=$?" >> $LOG
