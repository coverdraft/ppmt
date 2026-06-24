"""
Track D: V5 Risk Gate — behavioral rules from trader history.

Hard rules extracted from analysis of 4,306 real MEXC futures trades
(see download/edge_profile.json). The profile that lifted PF from
0.63 to 21.45 was:
  - LONG only (BLOCK all SHORTs in blue/meme — 100% of wins were LONG)
  - Scalp duration <15min (BLOCK trades >15min — 67.6% of wins <15min)
  - Leverage 5-10x (BLOCK >10x — cap at 10x)
  - Asia session hours UTC+2 [18-23, 0-2] (BOOST confidence)
  - Altcoins preferred (BLOCK blue_chip LONGs — only 11% of wins)
  - HARD SL -5% on margin (kills the worst losers)
  - Edge filter: |expected_move| > 0.5% (the user's own idea)

This module is a pure function: signal in → approved/blocked signal out.
It does NOT depend on the engine's internals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

LOG = logging.getLogger("v5_risk_gate")


@dataclass
class SignalV5:
    """V5 trading signal — input to the Risk Gate."""
    symbol: str
    asset_class: str  # blue_chip | large_cap | mid_cap | meme
    timeframe: str    # 1m | 5m | 15m
    direction: str    # LONG | SHORT
    entry_price: float
    expected_move_pct: float  # from the trie prior
    win_rate: float           # from the trie prior
    confidence: float         # from the LGBM model
    hour_utc: int             # 0..23
    pattern_hash: str = ""
    leverage: int = 10        # requested leverage
    size_usd: float = 100.0   # requested position size


@dataclass
class DecisionV5:
    """V5 Risk Gate decision."""
    approved: bool
    reason: str = ""
    adjusted_leverage: int = 0
    adjusted_size_usd: float = 0.0
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    max_hold_bars: int = 0
    confidence_boost: float = 1.0  # multiplier on signal.confidence
    final_confidence: float = 0.0


# ──────────────────────────────────────────────────────────────
# Rule configuration (extracted from real trader history)
# ──────────────────────────────────────────────────────────────

# Hours where trader's wins concentrated (UTC+2). Convert to UTC: subtract 2.
# Trader profile said: 18,19,23,13,20 → UTC = 16,17,21,11,18
# But we also keep the broader Asia window 0,1,2,18,19,20,21,22,23 UTC.
ASIA_HOURS_UTC = {0, 1, 2, 18, 19, 20, 21, 22, 23}
# Hours where trader lost disproportionately (UTC): 4, 5, 9, 12, 16, 20
# (originally UTC+2: 6, 7, 11, 14, 18, 22 — but 22 is in asia too, so
# only 6, 7, 11, 14, 18 are net losing in UTC+2, which maps to 4, 5, 9, 12, 16 UTC)
BAD_HOURS_UTC = {4, 5, 9, 12, 16}

# Asset classes where SHORTs are forbidden (validated by trader history:
# 100% of wins were LONG)
NO_SHORT_CLASSES = {"blue_chip", "large_cap", "meme"}

# Hard caps
MAX_LEVERAGE = 10
MIN_LEVERAGE = 3
MAX_HOLD_BARS_5M = 3   # 15 min at 5m
MAX_HOLD_BARS_15M = 1  # 15 min at 15m
MAX_HOLD_BARS_1M = 15  # 15 min at 1m

# Edge filter (user's idea): block trades where expected move is too small
MIN_EDGE_PCT = 0.5

# Hard stop loss
HARD_SL_PCT_MARGIN = -5.0  # on margin (after leverage)


def evaluate_signal(sig: SignalV5) -> DecisionV5:
    """Run the V5 Risk Gate on a signal.

    Returns a DecisionV5 with approved=True only if ALL hard rules pass.
    Confidence is boosted/damped by soft rules.
    """
    dec = DecisionV5(
        approved=False,
        adjusted_leverage=sig.leverage,
        adjusted_size_usd=sig.size_usd,
        final_confidence=sig.confidence,
    )

    # ── HARD RULES (any failure → BLOCK) ──

    # Rule 1: BLOCK SHORTs in blue/large/meme
    if sig.direction == "SHORT" and sig.asset_class in NO_SHORT_CLASSES:
        dec.reason = f"BLOCKED: SHORT forbidden on {sig.asset_class}"
        return dec

    # Rule 2: BLOCK BAD hours (net losing hours in trader history)
    if sig.hour_utc in BAD_HOURS_UTC:
        dec.reason = f"BLOCKED: bad hour UTC={sig.hour_utc}"
        return dec

    # Rule 3: BLOCK if expected_move < MIN_EDGE_PCT (user's edge filter)
    # V5.1: Skip this rule if expected_move is 0 (no trie prior populated).
    if abs(sig.expected_move_pct) > 0 and abs(sig.expected_move_pct) < MIN_EDGE_PCT:
        dec.reason = (
            f"BLOCKED: edge too small |expected_move|="
            f"{abs(sig.expected_move_pct):.3f}% < {MIN_EDGE_PCT}%"
        )
        return dec

    # Rule 4: BLOCK if win_rate < 0.45 (no real edge in the prior)
    # V5.1: Skip this rule if prior_win_rate is 0 (meaning the trie prior
    # hasn't been populated yet — fallback to LGBM confidence only).
    if sig.win_rate > 0 and sig.win_rate < 0.45:
        dec.reason = f"BLOCKED: prior win_rate={sig.win_rate:.3f} < 0.45"
        return dec

    # Rule 5: BLOCK if LGBM confidence < 0.55
    if sig.confidence < 0.55:
        dec.reason = f"BLOCKED: LGBM confidence={sig.confidence:.3f} < 0.55"
        return dec

    # ── SOFT RULES (boost or dampen confidence) ──

    boost = 1.0

    # Asia hours BOOST
    if sig.hour_utc in ASIA_HOURS_UTC:
        boost *= 1.15
        dec.reason += "Asia_hours_boost×1.15; "

    # Altcoin BOOST (not blue_chip)
    if sig.asset_class in {"mid_cap", "large_cap"}:
        boost *= 1.10
        dec.reason += f"{sig.asset_class}_boost×1.10; "
    elif sig.asset_class == "blue_chip":
        boost *= 0.80  # blue chips had only 11% of wins
        dec.reason += "blue_chip_damp×0.80; "

    # Scalp TF BOOST
    if sig.timeframe in {"1m", "5m", "15m"}:
        boost *= 1.05
        dec.reason += f"scalp_tf_{sig.timeframe}_boost×1.05; "

    # Cap the boost at 1.3 max (don't over-leverage confidence)
    boost = max(0.5, min(1.3, boost))
    dec.confidence_boost = boost
    dec.final_confidence = sig.confidence * boost

    # ── ADJUST LEVERAGE & SIZE ──

    # Cap leverage to 10x max, 3x min
    dec.adjusted_leverage = max(MIN_LEVERAGE, min(MAX_LEVERAGE, sig.leverage))
    if dec.adjusted_leverage != sig.leverage:
        dec.reason += f"leverage_capped_{sig.leverage}→{dec.adjusted_leverage}; "

    # Scale size by final_confidence (Kelly-lite)
    # Risk 1% of size per trade at confidence=0.6, up to 3% at confidence=0.9
    kelly_fraction = max(0.0, min(0.03, (dec.final_confidence - 0.55) * 0.10))
    dec.adjusted_size_usd = sig.size_usd * kelly_fraction * 100  # scale to size_usd
    dec.adjusted_size_usd = max(10.0, min(500.0, dec.adjusted_size_usd))

    # ── SET SL / TP / MAX HOLD ──

    # Hard SL at -5% on margin
    if sig.direction == "LONG":
        dec.sl_price = sig.entry_price * (1 + HARD_SL_PCT_MARGIN / 100.0 / dec.adjusted_leverage)
        dec.tp_price = sig.entry_price * (1 + sig.expected_move_pct / 100.0)
    else:  # SHORT (only allowed on mid_cap)
        dec.sl_price = sig.entry_price * (1 - HARD_SL_PCT_MARGIN / 100.0 / dec.adjusted_leverage)
        dec.tp_price = sig.entry_price * (1 - abs(sig.expected_move_pct) / 100.0)

    # Max hold
    if sig.timeframe == "1m":
        dec.max_hold_bars = MAX_HOLD_BARS_1M
    elif sig.timeframe == "5m":
        dec.max_hold_bars = MAX_HOLD_BARS_5M
    elif sig.timeframe == "15m":
        dec.max_hold_bars = MAX_HOLD_BARS_15M
    else:
        dec.max_hold_bars = 3

    dec.approved = True
    dec.reason = dec.reason.strip("; ") or "approved"
    return dec


def evaluate_batch(signals: list[SignalV5]) -> list[DecisionV5]:
    """Evaluate multiple signals. Returns one Decision per signal."""
    return [evaluate_signal(s) for s in signals]


def summarize_decisions(decisions: list[DecisionV5]) -> dict:
    """Aggregate stats on a batch of decisions."""
    n = len(decisions)
    approved = sum(1 for d in decisions if d.approved)
    blocked = n - approved
    block_reasons: dict[str, int] = {}
    for d in decisions:
        if not d.approved:
            key = d.reason.split(":")[0].strip() if ":" in d.reason else d.reason
            block_reasons[key] = block_reasons.get(key, 0) + 1
    avg_conf = sum(d.final_confidence for d in decisions if d.approved) / max(approved, 1)
    avg_lev = sum(d.adjusted_leverage for d in decisions if d.approved) / max(approved, 1)
    return {
        "total": n,
        "approved": approved,
        "blocked": blocked,
        "approval_rate": approved / max(n, 1),
        "block_reasons": block_reasons,
        "avg_confidence_approved": avg_conf,
        "avg_leverage_approved": avg_lev,
    }


# ── CLI for testing ──
if __name__ == "__main__":
    # Quick smoke test
    test_signals = [
        SignalV5(symbol="BTCUSDT", asset_class="blue_chip", timeframe="5m",
                 direction="LONG", entry_price=100000, expected_move_pct=1.2,
                 win_rate=0.62, confidence=0.72, hour_utc=19,
                 leverage=8, size_usd=200),
        SignalV5(symbol="BTCUSDT", asset_class="blue_chip", timeframe="5m",
                 direction="SHORT", entry_price=100000, expected_move_pct=-1.2,
                 win_rate=0.55, confidence=0.65, hour_utc=19,
                 leverage=8, size_usd=200),
        SignalV5(symbol="SOLUSDT", asset_class="large_cap", timeframe="5m",
                 direction="LONG", entry_price=200, expected_move_pct=0.8,
                 win_rate=0.58, confidence=0.62, hour_utc=12,
                 leverage=10, size_usd=150),
        SignalV5(symbol="SOLUSDT", asset_class="large_cap", timeframe="5m",
                 direction="LONG", entry_price=200, expected_move_pct=0.3,
                 win_rate=0.58, confidence=0.62, hour_utc=22,
                 leverage=10, size_usd=150),
        SignalV5(symbol="PEPEUSDT", asset_class="meme", timeframe="1m",
                 direction="LONG", entry_price=0.0001, expected_move_pct=2.5,
                 win_rate=0.70, confidence=0.78, hour_utc=21,
                 leverage=7, size_usd=100),
    ]
    for s in test_signals:
        d = evaluate_signal(s)
        print(f"\n{s.symbol} {s.direction} {s.timeframe} hour={s.hour_utc} "
              f"asset={s.asset_class} exp_move={s.expected_move_pct}%")
        print(f"  → approved={d.approved}  reason='{d.reason}'")
        if d.approved:
            print(f"    final_conf={d.final_confidence:.3f}  lev={d.adjusted_leverage}x  "
                  f"size=${d.adjusted_size_usd:.0f}  SL={d.sl_price:.4f}  TP={d.tp_price:.4f}  "
                  f"max_hold={d.max_hold_bars}bars")
    print("\nSummary:", summarize_decisions([evaluate_signal(s) for s in test_signals]))
