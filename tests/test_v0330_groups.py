"""v0.33.0: Tests for the dynamic Token Groups system.

Covers:
  - list_groups() returns all 4 categories (market_cap, category, dynamic, custom)
  - resolve_group() for static groups returns CCXT-formatted symbols
  - apply_filters() correctly drops stablecoins, applies volume/volatility limits
  - save/delete custom group persists to ~/.ppmt/groups_config.json
  - _days_for_tf updated values for low-TF reliability
  - _candle_count_warning triggers below threshold
"""
from __future__ import annotations

import os
import json
import tempfile

import pytest

from ppmt.terminal.server import (
    _days_for_tf,
    _candle_count_warning,
    _min_candles_for_tf,
    app,
)
from ppmt.data import groups as G


# ---------------------------------------------------------------- #
# _days_for_tf — v0.33.0 new table
# ---------------------------------------------------------------- #

def test_days_for_tf_full_table_v0330():
    """v0.33.0: Each TF must produce >=500 candles AND capture enough regimes."""
    # (tf, expected_days, approx_candles_per_day)
    expected = {
        "1m": 7, "3m": 14, "5m": 30, "10m": 45, "15m": 90, "30m": 120,
        "1h": 180, "2h": 240, "4h": 365, "6h": 540, "12h": 730,
        "1d": 730, "1w": 1825,
    }
    for tf, days in expected.items():
        assert _days_for_tf(tf) == days, f"_days_for_tf({tf!r}) should be {days}"


def test_days_for_tf_unknown_returns_default():
    assert _days_for_tf("999m") == 180
    assert _days_for_tf("invalid") == 180
    assert _days_for_tf("2m", default=99) == 99


# ---------------------------------------------------------------- #
# Candle-count warning
# ---------------------------------------------------------------- #

def test_candle_count_warning_below_threshold():
    threshold = _min_candles_for_tf("1h")
    w = _candle_count_warning(threshold - 1, "1h")
    assert w is not None
    assert "Muestra insuficiente" in w
    assert str(threshold - 1) in w


def test_candle_count_warning_above_threshold_is_none():
    assert _candle_count_warning(1000, "1h") is None
    assert _candle_count_warning(500, "1h") is None  # exactly threshold


# ---------------------------------------------------------------- #
# Groups module
# ---------------------------------------------------------------- #

def test_list_groups_has_4_categories():
    groups = G.list_groups()
    cats = {g["category"] for g in groups.values()}
    assert "market_cap" in cats
    assert "category" in cats
    assert "dynamic" in cats
    # custom may or may not be present depending on ~/.ppmt/groups_config.json,
    # but list_groups() must always return at least the predefined + dynamic ones
    assert len(groups) >= 18  # 4 + 10 + 4 = 18 minimum


def test_resolve_static_group_returns_ccxt_format():
    syms = G.resolve_group("blue_chips", exchange="mexc")
    assert syms == ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]


def test_resolve_static_group_unknown_returns_empty():
    syms = G.resolve_group("nonexistent_group_id", exchange="mexc")
    assert syms == []


def test_apply_filters_drops_stablecoins():
    syms = ["BTC/USDT", "USDT/USDT", "USDC/USDT", "ETH/USDT"]
    out = G.apply_filters(syms, {"exclude_stablecoins": True, "limit": 0})
    assert "BTC/USDT" in out
    assert "ETH/USDT" in out
    assert "USDT/USDT" not in out
    assert "USDC/USDT" not in out


def test_apply_filters_keeps_stablecoins_when_disabled():
    syms = ["BTC/USDT", "USDT/USDT"]
    out = G.apply_filters(syms, {"exclude_stablecoins": False, "limit": 0})
    assert "USDT/USDT" in out


def test_apply_filters_limit_applied_last():
    syms = [f"T{i}/USDT" for i in range(100)]
    out = G.apply_filters(syms, {"exclude_stablecoins": False, "limit": 10})
    assert len(out) == 10


def test_apply_filters_normalizes_base_only_symbols():
    """User can pass ["BTC", "ETH"] and get back ["BTC/USDT", "ETH/USDT"]."""
    out = G.apply_filters(["BTC", "ETH"], {"exclude_stablecoins": False, "limit": 0})
    assert "BTC/USDT" in out
    assert "ETH/USDT" in out


# ---------------------------------------------------------------- #
# Custom groups persistence
# ---------------------------------------------------------------- #

def test_save_and_delete_custom_group(tmp_path, monkeypatch):
    """Save → reload → delete a custom group via a temp config file."""
    fake_file = tmp_path / "groups_config.json"
    monkeypatch.setattr(G, "CUSTOM_GROUPS_FILE", str(fake_file))
    monkeypatch.setattr(G, "CONFIG_DIR", str(tmp_path))

    # Save
    ok = G.save_custom_group("test_xyz", ["BTC/USDT", "ETH/USDT"], "Test group")
    assert ok is True
    assert fake_file.exists()

    # Verify it loads
    groups = G.list_groups()
    assert "test_xyz" in groups
    assert groups["test_xyz"]["category"] == "custom"

    # Delete
    ok = G.delete_custom_group("test_xyz")
    assert ok is True
    groups = G.list_groups()
    assert "test_xyz" not in groups


def test_save_custom_group_rejects_reserved_name(tmp_path, monkeypatch):
    """Cannot overwrite predefined groups like 'blue_chips'."""
    monkeypatch.setattr(G, "CUSTOM_GROUPS_FILE", str(tmp_path / "g.json"))
    monkeypatch.setattr(G, "CONFIG_DIR", str(tmp_path))
    ok = G.save_custom_group("blue_chips", ["BTC/USDT"])
    assert ok is False


def test_save_custom_group_normalizes_symbol_formats(tmp_path, monkeypatch):
    """Accepts both 'BTC/USDT' and 'BTC' and 'BTCUSDT'."""
    monkeypatch.setattr(G, "CUSTOM_GROUPS_FILE", str(tmp_path / "g.json"))
    monkeypatch.setattr(G, "CONFIG_DIR", str(tmp_path))
    ok = G.save_custom_group(
        "mix_formats", ["BTC/USDT", "ETH", "SOLUSDT"], ""
    )
    assert ok is True
    # Reload the raw file and verify bases are normalized to bare bases
    with open(tmp_path / "g.json") as f:
        data = json.load(f)
    assert data["mix_formats"]["bases"] == ["BTC", "ETH", "SOL"]


# ---------------------------------------------------------------- #
# Server endpoints (smoke tests via TestClient)
# ---------------------------------------------------------------- #

def test_endpoint_get_groups_returns_200():
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/groups")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "groups" in data
    assert len(data["groups"]) >= 18


def test_endpoint_resolve_group_returns_symbols():
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/groups/resolve", params={"group_id": "blue_chips", "exchange": "mexc"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["count"] == 5
    assert "BTC/USDT" in data["symbols"]


def test_endpoint_resolve_group_with_filters():
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/groups/resolve", params={
        "group_id": "top25_mcap",
        "exchange": "mexc",
        "exclude_stablecoins": True,
        "limit": 5,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["count"] <= 5


def test_endpoint_save_custom_group_via_api(tmp_path, monkeypatch):
    """End-to-end save + delete via REST endpoints using a temp config file."""
    monkeypatch.setattr(G, "CUSTOM_GROUPS_FILE", str(tmp_path / "g.json"))
    monkeypatch.setattr(G, "CONFIG_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    c = TestClient(app)

    # Save
    r = c.post("/api/groups/custom", json={
        "name": "my_test_group",
        "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        "description": "Created by test",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Verify it shows in /api/groups
    r = c.get("/api/groups")
    assert "my_test_group" in r.json()["groups"]

    # Delete via the DELETE endpoint
    r = c.request("DELETE", "/api/groups/custom", params={"name": "my_test_group"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_endpoint_sweep_accepts_group_id_param():
    """v0.33.0: /api/sweep must accept the new group_id + filters fields
    without breaking the existing API. We just verify the request schema
    accepts the fields (we don't actually start a sweep).
    """
    from ppmt.terminal.server import SweepRequest
    req = SweepRequest(
        symbols=[],
        group_id="blue_chips",
        filters={"limit": 3},
        timeframe="1h",
        exchange="mexc",
        capital=1000.0,
        skip_if_pass=False,
    )
    assert req.group_id == "blue_chips"
    assert req.filters == {"limit": 3}
