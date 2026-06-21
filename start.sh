#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  PPMT — One-Command Startup for macOS
#  v0.58.0 · TAREA 20
#
#  Uso:
#    bash start.sh
#
#  Flujo:
#    1. Verifica Python 3.11+
#    2. Crea venv si no existe
#    3. Activa venv
#    4. Instala dependencias si es necesario
#    5. Levanta uvicorn en puerto 8000
#    6. Imprime URL de acceso
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ─── Colores ──────────────────────────────────────────────────────────
RST='\033[0m'
RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
CYN='\033[0;36m'
WHT='\033[1;37m'
DIM='\033[2m'

# ─── Rutas ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PORT=8000

echo ""
echo -e "${WHT}═══════════════════════════════════════════════════${RST}"
echo -e "${CYN}  PPMT — Probabilistic Pattern Matching Engine${RST}"
echo -e "${WHT}═══════════════════════════════════════════════════${RST}"
echo ""

# ══════════════════════════════════════════════════════════════════════
#  1. VERIFICAR PYTHON 3.11+
# ══════════════════════════════════════════════════════════════════════
echo -e "${CYN}[1/4]${RST} Verificando Python 3.11+..."

PY=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        # Extraer major.minor
        PY_VER=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

        if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 11 ]; }; then
            PY="$cmd"
            echo -e "  ${GRN}✓${RST} Python $PY_VER encontrado ($cmd)"
            break
        else
            echo -e "  ${YEL}⚠${RST}  $cmd es $PY_VER (necesita 3.11+)"
        fi
    fi
done

if [ -z "$PY" ]; then
    echo -e "${RED}ERROR:${RST} Python 3.11+ no encontrado."
    echo "  Instala con:  brew install python@3.11"
    echo "  O:           https://www.python.org/downloads/"
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════
#  2. CREAR VENV SI NO EXISTE
# ══════════════════════════════════════════════════════════════════════
echo -e "${CYN}[2/4]${RST} Verificando entorno virtual..."

if [ ! -d "$VENV_DIR" ]; then
    echo -e "  ${YEL}venv no encontrado. Creando...${RST}"
    $PY -m venv "$VENV_DIR"
    echo -e "  ${GRN}✓${RST} venv creado en $VENV_DIR"
else
    echo -e "  ${GRN}✓${RST} venv existente"
fi

# ══════════════════════════════════════════════════════════════════════
#  3. ACTIVAR VENV + INSTALAR DEPENDENCIAS
# ══════════════════════════════════════════════════════════════════════
echo -e "${CYN}[3/4]${RST} Activando venv e instalando dependencias..."

# Activar venv
source "$VENV_DIR/bin/activate"

# Instalar ppmt en modo editable (resuelve todas las deps de pyproject.toml)
if ! python -c "import ppmt" 2>/dev/null; then
    echo -e "  ${DIM}Instalando ppmt + dependencias...${RST}"
    cd "$SCRIPT_DIR"
    pip install -e . --quiet 2>/dev/null || pip install -e . 2>/dev/null || {
        echo -e "${RED}ERROR:${RST} No se pudo instalar PPMT."
        exit 1
    }
    echo -e "  ${GRN}✓${RST} PPMT instalado"
else
    echo -e "  ${GRN}✓${RST} PPMT ya instalado"
fi

# ══════════════════════════════════════════════════════════════════════
#  4. LEVANTAR SERVIDOR UVICORN
# ══════════════════════════════════════════════════════════════════════
echo -e "${CYN}[4/4]${RST} Levantando servidor..."
echo ""

cd "$SCRIPT_DIR"
python -m uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port $PORT --reload &
UVICORN_PID=$!

# Esperar a que uvicorn arranque (máximo 10 segundos)
echo -e "${DIM}  Esperando al servidor...${RST}"
for i in $(seq 1 10); do
    if curl -s "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo ""
echo -e "${GRN}═══════════════════════════════════════════════════${RST}"
echo -e "${GRN}  PPMT v0.58 Running -> http://localhost:$PORT${RST}"
echo -e "${GRN}═══════════════════════════════════════════════════${RST}"
echo ""
echo -e "${DIM}  API Health:     http://localhost:$PORT/api/health${RST}"
echo -e "${DIM}  Risk Status:    http://localhost:$PORT/api/risk/status${RST}"
echo -e "${DIM}  Portfolio Live: http://localhost:$PORT/api/portfolio/live${RST}"
echo -e "${DIM}  WebSocket:      ws://localhost:$PORT/ws/paper-live/SOL-USDT/5m${RST}"
echo ""
echo -e "${DIM}  PID: $UVICORN_PID  |  Detener: kill $UVICORN_PID${RST}"
echo ""

# Mantener el script corriendo hasta que uvicorn muera
wait $UVICORN_PID
