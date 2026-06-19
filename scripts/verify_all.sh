#!/usr/bin/env bash
#
# verify_all.sh — Verificar que el rebuild aplicó la semántica v0.40.24
#
# Después de correr rebuild_all.sh, este script lee todos los tries
# persistidos y reporta:
#   - Total de patterns por symbol/timeframe
#   - Cuántos patterns tienen wins < count (señal de que FaseC está activa)
#   - Ratio aggregate wins/count por symbol/timeframe
#
# Si TODOS los ratios son 1.0 → el rebuild falló silenciosamente.
# Si ALGUNOS ratios son < 1.0 → FaseC activa, listo para operar.
#
# USO:
#   bash scripts/verify_all.sh
#   bash scripts/verify_all.sh --symbol BTCUSDT
#
set -euo pipefail

PPMT_DB="${PPMT_DB:-$HOME/.ppmt/ppmt.db}"
FILTER_SYMBOL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbol) FILTER_SYMBOL="$2"; shift 2 ;;
        --help|-h)
            echo "Uso: $0 [--symbol SYM]"
            echo "  Verifica que el rebuild aplicó wins<count en los tries."
            exit 0
            ;;
        *) echo "Arg desconocido: $1"; exit 1 ;;
    esac
done

if [[ ! -f "$PPMT_DB" ]]; then
    echo "ERROR: No existe $PPMT_DB"
    exit 1
fi

echo "Verificando tries en $PPMT_DB..."
echo ""

# Llamar a Python con el script inline
# Nota: el script python no puede usar __file__ (es stdin), así que
# intentamos importar ppmt del entorno instalado. Si falla, fallar limpio.
python3 - "$PPMT_DB" "$FILTER_SYMBOL" << 'PYEOF'
import sqlite3
import sys

db_path = sys.argv[1]
filter_symbol = sys.argv[2]

# Importar ppmt (asumir que está instalado vía pip install -e .)
try:
    from ppmt.data.storage import PPMTStorage
except ImportError as e:
    print(f"ERROR: no se puede importar ppmt. ¿Corriste 'pip install -e .'?")
    print(f"  Detalle: {e}")
    sys.exit(2)

# Listar (symbol, timeframe)
conn = sqlite3.connect(db_path)
q = "SELECT DISTINCT symbol, timeframe FROM ohlcv"
if filter_symbol:
    q += f" WHERE symbol = '{filter_symbol}'"
q += " ORDER BY symbol, timeframe"
combos = conn.execute(q).fetchall()
conn.close()

if not combos:
    print("No hay data OHLCV en la DB.")
    sys.exit(0)

print(f"Encontrados {len(combos)} (symbol, timeframe) para verificar.")
print()
print(f"{'SYMBOL':<15} {'TF':<5} {'PATTERNS':<10} {'TOTAL_COUNT':<13} {'TOTAL_WINS':<12} {'RATIO':<8} {'WINS<CT':<8} STATUS")
print("-" * 90)

storage = PPMTStorage()
total_patterns_all = 0
total_wins_lt_count_all = 0
n_active = 0
n_inactive = 0
n_no_trie = 0

for symbol, timeframe in combos:
    # Cargar N3 trie (per_asset). La tabla `tries` usa (symbol, level)
    # donde symbol = "BTCUSDT" y level = "n3".
    try:
        trie = storage.load_trie(symbol, "n3")
    except Exception:
        trie = None

    if trie is None:
        print(f"{symbol:<15} {timeframe:<5} {'-':<10} {'-':<13} {'-':<12} {'-':<8} {'-':<8} NO_TRIE")
        n_no_trie += 1
        continue

    total_count_long = 0
    total_wins_long = 0
    total_count_short = 0
    total_wins_short = 0
    n_patterns = 0
    n_wins_lt_count = 0  # patterns donde wins < count (LONG o SHORT)

    try:
        for pat, node in trie.get_all_patterns(min_count=1):
            m = node.metadata
            n_patterns += 1
            if m.long_stats.count > 0:
                total_count_long += m.long_stats.count
                total_wins_long += m.long_stats.wins
                if m.long_stats.wins < m.long_stats.count:
                    n_wins_lt_count += 1
            if m.short_stats.count > 0:
                total_count_short += m.short_stats.count
                total_wins_short += m.short_stats.wins
                if m.short_stats.wins < m.short_stats.count:
                    n_wins_lt_count += 1
    except Exception as e:
        print(f"{symbol:<15} {timeframe:<5} ERROR: {e}")
        continue

    total_count = total_count_long + total_count_short
    total_wins = total_wins_long + total_wins_short
    ratio = (total_wins / total_count) if total_count > 0 else 0.0

    if total_count == 0:
        status = "EMPTY"
    elif ratio >= 0.99:
        status = "INACTIVE"  # wins == count, FaseC no aplicó
        n_inactive += 1
    else:
        status = "ACTIVE"  # wins < count en algunos, FaseC activa
        n_active += 1

    print(f"{symbol:<15} {timeframe:<5} {n_patterns:<10} {total_count:<13} {total_wins:<12} {ratio:<8.3f} {n_wins_lt_count:<8} {status}")

    total_patterns_all += n_patterns
    total_wins_lt_count_all += n_wins_lt_count

storage.close()

print()
print("=" * 90)
print(f"RESUMEN AGGREGATE:")
print(f"  Total patterns en todos los tries: {total_patterns_all}")
print(f"  Total patterns con wins < count:   {total_wins_lt_count_all}")
print(f"  Tries con FaseC ACTIVE:            {n_active}")
print(f"  Tries con FaseC INACTIVE:          {n_inactive}")
print(f"  Tries sin trie persistido:         {n_no_trie}")
print()
if n_active > 0 and n_inactive == 0:
    print("✓ PASS: FaseC v0.40.24 activa en todos los tries con data.")
    print("  Listo para arrancar `ppmt terminal` y validar 24-48h.")
elif n_active > 0:
    print("⚠ PARCIAL: FaseC activa en algunos tries, inactive en otros.")
    print("  Los inactive pueden ser por sample muy chico (todos los patterns ganaron).")
    print("  Revisar manualmente con `ppmt stats -s <SYM> -t <TF>` los marcados INACTIVE.")
else:
    print("✗ FAIL: ningún trie tiene wins < count.")
    print("  El rebuild NO aplicó la nueva semántica.")
    print("  Verificar:")
    print("    1. ¿Se corrió `git pull && pip install -e .`?")
    print("    2. ¿`ppmt --version` dice 0.40.24?")
    print("    3. Revisar el log del rebuild en ~/.ppmt/rebuild_all_*.log")
PYEOF
