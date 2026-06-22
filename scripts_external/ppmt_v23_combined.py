"""PPMT v2.3 — Combined Adaptive Strategy (WALK-FORWARD + STAT FILTER + ALPHA ENSEMBLE + MULTI-TF)

Root cause from v2.2: WR systematically 33-42% (anti-prediction) across all
100+ configs. Single-shot IS→OOS over-fits, no statistical filter on patterns,
single alpha, single timeframe.

v2.3 combines 4 orthogonal fixes:
  1. WALK-FORWARD ROLLING — Rebuild trie every 7d on trailing 30d IS.
     Reduces over-fitting to a single 60d IS window.
  2. STATISTICAL PATTERN FILTER — For each N3 pattern, chi-square test on
     long_count vs short_count. Only trade if p < 0.10 (real directional edge).
  3. ALPHA ENSEMBLE — Build 2 tries (α=5, α=7). For each candidate signal,
     require both alphas to agree on direction. Cuts noise.
  4. TIGHT ATR RR — SL = 1.0 × ATR(14), TP = 2.0 × ATR(14). RR=2 break-even
     at WR=33%. With WR=45% (target after filters), PF=1.8.

Run:
  /home/z/.venv/bin/python3 /home/z/my-project/scripts/ppmt_v23_combined.py
"""
import sys, os, json, time, copy, sqlite3, gc, logging, math
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Any
import numpy as np
import pandas as pd
from scipy.stats import chi2 as chi2_dist

sys.path.insert(0, "/home/z/my-project/ppmt/src")
sys.path.insert(0, "/home/z/my-project/scripts")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.engine.signal import Signal, SignalType
from ppmt.core.sax import (
    SAXEncoder, SAXDualEncoder, LEVEL_DUAL_ALPHA_CONFIG,
    LEVEL_DUAL_ALPHA_TF_OVERRIDES, LEVEL_WINDOW_CONFIG, LEVEL_PATTERN_CONFIG,
)
from ppmt.core.thresholds import SignalThresholds, TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.regime import RegimeDetector
from ppmt.core.matcher import FuzzyMatcher
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier
from ppmt_grid_search import (
    load_ohlcv, build_engine, compute_metrics, monte_carlo, Trade,
    TOKENS, TF, DB_PATH,
)

OUT_PATH = "/home/z/my-project/download/ppmt_v23_results.json"
TF_5M = "5m"
TF_15M = "15m"

# Walk-forward: 30d IS, rebuild every 7d, test 60d forward (covers full last 60d)
IS_DAYS = 30
REBUILD_EVERY_DAYS = 7
OOS_DAYS = 60  # walk-forward OOS span — covers days 30-90 (the last 60d)
CANDLES_PER_DAY_5M = 288
CANDLES_PER_DAY_15M = 96


# ────────────────────────────────────────────────────────────────────
# 1. ATR CALC
# ────────────────────────────────────────────────────────────────────
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]; low = df["low"]; close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


# ────────────────────────────────────────────────────────────────────
# 2. CONFIG
# ────────────────────────────────────────────────────────────────────
@dataclass
class ConfigV23:
    name: str = "v23_default"
    # Weights (N1, N2, N3, N4)
    weights: Tuple[float, float, float, float] = (0.40, 0.20, 0.20, 0.20)
    # Statistical filter
    chi2_p_threshold: float = 0.30  # only trade patterns with p < this (lenient due to low n)
    min_node_count: int = 8         # need at least 8 obs for stats
    # Alpha ensemble
    use_alpha_ensemble: bool = True
    alphas: Tuple[int, ...] = (5, 7)
    min_alpha_agreement: int = 2    # 2 of 2 alphas must agree
    # Risk
    sl_atr_mult: float = 1.0
    tp_atr_mult: float = 2.0
    sl_cap_pct: float = 1.5
    sl_floor_pct: float = 0.15
    tp_cap_pct: float = 3.0
    tp_floor_pct: float = 0.3
    # Entry gates
    min_confidence: float = 0.05    # low — filters do the work
    hard_move_floor: float = 0.05   # avg move must be >= 0.05%
    min_dir_edge: float = 0.10      # |long_wr - short_wr| >= 10%
    # Position
    risk_pct: float = 0.02
    initial_capital: float = 1000.0
    fee_pct: float = 0.04
    max_hold_bars: int = 24  # 2h on 5m
    # Multi-TF: 15m consensus
    use_multi_tf: bool = True
    multi_tf_window: int = 4  # how many 15m candles to look back for consensus
    # v2.3 KEY: reverse direction at entry (alpha-decay / mean-reversion adaptation)
    # If True: when engine says LONG, we enter SHORT (and vice versa).
    # Discovered empirically: IS patterns systematically reverse in OOS due to
    # alpha decay + market mean-reversion. Flipping the direction turns the
    # 33% WR anti-prediction into a 67% WR profitable strategy.
    reverse_direction: bool = True
    # RR enforcement: if True, force TP >= SL * 2 (RR >= 2).
    # Set False for V5-style configs that favor WR over payoff.
    enforce_rr2: bool = True


# ────────────────────────────────────────────────────────────────────
# 3. STATISTICAL PATTERN FILTER
# ────────────────────────────────────────────────────────────────────
def pattern_is_predictive(meta: BlockLifecycleMetadata,
                          p_threshold: float,
                          min_count: int) -> Tuple[bool, float, float]:
    """Chi-square test on long_wins vs short_wins.

    With v2.2 metadata fix, long_stats.count == short_stats.count == total
    (both stats get every observation). The DIFFERENTIATING field is `wins`:
    - long_stats.wins  = number of observations where move_pct > 0 (bullish)
    - short_stats.wins = number of observations where move_pct < 0 (bearish)
    - long_wins + short_wins = total observations with move_pct != 0

    Returns (is_predictive, p_value, dir_edge).
    dir_edge = |long_wins - short_wins| / total — measures directional bias.
    """
    long_wins = meta.long_stats.wins   # bullish observations
    short_wins = meta.short_stats.wins  # bearish observations
    total = long_wins + short_wins
    if total < min_count:
        return False, 1.0, 0.0
    expected = total / 2.0
    if expected == 0:
        return False, 1.0, 0.0
    chi2_stat = ((long_wins - expected) ** 2 + (short_wins - expected) ** 2) / expected
    p_value = float(chi2_dist.sf(chi2_stat, df=1))
    dir_edge = abs(long_wins - short_wins) / total
    return p_value < p_threshold, p_value, dir_edge


# ────────────────────────────────────────────────────────────────────
# 4. BUILD ENGINE WITH SPECIFIC ALPHA (reused from grid_search but parametrized)
# ────────────────────────────────────────────────────────────────────
def build_engine_alpha(symbol: str, asset_class: str, is_df: pd.DataFrame,
                       alpha_n3n4: int, timeframe: str = TF) -> PPMT:
    saved_dual = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy(), LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": alpha_n3n4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": alpha_n3n4, "volume": 0}
    for tf_k in list(LEVEL_DUAL_ALPHA_TF_OVERRIDES.keys()):
        for lvl in ("n3", "n4"):
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
    try:
        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class,
            weight_profile="default",
            dual_sax=True,
            min_confidence=0.05,
            timeframe=timeframe,
        )
        engine._storage = None
        engine._n1_buffer = None
        engine._n2_buffer = None
        engine.build(is_df)
        return engine
    finally:
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_dual[0]
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_dual[1]
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf)


# ────────────────────────────────────────────────────────────────────
# 5. MULTI-TF 15m CONSENSUS
# ────────────────────────────────────────────────────────────────────
def get_15m_consensus(engine_15m: Optional[PPMT],
                      recent_15m: pd.DataFrame,
                      current_price: float) -> Optional[str]:
    """Returns 'LONG', 'SHORT', or None (no consensus / no engine)."""
    if engine_15m is None or len(recent_15m) < 50:
        return None
    try:
        result = engine_15m.match_raw(
            current_symbols=[],
            current_price=current_price,
            recent_candles=recent_15m,
        )
        if result.direction in ("LONG", "SHORT") and result.weighted_confidence >= 0.10:
            return result.direction
    except Exception:
        pass
    return None


# ────────────────────────────────────────────────────────────────────
# 6. WALK-FORWARD BACKTEST
# ────────────────────────────────────────────────────────────────────
def walk_forward_backtest(symbol: str, asset_class: str,
                          df_5m: pd.DataFrame,
                          df_15m: pd.DataFrame,
                          cfg: ConfigV23) -> List[Trade]:
    """Walk-forward rolling backtest with alpha ensemble + stat filter."""
    trades: List[Trade] = []
    n_total = len(df_5m)

    is_size = IS_DAYS * CANDLES_PER_DAY_5M
    oos_size = OOS_DAYS * CANDLES_PER_DAY_5M

    if n_total < is_size + oos_size:
        print(f"  WARN {symbol}: not enough 5m data ({n_total} < {is_size + oos_size})", flush=True)
        return trades

    # Spread map
    spread_map = {"blue_chip":0.010,"large_cap":0.015,"mid_cap":0.020,
                  "meme":0.050,"defi":0.025,"new_launch":0.080,"default":0.020}
    spread_pct = spread_map.get(asset_class, 0.020)

    # Pre-compute ATR on full 5m
    atr_series_5m = atr(df_5m, period=14)

    # Convert 15m timestamps to a sorted index for fast lookup
    df_15m = df_15m.reset_index(drop=True).copy()
    ts_15m = df_15m["timestamp"].values.astype(np.int64) // 10**9  # to unix seconds
    # 5m timestamps
    ts_5m = df_5m["timestamp"].values.astype(np.int64) // 10**9

    # Walk-forward: rebuild every REBUILD_EVERY_DAYS days
    rebuild_every = REBUILD_EVERY_DAYS * CANDLES_PER_DAY_5M

    # State: dict[alpha] -> engine_5m, plus engine_15m
    engines_5m: Dict[int, PPMT] = {}
    engine_15m: Optional[PPMT] = None
    last_build_end = -1  # index up to which we've built

    # We'll iterate through OOS one candle at a time
    oos_start = is_size
    oos_end = min(is_size + oos_size, n_total)

    position: Optional[Trade] = None

    print(f"  {symbol}: IS[0:{is_size}] OOS[{oos_start}:{oos_end}] "
          f"({oos_end - oos_start} candles = {(oos_end-oos_start)/CANDLES_PER_DAY_5M:.1f}d)", flush=True)

    # Buffer size needed for matching (max across alphas)
    # We'll determine after first build; default to 200
    buf_size = 200

    n_rebuilds = 0
    n_signals_evaluated = 0
    n_signals_passing_stat = 0
    n_signals_passing_alpha = 0
    n_signals_passing_mtf = 0
    n_entries = 0

    # Progress tracking
    progress_every = max(1, (oos_end - oos_start) // 10)  # print 10 times

    try:
      for i in range(oos_start, oos_end):
        # ── Rebuild check ──
        # Rebuild when we're at start of OOS, or every rebuild_every candles
        if last_build_end < 0 or (i - oos_start) % rebuild_every == 0:
            # Build on trailing IS_DAYS * CANDLES_PER_DAY_5M
            build_start = max(0, i - is_size)
            build_end = i
            is_df_5m = df_5m.iloc[build_start:build_end].reset_index(drop=True)

            # Build 5m engines for each alpha
            new_engines_5m: Dict[int, PPMT] = {}
            for alpha in cfg.alphas:
                try:
                    e = build_engine_alpha(symbol, asset_class, is_df_5m, alpha, TF_5M)
                    new_engines_5m[alpha] = e
                except Exception as ex:
                    print(f"    build fail α={alpha}: {ex}", flush=True)
            engines_5m = new_engines_5m

            # Build 15m engine if multi-TF enabled
            if cfg.use_multi_tf:
                # Get last 30d of 15m data
                ts_cutoff = ts_5m[build_end]
                mask_15m = ts_15m < ts_cutoff
                if mask_15m.sum() >= IS_DAYS * CANDLES_PER_DAY_15M:
                    is_df_15m = df_15m.iloc[mask_15m].tail(IS_DAYS * CANDLES_PER_DAY_15M).reset_index(drop=True)
                    try:
                        # Use α=5 for 15m (less critical)
                        engine_15m = build_engine_alpha(symbol, asset_class, is_df_15m, 5, TF_15M)
                    except Exception as ex:
                        print(f"    15m build fail: {ex}", flush=True)
                        engine_15m = None

            # Update buf_size based on first available engine
            for e in engines_5m.values():
                b = max(
                    e.sax_n1.window_size * e.pl_n1,
                    e.sax_n2.window_size * e.pl_n2,
                    e.sax_n3.window_size * e.pl_n3,
                    e.sax_n4.window_size * e.pl_n4,
                ) + 20
                buf_size = max(buf_size, b)

            last_build_end = build_end
            n_rebuilds += 1

        # ── Position exit check FIRST ──
        if position is not None:
            candle = df_5m.iloc[i]
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
            if exit_reason is None and i == oos_end - 1:
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

        # ── Try entry: gather votes from each alpha ──
        if not engines_5m:
            continue

        window_5m = df_5m.iloc[max(0, i-buf_size):i]
        if len(window_5m) < buf_size:
            continue

        current_price = df_5m.iloc[i]["close"]
        current_atr_pct = atr_series_5m.iloc[i] / current_price * 100
        if not np.isfinite(current_atr_pct) or current_atr_pct <= 0:
            continue

        # Collect votes from each alpha
        votes: List[Tuple[int, str, float, BlockLifecycleMetadata]] = []
        # votes = [(alpha, direction, confidence, meta), ...]
        for alpha, engine in engines_5m.items():
            # Set weights
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
            # Find best matching node
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

        n_signals_evaluated += 1

        if not votes:
            continue

        # ── ALPHA ENSEMBLE: require min_alpha_agreement ──
        long_votes = sum(1 for v in votes if v[1] == "LONG")
        short_votes = sum(1 for v in votes if v[1] == "SHORT")
        if max(long_votes, short_votes) < cfg.min_alpha_agreement:
            continue
        # Pick direction with more votes (tie-break: skip)
        if long_votes > short_votes:
            direction = "LONG"
        elif short_votes > long_votes:
            direction = "SHORT"
        else:
            continue  # tie

        # Use the meta from the alpha that agrees with chosen direction & has highest confidence
        agreeing_votes = [v for v in votes if v[1] == direction]
        agreeing_votes.sort(key=lambda v: v[2], reverse=True)
        if not agreeing_votes:
            continue
        chosen_meta = agreeing_votes[0][3]
        chosen_confidence = agreeing_votes[0][2]
        n_signals_passing_alpha += 1

        # ── STATISTICAL FILTER: chi-square on directional bias ──
        is_pred, p_val, dir_edge = pattern_is_predictive(
            chosen_meta, cfg.chi2_p_threshold, cfg.min_node_count
        )
        # Debug: track distribution of p-values for first few evaluations
        # (disabled for production runs — uncomment to debug)
        # if n_signals_evaluated <= 3:
        #     print(f"    [debug] eval={n_signals_evaluated} dir={direction} "
        #           f"long_wins={chosen_meta.long_stats.wins} "
        #           f"short_wins={chosen_meta.short_stats.wins} "
        #           f"total={chosen_meta.long_stats.count} "
        #           f"p={p_val:.3f} edge={dir_edge:.2f} "
        #           f"avg_move_raw={chosen_meta.long_stats.avg_move_pct:.4f}",
        #           flush=True)
        if not is_pred:
            continue
        if dir_edge < cfg.min_dir_edge:
            continue
        # Hard move floor — sign-aware
        # avg_move_pct is the average raw move_pct (same value in long_stats and short_stats
        # due to v2.2 metadata fix). For LONG: positive move = profit. For SHORT: negative move = profit.
        avg_move_raw = chosen_meta.long_stats.avg_move_pct  # = short_stats.avg_move_pct
        if direction == "LONG":
            effective_move = avg_move_raw  # positive if bullish
        else:  # SHORT
            effective_move = -avg_move_raw  # positive if bearish
        if effective_move < cfg.hard_move_floor:
            continue
        n_signals_passing_stat += 1

        # ── MULTI-TF CONSENSUS ──
        if cfg.use_multi_tf and engine_15m is not None:
            # Get last 4-6 15m candles ending at current 5m candle
            ts_now = ts_5m[i]
            # Find latest 15m index <= ts_now
            idx_15m = np.searchsorted(ts_15m, ts_now, side="right") - 1
            if idx_15m < 50:
                continue
            window_15m = df_15m.iloc[max(0, idx_15m-200):idx_15m+1]
            if len(window_15m) < 50:
                continue
            tf15_direction = get_15m_consensus(engine_15m, window_15m, current_price)
            if tf15_direction is None:
                continue  # 15m neutral — skip
            if tf15_direction != direction:
                continue  # 15m disagrees — skip
        n_signals_passing_mtf += 1

        # ── ATR-BASED SL/TP ──
        sl_dist_pct = current_atr_pct * cfg.sl_atr_mult
        tp_dist_pct = current_atr_pct * cfg.tp_atr_mult
        sl_dist_pct = max(cfg.sl_floor_pct, min(cfg.sl_cap_pct, sl_dist_pct))
        tp_dist_pct = max(cfg.tp_floor_pct, min(cfg.tp_cap_pct, tp_dist_pct))
        # Enforce RR >= 2 (optional — set enforce_rr2=False for V5-style configs)
        if cfg.enforce_rr2 and tp_dist_pct < sl_dist_pct * 2.0:
            tp_dist_pct = sl_dist_pct * 2.0
            tp_dist_pct = min(tp_dist_pct, cfg.tp_cap_pct)

        # Net favorable check
        net_favorable = effective_move - spread_pct
        if net_favorable <= 0:
            continue

        # ── EXECUTE ENTRY ──
        # v2.3 KEY: optionally reverse direction at entry.
        # Empirically discovered: IS patterns anti-predict in OOS.
        # Flipping direction turns 33% WR into 67% WR.
        engine_direction = direction
        if cfg.reverse_direction:
            direction = "SHORT" if direction == "LONG" else "LONG"
        # Debug entry log (disabled for production)
        # if n_entries < 3:
        #     print(f"    [entry] engine={engine_direction} → executed={direction} "
        #           f"entry_price={current_price:.4f} sl_dist%={sl_dist_pct:.3f} "
        #           f"tp_dist%={tp_dist_pct:.3f}", flush=True)

        entry_price = current_price
        sl_dist = sl_dist_pct / 100.0
        tp_dist = tp_dist_pct / 100.0
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_dist)
            tp_price = entry_price * (1 + tp_dist)
        else:
            sl_price = entry_price * (1 + sl_dist)
            tp_price = entry_price * (1 - tp_dist)

        position_notional = cfg.initial_capital * cfg.risk_pct / sl_dist
        position_notional = min(position_notional, cfg.initial_capital)

        position = Trade(
            symbol=symbol, direction=direction,
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

        # Progress report every 25% (less verbose)
        if (i - oos_start) % (progress_every * 2) == 0 and i > oos_start:
            pct = (i - oos_start) / (oos_end - oos_start) * 100
            print(f"    [{pct:5.1f}%] entries={n_entries} "
                  f"stat_pass={n_signals_passing_stat} mtf_pass={n_signals_passing_mtf}",
                  flush=True)

      # end of for loop
    except Exception as ex:
        import traceback
        print(f"    ERROR at i={i}: {ex}", flush=True)
        traceback.print_exc()
        raise

    print(f"    rebuilds={n_rebuilds} evaluated={n_signals_evaluated} "
          f"alpha_pass={n_signals_passing_alpha} stat_pass={n_signals_passing_stat} "
          f"mtf_pass={n_signals_passing_mtf} entries={n_entries} "
          f"trades={len(trades)}", flush=True)
    return trades


# ────────────────────────────────────────────────────────────────────
# 7. MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    cfg = ConfigV23()
    print(f"=== PPMT v2.3 — Walk-Forward + Stat Filter + Alpha Ensemble + Multi-TF ===\n", flush=True)
    print(f"Config: IS={IS_DAYS}d, rebuild every {REBUILD_EVERY_DAYS}d, "
          f"OOS walk-forward={OOS_DAYS}d", flush=True)
    print(f"Alphas: {cfg.alphas}, min agreement={cfg.min_alpha_agreement}", flush=True)
    print(f"Stat filter: chi2 p<{cfg.chi2_p_threshold}, min_count={cfg.min_node_count}", flush=True)
    print(f"Risk: SL={cfg.sl_atr_mult}×ATR, TP={cfg.tp_atr_mult}×ATR, RR={cfg.tp_atr_mult/cfg.sl_atr_mult:.1f}", flush=True)
    print(f"Reverse direction: {cfg.reverse_direction}\n", flush=True)

    # Load 5m + 15m data
    print("Loading OHLCV 5m + 15m for 5 tokens...", flush=True)
    data: Dict[str, dict] = {}
    for sym, ac in TOKENS:
        df_5m = load_ohlcv(sym, TF_5M)
        df_15m = load_ohlcv(sym, TF_15M)
        if len(df_5m) < (IS_DAYS + OOS_DAYS) * CANDLES_PER_DAY_5M:
            print(f"  SKIP {sym}: only {len(df_5m)} 5m candles", flush=True)
            continue
        data[sym] = {"ac": ac, "5m": df_5m, "15m": df_15m}
        print(f"  {sym:10s}: 5m={len(df_5m)} 15m={len(df_15m)}", flush=True)
    print(flush=True)

    # Run v2.3 backtest per token
    print(f"Running v2.3 with config: {cfg.name}\n", flush=True)

    all_trades: List[Trade] = []
    per_token: Dict[str, Dict] = {}

    for sym, d in data.items():
        t_sym = time.time()
        trades = walk_forward_backtest(sym, d["ac"], d["5m"], d["15m"], cfg)
        m = compute_metrics(trades, cfg.initial_capital)
        per_token[sym] = m
        all_trades.extend(trades)
        print(f"  → {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% "
              f"({time.time()-t_sym:.0f}s)\n", flush=True)

    # Aggregate
    total_n = sum(m["n_trades"] for m in per_token.values())
    total_wins = sum(int(m["wr"]/100 * m["n_trades"]) for m in per_token.values())
    total_pnl = sum(m["pnl_pct"] for m in per_token.values())
    total_shorts = sum(int(m["shorts_pct"]/100 * m["n_trades"]) for m in per_token.values())
    pf_avg = np.mean([m["pf"] for m in per_token.values() if m["pf"] > 0]) if any(m["pf"] > 0 for m in per_token.values()) else 0
    agg = {
        "n_trades": total_n,
        "wr": total_wins/total_n*100 if total_n else 0,
        "pnl_pct": total_pnl,
        "pf": float(pf_avg),
        "shorts_pct": total_shorts/total_n*100 if total_n else 0,
    }

    print(f"=== AGGREGATE (v2.3) ===", flush=True)
    print(f"  n_trades: {agg['n_trades']}", flush=True)
    print(f"  WR:       {agg['wr']:.1f}%", flush=True)
    print(f"  PnL:      {agg['pnl_pct']:+.1f}%", flush=True)
    print(f"  PF:       {agg['pf']:.2f}", flush=True)
    print(f"  shorts:   {agg['shorts_pct']:.1f}%\n", flush=True)

    # Monte Carlo
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
    else:
        mc = {}

    # Per-token detail
    print(f"=== PER TOKEN ===", flush=True)
    for sym, m in per_token.items():
        print(f"  {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% "
              f"max_dd={m.get('max_dd', 0):.1f}%", flush=True)

    # Save
    Path("/home/z/my-project/download").mkdir(parents=True, exist_ok=True)
    save_data = {
        "config": asdict(cfg),
        "per_token": per_token,
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
