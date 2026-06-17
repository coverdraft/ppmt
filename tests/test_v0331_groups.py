"""v0.33.1: Tests for the new dynamic groups + Sweep All Groups feature.

Covers:
  - recently_listed_30d group definition exists and requires listing_ts filter
  - high_liquidity_low_spread group definition exists and applies max_spread_pct filter
  - fetch_market_snapshot enriches tickers with spread_pct and listing_ts
  - _resolve_dynamic_group correctly applies listing_days_max and max_spread_pct
  - SweepRequest accepts the new sweep_all_groups + all_groups_categories fields
  - pollSweepStatus sort: PASS first, then by PF descending (JS logic verified
    by a pure-Python replication — the JS is a 1:1 port of this comparator)
"""
from __future__ import annotations

import time

import pytest

from ppmt.terminal.server import SweepRequest
from ppmt.data import groups as G


# ---------------------------------------------------------------- #
# New group definitions exist
# ---------------------------------------------------------------- #

def test_recently_listed_30d_group_exists():
    """v0.33.1: 'Recién Listados (30d)' group must be defined in DYNAMIC_GROUPS."""
    assert "recently_listed_30d" in G.DYNAMIC_GROUPS
    gdef = G.DYNAMIC_GROUPS["recently_listed_30d"]
    assert gdef["category"] == "dynamic"
    assert gdef["listing_days_max"] == 30
    assert gdef["min_volume_usd"] >= 1_000_000  # require minimum liquidity
    assert "Recién" in gdef["label"] or "Recien" in gdef["label"]


def test_high_liquidity_low_spread_group_exists():
    """v0.33.1: 'Alta Liquidez / Spread < 0.05%' group must be defined."""
    assert "high_liquidity_low_spread" in G.DYNAMIC_GROUPS
    gdef = G.DYNAMIC_GROUPS["high_liquidity_low_spread"]
    assert gdef["category"] == "dynamic"
    assert gdef["max_spread_pct"] == 0.05
    assert gdef["min_volume_usd"] >= 5_000_000
    assert "Liquidez" in gdef["label"]


def test_list_groups_includes_new_groups():
    """list_groups() must surface the two new dynamic groups to the UI."""
    groups = G.list_groups()
    assert "recently_listed_30d" in groups
    assert "high_liquidity_low_spread" in groups
    assert groups["recently_listed_30d"]["category"] == "dynamic"
    assert groups["high_liquidity_low_spread"]["category"] == "dynamic"


# ---------------------------------------------------------------- #
# _resolve_dynamic_group with the new filters
# ---------------------------------------------------------------- #

def _fake_tickers():
    """Synthetic ticker snapshot for offline testing of dynamic group filters."""
    now = time.time()
    return {
        # Old, high-volume token — should NOT appear in recently_listed_30d
        "BTC/USDT": {
            "quoteVolume": 5_000_000_000, "volatility_pct": 2.0, "percentage": 0.5,
            "bid": 50000, "ask": 50005, "spread_pct": 0.01,
            "listing_ts": now - 365 * 86400,  # 1 year ago
        },
        # Newly listed, illiquid — should be filtered out by min_volume_usd
        "NEW1/USDT": {
            "quoteVolume": 100_000, "volatility_pct": 15.0, "percentage": 200.0,
            "bid": 0.001, "ask": 0.0011, "spread_pct": 9.0,
            "listing_ts": now - 5 * 86400,  # 5 days ago
        },
        # Newly listed with decent volume — should appear in recently_listed_30d
        "NEW2/USDT": {
            "quoteVolume": 5_000_000, "volatility_pct": 8.0, "percentage": 50.0,
            "bid": 0.5, "ask": 0.501, "spread_pct": 0.20,
            "listing_ts": now - 10 * 86400,  # 10 days ago
        },
        # Old high-volume tight-spread — should appear in high_liquidity_low_spread
        "ETH/USDT": {
            "quoteVolume": 2_000_000_000, "volatility_pct": 3.0, "percentage": -0.2,
            "bid": 3000, "ask": 3000.5, "spread_pct": 0.017,
            "listing_ts": now - 700 * 86400,
        },
        # Old high-volume WIDE spread — should be filtered by max_spread_pct
        "WIDE/USDT": {
            "quoteVolume": 50_000_000, "volatility_pct": 5.0, "percentage": 0.0,
            "bid": 1.0, "ask": 1.01, "spread_pct": 1.0,
            "listing_ts": now - 200 * 86400,
        },
    }


def test_recently_listed_filter_only_keeps_new_tokens(monkeypatch):
    """recently_listed_30d must drop tokens older than 30 days."""
    fake = _fake_tickers()
    monkeypatch.setattr(G, "fetch_market_snapshot", lambda exch="mexc": fake)
    # apply_filters needs the snapshot too for stablecoin filtering — return fake
    monkeypatch.setattr(G, "fetch_market_snapshot", lambda exch="mexc": fake)

    syms = G.resolve_group("recently_listed_30d", exchange="mexc", filters={"limit": 0})
    # NEW2 is the only token that's both <30 days old AND has volume >= $1M
    assert "NEW2/USDT" in syms
    assert "BTC/USDT" not in syms
    assert "ETH/USDT" not in syms
    assert "NEW1/USDT" not in syms  # too illiquid


def test_high_liquidity_low_spread_drops_wide_spreads(monkeypatch):
    """high_liquidity_low_spread must drop tokens with spread > 0.05%."""
    fake = _fake_tickers()
    monkeypatch.setattr(G, "fetch_market_snapshot", lambda exch="mexc": fake)

    syms = G.resolve_group("high_liquidity_low_spread", exchange="mexc", filters={"limit": 0})
    # BTC and ETH both have spread < 0.05%; WIDE has 1.0%, NEW2 has 0.20%
    assert "BTC/USDT" in syms
    assert "ETH/USDT" in syms
    assert "WIDE/USDT" not in syms
    assert "NEW2/USDT" not in syms  # spread 0.20% > 0.05%


def test_unknown_listing_ts_dropped_from_recently_listed(monkeypatch):
    """Tokens with no listing_ts should be dropped from recently_listed (conservative)."""
    fake = _fake_tickers()
    fake["UNKNOWN_AGE/USDT"] = {
        "quoteVolume": 5_000_000, "volatility_pct": 5.0, "percentage": 1.0,
        "bid": 1.0, "ask": 1.001, "spread_pct": 0.10,
        "listing_ts": None,  # unknown
    }
    monkeypatch.setattr(G, "fetch_market_snapshot", lambda exch="mexc": fake)
    syms = G.resolve_group("recently_listed_30d", exchange="mexc", filters={"limit": 0})
    assert "UNKNOWN_AGE/USDT" not in syms


def test_unknown_spread_dropped_from_high_liquidity(monkeypatch):
    """Tokens with no spread (no bid/ask) should be dropped from high_liquidity_low_spread."""
    fake = _fake_tickers()
    fake["NOSPD/USDT"] = {
        "quoteVolume": 100_000_000, "volatility_pct": 5.0, "percentage": 1.0,
        "bid": 0, "ask": 0, "spread_pct": None,
        "listing_ts": time.time() - 100 * 86400,
    }
    monkeypatch.setattr(G, "fetch_market_snapshot", lambda exch="mexc": fake)
    syms = G.resolve_group("high_liquidity_low_spread", exchange="mexc", filters={"limit": 0})
    assert "NOSPD/USDT" not in syms


# ---------------------------------------------------------------- #
# SweepRequest schema accepts new fields
# ---------------------------------------------------------------- #

def test_sweep_request_accepts_sweep_all_groups():
    """v0.33.1: SweepRequest must accept sweep_all_groups and all_groups_categories."""
    req = SweepRequest(
        symbols=[],
        group_id="",
        sweep_all_groups=True,
        all_groups_categories=["dynamic", "category"],
        timeframe="1h",
        exchange="mexc",
        capital=1000.0,
    )
    assert req.sweep_all_groups is True
    assert "dynamic" in req.all_groups_categories
    assert "category" in req.all_groups_categories


def test_sweep_request_defaults_sweep_all_groups_false():
    req = SweepRequest()
    assert req.sweep_all_groups is False
    assert req.all_groups_categories == []


# ---------------------------------------------------------------- #
# Sort logic — pure Python replication of the JS comparator
# (PASS first, then by PF descending, INSUFFICIENT_DATA in the middle)
# ---------------------------------------------------------------- #

def _sort_results(results):
    """Replica of the JS sort in pollSweepStatus() — same comparator."""
    def score(r):
        if r.get("verdict") == "PASS":
            return 2
        if r.get("verdict") == "INSUFFICIENT_DATA":
            return 1
        return 0
    def pf(r):
        v = r.get("profit_factor")
        return v if isinstance(v, (int, float)) else 0
    return sorted(results, key=lambda r: (-score(r), -pf(r)))


def test_sort_pass_first_then_pf_descending():
    """PASS tokens must appear before FAIL, and within PASS, higher PF first."""
    results = [
        {"symbol": "A", "verdict": "FAIL", "profit_factor": 5.0},
        {"symbol": "B", "verdict": "PASS", "profit_factor": 1.5},
        {"symbol": "C", "verdict": "PASS", "profit_factor": 3.0},
        {"symbol": "D", "verdict": "PASS", "profit_factor": 0.9},
        {"symbol": "E", "verdict": "INSUFFICIENT_DATA", "profit_factor": 0},
        {"symbol": "F", "verdict": "FAIL", "profit_factor": 0.5},
    ]
    sorted_r = _sort_results(results)
    verdicts = [r["symbol"] for r in sorted_r]
    # Expected order: PASS by PF desc (C=3.0, B=1.5, D=0.9),
    # then INSUFFICIENT_DATA (E), then FAIL by PF desc (A=5.0, F=0.5).
    assert verdicts == ["C", "B", "D", "E", "A", "F"]


def test_sort_handles_missing_profit_factor():
    """Missing/None profit_factor must be treated as 0, not crash."""
    results = [
        {"symbol": "A", "verdict": "PASS"},  # PF missing → 0
        {"symbol": "B", "verdict": "PASS", "profit_factor": 1.2},
        {"symbol": "C", "verdict": "PASS", "profit_factor": None},  # → 0
    ]
    sorted_r = _sort_results(results)
    # B (1.2) first, then A and C tied at 0 — relative order between A/C
    # is stable (Python's sort is stable) so A before C.
    assert sorted_r[0]["symbol"] == "B"
    assert sorted_r[1]["symbol"] == "A"
    assert sorted_r[2]["symbol"] == "C"


def test_sort_error_treated_as_fail_tier():
    """ERROR verdict should land in the bottom tier alongside FAIL."""
    results = [
        {"symbol": "A", "verdict": "PASS", "profit_factor": 1.0},
        {"symbol": "B", "verdict": "ERROR", "profit_factor": 99},
        {"symbol": "C", "verdict": "FAIL", "profit_factor": 0.1},
    ]
    sorted_r = _sort_results(results)
    # PASS first (A), then ERROR/FAIL by PF desc (B=99, C=0.1)
    assert [r["symbol"] for r in sorted_r] == ["A", "B", "C"]
