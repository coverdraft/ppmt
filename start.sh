#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  PPMT V2 Terminal — First Run Wizard  (macOS)
#  v0.50.0 · ENTREGABLE 12
#
#  Uso:
#    bash start.sh
#
#  Si ~/.ppmt/ppmt.db NO existe → descarga 7 días de data real de
#  Binance (5 tokens, 1m + 5m), construye Tries y levanta la UI.
#  Si ya existe → va directo a levantar los servidores.
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
DB_PATH="$HOME/.ppmt/ppmt.db"
FRONTEND_DIR="$SCRIPT_DIR/terminal-v2"
BACKEND_PORT=8000
FRONTEND_PORT=5173

# ─── Tokens de referencia (cubren 3 asset classes → llenan pools) ───
#   BTC/USDT  → blue_chip  → __CLASS_blue_chip  (N2)
#   ETH/USDT  → blue_chip  → __CLASS_blue_chip  (N2)
#   SOL/USDT  → large_cap  → __CLASS_large_cap  (N2)
#   XRP/USDT  → large_cap  → __CLASS_large_cap  (N2)
#   DOGE/USDT → meme       → __CLASS_meme       (N2)
#   Todos     → __UNIVERSAL__                    (N1)
SYMBOLS="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT"
TIMEFRAMES="1m 5m"
DAYS=7

echo ""
echo -e "${WHT}═══════════════════════════════════════════════════${RST}"
echo -e "${RED}  PPMT V2 Terminal — Probabilistic Pattern Engine${RST}"
echo -e "${WHT}═══════════════════════════════════════════════════${RST}"
echo ""

# ══════════════════════════════════════════════════════════════════════
#  1. VERIFICAR DEPENDENCIAS
# ══════════════════════════════════════════════════════════════════════
echo -e "${CYN}[1/4]${RST} Verificando dependencias..."

# ─── Python ───────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo -e "${RED}ERROR:${RST} Python 3 no encontrado."
    echo "  Instala con:  brew install python3"
    echo "  O:           xcode-select --install"
    exit 1
fi
PY_VER=$($PY --version 2>&1 | head -1)
echo -e "  ${GRN}✓${RST} Python: $PY_VER"

# ─── Node / npm ───────────────────────────────────────────────────────
if ! command -v npm &>/dev/null; then
    echo -e "${RED}ERROR:${RST} npm no encontrado."
    echo "  Instala con:  brew install node"
    exit 1
fi
NPM_VER=$(npm --version 2>&1 | head -1)
echo -e "  ${GRN}✓${RST} npm:  v$NPM_VER"

# ─── PPMT instalado ──────────────────────────────────────────────────
if ! $PY -c "import ppmt" 2>/dev/null; then
    echo -e "${YEL}⚠${RST}  PPMT no instalado. Ejecutando pip install -e . ..."
    cd "$SCRIPT_DIR"
    $PY -m pip install -e . --break-system-packages 2>/dev/null || \
        $PY -m pip install -e . 2>/dev/null || \
        { echo -e "${RED}ERROR:${RST} No se pudo instalar PPMT."; exit 1; }
fi
echo -e "  ${GRN}✓${RST} PPMT engine instalado"

# ══════════════════════════════════════════════════════════════════════
#  2. FIRST RUN WIZARD — Descarga + Build si no hay DB
# ══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYN}[2/4]${RST} Verificando base de datos..."

if [ ! -f "$DB_PATH" ]; then
    echo ""
    echo -e "${YEL}📦 Primera vez detectada. Descargando ${DAYS} días de data real${RST}"
    echo -e "${DIM}    (5 tokens, timeframes ${TIMEFRAMES}, exchange Binance)${RST}"
    echo ""

    # ─── 2a. Descargar data real de Binance ────────────────────────
    echo -e "${WHT}    ↓ Descargando velas de Binance...${RST}"
    cd "$SCRIPT_DIR"
    $PY -m ppmt.data.bulk_downloader \
        --exchange binance \
        --days "$DAYS" \
        --timeframes $TIMEFRAMES \
        --symbols "$SYMBOLS" \
        --save-to-db

    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR:${RST} Falló la descarga de data. Verifica tu conexión a internet."
        exit 1
    fi
    echo -e "    ${GRN}✓${RST} Data descargada y guardada en $DB_PATH"

    # ─── 2b. Construir Tries compartidos ───────────────────────────
    echo ""
    echo -e "${WHT}    🔨 Construyendo Tries compartidos (N1/N2)...${RST}"

    # Build para timeframe 1m
    echo -e "${DIM}    → N1/N2/N3 para timeframe 1m...${RST}"
    $PY -m ppmt.data.sequential_builder \
        --symbols "$SYMBOLS" \
        --timeframe 1m \
        --pattern-length 5

    # Build para timeframe 5m
    echo -e "${DIM}    → N1/N2/N3 para timeframe 5m...${RST}"
    $PY -m ppmt.data.sequential_builder \
        --symbols "$SYMBOLS" \
        --timeframe 5m \
        --pattern-length 5

    echo -e "    ${GRN}✓${RST} Tries construidos"
    echo ""
    echo -e "${GRN}    Pools generados:${RST}"
    echo -e "${DIM}    __UNIVERSAL__         → N1 (compartido entre todos los tokens)"
    echo -e "    __CLASS_blue_chip     → N2 (BTC, ETH)"
    echo -e "    __CLASS_large_cap     → N2 (SOL, XRP)"
    echo -e "    __CLASS_meme          → N2 (DOGE)${RST}"
else
    DB_SIZE=$(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "?")
    echo -e "  ${GRN}✓${RST} DB existente: $DB_PATH ($DB_SIZE)"
fi

# ══════════════════════════════════════════════════════════════════════
#  3. INSTALAR DEPENDENCIAS DEL FRONTEND (si hace falta)
# ══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYN}[3/4]${RST} Verificando frontend..."

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo -e "${YEL}    Instalando dependencias npm...${RST}"
    cd "$FRONTEND_DIR"
    npm install --silent 2>/dev/null
    echo -e "    ${GRN}✓${RST} Dependencias instaladas"
else
    echo -e "  ${GRN}✓${RST} node_modules presente"
fi

# ══════════════════════════════════════════════════════════════════════
#  4. LEVANTAR SERVIDORES (dos pestañas de Terminal.app)
# ══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYN}[4/4]${RST} Levantando servidores..."

# ─── Detectar macOS vs Linux ─────────────────────────────────────────
OS_TYPE="$(uname -s)"

if [ "$OS_TYPE" = "Darwin" ]; then
    # ─── macOS: osascript con dos pestañas ─────────────────────────
    osascript <<APPLESCRIPT
tell application "Terminal"
    activate

    -- Pestaña 1: Backend (FastAPI en puerto ${BACKEND_PORT})
    tell application "Terminal"
        set backendTab to do script "cd '${SCRIPT_DIR}' && echo '🔧 PPMT Backend — Puerto ${BACKEND_PORT}' && echo '' && python3 -m uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload; echo ''; echo 'Backend detenido. Presiona ⌘+W para cerrar.'; read"
        set custom title of front window to "PPMT Backend :${BACKEND_PORT}"
    end tell

    -- Pestaña 2: Frontend (Vite en puerto ${FRONTEND_PORT})
    tell application "Terminal"
        set frontendTab to do script "cd '${FRONTEND_DIR}' && echo '🎨 PPMT Frontend — Puerto ${FRONTEND_PORT}' && echo '' && npm run dev -- --port ${FRONTEND_PORT}; echo ''; echo 'Frontend detenido. Presiona ⌘+W para cerrar.'; read"
        set custom title of front window to "PPMT Frontend :${FRONTEND_PORT}"
    end tell
end tell
APPLESCRIPT

elif [ "$OS_TYPE" = "Linux" ]; then
    # ─── Linux: tmux o background ─────────────────────────────────
    if command -v tmux &>/dev/null; then
        SESSION="ppmt-v2"
        tmux new-session -d -s "$SESSION" -c "$SCRIPT_DIR" \
            "python3 -m uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload"
        tmux new-window -t "$SESSION" -c "$FRONTEND_DIR" \
            "npm run dev -- --port ${FRONTEND_PORT}"
        tmux attach -t "$SESSION"
    else
        # Fallback: background processes
        cd "$SCRIPT_DIR"
        python3 -m uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload &
        BACKEND_PID=$!
        cd "$FRONTEND_DIR"
        npm run dev -- --port ${FRONTEND_PORT} &
        FRONTEND_PID=$!
        echo ""
        echo -e "${DIM}PIDs: Backend=$BACKEND_PID  Frontend=$FRONTEND_PID${RST}"
        echo -e "${DIM}Detener con: kill $BACKEND_PID $FRONTEND_PID${RST}"
        wait
    fi
else
    echo -e "${RED}ERROR:${RST} Sistema operativo no soportado: $OS_TYPE"
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════
#  MENSAJE FINAL
# ══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GRN}═══════════════════════════════════════════════════${RST}"
echo -e "${GRN}  ✅ Sistema listo.${RST}"
echo -e "${GRN}  Abre tu navegador en: http://localhost:${FRONTEND_PORT}${RST}"
echo -e "${GRN}═══════════════════════════════════════════════════${RST}"
echo ""
echo -e "${DIM}Tokens disponibles: BTC ETH SOL XRP DOGE${RST}"
echo -e "${DIM}Timeframes: 1m 5m${RST}"
echo -e "${DIM}Backend API:  http://localhost:${BACKEND_PORT}/api/health${RST}"
echo -e "${DIM}WebSocket:    ws://localhost:${BACKEND_PORT}/ws/paper-live/DOGE-USDT/1m${RST}"
echo ""
echo -e "${DIM}Modo Mock Live (sin dinero real):${RST}"
echo -e "${DIM}  PPMT_MOCK_LIVE=1 bash start.sh${RST}"
echo ""
