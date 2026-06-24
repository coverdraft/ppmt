"""
v5_risk_gate_cb_v2.py — Re-tuned Risk Gate for the cb_v2 LGBM model.

DIFFERENCES vs the original v5_risk_gate (which was tuned for v1 Binance data):

1. ALL signals are LONG — the cb_v2 label_hit_tp_first label is LONG-directional
   by construction (1 = price hit +0.6% TP before -0.4% SL on a LONG).
   The original gate's "BLOCK SHORT in blue/large/meme" rule is irrelevant.

2. Blue_chip LONGs are BOOSTED, not dampened — in cb_v2 OOS test set:
   - BTC LONGs at proba>=0.7: 92.4% precision
   - ETH LONGs at proba>=0.7: 91.3% precision
   - vs original v1 Binance: blue_chip LONGs had only 11% win rate
   The original gate's 0.80 damp on blue_chip is inverted to a 1.10 boost.

3. Meme coins are DAMPENED, not neutral — in cb_v2 OOS test set:
   - WIF LONGs at proba>=0.7: 83.5% precision (worst)
   - BONK LONGs at proba>=0.7: 84.1% precision
   - PEPE LONGs at proba>=0.7: 85.4% precision
   vs 88-92% for blue/large/mid cap. Dampen by 0.95.

4. Trie prior rules (expected_move, win_rate) are SKIPPED entirely —
   the cb_v2 extractor sets these to 0.0 because the trie prior wasn't computed.
   LGBM confidence alone is the signal.

5. BAD_HOURS filter is kept — the trader-history hours (4, 5, 9, 12, 16 UTC)
   were net losing in real trades; this is a behavioral rule, not a
   market-microstructure rule, so it should still apply.

6. Asia hours BOOST is kept — behavioral rule, still applies.

7. Confidence threshold raised from 0.55 to 0.60 — since LGBM is now the
   sole signal (no trie prior corroboration), require slightly higher confidence.

8. Leverage cap lowered from 10x to 7x — original gate allowed 10x based on
   trader history, but cb_v2 has higher per-trade variance (label_pnl among
   winners ranges from -3.95% to +11.81%, suggesting some positions hit
   extended stops). Cap at 7x to keep margin-of-safety.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger("v5_risk_gate_cb_v2")


@dataclass
class SignalV5Cb:
    """V5 cb_v2 trading signal — input to the re-tuned Risk Gate."""
    symbol: str
    asset_class: str       # blue_chip | large_cap | mid_cap | meme
    timeframe: str         # 1m | 5m | 15m
    direction: str = "LONG"  # always LONG in cb_v2 (label semantics)
    entry_price: float = 100.0
    expected_move_pct: float = 0.0  # always 0 in cb_v2 (no trie prior)
    win_rate: float = 0.0           # always 0 in cb_v2 (no trie prior)
    confidence: float = 0.0         # LGBM probability
    hour_utc: int = 0
    pattern_hash: str = ""
    leverage: int = 7
    size_usd: float = 100.0


@dataclass
class DecisionV5Cb:
    """V5 cb_v2 Risk Gate decision."""
    approved: bool
    reason: str = ""
    adjusted_leverage: int = 0
    adjusted_size_usd: float = 0.0
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    max_hold_bars: int = 0
    confidence_boost: float = 1.0
    final_confidence: float = 0.0


# Hours where trader's wins concentrated (Asia session UTC)
ASIA_HOURS_UTC = {0, 1, 2, 18, 19, 20, 21, 22, 23}

# Hours where trader lost disproportionately (UTC): 4, 5, 9, 12, 16
BAD_HOURS_UTC = {4, 5, 9, 12, 16}

# Hard caps (cb_v2 tuned)
MAX_LEVERAGE = 7   # lowered from 10 → 7
MIN_LEVERAGE = 3
MAX_HOLD_BARS_5M = 3
MAX_HOLD_BARS_15M = 1
MAX_HOLD_BARS_1M = 15

# Confidence threshold (cb_v2 tuned — higher since LGBM is sole signal)
MIN_CONFIDENCE = 0.60

# Hard stop loss on margin
HARD_SL_PCT_MARGIN = -5.0


def evaluate_signal_cb_v2(sig: SignalV5Cb) -> DecisionV5Cb:
    """Run the V5 cb_v2 Risk Gate on a signal.

    Returns a DecisionV5Cb with approved=True only if ALL hard rules pass.
    """
    dec = DecisionV5Cb(
        approved=False,
        adjusted_leverage=sig.leverage,
        adjusted_size_usd=sig.size_usd,
        final_confidence=sig.confidence,
    )

    # ── HARD RULES ──

    # Rule 1: BLOCK BAD hours (behavioral rule from trader history)
    if sig.hour_utc in BAD_HOURS_UTC:
        dec.reason = f"BLOCKED: bad hour UTC={sig.hour_utc}"
        return dec

    # Rule 2: BLOCK if LGBM confidence < MIN_CONFIDENCE
    if sig.confidence < MIN_CONFIDENCE:
        dec.reason = f"BLOCKED: LGBM confidence={sig.confidence:.3f} < {MIN_CONFIDENCE}"
        return dec

    # Note: SHORT block removed — all cb_v2 signals are LONG by label semantics.
    # Note: edge filter removed — expected_move is always 0 in cb_v2.
    # Note: win_rate filter removed — prior_win_rate is always 0 in cb_v2.

    # ── SOFT RULES (boost or dampen confidence) ──

    boost = 1.0

    # Asia hours BOOST
    if sig.hour_utc in ASIA_HOURS_UTC:
        boost *= 1.15
        dec.reason += "Asia_hours_boost×1.15; "

    # Asset class boost (cb_v2 tuned — INVERTED vs original)
    # Blue chips now BOOSTED (92% precision in cb_v2 OOS)
    if sig.asset_class == "blue_chip":
        boost *= 1.10
        dec.reason += "blue_chip_boost×1.10; "
    elif sig.asset_class == "large_cap":
        boost *= 1.10
        dec.reason += "large_cap_boost×1.10; "
    elif sig.asset_class == "mid_cap":
        boost *= 1.05
        dec.reason += "mid_cap_boost×1.05; "
    elif sig.asset_class == "meme":
        boost *= 0.95  # memes have lower precision in cb_v2 (83-85%)
        dec.reason += "meme_damp×0.95; "

    # Scalp TF BOOST (kept from original)
    if sig.timeframe in {"1m", "5m", "15m"}:
        boost *= 1.05
        dec.reason += f"scalp_tf_{sig.timeframe}_boost×1.05; "

    # Cap the boost
    boost = max(0.5, min(1.3, boost))
    dec.confidence_boost = boost
    dec.final_confidence = sig.confidence * boost

    # ── ADJUST LEVERAGE & SIZE ──

    dec.adjusted_leverage = max(MIN_LEVERAGE, min(MAX_LEVERAGE, sig.leverage))
    if dec.adjusted_leverage != sig.leverage:
        dec.reason += f"leverage_capped_{sig.leverage}→{dec.adjusted_leverage}; "

    # Kelly-lite sizing based on final_confidence
    kelly_fraction = max(0.0, min(0.03, (dec.final_confidence - 0.55) * 0.10))
    dec.adjusted_size_usd = sig.size_usd * kelly_fraction * 100
    dec.adjusted_size_usd = max(10.0, min(500.0, dec.adjusted_size_usd))

    # ── SET SL / TP / MAX HOLD ──

    # Always LONG in cb_v2
    dec.sl_price = sig.entry_price * (1 + HARD_SL_PCT_MARGIN / 100.0 / dec.adjusted_leverage)
    # TP based on the label's TP_RETURN (0.6% bar-level)
    dec.tp_price = sig.entry_price * 1.006

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


def summarize_decisions_cb_v2(decisions: list[DecisionV5Cb]) -> dict:
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


if __name__ == "__main__":
    test_signals = [
        SignalV5Cb(symbol="BTCUSDT", asset_class="blue_chip", timeframe="5m",
                   confidence=0.75, hour_utc=19, leverage=7, size_usd=200),
        SignalV5Cb(symbol="ETHUSDT", asset_class="blue_chip", timeframe="5m",
                   confidence=0.72, hour_utc=12, leverage=7, size_usd=200),  # bad hour
        SignalV5Cb(symbol="SOLUSDT", asset_class="large_cap", timeframe="5m",
                   confidence=0.68, hour_utc=22, leverage=7, size_usd=150),
        SignalV5Cb(symbol="PEPEUSDT", asset_class="meme", timeframe="1m",
                   confidence=0.58, hour_utc=21, leverage=7, size_usd=100),  # below thr
        SignalV5Cb(symbol="ADAUSDT", asset_class="mid_cap", timeframe="5m",
                   confidence=0.78, hour_utc=21, leverage=7, size_usd=100),
    ]
    for s in test_signals:
        d = evaluate_signal_cb_v2(s)
        print(f"\n{s.symbol} {s.timeframe} hour={s.hour_utc} "
              f"asset={s.asset_class} conf={s.confidence:.3f}")
        print(f"  → approved={d.approved}  reason='{d.reason}'")
        if d.approved:
            print(f"    final_conf={d.final_confidence:.3f}  lev={d.adjusted_leverage}x  "
                  f"size=${d.adjusted_size_usd:.0f}  max_hold={d.max_hold_bars}bars")
