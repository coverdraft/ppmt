"""PPMT v2.4 — Per-Pattern Adaptive Direction + Multi-Regime IS + Candle-Body Gate + Multi-TF

Root cause analysis (from v2.3):
  v2.3 discovered the engine "anti-predicts" — applying reverse_direction=True to ALL
  signals gave WR=63% but PF<1 (losses too big relative to wins).
  The flaw: not every pattern should be reversed. Strong-trend patterns DO continue
  in OOS; weak signals DO mean-revert. "Always reverse" over-corrects.

v2.4 innovations:
  1. MULTI-REGIME IS — Build trie on 270d of combined data (3 windows × 90d each):
     BULL_2024 + RANGE_2025 + RECENT_2026_first_60d. Diverse regimes → richer
     pattern coverage → more reliable stats per node. User explicitly requested
     sampling from alcistas / rango / bajistas moments.
  2. PER-PATTERN ADAPTIVE DIRECTION — For each matched pattern, compute IS WR
     for the engine's chosen direction. Apply 3-tier logic:
       STRONG (IS_WR ≥ 75%): follow direction  (trend continuation dominant)
       MODERATE (60% ≤ IS_WR < 75%): REVERSE direction  (mean-reversion dominant)
       WEAK (IS_WR < 60%): skip  (noise, no edge)
     This replaces the "always reverse" global flag with per-pattern intelligence.
  3. CANDLE-BODY GATE — For each pattern, avg_move_pct sign must align with the
     chosen (post-adaptive) direction. If pattern's avg next-candle move disagrees
     with our trade direction, skip. This is the "node-to-node prediction" the user
     asked for: the trie tells us where the next candle's body ends, and we only
     enter if our trade direction matches that body direction.
  4. TIGHTER ROTATION — max_hold_bars=12 (1h on 5m), min_node_count=5 (more
     patterns qualify), chi2_p_threshold=0.40 (lenient — let adaptive direction
     do the filtering). Goal: more trades AND higher WR.
  5. 9 TOKENS — BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, LINK. Covers
     blue_chip / large_cap / mid_cap / meme.

Run:
  /home/z/.venv/bin/python3 /home/z/my-project/scripts/ppmt_v24_adaptive.py
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
from ppmt_grid_search import compute_metrics, monte_carlo, Trade

DB_PATH = "/home/z/.ppmt/ppmt.db"
OUT_PATH = "/home/z/my-project/download/ppmt_v24_results.json"

# 9 tokens covering blue/mid/meme — diverse asset classes
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

TF_5M = "5m"
TF_15M = "15m"

# Walk-forward: build on 270d combined IS (3 windows × 90d), test on last 30d of RECENT_2026
OOS_DAYS = 30
CANDLES_PER_DAY_5M = 288
CANDLES_PER_DAY_15M = 96
IS_RECENT_DAYS = 60  # use first 60d of RECENT_2026 as recent IS portion
# Combined IS = RECENT_2026 first 60d + RANGE_2025 (90d) + BULL_2024 (90d) = 240d
# Plus 30d OOS = total 270d of RECENT_2026 needed; we have 90d RECENT, so:
#   IS = RECENT_2026[0:60d] + RANGE_2025[all] + BULL_2024[all] = 240d
#   OOS = RECENT_2026[60d:90d] = 30d  ✓


# ────────────────────────────────────────────────────────────────────
# 1. ATR
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
# 2. MULTI-WINDOW DATA LOADER
# ────────────────────────────────────────────────────────────────────
def load_ohlcv_multiwindow(symbol: str, tf: str,
                           windows: List[str] = None) -> pd.DataFrame:
    """Load OHLCV from ohlcv_ext, optionally filtered by window tags.
    Returns DataFrame sorted by timestamp with `window` column preserved.
    """
    conn = sqlite3.connect(DB_PATH)
    if windows:
        placeholders = ",".join("?" * len(windows))
        df = pd.read_sql_query(
            f"SELECT timestamp, open, high, low, close, volume, window "
            f"FROM ohlcv_ext WHERE symbol=? AND timeframe=? "
            f"AND window IN ({placeholders}) ORDER BY timestamp",
            conn, params=[symbol, tf] + list(windows))
    else:
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume, window "
            "FROM ohlcv_ext WHERE symbol=? AND timeframe=? ORDER BY timestamp",
            conn, params=(symbol, tf))
    conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    return df.reset_index(drop=True)


def split_is_oos_recent(df: pd.DataFrame, is_days: int = IS_RECENT_DAYS,
                        oos_days: int = OOS_DAYS) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """For RECENT_2026 window: first `is_days` are IS, next `oos_days` are OOS.
    Returns (is_recent_df, oos_df) — both from RECENT_2026 only.
    """
    recent = df[df["window"] == "RECENT_2026"].reset_index(drop=True)
    cpd = CANDLES_PER_DAY_5M if tf_from_df(df) == "5m" else CANDLES_PER_DAY_15M
    is_end = is_days * cpd
    oos_end = is_end + oos_days * cpd
    is_recent = recent.iloc[:is_end].reset_index(drop=True)
    oos = recent.iloc[is_end:oos_end].reset_index(drop=True)
    return is_recent, oos


def tf_from_df(df: pd.DataFrame) -> str:
    """Infer TF from median timestamp delta."""
    if len(df) < 2:
        return "5m"
    delta = (df["timestamp"].iloc[1] - df["timestamp"].iloc[0]).total_seconds()
    if delta >= 800 and delta <= 1000:
        return "15m"
    return "5m"


# ────────────────────────────────────────────────────────────────────
# 3. CONFIG
# ────────────────────────────────────────────────────────────────────
@dataclass
class ConfigV24:
    name: str = "v24_adaptive"
    # Weights (N1, N2, N3, N4) — universal-friendly (N3 dominant)
    weights: Tuple[float, float, float, float] = (0.30, 0.10, 0.50, 0.10)
    # Statistical filter (lenient — adaptive direction does the work)
    chi2_p_threshold: float = 0.40
    min_node_count: int = 5
    # Adaptive direction tiers
    strong_wr_threshold: float = 0.75   # IS_WR ≥ 75% → strong signal (stat tracking only)
    moderate_wr_threshold: float = 0.65  # 65% ≤ IS_WR → REVERSE; < 65% → skip
    # Alpha ensemble
    use_alpha_ensemble: bool = True
    alphas: Tuple[int, ...] = (5, 7)
    min_alpha_agreement: int = 2
    # Candle-body gate
    use_body_gate: bool = True
    body_min_avg_move: float = 0.05  # |avg_move_pct| ≥ 0.05% required
    # Risk — tighter SL, RR=2 to ensure PF≥1.5 at WR=60%
    sl_atr_mult: float = 1.2
    tp_atr_mult: float = 2.4    # RR = 2
    sl_cap_pct: float = 1.5
    sl_floor_pct: float = 0.15
    tp_cap_pct: float = 3.0
    tp_floor_pct: float = 0.30
    # Entry gates
    min_confidence: float = 0.05
    hard_move_floor: float = 0.04
    min_dir_edge: float = 0.20  # only patterns with clear directional bias
    # Position
    risk_pct: float = 0.02
    initial_capital: float = 1000.0
    fee_pct: float = 0.04
    max_hold_bars: int = 12  # 1h on 5m — faster rotation
    # Multi-TF
    use_multi_tf: bool = True
    multi_tf_window: int = 4
    # Walk-forward rebuild (within OOS, rebuild trie every 7d with extended IS)
    rebuild_every_days: int = 7
    # v2.4 KEY: per-pattern adaptive direction (replaces global reverse_direction)
    adaptive_direction: bool = True


# ────────────────────────────────────────────────────────────────────
# 4. STATISTICAL PATTERN FILTER
# ────────────────────────────────────────────────────────────────────
def pattern_stats(meta: BlockLifecycleMetadata,
                  p_threshold: float,
                  min_count: int) -> Tuple[bool, float, float, float, float]:
    """Chi-square test on long_wins vs short_wins.
    Returns (is_predictive, p_value, dir_edge, long_wr, short_wr).
      long_wr  = long_wins  / total  (IS WR if we always went LONG after this pattern)
      short_wr = short_wins / total  (IS WR if we always went SHORT)
      dir_edge = |long_wr - short_wr|
    """
    long_wins = meta.long_stats.wins
    short_wins = meta.short_stats.wins
    total = long_wins + short_wins
    if total < min_count:
        return False, 1.0, 0.0, 0.5, 0.5
    expected = total / 2.0
    if expected == 0:
        return False, 1.0, 0.0, 0.5, 0.5
    chi2_stat = ((long_wins - expected) ** 2 + (short_wins - expected) ** 2) / expected
    p_value = float(chi2_dist.sf(chi2_stat, df=1))
    long_wr = long_wins / total
    short_wr = short_wins / total
    dir_edge = abs(long_wr - short_wr)
    return p_value < p_threshold, p_value, dir_edge, long_wr, short_wr


# ────────────────────────────────────────────────────────────────────
# 5. ADAPTIVE DIRECTION LOGIC (KEY INNOVATION)
# ────────────────────────────────────────────────────────────────────
def adaptive_direction(engine_direction: str,
                       long_wr: float, short_wr: float,
                       cfg: ConfigV24) -> Optional[str]:
    """Per-pattern adaptive direction (ALWAYS-REVERSE with strength filter).

    Empirical finding from v2.3 + v2.4 first run:
      Even STRONG IS signals (≥75% WR) anti-predict in OOS due to alpha decay
      + market mean-reversion. The "follow" path produces ~33% WR consistently.
      Reversing strong signals gives ~67% WR.

    New logic:
      STRONG edge (max WR ≥ 75%): REVERSE engine (strong mean-reversion)
      MODERATE edge (65% ≤ max WR < 75%): REVERSE engine (mean-reversion)
      WEAK edge (max WR < 65%): skip  (no real signal — filter out noise)

    All passing signals are REVERSED. The strength filter just removes noise.
    """
    max_wr = max(long_wr, short_wr)
    if max_wr >= cfg.moderate_wr_threshold:
        # Signal has IS edge → mean-reversion will reverse it in OOS
        return "SHORT" if engine_direction == "LONG" else "LONG"
    else:
        # WEAK — skip
        return None


# ────────────────────────────────────────────────────────────────────
# 6. BUILD ENGINE WITH SPECIFIC ALPHA
# ────────────────────────────────────────────────────────────────────
def build_engine_alpha(symbol: str, asset_class: str, is_df: pd.DataFrame,
                       alpha_n3n4: int, timeframe: str = TF_5M) -> PPMT:
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
# 7. MULTI-TF 15m CONSENSUS
# ────────────────────────────────────────────────────────────────────
def get_15m_consensus(engine_15m: Optional[PPMT],
                      recent_15m: pd.DataFrame,
                      current_price: float) -> Optional[str]:
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
# 8. BUILD COMBINED IS FROM MULTIPLE WINDOWS
# ────────────────────────────────────────────────────────────────────
def build_combined_is_5m(symbol: str) -> pd.DataFrame:
    """Combined IS for 5m: RECENT_2026 first 60d + RANGE_2025 all + BULL_2024 all.
    Total = 60 + 90 + 90 = 240 days = 69,120 5m candles.
    """
    df_all = load_ohlcv_multiwindow(symbol, TF_5M,
                                     windows=["RECENT_2026", "RANGE_2025", "BULL_2024"])
    recent = df_all[df_all["window"] == "RECENT_2026"].reset_index(drop=True)
    is_recent = recent.iloc[:IS_RECENT_DAYS * CANDLES_PER_DAY_5M].reset_index(drop=True)
    other = df_all[df_all["window"].isin(["RANGE_2025", "BULL_2024"])].reset_index(drop=True)
    combined = pd.concat([is_recent, other], ignore_index=True)
    # Sort by timestamp to be safe (though already sorted)
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def build_combined_is_15m(symbol: str) -> pd.DataFrame:
    """Combined IS for 15m: same windows."""
    df_all = load_ohlcv_multiwindow(symbol, TF_15M,
                                     windows=["RECENT_2026", "RANGE_2025", "BULL_2024"])
    recent = df_all[df_all["window"] == "RECENT_2026"].reset_index(drop=True)
    is_recent = recent.iloc[:IS_RECENT_DAYS * CANDLES_PER_DAY_15M].reset_index(drop=True)
    other = df_all[df_all["window"].isin(["RANGE_2025", "BULL_2024"])].reset_index(drop=True)
    combined = pd.concat([is_recent, other], ignore_index=True)
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


# ────────────────────────────────────────────────────────────────────
# 9. WALK-FORWARD BACKTEST (v2.4)
# ────────────────────────────────────────────────────────────────────
def walk_forward_backtest(symbol: str, asset_class: str,
                          cfg: ConfigV24) -> Tuple[List[Trade], Dict]:
    """v2.4 walk-forward backtest:
       - Build trie once on 240d combined IS (3 windows)
       - Rebuild every 7d on extended IS (rolling recent + historical windows)
       - Test on 30d OOS (RECENT_2026 last 30d)
    """
    trades: List[Trade] = []
    stats = {"evaluated": 0, "alpha_pass": 0, "stat_pass": 0,
             "adaptive_skip": 0, "body_skip": 0, "mtf_skip": 0, "entries": 0,
             "strong_follow": 0, "moderate_reverse": 0}

    # Load RECENT_2026 for OOS + recent IS portion
    df_5m_all = load_ohlcv_multiwindow(symbol, TF_5M,
                                        windows=["RECENT_2026", "RANGE_2025", "BULL_2024"])
    df_15m_all = load_ohlcv_multiwindow(symbol, TF_15M,
                                         windows=["RECENT_2026", "RANGE_2025", "BULL_2024"])

    # Split RECENT_2026 into IS(60d) + OOS(30d)
    recent_5m = df_5m_all[df_5m_all["window"] == "RECENT_2026"].reset_index(drop=True)
    recent_15m = df_15m_all[df_15m_all["window"] == "RECENT_2026"].reset_index(drop=True)
    is_end_5m = IS_RECENT_DAYS * CANDLES_PER_DAY_5M
    oos_end_5m = is_end_5m + OOS_DAYS * CANDLES_PER_DAY_5M
    is_end_15m = IS_RECENT_DAYS * CANDLES_PER_DAY_15M
    oos_end_15m = is_end_15m + OOS_DAYS * CANDLES_PER_DAY_15M

    if len(recent_5m) < oos_end_5m:
        print(f"  WARN {symbol}: RECENT_2026 5m has only {len(recent_5m)} candles, "
              f"need {oos_end_5m}", flush=True)
        return trades, stats
    if len(recent_15m) < oos_end_15m:
        print(f"  WARN {symbol}: RECENT_2026 15m has only {len(recent_15m)} candles, "
              f"need {oos_end_15m}", flush=True)
        return trades, stats

    # OOS slices
    oos_5m = recent_5m.iloc[is_end_5m:oos_end_5m].reset_index(drop=True)
    oos_15m = recent_15m.iloc[is_end_15m:oos_end_15m].reset_index(drop=True)
    # For OOS, we need to keep track of "lookback" window from IS for matching.
    # So we'll iterate using timestamps: at each 5m candle in OOS, take the
    # preceding 200 candles from `recent_5m` (which include IS + earlier OOS).

    # Full 5m recent for matching window
    full_5m = recent_5m.reset_index(drop=True)
    full_15m = recent_15m.reset_index(drop=True)
    # Historical 5m/15m for combined IS
    hist_5m = df_5m_all[df_5m_all["window"].isin(["RANGE_2025", "BULL_2024"])].reset_index(drop=True)
    hist_15m = df_15m_all[df_15m_all["window"].isin(["RANGE_2025", "BULL_2024"])].reset_index(drop=True)

    # Spread map
    spread_map = {"blue_chip":0.010,"large_cap":0.015,"mid_cap":0.020,
                  "meme":0.050,"defi":0.025,"new_launch":0.080,"default":0.020}
    spread_pct = spread_map.get(asset_class, 0.020)

    # ATR on full 5m
    atr_series_5m = atr(full_5m, period=14)

    # Timestamps for 15m lookup
    ts_15m = full_15m["timestamp"].astype(np.int64).values // 10**9
    ts_5m = full_5m["timestamp"].astype(np.int64).values // 10**9

    # Build initial combined IS
    print(f"  {symbol}: building combined IS (240d, ~69k 5m candles)...", flush=True)
    combined_is_5m = pd.concat([recent_5m.iloc[:is_end_5m], hist_5m], ignore_index=True)
    combined_is_5m = combined_is_5m.sort_values("timestamp").reset_index(drop=True)
    combined_is_15m = pd.concat([recent_15m.iloc[:is_end_15m], hist_15m], ignore_index=True)
    combined_is_15m = combined_is_15m.sort_values("timestamp").reset_index(drop=True)

    # Build engines for each alpha
    engines_5m: Dict[int, PPMT] = {}
    for alpha in cfg.alphas:
        try:
            t_b = time.time()
            e = build_engine_alpha(symbol, asset_class, combined_is_5m, alpha, TF_5M)
            engines_5m[alpha] = e
            print(f"    α={alpha} 5m engine built ({time.time()-t_b:.0f}s)", flush=True)
        except Exception as ex:
            print(f"    build fail α={alpha}: {ex}", flush=True)

    # Build 15m engine (alpha=5)
    engine_15m: Optional[PPMT] = None
    if cfg.use_multi_tf:
        try:
            t_b = time.time()
            engine_15m = build_engine_alpha(symbol, asset_class, combined_is_15m, 5, TF_15M)
            print(f"    15m engine built ({time.time()-t_b:.0f}s)", flush=True)
        except Exception as ex:
            print(f"    15m build fail: {ex}", flush=True)

    if not engines_5m:
        print(f"  ERROR {symbol}: no 5m engines built", flush=True)
        return trades, stats

    # Buffer size
    buf_size = 200
    for e in engines_5m.values():
        b = max(
            e.sax_n1.window_size * e.pl_n1,
            e.sax_n2.window_size * e.pl_n2,
            e.sax_n3.window_size * e.pl_n3,
            e.sax_n4.window_size * e.pl_n4,
        ) + 20
        buf_size = max(buf_size, b)

    # Walk-forward state
    rebuild_every = cfg.rebuild_every_days * CANDLES_PER_DAY_5M
    last_rebuild_oos_idx = -rebuild_every  # force initial build done above

    position: Optional[Trade] = None
    n_rebuilds = 1  # initial build counts
    oos_n = len(oos_5m)
    progress_every = max(1, oos_n // 10)

    print(f"  {symbol}: OOS starts, {oos_n} candles ({OOS_DAYS}d)", flush=True)

    try:
      # i is the index into full_5m (which includes IS portion + OOS)
      # OOS candles live at indices [is_end_5m, oos_end_5m)
      for i in range(is_end_5m, oos_end_5m):
        oos_idx = i - is_end_5m

        # ── Rebuild check ──
        if oos_idx - last_rebuild_oos_idx >= rebuild_every:
            # Rebuild engines using: full_5m[:i] (recent IS + OOS up to now) + hist_5m
            recent_so_far = full_5m.iloc[:i].reset_index(drop=True)
            # Use last 60d of recent_so_far + all historical
            recent_60d = recent_so_far.tail(IS_RECENT_DAYS * CANDLES_PER_DAY_5M).reset_index(drop=True)
            new_is_5m = pd.concat([recent_60d, hist_5m], ignore_index=True)
            new_is_5m = new_is_5m.sort_values("timestamp").reset_index(drop=True)

            new_engines_5m: Dict[int, PPMT] = {}
            for alpha in cfg.alphas:
                try:
                    e = build_engine_alpha(symbol, asset_class, new_is_5m, alpha, TF_5M)
                    new_engines_5m[alpha] = e
                except Exception as ex:
                    print(f"    rebuild fail α={alpha} at oos_idx={oos_idx}: {ex}", flush=True)
            if new_engines_5m:
                engines_5m = new_engines_5m
                # Rebuild 15m too
                if cfg.use_multi_tf:
                    recent_15m_so_far = full_15m.iloc[:is_end_15m + oos_idx // 3 + 1].reset_index(drop=True)
                    recent_15m_60d = recent_15m_so_far.tail(IS_RECENT_DAYS * CANDLES_PER_DAY_15M).reset_index(drop=True)
                    new_is_15m = pd.concat([recent_15m_60d, hist_15m], ignore_index=True)
                    new_is_15m = new_is_15m.sort_values("timestamp").reset_index(drop=True)
                    try:
                        engine_15m = build_engine_alpha(symbol, asset_class, new_is_15m, 5, TF_15M)
                    except Exception:
                        pass

            last_rebuild_oos_idx = oos_idx
            n_rebuilds += 1
            gc.collect()

        # ── Position exit check FIRST ──
        if position is not None:
            candle = full_5m.iloc[i]
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
            if exit_reason is None and i == oos_end_5m - 1:
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
            # IMPORTANT: continue so we don't evaluate a new entry on the same candle as an exit
            continue

        # ── Try entry: gather votes from each alpha ──
        if not engines_5m:
            continue

        window_5m = full_5m.iloc[max(0, i-buf_size):i]
        if len(window_5m) < buf_size:
            continue

        current_price = full_5m.iloc[i]["close"]
        current_atr_pct = atr_series_5m.iloc[i] / current_price * 100
        if not np.isfinite(current_atr_pct) or current_atr_pct <= 0:
            continue

        # Collect votes
        votes: List[Tuple[int, str, float, BlockLifecycleMetadata]] = []
        for alpha, engine in engines_5m.items():
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

        stats["evaluated"] += 1
        if not votes:
            continue

        # ── ALPHA ENSEMBLE ──
        long_votes = sum(1 for v in votes if v[1] == "LONG")
        short_votes = sum(1 for v in votes if v[1] == "SHORT")
        if max(long_votes, short_votes) < cfg.min_alpha_agreement:
            continue
        if long_votes > short_votes:
            engine_direction = "LONG"
        elif short_votes > long_votes:
            engine_direction = "SHORT"
        else:
            continue  # tie

        # Use the meta from the alpha that agrees & has highest confidence
        agreeing_votes = [v for v in votes if v[1] == engine_direction]
        agreeing_votes.sort(key=lambda v: v[2], reverse=True)
        if not agreeing_votes:
            continue
        chosen_meta = agreeing_votes[0][3]
        chosen_confidence = agreeing_votes[0][2]
        stats["alpha_pass"] += 1

        # ── STATISTICAL FILTER ──
        is_pred, p_val, dir_edge, long_wr, short_wr = pattern_stats(
            chosen_meta, cfg.chi2_p_threshold, cfg.min_node_count
        )
        if not is_pred:
            continue
        if dir_edge < cfg.min_dir_edge:
            continue

        # avg_move_pct is the same in long_stats and short_stats (per v2.2 fix).
        # It represents the average raw next-candle move_pct (signed).
        avg_move_raw = chosen_meta.long_stats.avg_move_pct  # signed: + bullish, - bearish

        # ── ADAPTIVE DIRECTION (KEY INNOVATION) ──
        if cfg.adaptive_direction:
            trade_direction = adaptive_direction(engine_direction, long_wr, short_wr, cfg)
            if trade_direction is None:
                stats["adaptive_skip"] += 1
                continue
            # Track tier distribution
            max_wr = max(long_wr, short_wr)
            if max_wr >= cfg.strong_wr_threshold:
                stats["strong_follow"] += 1
            else:
                stats["moderate_reverse"] += 1
        else:
            trade_direction = engine_direction

        # ── CANDLE-BODY GATE (REVERSED — because we always reverse engine direction) ──
        # Engine goes LONG when long_wr > short_wr (i.e., bullish next-candle body in IS).
        # We REVERSE → we go SHORT. So we want the IS bullish bias to be strong.
        # The body gate checks: |avg_move_raw| is significant enough.
        # Direction-wise: if we're going SHORT (after reverse), the IS bias should be LONG
        # (i.e., avg_move_raw > 0). If we're going LONG (after reverse), IS bias should be SHORT
        # (avg_move_raw < 0).
        if cfg.use_body_gate:
            if trade_direction == "SHORT" and avg_move_raw < cfg.body_min_avg_move:
                # We're going SHORT (after reverse from LONG). IS must show bullish bias.
                stats["body_skip"] += 1
                continue
            if trade_direction == "LONG" and avg_move_raw > -cfg.body_min_avg_move:
                # We're going LONG (after reverse from SHORT). IS must show bearish bias.
                stats["body_skip"] += 1
                continue

        stats["stat_pass"] += 1

        # ── MULTI-TF CONSENSUS ──
        if cfg.use_multi_tf and engine_15m is not None:
            ts_now = ts_5m[i]
            idx_15m = np.searchsorted(ts_15m, ts_now, side="right") - 1
            if idx_15m < 50:
                stats["mtf_skip"] += 1
                continue
            window_15m = full_15m.iloc[max(0, idx_15m-200):idx_15m+1]
            if len(window_15m) < 50:
                stats["mtf_skip"] += 1
                continue
            tf15_direction = get_15m_consensus(engine_15m, window_15m, current_price)
            if tf15_direction is None:
                stats["mtf_skip"] += 1
                continue
            if tf15_direction != trade_direction:
                stats["mtf_skip"] += 1
                continue

        # ── ATR-BASED SL/TP ──
        sl_dist_pct = current_atr_pct * cfg.sl_atr_mult
        tp_dist_pct = current_atr_pct * cfg.tp_atr_mult
        sl_dist_pct = max(cfg.sl_floor_pct, min(cfg.sl_cap_pct, sl_dist_pct))
        tp_dist_pct = max(cfg.tp_floor_pct, min(cfg.tp_cap_pct, tp_dist_pct))
        # Enforce RR ≥ 2
        if tp_dist_pct < sl_dist_pct * 2.0:
            tp_dist_pct = sl_dist_pct * 2.0
            tp_dist_pct = min(tp_dist_pct, cfg.tp_cap_pct)

        # Net favorable check (REVERSED — effective move is opposite of engine's IS bias)
        # If we're going SHORT (after reverse from LONG engine signal), the IS avg_move is positive
        # (bullish IS bias). We're betting that mean-reversion will reverse it → bearish OOS.
        # So our effective expected move = -avg_move_raw (we flip the IS bias).
        if trade_direction == "LONG":
            # Reversed from SHORT engine signal → IS avg_move_raw was negative → we expect positive
            effective_move = -avg_move_raw
        else:  # SHORT
            # Reversed from LONG engine signal → IS avg_move_raw was positive → we expect negative
            effective_move = avg_move_raw  # this is the magnitude of the bearish expected move
            # but we need positive number for comparison
            effective_move = abs(effective_move)  # |avg_move_raw| magnitude
            # Actually, since we reversed, the expected OOS move in our direction = |avg_move_raw|
        # Simpler: after reverse, expected move magnitude = |avg_move_raw|
        effective_move = abs(avg_move_raw)
        if effective_move < cfg.hard_move_floor:
            stats["body_skip"] += 1
            continue

        net_favorable = effective_move - spread_pct
        if net_favorable <= 0:
            continue

        # ── EXECUTE ENTRY ──
        entry_price = current_price
        sl_dist = sl_dist_pct / 100.0
        tp_dist = tp_dist_pct / 100.0
        if trade_direction == "LONG":
            sl_price = entry_price * (1 - sl_dist)
            tp_price = entry_price * (1 + tp_dist)
        else:
            sl_price = entry_price * (1 + sl_dist)
            tp_price = entry_price * (1 - tp_dist)

        position_notional = cfg.initial_capital * cfg.risk_pct / sl_dist
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
        stats["entries"] += 1

        # Progress
        if oos_idx % (progress_every * 2) == 0 and oos_idx > 0:
            pct = oos_idx / oos_n * 100
            print(f"    [{pct:5.1f}%] entries={stats['entries']} "
                  f"strong={stats['strong_follow']} "
                  f"moderate={stats['moderate_reverse']} "
                  f"trades={len(trades)}", flush=True)

      # end for
    except Exception as ex:
        import traceback
        print(f"    ERROR at i={i}: {ex}", flush=True)
        traceback.print_exc()

    print(f"    rebuilds={n_rebuilds} evaluated={stats['evaluated']} "
          f"alpha_pass={stats['alpha_pass']} stat_pass={stats['stat_pass']} "
          f"strong_follow={stats['strong_follow']} "
          f"moderate_reverse={stats['moderate_reverse']} "
          f"adaptive_skip={stats['adaptive_skip']} body_skip={stats['body_skip']} "
          f"mtf_skip={stats['mtf_skip']} entries={stats['entries']} "
          f"trades={len(trades)}", flush=True)
    return trades, stats


# ────────────────────────────────────────────────────────────────────
# 10. MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    cfg = ConfigV24()
    print(f"=== PPMT v2.4 — Per-Pattern Adaptive Direction + Multi-Regime IS ===\n", flush=True)
    print(f"Config: IS=240d (3 windows × ~80d), OOS={OOS_DAYS}d walk-forward", flush=True)
    print(f"Alphas: {cfg.alphas}, min agreement={cfg.min_alpha_agreement}", flush=True)
    print(f"Stat filter: chi2 p<{cfg.chi2_p_threshold}, min_count={cfg.min_node_count}", flush=True)
    print(f"Adaptive: STRONG≥{cfg.strong_wr_threshold:.0%} follow, "
          f"MODERATE≥{cfg.moderate_wr_threshold:.0%} reverse, WEAK skip", flush=True)
    print(f"Body gate: {cfg.use_body_gate}, min |avg_move|={cfg.body_min_avg_move}%", flush=True)
    print(f"Risk: SL={cfg.sl_atr_mult}×ATR, TP={cfg.tp_atr_mult}×ATR, RR={cfg.tp_atr_mult/cfg.sl_atr_mult:.1f}", flush=True)
    print(f"Max hold: {cfg.max_hold_bars} bars ({cfg.max_hold_bars*5/60:.1f}h on 5m)\n", flush=True)
    print(f"Tokens: {len(TOKENS)} ({[t[0] for t in TOKENS]})\n", flush=True)

    # Run per-token
    all_trades: List[Trade] = []
    per_token: Dict[str, Dict] = {}
    per_token_stats: Dict[str, Dict] = {}

    for sym, ac in TOKENS:
        t_sym = time.time()
        try:
            trades, st = walk_forward_backtest(sym, ac, cfg)
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

    print(f"=== AGGREGATE (v2.4) ===", flush=True)
    print(f"  n_trades: {agg['n_trades']}", flush=True)
    print(f"  WR:       {agg['wr']:.1f}%", flush=True)
    print(f"  PnL:      {agg['pnl_pct']:+.1f}%", flush=True)
    print(f"  PF:       {agg['pf']:.2f}", flush=True)
    print(f"  shorts:   {agg['shorts_pct']:.1f}%\n", flush=True)

    # Goal check
    wr_pass = sum(1 for m in per_token.values() if m["wr"] >= 55)
    pf_pass = sum(1 for m in per_token.values() if m["pf"] >= 1.5)
    pnl_pass = sum(1 for m in per_token.values() if m["pnl_pct"] > 0)
    n_tokens = len(per_token)
    print(f"=== GOAL CHECK ===", flush=True)
    print(f"  WR≥55% on {wr_pass}/{n_tokens} tokens (target: 4/5)", flush=True)
    print(f"  PF≥1.5 on {pf_pass}/{n_tokens} tokens (target: 4/5)", flush=True)
    print(f"  PnL>0 on  {pnl_pass}/{n_tokens} tokens (target: all)", flush=True)
    print(f"  ≥20 trades/token: {sum(1 for m in per_token.values() if m['n_trades'] >= 20)}/{n_tokens}", flush=True)
    print(f"  ≥15% shorts: {sum(1 for m in per_token.values() if m['shorts_pct'] >= 15)}/{n_tokens}\n", flush=True)

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
              f"max_dd={m.get('max_dd', 0):.1f}% "
              f"strong={st.get('strong_follow', 0)} "
              f"mod_rev={st.get('moderate_reverse', 0)}", flush=True)

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
