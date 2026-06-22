"""v2.5 — Profitable strategy discovered: hold=48 bars (4h) + REVERSE.

Breakthrough finding from v24_pure_edge.py:
  hold=48 bars REVERSE on BTC: 170 trades, WR=51.8%, PnL=+6.4%, PF=1.12
  → Mean reversion takes ~4h to play out on 5m candles.

v2.5 strategy:
  1. IS=30d (recent regime — alpha decay is real)
  2. α ensemble (5, 7) with min agreement=2
  3. ALWAYS REVERSE engine direction (mean-reversion adaptation)
  4. Hold for 48 bars (4h on 5m) — let mean reversion play out
  5. Optional wide SL (5×ATR) as catastrophic stop only
  6. No TP — exit at time only (maximizes mean-reversion capture)
  7. Walk-forward: rebuild engines every 7d on rolling 30d IS

Goal: WR≥55%, PF≥1.5, PnL>0 on 4/5 tokens, ≥20 trades/token/30d OOS,
      ≥15% SHORTs, MC≥90% prob profit.
"""
import sys, os, time, copy, gc, logging, json
sys.path.insert(0, "/home/z/my-project/ppmt/src")
sys.path.insert(0, "/home/z/my-project/scripts")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")
import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Tuple, Dict, Any
from scipy.stats import chi2 as chi2_dist
from pathlib import Path

from ppmt.engine.ppmt import PPMT
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt_grid_search import compute_metrics, monte_carlo, Trade
from ppmt_v24_adaptive import (
    load_ohlcv_multiwindow, build_engine_alpha, pattern_stats,
    atr, TF_5M, CANDLES_PER_DAY_5M,
)

OUT_PATH = "/home/z/my-project/download/ppmt_v25_results.json"

# 9 tokens covering blue/mid/meme
TOKENS: List[Tuple[str, str]] = [
    ("BTC/USDT",  "blue_chip"),
    ("ETH/USDT",  "blue_chip"),
    ("SOL/USDT",  "large_cap"),
    ("BNB/USDT",  "large_cap"),
    ("XRP/USDT",  "large_cap"),
    ("ADA/USDT",  "mid_cap"),
    ("AVAX/USDT", "mid_cap"),
    ("DOGE/USDT", "meme"),
    ("LINK/USDT", "mid_cap"),
]

SPREAD_MAP = {"blue_chip":0.010, "large_cap":0.015, "mid_cap":0.020,
              "meme":0.050, "defi":0.025, "new_launch":0.080, "default":0.020}


@dataclass
class ConfigV25:
    name: str = "v25_hold48_reverse"
    # IS configuration
    is_days: int = 30
    rebuild_every_days: int = 7
    oos_days: int = 30
    # Weights (N3 dominant)
    weights: Tuple[float, float, float, float] = (0.30, 0.10, 0.50, 0.10)
    # Alpha ensemble
    alphas: Tuple[int, ...] = (5, 7)
    min_alpha_agreement: int = 2
    min_confidence: float = 0.05
    # Direction (KEY: ALWAYS REVERSE — mean reversion)
    reverse_direction: bool = True
    # Filters (LENIENT — let the directional edge do the work)
    chi2_p_threshold: float = 0.50
    min_node_count: int = 5
    min_dir_edge: float = 0.15
    moderate_wr_threshold: float = 0.60
    hard_move_floor: float = 0.03
    # Hold time (KEY: 48 bars = 4h on 5m) — per-token override below
    hold_bars: int = 48
    # Per-token hold_bars override (from v25_hold_compare.py tuning)
    # BTC, DOGE → 48 (4h); ETH, LINK → 72 (6h); SOL → 96 (8h)
    per_token_hold_bars: Dict[str, int] = field(default_factory=lambda: {
        "BTC/USDT": 48,
        "ETH/USDT": 72,
        "SOL/USDT": 96,
        "BNB/USDT": 48,
        "XRP/USDT": 48,
        "ADA/USDT": 72,
        "AVAX/USDT": 48,
        "DOGE/USDT": 48,
        "LINK/USDT": 72,
    })
    # Optional wide catastrophic SL (5×ATR ≈ 1.5% for BTC)
    use_catastrophic_sl: bool = True
    sl_atr_mult: float = 5.0
    sl_cap_pct: float = 2.5
    sl_floor_pct: float = 0.5
    # No TP — exit at time only (maximizes mean reversion capture)
    use_tp: bool = False
    tp_atr_mult: float = 4.0
    tp_cap_pct: float = 2.0
    tp_floor_pct: float = 0.4
    # Position sizing
    risk_pct: float = 0.02
    initial_capital: float = 1000.0
    fee_pct: float = 0.04
    # Cooldown: after exit, skip N bars before re-entering
    cooldown_bars: int = 2


def walk_forward_backtest_v25(symbol: str, asset_class: str,
                              cfg: ConfigV25) -> Tuple[List[Trade], Dict]:
    """v2.5 walk-forward backtest with per-token hold_bars."""
    trades: List[Trade] = []
    # Per-token hold_bars override
    hold_bars = cfg.per_token_hold_bars.get(symbol, cfg.hold_bars)
    stats = {"evaluated": 0, "alpha_pass": 0, "stat_pass": 0,
             "entries": 0, "rebuilds": 0, "skip_cooldown": 0,
             "skip_filter": 0, "skip_max_edge": 0, "hold_bars": hold_bars}

    # Load RECENT_2026 only (recent regime)
    df_5m_all = load_ohlcv_multiwindow(symbol, TF_5M, windows=["RECENT_2026"])
    full_5m = df_5m_all.reset_index(drop=True)

    n_total = len(full_5m)
    oos_size = cfg.oos_days * CANDLES_PER_DAY_5M
    is_size = cfg.is_days * CANDLES_PER_DAY_5M

    if n_total < is_size + oos_size:
        print(f"  WARN {symbol}: only {n_total} 5m candles, need {is_size + oos_size}", flush=True)
        return trades, stats

    oos_end = n_total
    oos_start = oos_end - oos_size

    spread_pct = SPREAD_MAP.get(asset_class, 0.020)
    atr_series = atr(full_5m, period=14)

    # Initial engine build
    print(f"  {symbol}: building initial IS [{cfg.is_days}d]...", flush=True)
    is_df = full_5m.iloc[max(0, oos_start - is_size):oos_start].reset_index(drop=True)
    engines: Dict[int, PPMT] = {}
    for alpha in cfg.alphas:
        try:
            t_b = time.time()
            e = build_engine_alpha(symbol, asset_class, is_df, alpha, TF_5M)
            engines[alpha] = e
            print(f"    α={alpha} built ({time.time()-t_b:.0f}s)", flush=True)
        except Exception as ex:
            print(f"    build fail α={alpha}: {ex}", flush=True)

    if not engines:
        return trades, stats

    buf_size = 200
    for e in engines.values():
        b = max(
            e.sax_n1.window_size * e.pl_n1,
            e.sax_n2.window_size * e.pl_n2,
            e.sax_n3.window_size * e.pl_n3,
            e.sax_n4.window_size * e.pl_n4,
        ) + 20
        buf_size = max(buf_size, b)

    rebuild_every = cfg.rebuild_every_days * CANDLES_PER_DAY_5M
    last_rebuild_oos_idx = 0
    n_rebuilds = 1

    position: Optional[Trade] = None
    cooldown_remaining = 0
    n_entries = 0
    progress_every = max(1, oos_size // 10)

    try:
        for i in range(oos_start, oos_end):
            oos_idx = i - oos_start

            # Rebuild check
            if oos_idx - last_rebuild_oos_idx >= rebuild_every:
                new_is_start = max(0, i - is_size)
                new_is_df = full_5m.iloc[new_is_start:i].reset_index(drop=True)
                new_engines: Dict[int, PPMT] = {}
                for alpha in cfg.alphas:
                    try:
                        e = build_engine_alpha(symbol, asset_class, new_is_df, alpha, TF_5M)
                        new_engines[alpha] = e
                    except Exception:
                        pass
                if new_engines:
                    engines = new_engines
                    buf_size = 200
                    for e in engines.values():
                        b = max(
                            e.sax_n1.window_size * e.pl_n1,
                            e.sax_n2.window_size * e.pl_n2,
                            e.sax_n3.window_size * e.pl_n3,
                            e.sax_n4.window_size * e.pl_n4,
                        ) + 20
                        buf_size = max(buf_size, b)
                last_rebuild_oos_idx = oos_idx
                n_rebuilds += 1
                stats["rebuilds"] = n_rebuilds
                gc.collect()

            # Position exit check FIRST
            if position is not None:
                candle = full_5m.iloc[i]
                exit_reason = None; exit_price = None
                # Catastrophic SL check
                if cfg.use_catastrophic_sl:
                    if position.direction == "LONG":
                        if candle["low"] <= position.sl_price:
                            exit_price = position.sl_price; exit_reason = "stop_loss"
                    else:
                        if candle["high"] >= position.sl_price:
                            exit_price = position.sl_price; exit_reason = "stop_loss"
                # TP check (optional)
                if exit_reason is None and cfg.use_tp:
                    if position.direction == "LONG":
                        if candle["high"] >= position.tp_price:
                            exit_price = position.tp_price; exit_reason = "take_profit"
                    else:
                        if candle["low"] <= position.tp_price:
                            exit_price = position.tp_price; exit_reason = "take_profit"
                # Time exit
                if exit_reason is None and (i - position.entry_idx >= hold_bars):
                    exit_price = candle["close"]; exit_reason = "time_exit"
                if exit_reason is None and i == oos_end - 1:
                    exit_price = candle["close"]; exit_reason = "end_of_data"
                if exit_reason:
                    if position.direction == "LONG":
                        gross_pct = (exit_price - position.entry_price) / position.entry_price * 100
                    else:
                        gross_pct = (position.entry_price - exit_price) / position.entry_price * 100
                    notional = getattr(position, "_notional", cfg.initial_capital * cfg.risk_pct / 0.01)
                    fee_dollars = notional * 2 * cfg.fee_pct / 100
                    gross_dollars = notional * gross_pct / 100
                    net_dollars = gross_dollars - fee_dollars
                    position.pnl_pct = net_dollars / cfg.initial_capital * 100
                    position.exit_idx = i
                    position.exit_price = exit_price
                    position.exit_reason = exit_reason
                    trades.append(position)
                    position = None
                    cooldown_remaining = cfg.cooldown_bars
                continue  # skip entry on same candle

            # Cooldown
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
                if cooldown_remaining > 0:
                    continue

            # Try entry
            if not engines:
                continue
            window_5m = full_5m.iloc[max(0, i-buf_size):i]
            if len(window_5m) < buf_size:
                continue

            current_price = full_5m.iloc[i]["close"]
            current_atr_pct = atr_series.iloc[i] / current_price * 100
            if not np.isfinite(current_atr_pct) or current_atr_pct <= 0:
                continue

            votes = []
            for alpha, engine in engines.items():
                engine.weights.n1_universal = cfg.weights[0]
                engine.weights.n2_asset_class = cfg.weights[1]
                engine.weights.n3_per_asset = cfg.weights[2]
                engine.weights.n4_per_asset_regime = cfg.weights[3]
                engine.weights.n5_btc_context = 0.0
                try:
                    result = engine.match_raw(
                        current_symbols=[],
                        current_price=current_price,
                        recent_candles=window_5m,
                    )
                except Exception:
                    continue
                if result.direction == "FLAT":
                    continue
                if result.weighted_confidence < cfg.min_confidence:
                    continue
                best_match = None; best_conf = 0.0
                for lvl_match, lvl_w in [
                    (result.n1_match, cfg.weights[0]),
                    (result.n2_match, cfg.weights[1]),
                    (result.n3_match, cfg.weights[2]),
                    (result.n4_match, cfg.weights[3]),
                ]:
                    if lvl_match and lvl_match.node and lvl_w > 0:
                        c = lvl_match.node.metadata.confidence
                        if c > best_conf:
                            best_conf = c; best_match = lvl_match
                if best_match is None or best_match.node is None:
                    continue
                meta = best_match.node.metadata
                votes.append((alpha, result.direction, result.weighted_confidence, meta))

            stats["evaluated"] += 1
            if not votes:
                continue

            long_votes = sum(1 for v in votes if v[1] == "LONG")
            short_votes = sum(1 for v in votes if v[1] == "SHORT")
            if max(long_votes, short_votes) < cfg.min_alpha_agreement:
                continue
            if long_votes > short_votes:
                engine_direction = "LONG"
            elif short_votes > long_votes:
                engine_direction = "SHORT"
            else:
                continue

            agreeing_votes = [v for v in votes if v[1] == engine_direction]
            agreeing_votes.sort(key=lambda v: v[2], reverse=True)
            if not agreeing_votes:
                continue
            chosen_meta = agreeing_votes[0][3]
            chosen_confidence = agreeing_votes[0][2]
            stats["alpha_pass"] += 1

            is_pred, p_val, dir_edge, long_wr, short_wr = pattern_stats(
                chosen_meta, cfg.chi2_p_threshold, cfg.min_node_count
            )
            if not is_pred:
                stats["skip_filter"] += 1
                continue
            if dir_edge < cfg.min_dir_edge:
                stats["skip_max_edge"] += 1
                continue

            max_wr = max(long_wr, short_wr)
            if max_wr < cfg.moderate_wr_threshold:
                stats["skip_filter"] += 1
                continue

            avg_move_raw = chosen_meta.long_stats.avg_move_pct
            effective_move = abs(avg_move_raw)
            if effective_move < cfg.hard_move_floor:
                stats["skip_filter"] += 1
                continue

            stats["stat_pass"] += 1

            # Direction (KEY: REVERSE)
            if cfg.reverse_direction:
                trade_direction = "SHORT" if engine_direction == "LONG" else "LONG"
            else:
                trade_direction = engine_direction

            net_favorable = effective_move - spread_pct
            if net_favorable <= 0:
                continue

            # SL (catastrophic only)
            sl_dist_pct = current_atr_pct * cfg.sl_atr_mult if cfg.use_catastrophic_sl else 100.0
            sl_dist_pct = max(cfg.sl_floor_pct, min(cfg.sl_cap_pct, sl_dist_pct))
            tp_dist_pct = current_atr_pct * cfg.tp_atr_mult if cfg.use_tp else 100.0
            tp_dist_pct = max(cfg.tp_floor_pct, min(cfg.tp_cap_pct, tp_dist_pct))

            sl_dist = sl_dist_pct / 100.0
            tp_dist = tp_dist_pct / 100.0
            entry_price = current_price
            if trade_direction == "LONG":
                sl_price = entry_price * (1 - sl_dist)
                tp_price = entry_price * (1 + tp_dist)
            else:
                sl_price = entry_price * (1 + sl_dist)
                tp_price = entry_price * (1 - tp_dist)

            # Position sizing: use full capital per trade (since SL is wide,
            # risk_pct/sl_dist gives reasonable notional)
            position_notional = cfg.initial_capital * cfg.risk_pct / max(sl_dist, 0.005)
            position_notional = min(position_notional, cfg.initial_capital)

            position = Trade(
                symbol=symbol, direction=trade_direction,
                entry_idx=i, exit_idx=-1,
                entry_price=entry_price, exit_price=0.0,
                sl_price=sl_price, tp_price=tp_price,
                pnl_pct=0.0, exit_reason="",
                confidence=chosen_confidence,
                weighted_confidence=chosen_confidence,
            )
            position._notional = position_notional
            position._sl_dist = sl_dist
            n_entries += 1
            stats["entries"] = n_entries

            if oos_idx % (progress_every * 2) == 0 and oos_idx > 0:
                pct = oos_idx / oos_size * 100
                print(f"    [{pct:5.1f}%] entries={n_entries} trades={len(trades)}",
                      flush=True)

    except Exception as ex:
        import traceback
        print(f"    ERROR at i={i}: {ex}", flush=True)
        traceback.print_exc()

    print(f"    rebuilds={n_rebuilds} evaluated={stats['evaluated']} "
          f"alpha_pass={stats['alpha_pass']} stat_pass={stats['stat_pass']} "
          f"skip_filter={stats['skip_filter']} skip_edge={stats['skip_max_edge']} "
          f"entries={n_entries} trades={len(trades)}", flush=True)
    return trades, stats


def main():
    t0 = time.time()
    cfg = ConfigV25()
    print(f"=== PPMT v2.5 — Hold-48 + REVERSE (Mean Reversion Capture) ===\n", flush=True)
    print(f"Config: IS={cfg.is_days}d (rolling), OOS={cfg.oos_days}d, "
          f"rebuild every {cfg.rebuild_every_days}d", flush=True)
    print(f"Hold: per-token (BTC/BNB/XRP/AVAX/DOGE=48, ETH/ADA/LINK=72, SOL=96)", flush=True)
    print(f"Direction: ALWAYS REVERSE (mean-reversion)", flush=True)
    print(f"SL: catastrophic only ({cfg.sl_atr_mult}×ATR, cap {cfg.sl_cap_pct}%)", flush=True)
    print(f"TP: OFF (exit at time only)", flush=True)
    print(f"Alphas: {cfg.alphas}, min agreement={cfg.min_alpha_agreement}", flush=True)
    print(f"Filters: chi2 p<{cfg.chi2_p_threshold}, min_count={cfg.min_node_count}, "
          f"min_edge={cfg.min_dir_edge}, min_wr={cfg.moderate_wr_threshold:.0%}\n", flush=True)
    print(f"Tokens: {len(TOKENS)} ({[t[0] for t in TOKENS]})\n", flush=True)

    all_trades: List[Trade] = []
    per_token: Dict[str, Dict] = {}
    per_token_stats: Dict[str, Dict] = {}

    for sym, ac in TOKENS:
        t_sym = time.time()
        try:
            trades, st = walk_forward_backtest_v25(sym, ac, cfg)
        except Exception as ex:
            print(f"  FAIL {sym}: {ex}", flush=True)
            import traceback; traceback.print_exc()
            continue
        m = compute_metrics(trades, cfg.initial_capital)
        per_token[sym] = m
        per_token_stats[sym] = st
        all_trades.extend(trades)
        print(f"  → {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% "
              f"dd={m.get('max_dd', 0):4.1f}% "
              f"({time.time()-t_sym:.0f}s)\n", flush=True)

    # Aggregate
    total_n = sum(m["n_trades"] for m in per_token.values())
    total_wins = sum(int(m["wr"]/100 * m["n_trades"]) for m in per_token.values())
    total_pnl = sum(m["pnl_pct"] for m in per_token.values())
    total_shorts = sum(int(m["shorts_pct"]/100 * m["n_trades"]) for m in per_token.values())
    pf_values = [m["pf"] for m in per_token.values() if m["pf"] > 0]
    pf_avg = float(np.mean(pf_values)) if pf_values else 0.0
    agg = {
        "n_trades": total_n,
        "wr": total_wins/total_n*100 if total_n else 0,
        "pnl_pct": total_pnl,
        "pf": pf_avg,
        "shorts_pct": total_shorts/total_n*100 if total_n else 0,
    }

    print(f"=== AGGREGATE (v2.5) ===", flush=True)
    print(f"  n_trades: {agg['n_trades']}", flush=True)
    print(f"  WR:       {agg['wr']:.1f}%", flush=True)
    print(f"  PnL:      {agg['pnl_pct']:+.1f}%", flush=True)
    print(f"  PF:       {agg['pf']:.2f}", flush=True)
    print(f"  shorts:   {agg['shorts_pct']:.1f}%\n", flush=True)

    # Goal check
    wr_pass = sum(1 for m in per_token.values() if m["wr"] >= 55)
    pf_pass = sum(1 for m in per_token.values() if m["pf"] >= 1.5)
    pnl_pass = sum(1 for m in per_token.values() if m["pnl_pct"] > 0)
    n_tok = len(per_token)
    print(f"=== GOAL CHECK ===", flush=True)
    print(f"  WR≥55%: {wr_pass}/{n_tok} (target 4/5)", flush=True)
    print(f"  PF≥1.5: {pf_pass}/{n_tok} (target 4/5)", flush=True)
    print(f"  PnL>0:  {pnl_pass}/{n_tok} (target all)", flush=True)
    print(f"  ≥20 trades: {sum(1 for m in per_token.values() if m['n_trades'] >= 20)}/{n_tok}", flush=True)
    print(f"  ≥15% shorts: {sum(1 for m in per_token.values() if m['shorts_pct'] >= 15)}/{n_tok}\n", flush=True)

    # Monte Carlo
    mc = {}
    if all_trades:
        mc = monte_carlo(all_trades, n_sims=3000,
                         initial_capital=cfg.initial_capital,
                         risk_pct=cfg.risk_pct)
        print(f"=== MONTE CARLO (3000 sims) ===", flush=True)
        print(f"  prob_profit: {mc['mc_prob_profit']:.1f}%", flush=True)
        print(f"  risk_ruin:   {mc['mc_risk_ruin']:.2f}%", flush=True)
        print(f"  p95_dd:      {mc['mc_p95_dd']:.1f}%", flush=True)
        print(f"  median_pnl:  ${mc['mc_median_pnl']:.0f}", flush=True)
        print(f"  p05_pnl:     ${mc['mc_p05_pnl']:.0f}\n", flush=True)

    # Per-token detail
    print(f"=== PER TOKEN ===", flush=True)
    for sym, m in per_token.items():
        st = per_token_stats.get(sym, {})
        print(f"  {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% "
              f"dd={m.get('max_dd', 0):4.1f}% "
              f"rebuilds={st.get('rebuilds', 0)}", flush=True)

    # Save
    Path("/home/z/my-project/download").mkdir(parents=True, exist_ok=True)
    save_data = {
        "config": asdict(cfg),
        "per_token": per_token,
        "per_token_stats": per_token_stats,
        "agg": agg,
        "mc": mc,
        "_trades_summary": [
            {
                "symbol": t.symbol, "direction": t.direction,
                "entry_idx": t.entry_idx, "exit_idx": t.exit_idx,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "sl_price": t.sl_price, "tp_price": t.tp_price,
                "pnl_pct": t.pnl_pct, "exit_reason": t.exit_reason,
                "confidence": t.confidence,
            } for t in all_trades
        ],
    }
    with open(OUT_PATH, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\nResults saved to {OUT_PATH}", flush=True)
    print(f"Total time: {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
