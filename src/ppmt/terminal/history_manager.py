"""
PPMT History Manager (v0.34.0)
==============================

Guarda automáticamente cada escaneo (validación individual o Sweep All
Groups) en SQLite para trazabilidad histórica.

Tablas creadas (en ~/.ppmt/ppmt.db, la misma DB existente):

  historical_scan  — 1 fila por escaneo completo (sweep o individual)
  scan_results     — N filas por escaneo (1 por token validado)

NO creamos tabla `real_trades` — ya existe en storage.py (save_trade()).

API pública:
    save_scan(...)         -> int  (scan_id)
    list_scans(limit=10)   -> list[dict]
    get_scan(scan_id)      -> dict | None
    list_by_symbol(symbol) -> list[dict]
    list_today()           -> list[dict]
    score_signal(metrics)  -> float  (0–100)

CLI (en cli/main.py):
    ppmt history --latest 10
    ppmt history --symbol DOGEUSDT
    ppmt history --today
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Paths — usa la misma DB que storage.py
# ============================================================
CONFIG_DIR = os.path.expanduser("~/.ppmt")
DB_PATH = os.path.join(CONFIG_DIR, "ppmt.db")


# ============================================================
# Pesos por defecto para score_signal()
# ============================================================
DEFAULT_SELECTION_WEIGHTS: Dict[str, float] = {
    "profit_factor": 0.35,
    "sharpe": 0.20,
    "win_rate": 0.15,
    "max_drawdown": 0.20,  # negativo: menos es mejor
    "trades": 0.10,
}


# ============================================================
# Schema
# ============================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS historical_scan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    grupo_utilizado TEXT,
    filtros_aplicados TEXT,
    tf_utilizado TEXT,
    dias_data INTEGER,
    total_tokens INTEGER,
    tokens_pasaron INTEGER,
    tokens_fallaron INTEGER,
    tokens_insuficientes INTEGER,
    tiempo_ejecucion REAL,
    score_avg REAL,
    resultado_resumen TEXT
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER,
    symbol TEXT,
    grupo TEXT,
    resultado TEXT,
    score REAL,
    win_rate REAL,
    profit_factor REAL,
    sharpe REAL,
    max_drawdown REAL,
    total_trades INTEGER,
    config_usada TEXT,
    cached INTEGER DEFAULT 0,
    test_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scan_id) REFERENCES historical_scan(id)
);

CREATE INDEX IF NOT EXISTS idx_scan_results_symbol ON scan_results(symbol);
CREATE INDEX IF NOT EXISTS idx_scan_results_scan ON scan_results(scan_id);
CREATE INDEX IF NOT EXISTS idx_historical_scan_ts ON historical_scan(scan_timestamp);
"""


# ============================================================
# Conexión
# ============================================================
def _get_conn() -> sqlite3.Connection:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


# ============================================================
# Scoring
# ============================================================
def score_signal(metrics: Dict[str, Any],
                 weights: Optional[Dict[str, float]] = None) -> float:
    """Calcula un score 0–100 para una señal basado en sus métricas.

    Métricas esperadas (opcional, usa defaults razonables si faltan):
        profit_factor, sharpe, win_rate, max_drawdown, total_trades

    Pesos por defecto (DEFAULT_SELECTION_WEIGHTS):
        PF 35%, Sharpe 20%, WR 15%, DD 20%, trades 10%
    """
    w = weights or DEFAULT_SELECTION_WEIGHTS

    pf = float(metrics.get("profit_factor", 0) or 0)
    sharpe = float(metrics.get("sharpe", 0) or 0)
    wr = float(metrics.get("win_rate", 0) or 0)
    dd = float(metrics.get("max_drawdown", 0) or 0)
    trades = float(metrics.get("total_trades", 0) or 0)

    # Normalizar cada métrica a 0–1
    # PF: 0–1 (malo), 1 (break-even), 2+ (bueno). Mapeo 0–3 → 0–1.
    pf_score = min(1.0, max(0.0, pf / 3.0))
    # Sharpe: 0–3 → 0–1
    sharpe_score = min(1.0, max(0.0, sharpe / 3.0))
    # Win rate: 0–1 directo (pero subimos el piso a 0.3 para penalizar <30%)
    wr_score = min(1.0, max(0.0, (wr - 0.3) / 0.7)) if wr > 0.3 else 0.0
    # Drawdown: 0% (perfecto) → 1, 50%+ (catastrófico) → 0
    dd_score = min(1.0, max(0.0, 1.0 - (dd / 0.5))) if dd >= 0 else 0.0
    # Trades: 0 → 0, 50+ → 1 (tamaño de muestra suficiente)
    trades_score = min(1.0, trades / 50.0)

    score = (
        pf_score * w["profit_factor"]
        + sharpe_score * w["sharpe"]
        + wr_score * w["win_rate"]
        + dd_score * w["max_drawdown"]
        + trades_score * w["trades"]
    ) * 100.0

    return round(score, 2)


# ============================================================
# Save
# ============================================================
def save_scan(
    grupo_utilizado: str,
    tf_utilizado: str,
    resultados: List[Dict[str, Any]],
    filtros_aplicados: Optional[Dict[str, Any]] = None,
    dias_data: int = 0,
    tiempo_ejecucion: float = 0.0,
) -> int:
    """Guarda un escaneo completo en SQLite.

    Args:
        grupo_utilizado: id del grupo o "sweep:group1,group2,..."
        tf_utilizado: timeframe usado (15m, 1h, ...)
        resultados: lista de dicts con claves:
            symbol, resultado (PASS/FAIL/INSUFFICIENT_DATA), win_rate,
            profit_factor, sharpe, max_drawdown, total_trades,
            config_usada (dict opcional), grupo (opcional), cached (opcional)
        filtros_aplicados: dict con los filtros usados (se serializa a JSON)
        dias_data: días de data usados en el backtest
        tiempo_ejecucion: segundos totales del escaneo

    Returns:
        scan_id de la fila insertada en historical_scan.
    """
    if not resultados:
        logger.warning("save_scan llamado con 0 resultados, no se guarda nada")
        return -1

    # Contar resultados
    n_pass = sum(1 for r in resultados if r.get("resultado") == "PASS")
    n_fail = sum(1 for r in resultados if r.get("resultado") == "FAIL")
    n_insuf = sum(1 for r in resultados
                  if r.get("resultado") == "INSUFFICIENT_DATA")

    # Score promedio
    scores = [score_signal(r) for r in resultados]
    score_avg = round(sum(scores) / len(scores), 2) if scores else 0.0

    # Resumen compacto para debug rápido
    resumen = {
        "pass": n_pass,
        "fail": n_fail,
        "insufficient": n_insuf,
        "score_avg": score_avg,
    }

    filtros_json = json.dumps(filtros_aplicados or {}, default=str)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO historical_scan (
                grupo_utilizado, filtros_aplicados, tf_utilizado,
                dias_data, total_tokens, tokens_pasaron, tokens_fallaron,
                tokens_insuficientes, tiempo_ejecucion, score_avg,
                resultado_resumen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            grupo_utilizado, filtros_json, tf_utilizado,
            dias_data, len(resultados), n_pass, n_fail, n_insuf,
            tiempo_ejecucion, score_avg, json.dumps(resumen),
        ))
        scan_id = cur.lastrowid

        # Insertar resultados por token
        for r, sc in zip(resultados, scores):
            cur.execute("""
                INSERT INTO scan_results (
                    scan_id, symbol, grupo, resultado, score,
                    win_rate, profit_factor, sharpe, max_drawdown,
                    total_trades, config_usada, cached
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scan_id,
                r.get("symbol", ""),
                r.get("grupo", grupo_utilizado),
                r.get("resultado", "UNKNOWN"),
                sc,
                float(r.get("win_rate", 0) or 0),
                float(r.get("profit_factor", 0) or 0),
                float(r.get("sharpe", 0) or 0),
                float(r.get("max_drawdown", 0) or 0),
                int(r.get("total_trades", 0) or 0),
                json.dumps(r.get("config_usada", {}), default=str),
                int(bool(r.get("cached", False))),
            ))
        conn.commit()
        logger.info(f"Scan #{scan_id} guardado: {len(resultados)} tokens, "
                    f"{n_pass} PASS, score avg {score_avg}")
        return scan_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Error guardando scan: {e}")
        return -1
    finally:
        conn.close()


# ============================================================
# Query
# ============================================================
def list_scans(limit: int = 10) -> List[Dict[str, Any]]:
    """Devuelve los últimos N escaneos (sin resultados detalle)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, scan_timestamp, grupo_utilizado, tf_utilizado,
                   total_tokens, tokens_pasaron, tokens_fallaron,
                   tokens_insuficientes, score_avg, tiempo_ejecucion,
                   resultado_resumen
            FROM historical_scan
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_scan(scan_id: int) -> Optional[Dict[str, Any]]:
    """Devuelve un escaneo completo con todos sus resultados."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM historical_scan WHERE id = ?", (scan_id,))
        scan = cur.fetchone()
        if not scan:
            return None
        cur.execute("""
            SELECT symbol, grupo, resultado, score, win_rate,
                   profit_factor, sharpe, max_drawdown, total_trades,
                   config_usada, cached, test_timestamp
            FROM scan_results WHERE scan_id = ?
            ORDER BY score DESC
        """, (scan_id,))
        results = [dict(row) for row in cur.fetchall()]
        return {**dict(scan), "resultados": results}
    finally:
        conn.close()


def list_by_symbol(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Historial de un token concreto a lo largo de escaneos."""
    # Aceptar 'DOGE' o 'DOGEUSDT' o 'DOGE/USDT'
    sym_norm = symbol.upper().strip()
    if "/" not in sym_norm:
        if sym_norm.endswith("USDT"):
            sym_norm = sym_norm[:-4] + "/USDT"
        else:
            sym_norm = sym_norm + "/USDT"

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT sr.symbol, sr.resultado, sr.score, sr.win_rate,
                   sr.profit_factor, sr.sharpe, sr.max_drawdown,
                   sr.total_trades, sr.test_timestamp,
                   hs.grupo_utilizado, hs.tf_utilizado, hs.id AS scan_id
            FROM scan_results sr
            JOIN historical_scan hs ON hs.id = sr.scan_id
            WHERE sr.symbol = ?
            ORDER BY sr.test_timestamp DESC
            LIMIT ?
        """, (sym_norm, limit))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_today() -> List[Dict[str, Any]]:
    """Escaneos de hoy."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, scan_timestamp, grupo_utilizado, tf_utilizado,
                   total_tokens, tokens_pasaron, score_avg
            FROM historical_scan
            WHERE date(scan_timestamp) = date('now')
            ORDER BY id DESC
        """)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# CLI entry point
# ============================================================
def cli_history(args) -> int:
    """Entry point para `ppmt history`.

    args: namespace con atributos --latest, --symbol, --today
    """
    if args.symbol:
        rows = list_by_symbol(args.symbol)
        if not rows:
            print(f"No hay escaneos previos para {args.symbol}")
            return 0
        print(f"\n📊 HISTORIAL DE {args.symbol}\n")
        print(f"{'Fecha':<20} {'Grupo':<22} {'TF':<6} {'Result':<8} "
              f"{'Score':<7} {'PF':<6} {'WR':<6} {'Trades':<7}")
        print("-" * 95)
        for r in rows:
            print(f"{r['test_timestamp'][:19]:<20} "
                  f"{(r['grupo_utilizado'] or '')[:22]:<22} "
                  f"{(r['tf_utilizado'] or ''):<6} "
                  f"{r['resultado']:<8} "
                  f"{r['score']:<7.1f} "
                  f"{r['profit_factor']:<6.2f} "
                  f"{r['win_rate']*100:<6.1f} "
                  f"{r['total_trades']:<7}")
        return 0

    if args.today:
        rows = list_today()
        if not rows:
            print("No hay escaneos hoy todavía.")
            return 0
        print(f"\n📅 ESCANEOS DE HOY ({len(rows)})\n")
        print(f"{'ID':<5} {'Hora':<10} {'Grupo':<25} {'TF':<6} "
              f"{'Total':<7} {'PASS':<6} {'Score':<7}")
        print("-" * 75)
        for r in rows:
            print(f"{r['id']:<5} "
                  f"{r['scan_timestamp'][11:19]:<10} "
                  f"{(r['grupo_utilizado'] or '')[:25]:<25} "
                  f"{(r['tf_utilizado'] or ''):<6} "
                  f"{r['total_tokens']:<7} "
                  f"{r['tokens_pasaron']:<6} "
                  f"{r['score_avg']:<7.1f}")
        return 0

    # Default: --latest
    limit = args.latest or 10
    rows = list_scans(limit=limit)
    if not rows:
        print("No hay escaneos guardados todavía. Ejecuta un sweep primero.")
        return 0

    print(f"\n📋 ÚLTIMOS {len(rows)} ESCANEOS\n")
    print(f"{'ID':<5} {'Fecha':<20} {'Grupo':<25} {'TF':<6} "
          f"{'Total':<7} {'PASS':<6} {'FAIL':<6} {'Score':<7} {'Tiempo':<7}")
    print("-" * 105)
    for r in rows:
        print(f"{r['id']:<5} "
              f"{r['scan_timestamp'][:19]:<20} "
              f"{(r['grupo_utilizado'] or '')[:25]:<25} "
              f"{(r['tf_utilizado'] or ''):<6} "
              f"{r['total_tokens']:<7} "
              f"{r['tokens_pasaron']:<6} "
              f"{r['tokens_fallaron']:<6} "
              f"{r['score_avg']:<7.1f} "
              f"{r['tiempo_ejecucion']:<7.1f}s")
    return 0
