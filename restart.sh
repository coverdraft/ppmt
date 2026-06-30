#!/usr/bin/env bash
# restart.sh — PULL + BUILD + RESTART del bot PPMT (rama terminal-web)
#
# Uso:
#   bash restart.sh            # pull + build + restart (default)
#   bash restart.sh --no-pull  # solo build + restart (si ya hiciste pull)
#   bash restart.sh --logs     # restart + abrir logs en vivo (Ctrl+C no para el bot)
#
# Requisitos: bun, git. Opcional: pm2 (si lo usas, lo detecta automáticamente).

set -euo pipefail

# ─── Configuración ─────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="terminal-web"
BOT_LOG="$REPO_DIR/bot.log"
PID_FILE="$REPO_DIR/.bot.pid"
PM2_NAME="ppmt"

cd "$REPO_DIR"

# Colores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN:${NC} $*"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }
step() { echo -e "\n${CYAN}═══ $* ═══${NC}"; }

DO_PULL=1
SHOW_LOGS=0
for arg in "$@"; do
  case "$arg" in
    --no-pull) DO_PULL=0 ;;
    --logs)    SHOW_LOGS=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) err "Arg desconocido: $arg"; exit 1 ;;
  esac
done

# ─── 1. Preflight checks ───────────────────────────────────────
step "1/5  Preflight"
command -v git  >/dev/null || { err "git no encontrado"; exit 1; }
command -v bun  >/dev/null || { err "bun no encontrado (instala: curl -fsSL https://bun.sh/install | bash)"; exit 1; }

if [[ ! -d ".git" ]]; then
  err "No estás en la raíz del repo (falta .git). Ejecuta desde la carpeta ppmt/"
  exit 1
fi

log "Repo:        $REPO_DIR"
log "Branch:      $BRANCH"
log "Bot log:     $BOT_LOG"
log "Bun:         $(bun --version)"
log "PM2:         $(command -v pm2 >/dev/null && echo 'detectado' || echo 'no (usaremos nohup)')"

# ─── 2. Pull desde GitHub ──────────────────────────────────────
if [[ $DO_PULL -eq 1 ]]; then
  step "2/5  Git pull"
  git fetch origin "$BRANCH"
  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse "origin/$BRANCH")
  if [[ "$LOCAL" == "$REMOTE" ]]; then
    log "Ya estás al día ($LOCAL). Nada que pull."
  else
    log "Pulling $LOCAL → $REMOTE ..."
    git pull origin "$BRANCH"
    log "Pull OK. Nuevo HEAD:"
    git log --oneline -3
  fi
else
  step "2/5  Git pull (SKIPPED por --no-pull)"
fi

# ─── 3. Instalar deps + build ──────────────────────────────────
step "3/5  Bun install + build"
log "Instalando dependencias..."
bun install --frozen-lockfile 2>&1 | tail -5 || bun install 2>&1 | tail -5

log "Build de producción (esto tarda 10-30s)..."
bun run build 2>&1 | tail -20

# ─── 4. Parar el bot viejo ─────────────────────────────────────
step "4/5  Parar bot viejo"

stop_pm2() {
  if command -v pm2 >/dev/null && pm2 info "$PM2_NAME" >/dev/null 2>&1; then
    log "PM2 detectado. Stopping $PM2_NAME..."
    pm2 stop "$PM2_NAME" 2>/dev/null || true
    pm2 delete "$PM2_NAME" 2>/dev/null || true
    sleep 2
    return 0
  fi
  return 1
}

stop_nohup() {
  if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
      log "Matando PID $OLD_PID (leído de $PID_FILE)..."
      kill "$OLD_PID" 2>/dev/null || true
      sleep 3
      kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  # Backup: matar por nombre de proceso (bun run start / next start)
  pkill -f "next start" 2>/dev/null || true
  pkill -f "bun.*run.*start" 2>/dev/null || true
  sleep 2
}

if ! stop_pm2; then
  stop_nohup
fi

log "Bot viejo detenido."

# ─── 5. Arrancar el bot nuevo ──────────────────────────────────
step "5/5  Arrancar bot nuevo"

if command -v pm2 >/dev/null; then
  log "Iniciando con PM2 (auto-restart en crash)..."
  pm2 start "bun run start" --name "$PM2_NAME" --cwd "$REPO_DIR"
  pm2 save
  log "PM2 iniciado. Estado:"
  pm2 list
else
  log "Iniciando con nohup (sin auto-restart)..."
  : > "$BOT_LOG"  # truncar log viejo
  nohup bun run start > "$BOT_LOG" 2>&1 &
  NEW_PID=$!
  echo "$NEW_PID" > "$PID_FILE"
  disown "$NEW_PID" 2>/dev/null || true
  log "Bot arrancado. PID=$NEW_PID (guardado en $PID_FILE)"
  sleep 5
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    err "El bot murió en los primeros 5s. Últimas 30 líneas del log:"
    tail -30 "$BOT_LOG"
    exit 1
  fi
  log "Bot vivo tras 5s ✓"
fi

# ─── Resumen ───────────────────────────────────────────────────
echo
step "✓ Restart completo"
log "Commit actual: $(git log --oneline -1)"
log "Logs:          tail -f $BOT_LOG    (o  pm2 logs $PM2_NAME)"
log "UI:            http://localhost:3000  (o tu dominio)"
echo
log "Marcas v82j a vigilar en logs del próximo trade:"
echo "  [Paper/v82j] <SYM> PARTIAL_TP1 10% @ ... (R=0.50)"
echo "  [Paper/v82j] <SYM> PARTIAL_TP2 15% @ ... (R=1.00)"
echo "  [Paper/v82j] <SYM> PARTIAL_TP3 20% @ ... (R=2.00)"
echo "  [Paper/v82j] <SYM> PARTIAL_TP4 25% @ ... (R=4.00)"
echo "  [Paper/v82h] <SYM> PYRAMID +50% @ ... (R was 1.00, ...)"

if [[ $SHOW_LOGS -eq 1 ]]; then
  echo
  log "Abriendo logs en vivo (Ctrl+C para salir sin parar el bot)..."
  if command -v pm2 >/dev/null && pm2 info "$PM2_NAME" >/dev/null 2>&1; then
    pm2 logs "$PM2_NAME" --lines 30
  else
    tail -f "$BOT_LOG"
  fi
fi
