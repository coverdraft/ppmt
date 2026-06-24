"""
PPMT v7 — Sectorial Tries + RegimePartitionedTrie (F3)
========================================================

Builds the 4 sectorial tries (blue_chip, large_cap, old_meme, new_meme)
on top of:
  - F1: TrieNodeV6Metadata (per-node regression stats)
  - F2: OHLCVCompositeEncoder (per-candle symbol → trie key)

Key design decisions (PPMT_v7_MASTER_PLAN.md §4.4, §4.5):
  - 4 sectors × allowed seq_lengths × 4 vol_regimes = 64 sub-tries max
  - Two levels (N1 + N2), not four (audit: N3/N4 redundant with N1/N2)
    * N1: unconditional mean(fwd_ret_15m) of nodes matching the key
    * N2: same but only using observations from current vol_regime
  - N2 fallback: if N2 node has < min_obs, return N1 prediction
    (lesson from v2.1 Config F: "N4=0% — sparse N4 data hurts more than helps")
  - INSERT-AFTER-PREDICT: trie insertion happens at T+15m, never before
    (caller enforces, see §11.1)

Architecture:
  SectorTrieContainer
    ├── blue_chip:  dict[seq_len -> RegimePartitionedTrie]
    ├── large_cap:  dict[seq_len -> RegimePartitionedTrie]
    ├── old_meme:   dict[seq_len -> RegimePartitionedTrie]
    └── new_meme:   dict[seq_len -> RegimePartitionedTrie]

  RegimePartitionedTrie
    ├── global_trie:   dict[key -> TrieNodeV6Metadata]  (N1 source)
    └── regime_tries:  dict[vol_regime -> dict[key -> TrieNodeV6Metadata]] (N2 source)

  Each trie is a flat dict, NOT a nested tree. Because:
    - All keys are fixed-length strings (seq_len chars from 'a'..'z')
    - We only ever do exact-match lookup (no need to traverse children)
    - Nested trie tree is memory overhead with no benefit for fixed-length keys

Vol_regime computation (caller provides):
  0 = low vol (ATR% < 25th percentile)
  1 = normal vol (25-50th)
  2 = high vol (50-75th)
  3 = extreme vol (> 75th)
  (Computed by F4 from atr_percentile_50 feature; here we just consume it.)

Persistence:
  Container saves to data/v7_models/tries/{sector}_{seq_len}.json
  Each file is a JSON dict of {key: metadata_dict}.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Same dir
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v7_trie_metadata import TrieNodeV6Metadata
from v7_ohlcv_encoder import (
    OHLCVCompositeEncoder,
    SECTOR_BINS,
    SECTOR_SEQ_LENGTHS,
    SECTOR_TOKENS,
    DEFAULT_WEIGHTS,
    symbol_to_sector,
)


# ---------------------------------------------------------------------------
# RegimePartitionedTrie (one per sector × seq_len)
# ---------------------------------------------------------------------------

@dataclass
class RegimePartitionedTrie:
    """
    A trie partitioned by vol_regime (0-3).

    Maintains:
      - global_trie: dict[key -> TrieNodeV6Metadata]
          Used for N1 predictions (unconditional mean).
      - regime_tries: dict[vol_regime -> dict[key -> TrieNodeV6Metadata]]
          Used for N2 predictions (regime-conditional mean).

    Insertion: a single observation updates BOTH the global trie and
    the regime-specific trie. This doubles storage but makes N1 and N2
    queries O(1) dict lookups.

    The metadata in regime_tries[r] is INDEPENDENT from global_trie —
    they track separate sums. This is intentional: N2 should reflect
    regime-conditional distribution, not be a copy of N1.
    """

    sector: str
    seq_len: int
    min_observations: int = 3  # below this, prediction returns 0.0
    min_observations_regime: int = 3  # N2 fallback threshold
    max_nodes: int = 100_000  # LRU eviction threshold

    # Internal storage
    global_trie: Dict[str, TrieNodeV6Metadata] = field(default_factory=dict)
    regime_tries: Dict[int, Dict[str, TrieNodeV6Metadata]] = field(default_factory=dict)

    # Bookkeeping
    _insert_count: int = 0
    _prune_count: int = 0
    _last_prune_at: int = 0

    PRUNE_EVERY_N_INSERTS: int = 1000

    def __post_init__(self) -> None:
        # Initialize regime sub-tries lazily — only create when first obs arrives
        if self.sector not in SECTOR_BINS:
            raise ValueError(f"Unknown sector {self.sector!r}")
        if self.seq_len not in SECTOR_SEQ_LENGTHS[self.sector]:
            raise ValueError(
                f"seq_len={self.seq_len} not allowed for sector={self.sector!r}. "
                f"Allowed: {SECTOR_SEQ_LENGTHS[self.sector]}"
            )

    # ------------- insert -------------

    def insert(
        self,
        key: str,
        fwd_ret_15m: float,
        vol_regime: int,
        timestamp: float,
        is_trading_observation: bool = False,
    ) -> None:
        """
        Insert one observation.

        CRITICAL (ANTI-LEAKAGE): caller MUST ensure this is called AFTER
        the prediction for this key was made. Standard pattern is to
        insert at T+15m (when fwd_ret_15m becomes known). See
        PPMT_v7_MASTER_PLAN.md §11.1.

        Args:
            key: trie key string of length seq_len
            fwd_ret_15m: forward 15m return (the regression target)
            vol_regime: 0-3
            timestamp: epoch seconds of the candle close
            is_trading_observation: True if this obs was used in a live
                trading decision (gates trust via metadata.trading_observations)
        """
        if not (0 <= vol_regime <= 3):
            raise ValueError(f"vol_regime must be 0-3, got {vol_regime}")
        if len(key) != self.seq_len:
            raise ValueError(
                f"key length {len(key)} != seq_len {self.seq_len}"
            )

        # --- Update global trie (N1 source) ---
        if key not in self.global_trie:
            self.global_trie[key] = TrieNodeV6Metadata()
        self.global_trie[key].update_from_observation(
            fwd_ret_15m=fwd_ret_15m,
            vol_regime=vol_regime,
            timestamp=timestamp,
            is_trading_observation=is_trading_observation,
        )

        # --- Update regime-specific trie (N2 source) ---
        if vol_regime not in self.regime_tries:
            self.regime_tries[vol_regime] = {}
        if key not in self.regime_tries[vol_regime]:
            self.regime_tries[vol_regime][key] = TrieNodeV6Metadata()
        self.regime_tries[vol_regime][key].update_from_observation(
            fwd_ret_15m=fwd_ret_15m,
            vol_regime=vol_regime,  # same regime (redundant but consistent)
            timestamp=timestamp,
            is_trading_observation=is_trading_observation,
        )

        # --- Periodic prune ---
        self._insert_count += 1
        if self._insert_count - self._last_prune_at >= self.PRUNE_EVERY_N_INSERTS:
            self.prune()
            self._last_prune_at = self._insert_count

    # ------------- query N1 (unconditional) -------------

    def query_n1(self, key: str) -> Tuple[float, float, int]:
        """
        N1 query: unconditional prediction.

        Returns:
            (prediction, confidence, count)
            - prediction: mean fwd_ret_15m if count >= min_observations, else 0.0
            - confidence: in [0, 1]
            - count: number of observations for this key
        """
        node = self.global_trie.get(key)
        if node is None or node.historical_count < self.min_observations:
            return (0.0, 0.0, 0 if node is None else node.historical_count)
        return (node.prediction, node.confidence, node.historical_count)

    # ------------- query N2 (regime-conditional) -------------

    def query_n2(
        self,
        key: str,
        vol_regime: int,
        fallback_to_n1: bool = True,
    ) -> Tuple[float, float, int, str]:
        """
        N2 query: regime-conditional prediction.

        If the regime-specific node has < min_observations_regime
        observations, fall back to N1 prediction (lesson from v2.1
        Config F: sparse N2/N4 data hurts more than it helps).

        Args:
            key: trie key
            vol_regime: 0-3
            fallback_to_n1: if True (default), return N1 prediction when
                N2 node is too sparse. If False, return (0.0, 0.0, 0, 'n2_sparse').

        Returns:
            (prediction, confidence, count, source)
            - source: 'n2' if regime-conditional, 'n1_fallback' if fell back,
                      'n2_empty' if no data at all
        """
        if not (0 <= vol_regime <= 3):
            raise ValueError(f"vol_regime must be 0-3, got {vol_regime}")

        regime_trie = self.regime_tries.get(vol_regime, {})
        node = regime_trie.get(key)

        if node is None or node.historical_count < self.min_observations_regime:
            # N2 too sparse — fall back
            if fallback_to_n1:
                pred, conf, cnt = self.query_n1(key)
                if cnt > 0:
                    return (pred, conf, cnt, "n1_fallback")
            return (0.0, 0.0, 0, "n2_empty")

        return (node.prediction, node.confidence, node.historical_count, "n2")

    # ------------- query all (utility for feature engineering) -------------

    def query_all(
        self,
        key: str,
        vol_regime: int,
    ) -> Dict[str, Any]:
        """
        Return all trie features for a (key, vol_regime) pair.

        Used by F6 (trie conflict features) to compute trie_agreement,
        trie_conflict, trie_strength.

        Returns a dict with keys:
            n1_pred, n1_conf, n1_count,
            n2_pred, n2_conf, n2_count, n2_source,
            agreement (in [0, 1] — 1 if N1 and N2 agree on sign and magnitude),
            conflict (in [0, 1] — 1 if they disagree on sign),
            strength (in [0, 1] — combined confidence)
        """
        n1_pred, n1_conf, n1_count = self.query_n1(key)
        n2_pred, n2_conf, n2_count, n2_source = self.query_n2(key, vol_regime)

        # Agreement: how close N1 and N2 predictions are (in [0, 1])
        if n1_count == 0 or n2_count == 0:
            agreement = 0.0
        else:
            # 1 - |n1 - n2| / (|n1| + |n2| + epsilon)
            denom = abs(n1_pred) + abs(n2_pred) + 1e-6
            agreement = 1.0 - abs(n1_pred - n2_pred) / denom
            agreement = max(0.0, min(1.0, agreement))

        # Conflict: 1 if signs differ (treating 0 as "no signal" — conflicts
        # with any non-zero opposite prediction, scaled by magnitude)
        if n1_count == 0 or n2_count == 0:
            conflict = 0.0
        elif (n1_pred > 0 and n2_pred < 0) or (n1_pred < 0 and n2_pred > 0):
            # Opposite signs — clear conflict
            conflict = min(1.0, abs(n1_pred - n2_pred) / 1.0)  # 1.0% = full conflict
        elif n1_pred == 0.0 and n2_pred != 0.0:
            # N1 neutral, N2 has signal — partial conflict (uncertainty)
            conflict = min(1.0, abs(n2_pred) / 1.0) * 0.5
        elif n2_pred == 0.0 and n1_pred != 0.0:
            # N2 neutral, N1 has signal — partial conflict
            conflict = min(1.0, abs(n1_pred) / 1.0) * 0.5
        else:
            conflict = 0.0

        # Strength: combined confidence
        if n1_count == 0:
            strength = 0.0
        elif n2_source == "n2":
            strength = (n1_conf + n2_conf) / 2.0
        else:
            strength = n1_conf * 0.7  # penalize fallback

        return {
            "n1_pred": n1_pred,
            "n1_conf": n1_conf,
            "n1_count": n1_count,
            "n2_pred": n2_pred,
            "n2_conf": n2_conf,
            "n2_count": n2_count,
            "n2_source": n2_source,
            "agreement": agreement,
            "conflict": conflict,
            "strength": strength,
        }

    # ------------- prune -------------

    def prune(self, min_count: Optional[int] = None) -> int:
        """
        Remove nodes with fewer than `min_count` observations.

        Default: min_count = max(1, min_observations - 1) — keeps nodes
        that are 1 observation away from being trustworthy.

        Returns: number of nodes pruned.
        """
        if min_count is None:
            min_count = max(1, self.min_observations - 1)

        pruned = 0

        # Prune global trie
        empty_keys = []
        for k, node in self.global_trie.items():
            if node.historical_count < min_count:
                empty_keys.append(k)
        for k in empty_keys:
            del self.global_trie[k]
            pruned += 1

        # Prune regime tries
        for regime, trie in self.regime_tries.items():
            empty_keys = []
            for k, node in trie.items():
                if node.historical_count < min_count:
                    empty_keys.append(k)
            for k in empty_keys:
                del trie[k]
                pruned += 1

        self._prune_count += pruned
        return pruned

    # ------------- LRU eviction -------------

    def evict_lru(self, target_size: Optional[int] = None) -> int:
        """
        If global_trie exceeds max_nodes, evict least-recently-updated
        nodes until below target_size.

        Returns: number of nodes evicted.
        """
        if target_size is None:
            target_size = int(self.max_nodes * 0.9)  # evict to 90% of cap

        if len(self.global_trie) <= self.max_nodes:
            return 0

        # Sort by last_observation_time ascending (oldest first)
        sorted_keys = sorted(
            self.global_trie.keys(),
            key=lambda k: self.global_trie[k].last_observation_time,
        )
        n_evict = len(self.global_trie) - target_size
        evicted = 0
        for k in sorted_keys[:n_evict]:
            del self.global_trie[k]
            # Also remove from regime tries
            for regime_trie in self.regime_tries.values():
                if k in regime_trie:
                    del regime_trie[k]
            evicted += 1
        return evicted

    # ------------- stats -------------

    def stats(self) -> Dict[str, Any]:
        """Return trie statistics for monitoring."""
        n_global = len(self.global_trie)
        n_regime = {r: len(t) for r, t in self.regime_tries.items()}
        total_obs = sum(n.historical_count for n in self.global_trie.values())
        avg_obs = total_obs / max(1, n_global)
        # Distribution of counts
        counts = sorted(n.historical_count for n in self.global_trie.values())
        if counts:
            median_obs = counts[len(counts) // 2]
            max_obs = counts[-1]
        else:
            median_obs = 0
            max_obs = 0
        return {
            "sector": self.sector,
            "seq_len": self.seq_len,
            "global_nodes": n_global,
            "regime_nodes": n_regime,
            "total_observations": total_obs,
            "avg_obs_per_node": round(avg_obs, 2),
            "median_obs_per_node": median_obs,
            "max_obs_per_node": max_obs,
            "insert_count": self._insert_count,
            "prune_count": self._prune_count,
        }

    # ------------- persistence -------------

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {
            "sector": self.sector,
            "seq_len": self.seq_len,
            "min_observations": self.min_observations,
            "min_observations_regime": self.min_observations_regime,
            "max_nodes": self.max_nodes,
            "global_trie": {k: v.to_dict() for k, v in self.global_trie.items()},
            "regime_tries": {
                str(r): {k: v.to_dict() for k, v in t.items()}
                for r, t in self.regime_tries.items()
            },
            "_insert_count": self._insert_count,
            "_prune_count": self._prune_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RegimePartitionedTrie":
        """Deserialize from dict."""
        trie = cls(
            sector=d["sector"],
            seq_len=d["seq_len"],
            min_observations=d.get("min_observations", 3),
            min_observations_regime=d.get("min_observations_regime", 3),
            max_nodes=d.get("max_nodes", 100_000),
        )
        trie.global_trie = {
            k: TrieNodeV6Metadata.from_dict(v)
            for k, v in d.get("global_trie", {}).items()
        }
        trie.regime_tries = {
            int(r): {k: TrieNodeV6Metadata.from_dict(v) for k, v in t.items()}
            for r, t in d.get("regime_tries", {}).items()
        }
        trie._insert_count = d.get("_insert_count", 0)
        trie._prune_count = d.get("_prune_count", 0)
        return trie

    def to_json(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def from_json(cls, path: str) -> "RegimePartitionedTrie":
        with open(path, "r") as f:
            d = json.load(f)
        return cls.from_dict(d)


# ---------------------------------------------------------------------------
# SectorTrieContainer — manages all 4 sectors × seq_lengths
# ---------------------------------------------------------------------------

@dataclass
class SectorTrieContainer:
    """
    Top-level container for all 4 sectorial tries.

    Layout:
        tries[sector][seq_len] = RegimePartitionedTrie

    Usage:
        container = SectorTrieContainer()
        container.build_from_encoders(encoders_dict)
        container.insert(symbol, candles, fwd_ret_15m, vol_regime, timestamp)
        features = container.extract_features(symbol, candles, vol_regime)
    """

    # min_observations per sector (from config/v7.yaml)
    SECTOR_MIN_OBS: Dict[str, int] = field(default_factory=lambda: {
        "blue_chip": 30,
        "large_cap": 20,
        "old_meme": 15,
        "new_meme": 10,
    })

    tries: Dict[str, Dict[int, RegimePartitionedTrie]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Initialize empty tries for all sectors × seq_lengths
        for sector, seq_lengths in SECTOR_SEQ_LENGTHS.items():
            self.tries[sector] = {}
            for seq_len in seq_lengths:
                self.tries[sector][seq_len] = RegimePartitionedTrie(
                    sector=sector,
                    seq_len=seq_len,
                    min_observations=self.SECTOR_MIN_OBS.get(sector, 10),
                    min_observations_regime=max(
                        3, self.SECTOR_MIN_OBS.get(sector, 10) // 3
                    ),
                )

    # ------------- insert (high-level) -------------

    def insert_observation(
        self,
        symbol: str,
        candles: List[Tuple[float, float, float, float, float, float]],
        encoder: OHLCVCompositeEncoder,
        fwd_ret_15m: float,
        vol_regime: int,
        timestamp: float,
        is_trading_observation: bool = False,
    ) -> int:
        """
        Insert one observation across all seq_lengths for the symbol's sector.

        Args:
            symbol: e.g., 'BTCUSDT' (will be normalized)
            candles: list of (o,h,l,c,v,vol_ma20) tuples, oldest→newest
            encoder: fitted OHLCVCompositeEncoder for this symbol's sector
            fwd_ret_15m: forward 15m return (regression target)
            vol_regime: 0-3
            timestamp: epoch seconds of the candle close
            is_trading_observation: True if used in live decision

        Returns:
            number of tries inserted into (typically = len(seq_lengths))
        """
        sector = symbol_to_sector(symbol)
        n_inserted = 0
        for seq_len, trie in self.tries[sector].items():
            if len(candles) < seq_len:
                continue
            try:
                key = encoder.encode_sequence(candles, seq_len=seq_len)
            except ValueError:
                continue
            trie.insert(
                key=key,
                fwd_ret_15m=fwd_ret_15m,
                vol_regime=vol_regime,
                timestamp=timestamp,
                is_trading_observation=is_trading_observation,
            )
            n_inserted += 1
        return n_inserted

    # ------------- extract features (high-level) -------------

    def extract_features(
        self,
        symbol: str,
        candles: List[Tuple[float, float, float, float, float, float]],
        encoder: OHLCVCompositeEncoder,
        vol_regime: int,
    ) -> Dict[str, float]:
        """
        Extract trie features for a single (symbol, candles, vol_regime).

        Returns a flat dict with keys:
            trie_n1_pred_{seq_len}
            trie_n1_conf_{seq_len}
            trie_n1_count_{seq_len}
            trie_n2_pred_{seq_len}
            trie_n2_conf_{seq_len}
            trie_n2_count_{seq_len}
            trie_n2_source_{seq_len}     (encoded as int: 0=empty, 1=fallback, 2=n2)
            trie_agreement_{seq_len}
            trie_conflict_{seq_len}
            trie_strength_{seq_len}
            trie_n1_pred_avg              (mean across seq_lengths)
            trie_n2_pred_avg
            trie_agreement_avg
            trie_strength_avg
            trie_any_signal               (1.0 if any seq_len has n1_count > 0)
        """
        sector = symbol_to_sector(symbol)
        features: Dict[str, float] = {}

        n1_preds = []
        n2_preds = []
        agreements = []
        strengths = []
        any_signal = 0.0

        for seq_len, trie in self.tries[sector].items():
            if len(candles) < seq_len:
                # Not enough history — zero features
                features[f"trie_n1_pred_{seq_len}"] = 0.0
                features[f"trie_n1_conf_{seq_len}"] = 0.0
                features[f"trie_n1_count_{seq_len}"] = 0.0
                features[f"trie_n2_pred_{seq_len}"] = 0.0
                features[f"trie_n2_conf_{seq_len}"] = 0.0
                features[f"trie_n2_count_{seq_len}"] = 0.0
                features[f"trie_n2_source_{seq_len}"] = 0.0
                features[f"trie_agreement_{seq_len}"] = 0.0
                features[f"trie_conflict_{seq_len}"] = 0.0
                features[f"trie_strength_{seq_len}"] = 0.0
                continue

            try:
                key = encoder.encode_sequence(candles, seq_len=seq_len)
            except ValueError:
                continue

            result = trie.query_all(key, vol_regime=vol_regime)

            features[f"trie_n1_pred_{seq_len}"] = result["n1_pred"]
            features[f"trie_n1_conf_{seq_len}"] = result["n1_conf"]
            features[f"trie_n1_count_{seq_len}"] = float(result["n1_count"])
            features[f"trie_n2_pred_{seq_len}"] = result["n2_pred"]
            features[f"trie_n2_conf_{seq_len}"] = result["n2_conf"]
            features[f"trie_n2_count_{seq_len}"] = float(result["n2_count"])
            # Encode source as int: 0=empty, 1=n1_fallback, 2=n2
            src_map = {"n2_empty": 0.0, "n1_fallback": 1.0, "n2": 2.0}
            features[f"trie_n2_source_{seq_len}"] = src_map.get(result["n2_source"], 0.0)
            features[f"trie_agreement_{seq_len}"] = result["agreement"]
            features[f"trie_conflict_{seq_len}"] = result["conflict"]
            features[f"trie_strength_{seq_len}"] = result["strength"]

            n1_preds.append(result["n1_pred"])
            n2_preds.append(result["n2_pred"])
            agreements.append(result["agreement"])
            strengths.append(result["strength"])
            if result["n1_count"] > 0:
                any_signal = 1.0

        # Aggregates
        n = max(1, len(n1_preds))
        features["trie_n1_pred_avg"] = sum(n1_preds) / n
        features["trie_n2_pred_avg"] = sum(n2_preds) / n
        features["trie_agreement_avg"] = sum(agreements) / n
        features["trie_strength_avg"] = sum(strengths) / n
        features["trie_any_signal"] = any_signal

        return features

    # ------------- stats -------------

    def stats(self) -> Dict[str, Any]:
        return {
            sector: {
                str(seq_len): trie.stats()
                for seq_len, trie in seq_tries.items()
            }
            for sector, seq_tries in self.tries.items()
        }

    # ------------- persistence -------------

    def save_all(self, base_dir: str) -> None:
        """Save all tries to {base_dir}/{sector}_{seq_len}.json."""
        os.makedirs(base_dir, exist_ok=True)
        for sector, seq_tries in self.tries.items():
            for seq_len, trie in seq_tries.items():
                path = os.path.join(base_dir, f"{sector}_{seq_len}.json")
                trie.to_json(path)

    def load_all(self, base_dir: str) -> int:
        """
        Load all tries from {base_dir}/{sector}_{seq_len}.json.

        Returns: number of tries loaded.
        """
        n_loaded = 0
        for sector, seq_tries in self.tries.items():
            for seq_len in seq_tries:
                path = os.path.join(base_dir, f"{sector}_{seq_len}.json")
                if os.path.exists(path):
                    self.tries[sector][seq_len] = RegimePartitionedTrie.from_json(path)
                    n_loaded += 1
        return n_loaded


# ---------------------------------------------------------------------------
# Vol regime computation (utility)
# ---------------------------------------------------------------------------

def compute_vol_regime(
    atr_percentile: float,
    breakpoints: Tuple[float, float, float] = (25.0, 50.0, 75.0),
) -> int:
    """
    Map an atr_percentile (0-100) to a vol_regime (0-3).

    Breakpoints default to quartiles:
        0 = low vol     (atr_percentile < 25)
        1 = normal vol  (25 <= atr_percentile < 50)
        2 = high vol    (50 <= atr_percentile < 75)
        3 = extreme vol (atr_percentile >= 75)

    Args:
        atr_percentile: 0-100 (computed by F4 from atr_percentile_50 feature)
        breakpoints: (p25, p50, p75) — customizable
    """
    if atr_percentile < breakpoints[0]:
        return 0
    elif atr_percentile < breakpoints[1]:
        return 1
    elif atr_percentile < breakpoints[2]:
        return 2
    else:
        return 3
