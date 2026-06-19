#!/usr/bin/env bash
#
# rebuild_all.sh — Rebuild todos los tries persistidos con semántica v0.40.24
#
# SIN ESTE REBUILD, v0.40.23/v0.40.24 son NOOP en vivo.
# Los tries viejos tienen wins≡count (v0.40.22) o won-on-pattern (v0.40.23).
# Hay que reconstruirlos desde la data OHLCV almacenada para que apliquen
# la nueva semántica won=post-pattern-outcome.
#
# USO:
#   ./rebuild_all.sh                  # rebuild todos los symbols/timeframes
#   ./rebuild_all.sh --dry-run        # solo mostrar qué haría, sin ejecutar
#   ./rebuild_all.sh --symbol BTCUSDT # solo un symbol
#   ./rebuild_all.sh --timeframe 1m   # solo un timeframe
#
# REQUISITOS:
#   - ppmt instalado (pip install -e .)
#   - data OHLCV ya ingestada (ppmt ingest -s ... -t ...)
#   - Python 3 con sqlite3 module (stdlib, viene con Python)
#
# SALIDA:
#   - Por cada (symbol, timeframe): log de progreso + resultado
#   - Al final: tabla resumen con éxitos/fallos
#   - Backup automático de tries viejos a ~/.ppmt/tries_backup_v<timestamp>/
#
set -euo pipefail

# === Config ===
PPMT_DB="${PPMT_DB:-$HOME/.ppmt/ppmt.db}"
DRY_RUN=0
FILTER_SYMBOL=""
FILTER_TIMEFRAME=""
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$HOME/.ppmt/tries_backup_v04024_${TIMESTAMP}"
LOG_FILE="$HOME/.ppmt/rebuild_all_${TIMESTAMP}.log"

# === Parse args ===
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --symbol) FILTER_SYMBOL="$2"; shift 2 ;;
        --timeframe) FILTER_TIMEFRAME="$2"; shift 2 ;;
        --help|-h)
            echo "Uso: $0 [--dry-run] [--symbol SYM] [--timeframe TF]"
            echo "  Rebuild todos los tries con semántica v0.40.24."
            exit 0
            ;;
        *) echo "Arg desconocido: $1"; exit 1 ;;
    esac
done

# === Checks ===
if ! command -v ppmt &>/dev/null; then
    echo "ERROR: ppmt no está instalado. Ejecutá: pip install -e ."
    exit 1
fi

if [[ ! -f "$PPMT_DB" ]]; then
    echo "ERROR: No existe $PPMT_DB"
    echo "  ¿Instalaste ppmt? ¿Corriste 'ppmt ingest' alguna vez?"
    exit 1
fi

PPMT_VERSION=$(ppmt --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
echo "PPMT version: $PPMT_VERSION"
if [[ "$PPMT_VERSION" < "0.40.24" ]]; then
    echo "ERROR: Necesitás v0.40.24 o superior. Tenés $PPMT_VERSION"
    echo "  Ejecutá: git pull origin main && pip install -e ."
    exit 1
fi

echo "DB: $PPMT_DB"
echo "Log: $LOG_FILE"
echo ""

# === Backup tries viejos ===
mkdir -p "$BACKUP_DIR"
echo "Backup de tries viejos en: $BACKUP_DIR"
# Copiar cualquier archivo de trie que exista
if [[ -d "$HOME/.ppmt/storage" ]]; then
    cp -r "$HOME/.ppmt/storage" "$BACKUP_DIR/storage" 2>/dev/null || true
fi
# Dumpear tabla tries con Python (sqlite3 CLI puede no estar instalado)
python3 -c "
import sqlite3, sys, os
db = '$PPMT_DB'
out = '$BACKUP_DIR/tries_table.sql'
try:
    conn = sqlite3.connect(db)
    with open(out, 'w') as f:
        for line in conn.iterdump():
            if 'tries' in line.lower() or 'CREATE TABLE' in line or 'INSERT INTO' in line:
                f.write(line + '\n')
    conn.close()
    print(f'  Backup tabla tries: {out}')
except Exception as e:
    print(f'  (no se pudo dumpear tabla tries: {e})', file=sys.stderr)
" 2>&1 || true
echo ""

# === Obtener lista de (symbol, timeframe) a rebuild ===
ROWS=$(python3 -c "
import sqlite3
db = '$PPMT_DB'
symbol_filter = '$FILTER_SYMBOL'
tf_filter = '$FILTER_TIMEFRAME'
conn = sqlite3.connect(db)
q = 'SELECT DISTINCT symbol, timeframe FROM ohlcv'
conds = []
if symbol_filter:
    conds.append(f\"symbol = '{symbol_filter}'\")
if tf_filter:
    conds.append(f\"timeframe = '{tf_filter}'\")
if conds:
    q += ' WHERE ' + ' AND '.join(conds)
q += ' ORDER BY symbol, timeframe'
for sym, tf in conn.execute(q).fetchall():
    print(f'{sym}|{tf}')
conn.close()
")

if [[ -z "$ROWS" ]]; then
    echo "No hay data OHLCV en $PPMT_DB."
    echo "  Corré 'ppmt ingest -s BTCUSDT -t 1h --days 30' para cargar data."
    exit 0
fi

# Contar rows
N_ROWS=$(echo "$ROWS" | wc -l | tr -d ' ')
echo "Encontrados $N_ROWS (symbol, timeframe) para rebuild:"
echo "$ROWS" | while IFS='|' read -r sym tf; do
    echo "  - $sym | $tf"
done
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] No se ejecuta nada. Sacá --dry-run para rebuild real."
    exit 0
fi

# === Rebuild loop ===
SUCCESS=0
FAIL=0
FAILED_LIST=()

echo "=== REBUILD START $(date) ===" | tee -a "$LOG_FILE"

while IFS='|' read -r SYMBOL TF; do
    echo "" | tee -a "$LOG_FILE"
    echo ">>> Rebuilding $SYMBOL $TF" | tee -a "$LOG_FILE"

    if ppmt build -s "$SYMBOL" -t "$TF" 2>&1 | tee -a "$LOG_FILE"; then
        echo "  ✓ OK: $SYMBOL $TF" | tee -a "$LOG_FILE"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "  ✗ FAIL: $SYMBOL $TF" | tee -a "$LOG_FILE"
        FAIL=$((FAIL + 1))
        FAILED_LIST+=("$SYMBOL $TF")
    fi
done <<< "$ROWS"

echo "" | tee -a "$LOG_FILE"
echo "=== REBUILD END $(date) ===" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "RESUMEN:" | tee -a "$LOG_FILE"
echo "  Éxitos: $SUCCESS" | tee -a "$LOG_FILE"
echo "  Fallos: $FAIL" | tee -a "$LOG_FILE"
if [[ $FAIL -gt 0 ]]; then
    echo "  Fallidos:" | tee -a "$LOG_FILE"
    for f in "${FAILED_LIST[@]}"; do
        echo "    - $f" | tee -a "$LOG_FILE"
    done
fi
echo "" | tee -a "$LOG_FILE"
echo "Backup de tries viejos: $BACKUP_DIR" | tee -a "$LOG_FILE"
echo "Log completo: $LOG_FILE" | tee -a "$LOG_FILE"
echo ""

# === Verificación post-rebuild ===
echo "=== VERIFICACIÓN ==="
echo "Para cada symbol rebuilt, el ratio wins/count debería ser 0.4-0.6"
echo "(con v0.40.22 era siempre 1.0). Verificá con:"
echo ""
echo "$ROWS" | while IFS='|' read -r sym tf; do
    echo "  ppmt stats -s $sym -t $tf"
done
echo ""
echo "Si los ratios siguen en 1.0, el rebuild falló silenciosamente."
echo "Revisá $LOG_FILE"

exit $FAIL
