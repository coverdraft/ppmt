"""PPMT v2.2.1 — ATR-based SL/TP + Directional Edge Filter

After v2.2 grid showed 0/88 configs profitable (all PnL -120% to -250%),
we diagnose the root cause: SL/TP based on metadata.max_drawdown_pct
(which is the WORST observed drawdown in the pattern window) is structurally
too wide. Most trades exit by max_hold without hitting either SL or TP,
accumulating fee drag.

This script changes the approach:
  1. SL/TP based on ATR(14) of recent candles — adapts to real volatility
  2. Directional edge filter: only trade if |long_edge - short_edge| > threshold
  3. Quality gate: only trade if matched node has historical_count >= 5
  4. Tighter caps: SL max 1.5%, TP max 3%, max_hold 24 bars (2h)
  5. Mini-grid of 12 promising configs
"""
import sys, os, json, time, copy, sqlite3, gc
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.engine.signal import Signal, SignalType
from ppmt.core.sax import (
    LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES,
)
from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt_grid_search import (
    load_ohlcv, build_engine, compute_metrics, monte_carlo, Trade,
    TOKENS, TF, IS_DAYS, OOS_DAYS, DB_PATH,
)

OUT_PATH = "/home/z/my-project/download/ppmt_v221_results.json"

# ────────────────────────────────────────────────────────────────────
# ATR Calculation
# ────────────────────────────────────────────────────────────────────
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Standard ATR (Wilder's smoothing)."""
    high = df["high"]; low = df["low"]; close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()

# ────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────
@dataclass
class Config221:
    name: str
    weights: Tuple[float, float, float, float]
    ev_threshold: float = 0.15
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    min_dir_edge: float = 0.10   # |long_edge - short_edge| gate
    min_node_count: int = 5      # require 5+ observations
    hard_move_floor: float = 0.10
    min_confidence: float = 0.10
    risk_pct: float = 0.02
    initial_capital: float = 1000.0
    fee_pct: float = 0.04
    max_hold_bars: int = 24      # 2h on 5m

# ────────────────────────────────────────────────────────────────────
# Backtest v2.2.1
# ────────────────────────────────────────────────────────────────────
def backtest_v221(engine: PPMT, oos_df: pd.DataFrame, cfg: Config221,
                  symbol: str) -> List[Trade]:
    trades: List[Trade] = []
    position: Optional[Trade] = None
    buf_size = max(
        engine.sax_n1.window_size * engine.pl_n1,
        engine.sax_n2.window_size * engine.pl_n2,
        engine.sax_n3.window_size * engine.pl_n3,
        engine.sax_n4.window_size * engine.pl_n4,
    ) + 20

    # Configure engine weights
    engine.weights.n1_universal = cfg.weights[0]
    engine.weights.n2_asset_class = cfg.weights[1]
    engine.weights.n3_per_asset = cfg.weights[2]
    engine.weights.n4_per_asset_regime = cfg.weights[3]
    engine.weights.n5_btc_context = 0.0

    # Pre-compute ATR series on OOS
    atr_series = atr(oos_df, period=14)

    # Asset class for spread
    spread_map = {"blue_chip":0.010,"large_cap":0.015,"mid_cap":0.020,
                  "meme":0.050,"defi":0.025,"new_launch":0.080,"default":0.020}
    asset_class = next((ac for s, ac in TOKENS if s == symbol), "default")
    spread_pct = spread_map.get(asset_class, 0.020)

    n = len(oos_df)
    for i in range(buf_size, n):
        window = oos_df.iloc[i-buf_size:i]

        # Position exit check first
        if position is not None:
            candle = oos_df.iloc[i]
            exit_reason = None
            exit_price = None
            if position.direction == "LONG":
                if candle["low"] <= position.sl_price:
                    exit_price = position.sl_price; exit_reason = "stop_loss"
                elif candle["high"] >= position.tp_price:
                    exit_price = position.tp_price; exit_reason = "take_profit"
            else:
                if candle["high"] >= position.sl_price:
                    exit_price = position.sl_price; exit_reason = "stop_loss"
                elif candle["low"] <= position.tp_price:
                    exit_price = position.tp_price; exit_reason = "take_profit"
            if exit_reason is None and i - position.entry_idx >= cfg.max_hold_bars:
                exit_price = candle["close"]; exit_reason = "max_hold"
            if exit_reason is None and i == n - 1:
                exit_price = candle["close"]; exit_reason = "end_of_data"
            if exit_reason is not None:
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
            continue

        # Try entry
        try:
            result = engine.match_raw(
                current_symbols=[],
                current_price=oos_df.iloc[i]["close"],
                recent_candles=window,
            )
        except Exception:
            continue

        if result.direction == "FLAT":
            continue
        if result.weighted_confidence < cfg.min_confidence:
            continue

        # Find best matching node
        best_match = None; best_conf = 0.0; best_w = 0.0
        for lvl_match, lvl_w in [
            (result.n1_match, cfg.weights[0]),
            (result.n2_match, cfg.weights[1]),
            (result.n3_match, cfg.weights[2]),
            (result.n4_match, cfg.weights[3]),
        ]:
            if lvl_match and lvl_match.node and lvl_w > 0:
                c = lvl_match.node.metadata.confidence
                if c > best_conf:
                    best_conf = c; best_match = lvl_match; best_w = lvl_w
        if best_match is None or best_match.node is None:
            continue
        meta = best_match.node.metadata

        # ─── NEW: Quality gate ───
        if meta.historical_count < cfg.min_node_count:
            continue

        # ─── NEW: Directional edge filter ───
        # best_direction_p7 already chose direction; here we additionally
        # require that the directional edge be strong enough.
        long_e = meta.long_edge()
        short_e = meta.short_edge()
        dir_edge = abs(long_e - short_e)
        if dir_edge < cfg.min_dir_edge:
            continue

        # ─── NEW: Net EV Gate using ATR-based RR ───
        current_atr_pct = atr_series.iloc[i] / oos_df.iloc[i]["close"] * 100
        if not np.isfinite(current_atr_pct) or current_atr_pct <= 0:
            continue
        sl_dist_pct = current_atr_pct * cfg.sl_atr_mult
        tp_dist_pct = current_atr_pct * cfg.tp_atr_mult
        # Cap to reasonable 5m ranges
        sl_dist_pct = min(sl_dist_pct, 1.5)
        sl_dist_pct = max(sl_dist_pct, 0.15)
        tp_dist_pct = min(tp_dist_pct, 3.0)
        tp_dist_pct = max(tp_dist_pct, 0.3)
        # Hard move floor
        effective_move = meta.avg_move_long if result.direction == "LONG" else abs(meta.avg_move_short)
        if effective_move < cfg.hard_move_floor:
            continue
        # Net favorable check (use avg move, not max favorable)
        net_favorable = effective_move - spread_pct
        if net_favorable <= 0:
            continue
        net_rr = min(net_favorable / max(sl_dist_pct, 0.01), 3.0)
        net_ev = result.weighted_confidence * net_rr
        if net_ev < cfg.ev_threshold:
            continue

        # ─── Execute entry with ATR-based SL/TP ───
        entry_price = oos_df.iloc[i]["close"]
        sl_dist = sl_dist_pct / 100.0
        tp_dist = tp_dist_pct / 100.0
        if result.direction == "LONG":
            sl_price = entry_price * (1 - sl_dist)
            tp_price = entry_price * (1 + tp_dist)
        else:
            sl_price = entry_price * (1 + sl_dist)
            tp_price = entry_price * (1 - tp_dist)

        position_notional = cfg.initial_capital * cfg.risk_pct / sl_dist
        position_notional = min(position_notional, cfg.initial_capital)

        position = Trade(
            symbol=symbol, direction=result.direction,
            entry_idx=i, exit_idx=-1,
            entry_price=entry_price, exit_price=0.0,
            sl_price=sl_price, tp_price=tp_price,
            pnl_pct=0.0, exit_reason="",
            confidence=best_conf,
            weighted_confidence=result.weighted_confidence,
        )
        position._notional = position_notional
        position._sl_dist = sl_dist

    return trades

# ────────────────────────────────────────────────────────────────────
# Mini-grid
# ────────────────────────────────────────────────────────────────────
def build_grid_v221() -> List[Config221]:
    configs = []
    # 4 weight profiles that performed best in v2.2 + 2 new ones
    weight_profiles = [
        ("F_base",          (0.10, 0.00, 0.90, 0.00)),  # baseline
        ("univ_40_20_20_20",(0.40, 0.20, 0.20, 0.20)),  # best of v2.2
        ("n1_dom_60_0_20_20",(0.60, 0.00, 0.20, 0.20)), # N1 dominant
        ("class_30_30_20_20",(0.30, 0.30, 0.20, 0.20)), # class-heavy
    ]
    # 3 parameter combos
    param_combos = [
        # (ev, sl_atr, tp_atr, min_dir_edge, min_count, floor)
        (0.10, 1.5, 3.0, 0.05, 3, 0.05),  # lenient
        (0.15, 1.5, 3.0, 0.10, 5, 0.10),  # balanced
        (0.20, 2.0, 4.0, 0.15, 8, 0.15),  # strict
    ]
    for wname, w in weight_profiles:
        for pi, (ev, sl, tp, edge, cnt, floor) in enumerate(param_combos):
            configs.append(Config221(
                name=f"{wname}_p{pi+1}",
                weights=w,
                ev_threshold=ev,
                sl_atr_mult=sl, tp_atr_mult=tp,
                min_dir_edge=edge, min_node_count=cnt,
                hard_move_floor=floor,
            ))
    return configs

# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print(f"=== PPMT v2.2.1 — ATR-based + Edge Filter — {TF} ===\n", flush=True)

    # Load OHLCV
    data = {}
    for sym, ac in TOKENS:
        df = load_ohlcv(sym, TF)
        is_df = df.iloc[:-OOS_DAYS*288]
        oos_df = df.iloc[-OOS_DAYS*288:].reset_index(drop=True)
        data[sym] = (ac, is_df, oos_df)
        print(f"  {sym:10s}: IS={len(is_df)} OOS={len(oos_df)}", flush=True)

    # Build engines once
    print("\nBuilding tries (α=5)...", flush=True)
    engines: Dict[str, PPMT] = {}
    for sym, (ac, is_df, _) in data.items():
        e = build_engine(sym, ac, is_df, alpha_n3n4=5)
        engines[sym] = e
        print(f"  {sym:10s}: N1={e.trie_n1.pattern_count} N3={e.trie_n3.pattern_count}", flush=True)
    print(f"Build done in {time.time()-t0:.1f}s\n", flush=True)

    # Mini-grid
    grid = build_grid_v221()
    print(f"Grid: {len(grid)} configs × {len(engines)} tokens\n", flush=True)

    all_results = []
    t1 = time.time()
    for ci, cfg in enumerate(grid):
        per_token = {}
        all_trades_cfg = []
        for sym, engine in engines.items():
            ac, is_df, oos_df = data[sym]
            # Reset weights before each cfg
            engine.weights.n1_universal = 0.10
            engine.weights.n2_asset_class = 0.00
            engine.weights.n3_per_asset = 0.90
            engine.weights.n4_per_asset_regime = 0.00
            trades = backtest_v221(engine, oos_df, cfg, sym)
            per_token[sym] = compute_metrics(trades, cfg.initial_capital)
            all_trades_cfg.extend(trades)

        # Aggregate
        total_trades = sum(m["n_trades"] for m in per_token.values())
        total_wins = sum(int(m["wr"]/100 * m["n_trades"]) for m in per_token.values())
        total_pnl = sum(m["pnl_pct"] for m in per_token.values())
        total_shorts = sum(int(m["shorts_pct"]/100 * m["n_trades"]) for m in per_token.values())
        pf_avg = np.mean([m["pf"] for m in per_token.values() if m["pf"] > 0]) if any(m["pf"] > 0 for m in per_token.values()) else 0
        agg = {
            "n_trades": total_trades,
            "wr": total_wins/total_trades*100 if total_trades else 0,
            "pnl_pct": total_pnl,
            "pf": float(pf_avg),
            "shorts_pct": total_shorts/total_trades*100 if total_trades else 0,
        }

        # MC on this config
        mc = monte_carlo(all_trades_cfg, n_sims=2000,
                         initial_capital=cfg.initial_capital,
                         risk_pct=cfg.risk_pct)

        all_results.append({
            "cfg": asdict(cfg),
            "per_token": per_token,
            "agg": agg,
            "mc": mc,
            "_trades": all_trades_cfg,
        })
        print(f"  [{ci+1:2d}/{len(grid)}] {cfg.name:30s} PnL={agg['pnl_pct']:+7.1f}% "
              f"WR={agg['wr']:5.1f}% n={agg['n_trades']:4d} PF={agg['pf']:.2f} "
              f"shorts={agg['shorts_pct']:4.1f}% "
              f"MC:profit={mc['mc_prob_profit']:5.1f}% ruin={mc['mc_risk_ruin']:.2f}% "
              f"({time.time()-t1:.0f}s)", flush=True)

    # Rank by PnL
    all_results.sort(key=lambda r: r["agg"]["pnl_pct"], reverse=True)
    print(f"\n=== TOP 5 ===", flush=True)
    for r in all_results[:5]:
        a = r["agg"]; mc = r["mc"]
        print(f"\n  {r['cfg']['name']}  PnL={a['pnl_pct']:+.1f}% WR={a['wr']:.1f}% "
              f"PF={a['pf']:.2f} n={a['n_trades']} shorts={a['shorts_pct']:.1f}%", flush=True)
        print(f"  MC: profit_prob={mc['mc_prob_profit']:.1f}% ruin={mc['mc_risk_ruin']:.2f}% "
              f"p95_dd={mc['mc_p95_dd']:.1f}% median_pnl=${mc['mc_median_pnl']:.0f}", flush=True)
        for sym, m in r["per_token"].items():
            print(f"    {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
                  f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} shorts={m['shorts_pct']:4.1f}%", flush=True)

    # Save top 5
    Path("/home/z/my-project/download").mkdir(parents=True, exist_ok=True)
    save_data = []
    for r in all_results[:5]:
        rr = {k: v for k, v in r.items() if k != "_trades"}
        save_data.append(rr)
    with open(OUT_PATH, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\nSaved to {OUT_PATH}", flush=True)
    print(f"Total: {time.time()-t0:.1f}s", flush=True)

if __name__ == "__main__":
    main()
