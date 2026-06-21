#!/bin/bash
echo "═══════════════════════════════════════"
echo "  PPMT V2 — Motor de Trading"
echo "═══════════════════════════════════════"
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || { python3 -m venv venv; source venv/bin/activate; }
echo "[1/2] Backend en puerto 8000..."
python3 -m uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port 8000 --reload
