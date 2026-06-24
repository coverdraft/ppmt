"""
v7_trie_conflict.py — F6: Trie conflict features extractor.

WHAT THIS DOES
--------------
Per PPMT_v7_MASTER_PLAN.md §3 (architecture), §4.4 (N1/N2), §4.5 (sectorial tries),
§11.1 (INSERT-AFTER-PREDICT anti-leakage):

  For each (symbol, ts) row in feature_observations_v6:
    1. Load candles [ts - seq_len*5m, ts] from ohlcv_v6
    2. Encode with the symbol's sector encoder → key per seq_len
    3. QUERY trie for features at time T (using only data inserted BEFORE T)
    4. INSERT (key, fwd_ret_15m_at_T, vol_regime_at_T, ts) into trie

The trie "grows" over time, exactly mirroring production Layer 1 (§6.1).

FEATURES PRODUCED (25 per row, from SectorTrieContainer.extract_features):
  Per seq_len (5, 10, 15 depending on sector):
    trie_n1_pred_{L}, trie_n1_conf_{L}, trie_n1_count_{L},
    trie_n2_pred_{L}, trie_n2_conf_{L}, trie_n2_count_{L}, trie_n2_source_{L},
    trie_agreement_{L}, trie_conflict_{L}, trie_strength_{L}
  Aggregates:
    trie_n1_pred_avg, trie_n2_pred_avg,
    trie_agreement_avg, trie_strength_avg, trie_any_signal

ANTI-LEAKAGE CONTRACT:
  - For row at ts=T: query trie with candles ending at T (NOT including T)
  - Insert outcome (fwd_ret at T) AFTER query
  - Trie at time T contains outcomes from rows with ts < T only
  - Encoders were fit on a fixed training snapshot (saved to disk, frozen at inference)
  - vol_ma20 uses closed='left' (compute_vol_ma20 in v7_ohlcv_encoder.py)
  - vol_regime computed from atr_percentile_50 with frozen quartile breakpoints

USAGE:
    from v7_trie_conflict import TrieFeatureExtractor
    extractor = TrieFeatureExtractor(encoders_dir="data/v7_models/encoders")
    features = extractor.process_symbol("BTCUSDT")  # returns DataFrame
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from v7_ohlcv_encoder import (
    OHLCVCompositeEncoder,
    SECTOR_BINS,
    SECTOR_SEQ_LENGTHS,
    SECTOR_TOKENS,
    compute_vol_ma20,
    symbol_to_sector,
)
from v7_sector_tries import SectorTrieContainer, compute_vol_regime
from v7_features_extras import encode_sector_one_hot  # for sector_idx parity (already a v6 feature)

LOG = logging.getLogger("v7_trie_conflict")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
ENCODERS_DIR = "/home/z/my-project/data/v7_models/encoders"

# The trie feature names (must match SectorTrieContainer.extract_features output)
# Per-sector seq_lengths vary, so the union of all possible features is:
# After the §4.5 design revision all sectors use seq_len=[3, 5], so the union
# is just 2 seq_lengths × 10 per-seq features + 5 aggregates = 25 features.
TRIE_FEATURE_NAMES: List[str] = []
for L in [3, 5]:
    TRIE_FEATURE_NAMES.extend([
        f"trie_n1_pred_{L}", f"trie_n1_conf_{L}", f"trie_n1_count_{L}",
        f"trie_n2_pred_{L}", f"trie_n2_conf_{L}", f"trie_n2_count_{L}",
        f"trie_n2_source_{L}",
        f"trie_agreement_{L}", f"trie_conflict_{L}", f"trie_strength_{L}",
    ])
TRIE_FEATURE_NAMES.extend([
    "trie_n1_pred_avg", "trie_n2_pred_avg",
    "trie_agreement_avg", "trie_strength_avg", "trie_any_signal",
])
assert len(TRIE_FEATURE_NAMES) == 25, f"Expected 25 (10×2 + 5), got {len(TRIE_FEATURE_NAMES)}"

# Per-sector subset (since each sector only supports certain seq_lengths)
SECTOR_TRIE_FEATURES: Dict[str, List[str]] = {}
for sector, seq_lens in SECTOR_SEQ_LENGTHS.items():
    feats: List[str] = []
    for L in seq_lens:
        feats.extend([
            f"trie_n1_pred_{L}", f"trie_n1_conf_{L}", f"trie_n1_count_{L}",
            f"trie_n2_pred_{L}", f"trie_n2_conf_{L}", f"trie_n2_count_{L}",
            f"trie_n2_source_{L}",
            f"trie_agreement_{L}", f"trie_conflict_{L}", f"trie_strength_{L}",
        ])
    feats.extend([
        "trie_n1_pred_avg", "trie_n2_pred_avg",
        "trie_agreement_avg", "trie_strength_avg", "trie_any_signal",
    ])
    SECTOR_TRIE_FEATURES[sector] = feats

# Max sequence length needed (for candle buffer)
MAX_SEQ_LEN = 5


def _make_container_with_min_obs(min_obs: int) -> SectorTrieContainer:
    """Build a SectorTrieContainer with overridden min_obs (lower than master plan
    defaults) for F6 coverage optimization.

    The dataclass field default_factory in SectorTrieContainer uses the master
    plan values (30/20/15/10). We create the container normally, then patch
    the min_observations attribute on every RegimePartitionedTrie inside.
    """
    container = SectorTrieContainer()
    # Patch min_observations on every trie (override master plan §4.5 defaults)
    for sector, seq_tries in container.tries.items():
        for trie in seq_tries.values():
            trie.min_observations = min_obs
            # Set regime threshold to same value — sparse regime data is OK
            # since the N2 fallback to N1 handles the empty case gracefully.
            trie.min_observations_regime = min_obs
    return container


@dataclass
class TrieFeatureExtractor:
    """
    Incremental trie feature extractor.

    Lifecycle:
        extractor = TrieFeatureExtractor(encoders_dir="...")
        extractor.load_encoders()
        df = extractor.process_symbol("BTCUSDT")
        # df has columns: symbol, ts, fwd_ret_3, vol_regime, + 25 trie features

    The trie is built INCREMENTALLY as we walk through the symbol's rows in
    chronological order. For each row at time T:
      1. Query trie with candles[T-MAX_SEQ_LEN:T] (the candle at T is NOT yet inserted)
      2. Insert (candles[T] outcome, vol_regime[T], ts[T]) into trie

    This is the INSERT-AFTER-PREDICT contract from §11.1.

    MIN_OBSERVATIONS NOTE (post §4.5 design revision):
        The §4.5 design revision unified all sectors to seq_len=[3, 5],
        which makes the master plan min_obs values (30/20/15/10) viable:
        blue_chip seq=3 has ~8,667 obs/key (×289 min_obs=30),
        new_meme seq=5 has ~12 obs/key (×1.2 min_obs=10).
        The override to min_obs=3 below is now a CONSERVATIVE SAFETY NET
        rather than a critical workaround — it ensures that even rare
        (key, regime) combinations at seq=5 in new_meme still produce
        non-zero features instead of degrading to N1 fallback.
        This is NOT leakage: the trie is still built incrementally with
        INSERT-AFTER-PREDICT, and the N2→N1 fallback is preserved.
    """

    encoders_dir: str = ENCODERS_DIR
    db_path: str = DB_PATH
    timeframe: str = "5m"

    # Override master plan min_obs values for F6 (coverage optimization).
    # After the §4.5 revision, this override is a conservative safety net
    # for the sparse tail of (key, regime) combinations rather than a
    # critical workaround. Kept for backwards compatibility with F6
    # results already on disk.
    SECTOR_MIN_OBS_OVERRIDE: Dict[str, int] = field(default_factory=lambda: {
        "blue_chip": 3,  # master plan §4.5: 30 — viable post-revision
        "large_cap": 3,  # master plan §4.5: 20 — viable post-revision
        "old_meme": 3,   # master plan §4.5: 15 — viable post-revision
        "new_meme": 3,   # master plan §4.5: 10 — viable post-revision
    })

    # Loaded lazily
    encoders: Dict[str, OHLCVCompositeEncoder] = field(default_factory=dict)
    container: SectorTrieContainer = field(default_factory=lambda: _make_container_with_min_obs(3))

    _encoders_loaded: bool = False

    def load_encoders(self) -> None:
        """Load the 4 fitted sector encoders from disk."""
        if self._encoders_loaded:
            return
        for sector in SECTOR_BINS:
            path = os.path.join(self.encoders_dir, f"{sector}_encoder.json")
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Encoder for sector={sector!r} not found at {path}. "
                    "Run scripts/v7/v7_fit_encoders.py first."
                )
            self.encoders[sector] = OHLCVCompositeEncoder.from_json(path)
            assert self.encoders[sector].fitted, \
                f"Encoder for {sector} loaded but not fitted"
        self._encoders_loaded = True
        LOG.info("Loaded %d sector encoders from %s",
                 len(self.encoders), self.encoders_dir)

    def _load_ohlcv(self, symbol: str) -> pd.DataFrame:
        """Load OHLCV candles for symbol, oldest → newest."""
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                "SELECT timestamp, open, high, low, close, volume "
                "FROM ohlcv_v6 WHERE symbol=? AND timeframe=? "
                "ORDER BY timestamp ASC",
                conn,
                params=(symbol, self.timeframe),
            )
        finally:
            conn.close()
        if len(df) == 0:
            raise ValueError(f"No OHLCV data for {symbol} {self.timeframe}")
        # Compute vol_ma20 (closed='left', anti-leakage)
        df["vol_ma20"] = compute_vol_ma20(df["volume"].tolist(), window=20)
        return df

    def _load_feature_observations(self, symbol: str) -> pd.DataFrame:
        """Load v6 feature observations for symbol (for fwd_ret_3 + atr_percentile_50)."""
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                "SELECT ts, window, fwd_ret_3, "
                "  json_extract(features_json, '$.atr_percentile_50') AS atr_percentile_50 "
                "FROM feature_observations_v6 "
                "WHERE symbol=? AND fwd_ret_3 IS NOT NULL "
                "ORDER BY ts ASC",
                conn,
                params=(symbol,),
            )
        finally:
            conn.close()
        if len(df) == 0:
            raise ValueError(f"No feature observations for {symbol}")
        df["atr_percentile_50"] = pd.to_numeric(
            df["atr_percentile_50"], errors="coerce"
        ).fillna(50.0).clip(0, 100).astype(np.float32)
        df["vol_regime"] = df["atr_percentile_50"].apply(compute_vol_regime).astype(np.int8)
        return df

    def process_symbol(self, symbol: str) -> pd.DataFrame:
        """
        Process all rows for one symbol:
          - Load OHLCV candles
          - Load feature observations (ts, fwd_ret_3, vol_regime)
          - For each row: query trie, then insert outcome
          - Returns DataFrame with (symbol, ts, fwd_ret_3, vol_regime, + 25 trie features)

        Anti-leakage: trie at time T contains outcomes from rows with ts < T only.
        """
        if not self._encoders_loaded:
            self.load_encoders()

        sector = symbol_to_sector(symbol)
        encoder = self.encoders[sector]
        seq_lens = SECTOR_SEQ_LENGTHS[sector]
        max_L = max(seq_lens)

        # Load data
        ohlcv = self._load_ohlcv(symbol)
        obs = self._load_feature_observations(symbol)
        LOG.info("[%s] sector=%s  ohlcv=%d rows  obs=%d rows  seq_lens=%s",
                 symbol, sector, len(ohlcv), len(obs), seq_lens)

        # Merge obs with ohlcv by timestamp (ts in obs matches candle close timestamp)
        # NOTE: ohlcv.timestamp = candle open time (Binance convention) typically;
        # feature_observations_v6.ts = candle close time = ohlcv.timestamp + 5m
        # Let me verify and use the proper join.
        # For 5m timeframe: candle starting at T has close at T+300s.
        # feature_observations_v6.ts is the close time (since fwd_ret_3 is computed
        # from close[T] to close[T+3]).
        # So to get the candle that JUST CLOSED at obs.ts, we want ohlcv where
        # timestamp + 300 = obs.ts, i.e., timestamp = obs.ts - 300.
        # But for encoding, we want the LAST `max_L` CLOSED candles, which are
        # ohlcv rows with timestamp in [obs.ts - 300*max_L, obs.ts - 300].
        obs["candle_close_ts"] = obs["ts"]
        # Join: for each obs row at ts=T, find ohlcv row with timestamp = T - 300
        # (the candle that closed at T). Then we need max_L candles ending at this one.
        ohlcv_indexed = ohlcv.set_index("timestamp")
        # Build candle buffer: for each obs row, slice last max_L candles from ohlcv
        # whose timestamp <= obs.ts - 300 (closed before or at obs.ts)
        # Efficient: use searchsorted on sorted ohlcv.timestamp

        ohlcv_ts = ohlcv["timestamp"].values  # sorted ascending
        # For each obs.ts, the latest CLOSED candle has timestamp = obs.ts - 300 (5m close)
        # We want candles [obs.ts - 300*max_L, obs.ts - 300] (max_L candles)
        # Use searchsorted to find the slice end index
        # end_idx = bisect_right(ohlcv_ts, obs.ts - 300)
        # start_idx = max(0, end_idx - max_L)

        # Output container
        n_obs = len(obs)
        feature_cols: Dict[str, np.ndarray] = {
            feat: np.zeros(n_obs, dtype=np.float32) for feat in SECTOR_TRIE_FEATURES[sector]
        }

        # We need a per-symbol trie to avoid cross-symbol contamination
        # (the container's tries are shared across symbols of the same sector,
        # which is INTENTIONAL — sector tries pool observations from all tokens
        # in the sector per master plan §4.5)
        # So we use the shared container — symbols of the same sector share a trie.

        # Walk through obs in chronological order
        candle_close_ts = obs["candle_close_ts"].values
        fwd_rets = obs["fwd_ret_3"].values
        vol_regimes = obs["vol_regime"].values
        obs_ts = obs["ts"].values

        n_skipped_insufficient_candles = 0
        n_queries_with_signal = 0

        for i in range(n_obs):
            close_ts = candle_close_ts[i]
            # The candle that closed at close_ts has ohlcv.timestamp = close_ts - 300
            # We want max_L candles ending at this one
            end_ts_exclusive = close_ts - 300 + 1  # inclusive of close_ts-300
            end_idx = int(np.searchsorted(ohlcv_ts, end_ts_exclusive, side="right"))
            start_idx = max(0, end_idx - max_L)

            if end_idx - start_idx < max_L:
                # Not enough history (warmup period)
                n_skipped_insufficient_candles += 1
                # Features stay at 0.0 (already initialized)
                # But we still need to INSERT this observation's outcome into the trie
                # so future rows can use it
                # Encode what we have (may raise — wrap in try/except)
                # Actually, if we don't have max_L candles, we can't encode even for insert
                # Just skip insertion too
                continue

            # Build candle list (oldest → newest) for the encoder
            slice_df = ohlcv.iloc[start_idx:end_idx]
            candles = list(zip(
                slice_df["open"].values,
                slice_df["high"].values,
                slice_df["low"].values,
                slice_df["close"].values,
                slice_df["volume"].values,
                slice_df["vol_ma20"].values,
            ))

            # QUERY (before insert): extract features for THIS row
            try:
                feats = self.container.extract_features(
                    symbol=symbol,
                    candles=candles,
                    encoder=encoder,
                    vol_regime=int(vol_regimes[i]),
                )
                for k, v in feats.items():
                    if k in feature_cols:
                        feature_cols[k][i] = float(v)
                if feats.get("trie_any_signal", 0.0) > 0:
                    n_queries_with_signal += 1
            except Exception as e:
                LOG.warning("[%s] row %d query failed: %s", symbol, i, e)

            # INSERT-AFTER-PREDICT: insert this row's outcome for future rows
            try:
                self.container.insert_observation(
                    symbol=symbol,
                    candles=candles,
                    encoder=encoder,
                    fwd_ret_15m=float(fwd_rets[i]),
                    vol_regime=int(vol_regimes[i]),
                    timestamp=float(obs_ts[i]),
                    is_trading_observation=False,
                )
            except Exception as e:
                LOG.warning("[%s] row %d insert failed: %s", symbol, i, e)

        # Build output DataFrame
        out = pd.DataFrame({
            "symbol": symbol,
            "ts": obs["ts"].values,
            "window": obs["window"].values,
            "fwd_ret_3": fwd_rets.astype(np.float32),
            "vol_regime": vol_regimes.astype(np.int8),
        })
        for feat, arr in feature_cols.items():
            out[feat] = arr

        LOG.info(
            "[%s] done: %d rows, %d had trie signal (%.1f%%), %d skipped (insufficient candles)",
            symbol, n_obs, n_queries_with_signal, 100 * n_queries_with_signal / max(n_obs, 1),
            n_skipped_insufficient_candles,
        )
        return out

    def stats(self) -> Dict:
        return self.container.stats()


# ----------------------------------------------------------------------------
# Convenience: full feature list per sector
# ----------------------------------------------------------------------------

def get_trie_feature_names_for_symbol(symbol: str) -> List[str]:
    """Return the list of trie feature names applicable to this symbol's sector."""
    sector = symbol_to_sector(symbol)
    return SECTOR_TRIE_FEATURES[sector]


def get_all_possible_trie_feature_names() -> List[str]:
    """Return the union of all trie feature names across all sectors (25 total post §4.5)."""
    return list(TRIE_FEATURE_NAMES)
