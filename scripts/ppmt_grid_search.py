"""PPMT v2.2 — Universal Optimization Pipeline

End-to-end pipeline:
  1. Load 90d OHLCV from DB for 5 tokens (BTC, ETH, SOL, DOGE, LINK) @ 5m
  2. Build tries with α=5 (goldilocks: 125 N3 patterns) per token, IS = first 60d
  3. Backtest OOS = last 30d with parameterized config
  4. Grid search over weights × EV × SL × floor × min_conf
  5. Top 3 configs → Monte Carlo 5k sims
  6. Print summary + write JSON to /home/z/my-project/download/

Run:
  python3 /home/z/my-project/scripts/ppmt_grid_search.py
"""
import sys, os, json, time, copy, sqlite3, random, logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

# Silence noisy loggers
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights, WEIGHT_PROFILES, TIMEFRAME_WEIGHT_OVERRIDES
from ppmt.engine.signal import SignalGenerator, Signal, SignalType
from ppmt.core.sax import (
    SAXEncoder, SAXDualEncoder, LEVEL_DUAL_ALPHA_CONFIG,
    LEVEL_DUAL_ALPHA_TF_OVERRIDES, LEVEL_WINDOW_CONFIG, LEVEL_PATTERN_CONFIG,
)
from ppmt.core.thresholds import SignalThresholds, TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.metadata import BlockLifecycleMetadata, compute_outcome_directional
from ppmt.core.regime import RegimeDetector
from ppmt.core.matcher import FuzzyMatcher
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier

DB_PATH = "/home/z/.ppmt/ppmt.db"
OUT_PATH = "/home/z/my-project/download/ppmt_grid_results.json"
TF = "5m"
TOKENS = [
    ("BTC/USDT",  "blue_chip"),
    ("ETH/USDT",  "blue_chip"),
    ("SOL/USDT",  "large_cap"),
    ("DOGE/USDT", "meme"),
    ("LINK/USDT", "mid_cap"),
]
IS_DAYS = 60   # first 60d for in-sample
OOS_DAYS = 30  # last 30d for OOS

# ────────────────────────────────────────────────────────────────────
# 1. LOAD OHLCV
# ────────────────────────────────────────────────────────────────────
def load_ohlcv(symbol: str, tf: str = TF) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv "
        "WHERE symbol=? AND timeframe=? ORDER BY timestamp",
        conn, params=(symbol, tf)
    )
    conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    return df.reset_index(drop=True)

# ────────────────────────────────────────────────────────────────────
# 2. BUILD TRIE (per-token, fresh, in-memory — no DB noise)
# ────────────────────────────────────────────────────────────────────
def build_engine(symbol: str, asset_class: str, is_df: pd.DataFrame,
                 alpha_n3n4: int = 5) -> PPMT:
    """Build a PPMT engine with custom α for N3/N4."""
    # Patch global configs BEFORE creating the engine
    saved_dual = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy(), LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": alpha_n3n4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": alpha_n3n4, "volume": 0}
    # Remove TF overrides for n3/n4 so our config takes precedence
    for tf_k in list(LEVEL_DUAL_ALPHA_TF_OVERRIDES.keys()):
        for lvl in ("n3", "n4"):
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
    try:
        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class,
            weight_profile="default",
            dual_sax=True,
            min_confidence=0.08,
            timeframe=TF,
        )
        # Use a fresh empty storage to avoid cross-contamination between builds
        # (we want per-token tries, not shared universal pools, for grid search)
        engine._storage = None  # bypass shared-pool flush
        engine._n1_buffer = None  # disable buffer flush path
        engine._n2_buffer = None
        # Re-init per-token tries (they're already fresh in __init__, but be safe)
        n_before_n3 = engine.trie_n3.pattern_count if engine.trie_n3 else 0
        n = engine.build(is_df)
        return engine
    finally:
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_dual[0]
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_dual[1]
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf)

# ────────────────────────────────────────────────────────────────────
# 3. BACKTEST (parameterized)
# ────────────────────────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    name: str = "cfg"
    weights: Tuple[float, float, float, float] = (0.10, 0.00, 0.90, 0.00)  # N1,N2,N3,N4
    ev_threshold: float = 0.20
    sl_multiplier: float = 2.0
    hard_move_floor: float = 0.10  # %
    min_confidence: float = 0.10
    risk_pct: float = 0.02  # 2% capital per trade
    initial_capital: float = 1000.0
    fee_pct: float = 0.04  # taker fee binance futures
    max_hold_bars: int = 36  # 3h max on 5m

@dataclass
class Trade:
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    pnl_pct: float  # net of fees, in % of capital
    exit_reason: str
    confidence: float
    weighted_confidence: float

def backtest(engine: PPMT, oos_df: pd.DataFrame, cfg: BacktestConfig,
             symbol: str) -> List[Trade]:
    """Walk-forward backtest on OOS data."""
    trades: List[Trade] = []
    position: Optional[Trade] = None
    capital = cfg.initial_capital
    w = engine.sax_n3.window_size  # use N3's window for buffer sizing
    pl = engine.pl_n3
    # Buffer needs to be large enough to encode all levels
    buf_size = max(
        engine.sax_n1.window_size * engine.pl_n1,
        engine.sax_n2.window_size * engine.pl_n2,
        engine.sax_n3.window_size * engine.pl_n3,
        engine.sax_n4.window_size * engine.pl_n4,
    ) + 20

    # Configure engine weights from cfg
    engine.weights.n1_universal = cfg.weights[0]
    engine.weights.n2_asset_class = cfg.weights[1]
    engine.weights.n3_per_asset = cfg.weights[2]
    engine.weights.n4_per_asset_regime = cfg.weights[3]
    engine.weights.n5_btc_context = 0.0

    # Note: SignalThresholds is frozen, but we do our own gating in this
    # backtest (EV gate, hard_move_floor, min_confidence) so we don't need
    # to mutate engine.signal_generator.thresholds.
    # Patch the global TIMEFRAME_HARD_MOVE_FLOOR for 5m so any internal
    # reference sees our value.
    from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
    TIMEFRAME_HARD_MOVE_FLOOR[TF] = cfg.hard_move_floor

    n = len(oos_df)
    for i in range(buf_size, n):
        window = oos_df.iloc[i-buf_size:i]

        # If in position, check exit conditions FIRST
        if position is not None:
            candle = oos_df.iloc[i]
            exit_reason = None
            exit_price = None
            # SL/TP check
            if position.direction == "LONG":
                if candle["low"] <= position.sl_price:
                    exit_price = position.sl_price
                    exit_reason = "stop_loss"
                elif candle["high"] >= position.tp_price:
                    exit_price = position.tp_price
                    exit_reason = "take_profit"
            else:  # SHORT
                if candle["high"] >= position.sl_price:
                    exit_price = position.sl_price
                    exit_reason = "stop_loss"
                elif candle["low"] <= position.tp_price:
                    exit_price = position.tp_price
                    exit_reason = "take_profit"
            # Max hold
            if exit_reason is None and i - position.entry_idx >= cfg.max_hold_bars:
                exit_price = candle["close"]
                exit_reason = "max_hold"
            # Force exit at last bar
            if exit_reason is None and i == n - 1:
                exit_price = candle["close"]
                exit_reason = "end_of_data"
            if exit_reason is not None:
                # Compute PnL net of fees, in % of capital
                if position.direction == "LONG":
                    gross_pct = (exit_price - position.entry_price) / position.entry_price * 100
                else:
                    gross_pct = (position.entry_price - exit_price) / position.entry_price * 100
                # Fees: entry + exit = 2 × fee_pct (on notional)
                notional = getattr(position, "_notional", cfg.initial_capital * cfg.risk_pct / 0.01)
                fee_dollars = notional * 2 * cfg.fee_pct / 100
                gross_dollars = notional * gross_pct / 100
                net_dollars = gross_dollars - fee_dollars
                # As % of initial capital
                net_pct_capital = net_dollars / cfg.initial_capital * 100
                position.pnl_pct = net_pct_capital
                position.exit_idx = i
                position.exit_price = exit_price
                position.exit_reason = exit_reason
                trades.append(position)
                position = None
            continue  # skip new entry on same bar

        # No position → try to enter
        try:
            result = engine.match_raw(
                current_symbols=[],  # ignored when recent_candles provided
                current_price=oos_df.iloc[i]["close"],
                recent_candles=window,
            )
        except Exception:
            continue

        if result.direction == "FLAT":
            continue
        if result.weighted_confidence < cfg.min_confidence:
            continue

        # Find best matching node to get metadata for SL/TP
        best_match = None
        best_conf = 0.0
        for lvl_match, lvl_w in [
            (result.n1_match, cfg.weights[0]),
            (result.n2_match, cfg.weights[1]),
            (result.n3_match, cfg.weights[2]),
            (result.n4_match, cfg.weights[3]),
        ]:
            if lvl_match and lvl_match.node and lvl_w > 0:
                c = lvl_match.node.metadata.confidence
                if c > best_conf:
                    best_conf = c
                    best_match = lvl_match
        if best_match is None or best_match.node is None:
            continue
        meta = best_match.node.metadata

        # Net EV Gate
        spread_pct_map = {
            "blue_chip": 0.010, "large_cap": 0.015, "mid_cap": 0.020,
            "meme": 0.050, "defi": 0.025, "new_launch": 0.080, "default": 0.020,
        }
        asset_class = next((ac for s, ac in TOKENS if s == symbol), "default")
        spread_pct = spread_pct_map.get(asset_class, 0.020)
        net_favorable = meta.max_favorable_pct - spread_pct
        if net_favorable <= 0:
            continue
        net_rr = min(net_favorable / max(abs(meta.max_drawdown_pct), 0.001), 3.0)
        net_ev = result.weighted_confidence * net_rr
        if net_ev < cfg.ev_threshold:
            continue

        # Direction
        direction = result.direction  # "LONG" or "SHORT"
        if direction == "FLAT":
            continue

        # SL/TP from metadata × sl_multiplier — with RR symmetry enforcement
        entry_price = oos_df.iloc[i]["close"]
        dd = abs(meta.max_drawdown_pct) / 100.0
        # TP based on avg directional move (not max_favorable which is extreme)
        if direction == "LONG":
            fav = max(meta.avg_move_long, meta.expected_move_pct, 0.0) / 100.0
        else:
            fav = max(abs(meta.avg_move_short), abs(meta.expected_move_pct), 0.0) / 100.0
        sl_dist = dd * cfg.sl_multiplier
        tp_dist = fav * 1.0
        # ─── RR SYMMETRY ENFORCEMENT (v2.2) ─────────────────────────
        # If max_drawdown > avg_move (which is almost always, since max > avg),
        # the SL is structurally wider than TP → guaranteed negative EV.
        # Fix: enforce minimum RR of 2.0 by widening TP if needed.
        min_rr = 2.0
        if tp_dist < sl_dist * min_rr:
            tp_dist = sl_dist * min_rr
        # Sanity caps — 5m typical SL 0.3-1.5%, TP 0.5-2.5%
        sl_dist = min(sl_dist, 0.025)  # max 2.5% SL
        sl_dist = max(sl_dist, 0.0015)  # min 0.15% SL (avoid noise-stop)
        tp_dist = min(tp_dist, 0.04)   # max 4% TP
        tp_dist = max(tp_dist, 0.003)  # min 0.3% TP
        # Re-enforce RR after caps
        if tp_dist < sl_dist * min_rr:
            tp_dist = sl_dist * min_rr
            tp_dist = min(tp_dist, 0.04)
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_dist)
            tp_price = entry_price * (1 + tp_dist)
        else:
            sl_price = entry_price * (1 + sl_dist)
            tp_price = entry_price * (1 - tp_dist)

        # Hard move floor
        effective_move = meta.avg_move_long if direction == "LONG" else abs(meta.avg_move_short)
        if effective_move < cfg.hard_move_floor:
            continue

        # Position sizing: fixed-fractional based on SL distance
        # risk_pct of capital = SL distance × position_size
        # → position_size = capital * risk_pct / sl_dist
        # PnL% of capital = net_move% / sl_dist% × risk_pct
        position_notional = cfg.initial_capital * cfg.risk_pct / sl_dist
        # Cap to 1x leverage (no borrowed money in paper)
        position_notional = min(position_notional, cfg.initial_capital)

        position = Trade(
            symbol=symbol, direction=direction,
            entry_idx=i, exit_idx=-1,
            entry_price=entry_price, exit_price=0.0,
            sl_price=sl_price, tp_price=tp_price,
            pnl_pct=0.0, exit_reason="",
            confidence=best_conf,
            weighted_confidence=result.weighted_confidence,
        )
        position._notional = position_notional  # stash for exit calc
        position._sl_dist = sl_dist

    return trades

# ────────────────────────────────────────────────────────────────────
# 4. METRICS
# ────────────────────────────────────────────────────────────────────
def compute_metrics(trades: List[Trade], initial_capital: float = 1000.0) -> Dict:
    if not trades:
        return {"n_trades": 0, "wr": 0, "pnl_pct": 0, "pf": 0,
                "shorts_pct": 0, "avg_pnl": 0, "max_dd": 0}
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    gross_profit = sum(t.pnl_pct for t in wins)
    gross_loss = abs(sum(t.pnl_pct for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else 0
    total_pnl = sum(t.pnl_pct for t in trades)
    # max drawdown on equity curve
    equity = initial_capital
    peak = equity
    max_dd = 0
    for t in trades:
        equity += t.pnl_pct / 100 * initial_capital  # pnl_pct is already % of capital
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    shorts = sum(1 for t in trades if t.direction == "SHORT")
    return {
        "n_trades": len(trades),
        "wr": len(wins) / len(trades) * 100,
        "pnl_pct": total_pnl,
        "pf": pf,
        "shorts_pct": shorts / len(trades) * 100,
        "avg_pnl": total_pnl / len(trades),
        "max_dd": max_dd,
    }

# ────────────────────────────────────────────────────────────────────
# 5. MONTE CARLO
# ────────────────────────────────────────────────────────────────────
def monte_carlo(trades: List[Trade], n_sims: int = 5000,
                initial_capital: float = 1000.0,
                risk_pct: float = 0.02) -> Dict:
    if not trades:
        return {"mc_prob_profit": 0, "mc_risk_ruin": 0, "mc_p95_dd": 0,
                "mc_median_pnl": 0, "mc_p05_pnl": 0}
    pnl_pct_arr = np.array([t.pnl_pct for t in trades])
    rng = np.random.default_rng(seed=42)
    final_pnls = np.zeros(n_sims)
    max_dds = np.zeros(n_sims)
    for i in range(n_sims):
        # Shuffle trade order
        seq = rng.permutation(pnl_pct_arr)
        equity = initial_capital
        peak = equity
        max_dd = 0
        for p in seq:
            equity += p / 100 * initial_capital  # pnl_pct is % of capital
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 100
            max_dd = max(max_dd, dd)
            if equity <= 0:
                equity = 0
                break
        final_pnls[i] = equity - initial_capital
        max_dds[i] = max_dd
    prob_profit = np.mean(final_pnls > 0) * 100
    risk_ruin = np.mean(final_pnls < -0.5 * initial_capital) * 100  # lose 50%+
    return {
        "mc_prob_profit": float(prob_profit),
        "mc_risk_ruin": float(risk_ruin),
        "mc_p95_dd": float(np.percentile(max_dds, 95)),
        "mc_median_pnl": float(np.median(final_pnls)),
        "mc_p05_pnl": float(np.percentile(final_pnls, 5)),
    }

# ────────────────────────────────────────────────────────────────────
# 6. GRID
# ────────────────────────────────────────────────────────────────────
def build_grid() -> List[BacktestConfig]:
    """A curated grid of ~60 configs focused on balance + universal applicability."""
    configs: List[BacktestConfig] = []

    # Weight profiles to test (N1, N2, N3, N4)
    weight_profiles = [
        # Config F baseline (current problematic)
        ("F_base",          (0.10, 0.00, 0.90, 0.00)),
        # Universal-friendly: N1 significant, N3 moderate, N4 small
        ("univ_20_0_60_20", (0.20, 0.00, 0.60, 0.20)),
        ("univ_30_0_50_20", (0.30, 0.00, 0.50, 0.20)),
        ("univ_30_20_30_20",(0.30, 0.20, 0.30, 0.20)),  # classic balanced
        ("univ_40_0_40_20", (0.40, 0.00, 0.40, 0.20)),
        ("univ_40_20_20_20",(0.40, 0.20, 0.20, 0.20)),
        # N3 dominant but with N4 safety
        ("n3_dom_15_0_70_15",(0.15, 0.00, 0.70, 0.15)),
        # N1 dominant (transfer learning friendly for new tokens)
        ("n1_dom_50_0_30_20",(0.50, 0.00, 0.30, 0.20)),
        ("n1_dom_60_0_20_20",(0.60, 0.00, 0.20, 0.20)),
        # N2 back in play (class-level transfer)
        ("class_30_30_20_20",(0.30, 0.30, 0.20, 0.20)),
        # Even split
        ("even_25_25_25_25",(0.25, 0.25, 0.25, 0.25)),
    ]

    ev_thresholds = [0.10, 0.20]
    sl_multipliers = [1.5, 2.0]
    move_floors = [0.05, 0.10]

    # Curated subset: 11 weights × 2 EV × 2 SL × 2 floor = 88 configs
    for wname, weights in weight_profiles:
        for ev in ev_thresholds:
            for sl in sl_multipliers:
                for floor in move_floors:
                    configs.append(BacktestConfig(
                        name=f"{wname}_ev{ev}_sl{sl}_f{floor}",
                        weights=weights,
                        ev_threshold=ev,
                        sl_multiplier=sl,
                        hard_move_floor=floor,
                        min_confidence=0.08,
                    ))
    return configs

# ────────────────────────────────────────────────────────────────────
# 7. MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print(f"=== PPMT v2.2 Universal Optimization — {TF} ===")
    print(f"Loading OHLCV for {len(TOKENS)} tokens...")

    # Load and split IS/OOS
    data = {}
    for sym, ac in TOKENS:
        df = load_ohlcv(sym, TF)
        if len(df) < 5000:
            print(f"  WARN {sym}: only {len(df)} rows, skipping")
            continue
        is_df = df.iloc[:-OOS_DAYS*288]  # 288 = 5m candles per day
        oos_df = df.iloc[-OOS_DAYS*288:].reset_index(drop=True)
        data[sym] = (ac, is_df, oos_df)
        print(f"  OK {sym:10s}: {len(df)} candles | IS={len(is_df)} OOS={len(oos_df)}")
    print()

    # Build engines (one per token, fresh tries)
    print("Building per-token tries (α=5 for N3/N4)...")
    engines: Dict[str, PPMT] = {}
    for sym, (ac, is_df, _) in data.items():
        e = build_engine(sym, ac, is_df, alpha_n3n4=5)
        engines[sym] = e
        n3 = e.trie_n3.pattern_count if e.trie_n3 else 0
        n4 = e.trie_n4.pattern_count if hasattr(e.trie_n4, 'pattern_count') else 0
        print(f"  {sym:10s}: N3={n3} patterns, N4={n4} patterns")
    print(f"Build done in {time.time()-t0:.1f}s\n")

    # Grid search
    grid = build_grid()
    print(f"Grid search: {len(grid)} configs × {len(engines)} tokens = "
          f"{len(grid)*len(engines)} backtests")
    all_results = []
    t1 = time.time()
    for ci, cfg in enumerate(grid):
        per_token = {}
        for sym, engine in engines.items():
            # Re-build engine state fresh (weights are mutated in backtest)
            ac, is_df, oos_df = data[sym]
            # Need to reset engine weights to baseline before each cfg
            engine.weights.n1_universal = 0.10
            engine.weights.n2_asset_class = 0.00
            engine.weights.n3_per_asset = 0.90
            engine.weights.n4_per_asset_regime = 0.00
            engine.weights.n5_btc_context = 0.0
            trades = backtest(engine, oos_df, cfg, sym)
            per_token[sym] = compute_metrics(trades, cfg.initial_capital)
            per_token[sym]["trades"] = trades  # keep for MC later

        # Aggregate
        agg = aggregate(per_token)
        all_results.append({
            "cfg": asdict(cfg),
            "per_token": {k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                          for k, v in per_token.items()},
            "agg": agg,
        })
        if (ci+1) % 5 == 0 or ci == 0:
            print(f"  [{ci+1:3d}/{len(grid)}] {cfg.name:35s} "
                  f"agg_pnl={agg['pnl_pct']:+7.1f}% wr={agg['wr']:5.1f}% "
                  f"n={agg['n_trades']:3d} pf={agg['pf']:.2f} "
                  f"shorts={agg['shorts_pct']:4.1f}%  "
                  f"({time.time()-t1:.0f}s)", flush=True)

    print(f"\nGrid done in {time.time()-t1:.1f}s")

    # Rank
    def score(r):
        a = r["agg"]
        # Composite: positive PnL required, then WR, then PF, then shorts%
        if a["n_trades"] < 30: return -999
        if a["pnl_pct"] <= 0: return -100 + a["pnl_pct"]
        # Reward balance across tokens (min token PnL)
        min_pnl = min(t["pnl_pct"] for t in r["per_token"].values())
        return a["pnl_pct"] + min_pnl * 0.5 + a["shorts_pct"] * 0.5

    all_results.sort(key=score, reverse=True)
    print("\n=== TOP 10 CONFIGS ===")
    for r in all_results[:10]:
        a = r["agg"]
        print(f"  {r['cfg']['name']:35s} PnL={a['pnl_pct']:+7.1f}% "
              f"WR={a['wr']:5.1f}% n={a['n_trades']:3d} "
              f"PF={a['pf']:.2f} shorts={a['shorts_pct']:4.1f}%")
        for sym, m in r["per_token"].items():
            print(f"     {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
                  f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
                  f"shorts={m['shorts_pct']:4.1f}%")

    # MC on top 3
    print("\n=== MONTE CARLO (top 3 configs, 5000 sims each) ===")
    for r in all_results[:3]:
        print(f"\n  {r['cfg']['name']}")
        all_trades = []
        for sym, m in r.get("_trades", {}).items() if "_trades" in r else {}:
            all_trades.extend(m)
        # Re-run backtest to get trades (we stripped them above for serialization)
        # Actually easier: re-run the top 3 configs
        cfg = BacktestConfig(**r["cfg"])
        all_trades = []
        for sym, engine in engines.items():
            engine.weights.n1_universal = 0.10
            engine.weights.n2_asset_class = 0.00
            engine.weights.n3_per_asset = 0.90
            engine.weights.n4_per_asset_regime = 0.00
            ac, is_df, oos_df = data[sym]
            trades = backtest(engine, oos_df, cfg, sym)
            all_trades.extend(trades)
        mc = monte_carlo(all_trades, n_sims=5000,
                         initial_capital=cfg.initial_capital,
                         risk_pct=cfg.risk_pct)
        r["mc"] = mc
        print(f"    trades={len(all_trades)} prob_profit={mc['mc_prob_profit']:.1f}% "
              f"risk_ruin={mc['mc_risk_ruin']:.2f}% p95_dd={mc['mc_p95_dd']:.1f}% "
              f"median_pnl=${mc['mc_median_pnl']:.0f}")

    # Save
    Path("/home/z/my-project/download").mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(all_results[:20], f, indent=2, default=str)
    print(f"\nResults saved to {OUT_PATH}")
    print(f"Total time: {time.time()-t0:.1f}s")

def aggregate(per_token: Dict) -> Dict:
    """Aggregate metrics across tokens."""
    all_trades_count = sum(m["n_trades"] for m in per_token.values())
    if all_trades_count == 0:
        return {"n_trades": 0, "wr": 0, "pnl_pct": 0, "pf": 0, "shorts_pct": 0}
    total_wins = sum(int(m["wr"]/100 * m["n_trades"]) for m in per_token.values())
    total_pnl = sum(m["pnl_pct"] for m in per_token.values())
    total_shorts = sum(int(m["shorts_pct"]/100 * m["n_trades"]) for m in per_token.values())
    # PF is approximate at aggregate level
    pf_avg = np.mean([m["pf"] for m in per_token.values() if m["pf"] > 0]) if any(m["pf"] > 0 for m in per_token.values()) else 0
    return {
        "n_trades": all_trades_count,
        "wr": total_wins / all_trades_count * 100 if all_trades_count else 0,
        "pnl_pct": total_pnl,
        "pf": float(pf_avg),
        "shorts_pct": total_shorts / all_trades_count * 100 if all_trades_count else 0,
    }

if __name__ == "__main__":
    main()
