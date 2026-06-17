"""
Tests v0.34.0 — PPMT
====================

Tests mínimos para validar las 5 mejoras de v0.34.0:
1. get_recalibration_interval() — recalibración TF-aware
2. listing_days_min en recently_listed_30d
3. SweepResultCache — caché por símbolo
4. history_manager.save_scan / list_scans / list_by_symbol
5. score_signal — scoring determinístico

CÓMO EJECUTAR:
    cd ~/Projects/ppmt  # o donde tengas el repo
    python3 -m pytest src/tests/test_v0340.py -v

Si no tienes pytest:
    python3 src/tests/test_v0340.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
import tempfile

# Hacer el paquete ppmt importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# Tests de recalibración TF-aware
# ============================================================
def test_recalibration_low_tf_uses_base():
    from ppmt.engine.realtime import get_recalibration_interval
    assert get_recalibration_interval(1) == 2000, "1m debe usar base 2000"
    assert get_recalibration_interval(5) == 2000, "5m debe usar base 2000"
    assert get_recalibration_interval(15) == 2000, "15m debe usar base 2000"


def test_recalibration_1h_scales_4x():
    from ppmt.engine.realtime import get_recalibration_interval
    assert get_recalibration_interval(60) == 8000, "1h debe escalar 4x"


def test_recalibration_4h_scales_16x():
    from ppmt.engine.realtime import get_recalibration_interval
    assert get_recalibration_interval(240) == 32000, "4h debe escalar 16x"


def test_recalibration_1d_capped_at_ceiling():
    from ppmt.engine.realtime import get_recalibration_interval
    # 2000 * 96 = 192000, capped to 50000
    assert get_recalibration_interval(1440) == 50000, "1d debe estar capped"


def test_recalibration_invalid_returns_base():
    from ppmt.engine.realtime import get_recalibration_interval
    assert get_recalibration_interval(0) == 2000
    assert get_recalibration_interval(-5) == 2000


def test_tf_to_minutes_helper():
    from ppmt.engine.realtime import _tf_to_minutes
    assert _tf_to_minutes("15m") == 15
    assert _tf_to_minutes("1h") == 60
    assert _tf_to_minutes("4h") == 240
    assert _tf_to_minutes("1d") == 1440
    assert _tf_to_minutes("unknown") == 15  # default seguro


# ============================================================
# Tests del filtro min_dias
# ============================================================
def test_recently_listed_has_min_days():
    from ppmt.data.groups import DYNAMIC_GROUPS
    g = DYNAMIC_GROUPS["recently_listed_30d"]
    assert "listing_days_min" in g, "Falta listing_days_min en recently_listed_30d"
    assert g["listing_days_min"] == 3, f"Expected 3, got {g['listing_days_min']}"
    assert g["listing_days_max"] == 30, "listing_days_max debe seguir siendo 30"


# ============================================================
# Tests de SweepResultCache
# ============================================================
def test_cache_miss_then_hit():
    from ppmt.terminal.sweep_cache import SweepResultCache
    c = SweepResultCache(ttl_sec=60)
    key = c.make_key("BTC/USDT", "15m")
    assert c.get(key) is None, "Caché nueva debe dar miss"
    c.set(key, {"verdict": "PASS", "pf": 1.5})
    assert c.get(key) == {"verdict": "PASS", "pf": 1.5}, "Debe dar hit tras set"


def test_cache_expiration():
    from ppmt.terminal.sweep_cache import SweepResultCache
    c = SweepResultCache(ttl_sec=0)  # expira inmediatamente
    key = c.make_key("ETH/USDT", "1h")
    c.set(key, {"verdict": "FAIL"})
    time.sleep(0.05)
    assert c.get(key) is None, "Debe expirar con TTL=0"


def test_cache_clear():
    from ppmt.terminal.sweep_cache import SweepResultCache
    c = SweepResultCache(ttl_sec=60)
    c.set(c.make_key("X", "15m"), {"a": 1})
    c.set(c.make_key("Y", "15m"), {"b": 2})
    assert len(c._cache) == 2
    c.clear()
    assert len(c._cache) == 0, "clear() debe vaciar la caché"


def test_cache_key_format():
    from ppmt.terminal.sweep_cache import SweepResultCache
    c = SweepResultCache()
    assert c.make_key("BTC/USDT", "15m") == "BTC/USDT|15m"
    # Diferentes TFs → diferentes keys
    assert c.make_key("BTC/USDT", "15m") != c.make_key("BTC/USDT", "1h")
    # Diferentes símbolos → diferentes keys
    assert c.make_key("BTC/USDT", "15m") != c.make_key("ETH/USDT", "15m")


def test_cache_independent_per_symbol():
    from ppmt.terminal.sweep_cache import SweepResultCache
    c = SweepResultCache(ttl_sec=60)
    c.set(c.make_key("BTC/USDT", "15m"), {"pf": 1.5})
    c.set(c.make_key("ETH/USDT", "15m"), {"pf": 1.2})
    assert c.get(c.make_key("BTC/USDT", "15m"))["pf"] == 1.5
    assert c.get(c.make_key("ETH/USDT", "15m"))["pf"] == 1.2


# ============================================================
# Tests de history_manager (usando DB temporal)
# ============================================================
def _setup_temp_db(monkeypatch):
    """Redirige DB_PATH a un archivo temporal."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["PPMT_TEST_DB"] = tmp.name
    # Reimportar para que coja el path nuevo
    import importlib
    import ppmt.terminal.history_manager as hm
    hm.DB_PATH = tmp.name
    return tmp.name


def test_history_save_and_list():
    import ppmt.terminal.history_manager as hm
    tmp = _setup_temp_db(None)

    try:
        resultados = [
            {"symbol": "BTC/USDT", "resultado": "PASS",
             "win_rate": 0.65, "profit_factor": 1.8, "sharpe": 1.5,
             "max_drawdown": 0.12, "total_trades": 80},
            {"symbol": "ETH/USDT", "resultado": "FAIL",
             "win_rate": 0.35, "profit_factor": 0.7, "sharpe": 0.5,
             "max_drawdown": 0.25, "total_trades": 60},
            {"symbol": "DOGE/USDT", "resultado": "INSUFFICIENT_DATA",
             "win_rate": 0, "profit_factor": 0, "sharpe": 0,
             "max_drawdown": 0, "total_trades": 3},
        ]
        scan_id = hm.save_scan(
            grupo_utilizado="blue_chips",
            tf_utilizado="15m",
            resultados=resultados,
            filtros_aplicados={"exclude_stablecoins": True},
            dias_data=21,
            tiempo_ejecucion=12.5,
        )
        assert scan_id > 0, "scan_id debe ser positivo"

        rows = hm.list_scans(limit=10)
        assert len(rows) == 1
        assert rows[0]["total_tokens"] == 3
        assert rows[0]["tokens_pasaron"] == 1
        assert rows[0]["tokens_fallaron"] == 1
        assert rows[0]["tokens_insuficientes"] == 1
        assert rows[0]["grupo_utilizado"] == "blue_chips"

        # Recuperar el scan completo
        scan = hm.get_scan(scan_id)
        assert scan is not None
        assert len(scan["resultados"]) == 3
        # Ordenado por score desc: BTC primero
        assert scan["resultados"][0]["symbol"] == "BTC/USDT"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def test_history_list_by_symbol():
    import ppmt.terminal.history_manager as hm
    tmp = _setup_temp_db(None)

    try:
        # Scan 1 con BTC
        hm.save_scan("g1", "15m", [
            {"symbol": "BTC/USDT", "resultado": "PASS", "profit_factor": 1.5,
             "win_rate": 0.6, "sharpe": 1.0, "max_drawdown": 0.1, "total_trades": 50},
        ])
        # Scan 2 con BTC y ETH
        hm.save_scan("g2", "1h", [
            {"symbol": "BTC/USDT", "resultado": "FAIL", "profit_factor": 0.8,
             "win_rate": 0.4, "sharpe": 0.5, "max_drawdown": 0.2, "total_trades": 40},
            {"symbol": "ETH/USDT", "resultado": "PASS", "profit_factor": 2.0,
             "win_rate": 0.7, "sharpe": 1.8, "max_drawdown": 0.08, "total_trades": 60},
        ])

        btc_history = hm.list_by_symbol("BTC")
        assert len(btc_history) == 2
        # Acepta variantes
        btc2 = hm.list_by_symbol("BTCUSDT")
        assert len(btc2) == 2
        btc3 = hm.list_by_symbol("BTC/USDT")
        assert len(btc3) == 2

        eth_history = hm.list_by_symbol("ETH")
        assert len(eth_history) == 1
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def test_history_empty_when_no_data():
    import ppmt.terminal.history_manager as hm
    tmp = _setup_temp_db(None)
    try:
        assert hm.list_scans(limit=10) == []
        assert hm.list_by_symbol("BTC") == []
        assert hm.list_today() == []
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ============================================================
# Tests de score_signal
# ============================================================
def test_score_perfect_signal():
    import ppmt.terminal.history_manager as hm
    # PF 3.0 (max), Sharpe 3.0 (max), WR 1.0 (max), DD 0 (max), trades 50+
    score = hm.score_signal({
        "profit_factor": 3.0,
        "sharpe": 3.0,
        "win_rate": 1.0,
        "max_drawdown": 0.0,
        "total_trades": 100,
    })
    assert score == 100.0, f"Señal perfecta debe dar 100, dio {score}"


def test_score_zero_signal():
    import ppmt.terminal.history_manager as hm
    score = hm.score_signal({
        "profit_factor": 0,
        "sharpe": 0,
        "win_rate": 0,
        "max_drawdown": 0.5,  # 50% DD = score 0
        "total_trades": 0,
    })
    assert score == 0.0, f"Señal nula debe dar 0, dio {score}"


def test_score_deterministic():
    import ppmt.terminal.history_manager as hm
    metrics = {
        "profit_factor": 1.8,
        "sharpe": 1.5,
        "win_rate": 0.65,
        "max_drawdown": 0.12,
        "total_trades": 80,
    }
    s1 = hm.score_signal(metrics)
    s2 = hm.score_signal(metrics)
    assert s1 == s2, "score_signal debe ser determinístico"


def test_score_better_signal_scores_higher():
    import ppmt.terminal.history_manager as hm
    good = hm.score_signal({
        "profit_factor": 2.5, "sharpe": 2.5, "win_rate": 0.75,
        "max_drawdown": 0.08, "total_trades": 90,
    })
    bad = hm.score_signal({
        "profit_factor": 0.9, "sharpe": 0.3, "win_rate": 0.35,
        "max_drawdown": 0.35, "total_trades": 20,
    })
    assert good > bad, f"Señal buena ({good}) debe > señal mala ({bad})"
    assert good > 60, f"Señal buena debe > 60, dio {good}"
    assert bad < 30, f"Señal mala debe < 30, dio {bad}"


# ============================================================
# Runner manual (sin pytest)
# ============================================================
if __name__ == "__main__":
    tests = [
        ("test_recalibration_low_tf_uses_base", test_recalibration_low_tf_uses_base),
        ("test_recalibration_1h_scales_4x", test_recalibration_1h_scales_4x),
        ("test_recalibration_4h_scales_16x", test_recalibration_4h_scales_16x),
        ("test_recalibration_1d_capped_at_ceiling", test_recalibration_1d_capped_at_ceiling),
        ("test_recalibration_invalid_returns_base", test_recalibration_invalid_returns_base),
        ("test_tf_to_minutes_helper", test_tf_to_minutes_helper),
        ("test_recently_listed_has_min_days", test_recently_listed_has_min_days),
        ("test_cache_miss_then_hit", test_cache_miss_then_hit),
        ("test_cache_expiration", test_cache_expiration),
        ("test_cache_clear", test_cache_clear),
        ("test_cache_key_format", test_cache_key_format),
        ("test_cache_independent_per_symbol", test_cache_independent_per_symbol),
        ("test_history_save_and_list", test_history_save_and_list),
        ("test_history_list_by_symbol", test_history_list_by_symbol),
        ("test_history_empty_when_no_data", test_history_empty_when_no_data),
        ("test_score_perfect_signal", test_score_perfect_signal),
        ("test_score_zero_signal", test_score_zero_signal),
        ("test_score_deterministic", test_score_deterministic),
        ("test_score_better_signal_scores_higher", test_score_better_signal_scores_higher),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
    print(f"\nResultado: {passed} pasaron, {failed} fallaron, "
          f"total {len(tests)}.")
    sys.exit(0 if failed == 0 else 1)
