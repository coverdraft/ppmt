#!/usr/bin/env bash
# run_paper.sh — Lanzador conveniente para el paper trader.
# Uso:  ./scripts/v5/run_paper.sh              # 7 días, thr=0.80
#        ./scripts/v5/run_paper.sh --days 1    # 1 día, thr=0.80
#        ./scripts/v5/run_paper.sh --threshold 0.70 --days 7
#
# Después de arrancar:
#   tail -f logs/v5_paper_trader.log        # ver progreso
#   pgrep -f v5_paper_trader                # ver PID
#   kill <PID>                              # frenar
#   ./scripts/v5/run_paper.sh               # resumir (sin --fresh-state)
#
# Para arrancar de cero (ignorar estado guardado):
#   ./scripts/v5/run_paper.sh --fresh-state

set -e

cd "$(dirname "$0")/../.."

# Activar venv si existe, sino usar python3 del sistema
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

# Crear dirs necesarios
mkdir -p logs state/v5_cb_v2

# Si ya hay un trader corriendo, no arrancar otro
if pgrep -f "v5_paper_trader_cb_v2.py" > /dev/null; then
    echo "ERROR: ya hay un paper trader corriendo (PID $(pgrep -f v5_paper_trader_cb_v2.py))"
    echo "Para frenarlo: kill $(pgrep -f v5_paper_trader_cb_v2.py)"
    exit 1
fi

# Defaults
DAYS=7
THRESHOLD=0.80
EXTRA_ARGS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days)        DAYS="$2"; shift 2 ;;
        --threshold)   THRESHOLD="$2"; shift 2 ;;
        --fresh-state) EXTRA_ARGS="$EXTRA_ARGS --fresh-state"; shift ;;
        *)             EXTRA_ARGS="$EXTRA_ARGS $1"; shift ;;
    esac
done

echo "Arrancando paper trader: days=$DAYS threshold=$THRESHOLD"
echo "Log:     logs/v5_paper_trader.log"
echo "State:   state/v5_cb_v2/paper_trader_state.json"
echo "Para frenar: kill \$!  (o pgrep -f v5_paper_trader)"
echo "---"

nohup python3 scripts/v5/v5_paper_trader_cb_v2.py \
    --mode live \
    --days "$DAYS" \
    --threshold "$THRESHOLD" \
    --position-usd 100 \
    --max-concurrent 3 \
    --leverage 7 \
    --account 10000 \
    $EXTRA_ARGS \
    > /dev/null 2>&1 &

PID=$!
echo "PID=$PID"
echo "Verificar: ps -p $PID"
echo "Log live:  tail -f logs/v5_paper_trader.log"
