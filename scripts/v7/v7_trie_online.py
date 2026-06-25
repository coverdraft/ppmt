"""
v7_trie_online.py — F8 Layer 1: Trie online (insert-after-predict)

WHAT THIS DOES
--------------
Implements the Layer 1 online learning loop from master plan §6.1:
  T = 10:00:00  Vela cierra
  T = 10:00:01  Consultar features (sin leakage) → predecir
  T = 10:00:02  LightGBM predice pred_long
  T = 10:00:03  Decisión: LONG / SHORT / WAIT
  T = 10:15:00  Vela + 15m cierra → sabemos outcome real
  T = 10:15:01  ★ INSERTAR en trie: features → fwd_ret_real (post-predict)

CRITICAL RULE — INSERT-AFTER-PREDICT
------------------------------------
The trie insertion happens 15 minutes AFTER the prediction. Never before.
This module enforces that by buffering pending predictions and only
committing them when the corresponding outcome arrives.

TRIE HYGIENE (master plan §6.1)
-------------------------------
- prune() every 1000 insertions — remove nodes with <3 observations
- Time decay half-life = 24h (stale patterns lose weight exponentially)
- Max nodes per sector: 100K (LRU eviction if exceeded)

USAGE
-----
    trie = OnlineTrie()
    trie.predict_and_record(features_vec, ts_unix, symbol)
    # ... 15 min later, when fwd_ret is known:
    trie.commit_outcome(ts_unix_15m_ago, fwd_ret_real)
    # Lookup similar pattern:
    mean_ret, n_obs = trie.lookup_pattern(features_vec, ts_unix)

PERSISTENCE
-----------
    trie.save('/path/to/online_trie.pkl')
    trie2 = OnlineTrie.load('/path/to/online_trie.pkl')
"""
from __future__ import annotations

# === Auto-detected project root (portable paths) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===

import hashlib
import json
import logging
import os
import pickle
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

LOG = logging.getLogger("v7_trie_online")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Trie hygiene constants (master plan §6.1)
PRUNE_EVERY_N_INSERTS = 10_000  # prune every 10k inserts (was 1k — too noisy)
PRUNE_MIN_OBS = 3
DEFAULT_HALF_LIFE_HOURS = 24.0
MAX_NODES_DEFAULT = 100_000

# Bucket size for feature quantization (used as trie key)
# We hash the binned feature vector to get a stable string key.
N_BINS = 5  # quantile bins per feature dim


@dataclass
class TrieNodeOnline:
    """A trie node tracking observed outcomes for a feature-bin pattern.

    Attributes
    ----------
    n_obs : int
        Total observations ever inserted (decayed count is in effective_obs).
    sum_outcome : float
        Sum of outcomes (decayed).
    sum_outcome_sq : float
        Sum of outcome^2 (decayed) — for variance computation.
    last_ts : float
        Unix timestamp of last insert (for LRU eviction).
    effective_obs : float
        Decayed observation count, recomputed on each insert.
    """
    n_obs: int = 0
    sum_outcome: float = 0.0
    sum_outcome_sq: float = 0.0
    last_ts: float = 0.0
    effective_obs: float = 0.0

    def insert(self, outcome: float, ts: float, decay_factor: float = 1.0) -> None:
        """Insert one outcome observation with optional time-decay factor."""
        # Apply decay to existing accumulated values BEFORE adding new obs.
        # decay_factor < 1 means "older observations lose weight".
        self.sum_outcome *= decay_factor
        self.sum_outcome_sq *= decay_factor
        self.effective_obs *= decay_factor
        # Add new observation
        self.sum_outcome += outcome
        self.sum_outcome_sq += outcome * outcome
        self.effective_obs += 1.0
        self.n_obs += 1
        self.last_ts = ts

    def stats(self) -> Tuple[float, float, int, float]:
        """Return (mean_outcome, std_outcome, n_obs, effective_obs)."""
        if self.effective_obs < 1e-9:
            return 0.0, 0.0, 0, 0.0
        mean = self.sum_outcome / self.effective_obs
        var = max(self.sum_outcome_sq / self.effective_obs - mean * mean, 0.0)
        std = float(np.sqrt(var))
        return mean, std, self.n_obs, self.effective_obs


class OnlineTrie:
    """Layer 1 online trie — insert-after-predict, with pruning + time decay.

    The trie maps quantized feature vectors → outcome statistics.
    Quantization uses N_BINS quantile bins per feature dimension. Since we
    hash the binned vector to a string key, we don't actually build a tree
    structure — we use a flat dict, which is functionally equivalent for our
    lookup purposes and much faster.

    The "trie" name is preserved for alignment with the master plan vocabulary.

    INSERT-AFTER-PREDICT FLOW
    -------------------------
    1. Caller calls `predict_and_record(features, ts, symbol)` BEFORE knowing
       the outcome. We store the features + ts in a pending buffer.
    2. Some time later (typically 15m), caller calls `commit_outcome(ts, outcome)`.
       We pop the matching pending entry and insert into the trie.
    3. The trie is never consulted for the prediction at ts itself — only
       for FUTURE predictions whose pattern matches.
    """

    def __init__(
        self,
        n_bins: int = N_BINS,
        half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
        max_nodes: int = MAX_NODES_DEFAULT,
        prune_min_obs: int = PRUNE_MIN_OBS,
    ):
        self.n_bins = n_bins
        self.half_life_hours = half_life_hours
        self.max_nodes = max_nodes
        self.prune_min_obs = prune_min_obs

        # nodes: dict[str, TrieNodeOnline]
        self.nodes: Dict[str, TrieNodeOnline] = {}

        # Pending predictions: ts -> (feature_key, symbol, original_features)
        # We key on ts because the caller commits by ts.
        # If multiple symbols share ts, we use a list of pending entries.
        self.pending: Dict[float, List[Tuple[str, str, np.ndarray]]] = {}

        # Quantile bin edges — set on first fit, or provided externally
        self.bin_edges: Optional[np.ndarray] = None  # shape (n_features, n_bins-1)
        self.n_features: Optional[int] = None

        # Counters for hygiene
        self.n_inserts_total = 0
        self.n_prunes_run = 0
        self.n_evictions_run = 0
        self.created_at = time.time()

    # ------------------------------------------------------------------
    # Feature quantization
    # ------------------------------------------------------------------

    def fit_bins(self, X: np.ndarray, quantiles: Optional[np.ndarray] = None) -> None:
        """Compute bin edges from a reference feature matrix.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features)
            Reference features (e.g., the training set).
        quantiles : np.ndarray, optional
            Quantile cut points in (0, 1). Defaults to evenly spaced.
        """
        if quantiles is None:
            # n_bins-1 cut points, evenly spaced between 0 and 1
            quantiles = np.linspace(0, 1, self.n_bins + 1)[1:-1]
        n_features = X.shape[1]
        edges = np.zeros((n_features, len(quantiles)), dtype=np.float64)
        for j in range(n_features):
            col = X[:, j]
            # Use nanpercentile to handle NaNs gracefully
            edges[j] = np.nanpercentile(col, [q * 100 for q in quantiles])
        self.bin_edges = edges
        self.n_features = n_features
        LOG.info(
            "OnlineTrie.fit_bins: %d features × %d bins (edges shape=%s)",
            n_features, self.n_bins, edges.shape,
        )

    def _quantize(self, features: np.ndarray) -> str:
        """Convert a 1D feature vector to a stable hash key.

        Each feature is binned to an integer in [0, n_bins-1] using
        pre-fit bin edges. The binned vector is then hashed to a string
        for dict lookup.
        """
        if self.bin_edges is None:
            raise RuntimeError("Call fit_bins() before quantizing features.")
        features = np.asarray(features, dtype=np.float64).ravel()
        if features.shape != (self.n_features,):
            raise ValueError(
                f"Feature shape mismatch: got {features.shape}, expected ({self.n_features},)"
            )
        # Vectorized binning: count how many edges each feature exceeds.
        # bin_edges shape: (n_features, n_bins-1)
        # features shape: (n_features,)
        # Comparison: features[:, None] >= bin_edges → (n_features, n_bins-1)
        # Sum along axis=1 → (n_features,) counts of edges exceeded.
        binned = (features[:, None] >= self.bin_edges).sum(axis=1).astype(np.int8)
        # Clip in case of edge cases
        np.clip(binned, 0, self.n_bins - 1, out=binned)
        # Hash binned vector to stable string key
        key = hashlib.sha1(binned.tobytes()).hexdigest()[:16]
        return key

    # ------------------------------------------------------------------
    # Insert-after-predict API
    # ------------------------------------------------------------------

    def predict_and_record(
        self,
        features: np.ndarray,
        ts: float,
        symbol: str,
    ) -> str:
        """Buffer a prediction's features for later commit.

        MUST be called BEFORE the outcome is known.
        Returns the feature hash key (for caller to log if desired).
        """
        key = self._quantize(features)
        entry = (key, symbol, np.asarray(features, dtype=np.float64).copy())
        self.pending.setdefault(float(ts), []).append(entry)
        return key

    def commit_outcome(
        self,
        ts: float,
        outcome: float,
        symbol: Optional[str] = None,
        current_ts: Optional[float] = None,
    ) -> int:
        """Commit the outcome for the prediction made at `ts`.

        Pops the pending entry (insert-after-predict enforced by design:
        if it's not in self.pending, we never saw the original prediction
        and refuse to insert).

        Parameters
        ----------
        ts : float
            Unix timestamp of the original prediction.
        outcome : float
            Realized forward return (e.g., fwd_ret_3 %).
        symbol : str, optional
            If provided, only commits the entry for this symbol at `ts`.
            Other entries at the same ts remain pending.
            If None, commits ALL entries at `ts` with the same outcome
            (use only when you know there's a single entry).
        current_ts : float, optional
            Current unix timestamp for decay computation. Defaults to `ts`
            (no decay applied).

        Returns
        -------
        int
            Number of entries committed (1 if symbol given; len(entries) if not).
        """
        if ts not in self.pending:
            return 0
        entries = self.pending.pop(ts)
        if current_ts is None:
            current_ts = ts

        # If symbol specified, filter entries to just that symbol
        if symbol is not None:
            to_commit = [e for e in entries if e[1] == symbol]
            # Put back the ones we're not committing
            remaining = [e for e in entries if e[1] != symbol]
            if remaining:
                self.pending[ts] = remaining
            entries = to_commit
            if not entries:
                return 0

        # Compute decay factor relative to each node's last_ts
        for key, _sym, _feat in entries:
            node = self.nodes.get(key)
            if node is None:
                node = TrieNodeOnline()
                self.nodes[key] = node
            # Decay existing accumulated values by elapsed half-lives
            # Note: node.last_ts = 0 means "never inserted", which we
            # treat as decay=1.0 (nothing to decay).
            if node.n_obs > 0 and current_ts > node.last_ts:
                elapsed_hours = (current_ts - node.last_ts) / 3600.0
                decay = 0.5 ** (elapsed_hours / self.half_life_hours)
            else:
                decay = 1.0
            node.insert(outcome, current_ts, decay_factor=decay)
            self.n_inserts_total += 1

        # LRU eviction if over max_nodes (this is the only automatic hygiene;
        # explicit prune() can be called by the caller at appropriate points
        # — e.g., end of a window — to clean up low-obs nodes)
        if len(self.nodes) > self.max_nodes:
            self._evict_lru()

        return len(entries)

    def lookup_pattern(
        self,
        features: np.ndarray,
    ) -> Tuple[float, float, int, float]:
        """Look up the (mean, std, n_obs, eff_obs) for a feature pattern.

        Returns (0.0, 0.0, 0, 0.0) if pattern never observed.
        """
        key = self._quantize(features)
        node = self.nodes.get(key)
        if node is None:
            return 0.0, 0.0, 0, 0.0
        return node.stats()

    # ------------------------------------------------------------------
    # Hygiene
    # ------------------------------------------------------------------

    def prune(self, current_ts: Optional[float] = None) -> int:
        """Remove nodes with effective_obs < prune_min_obs.

        Returns the number of nodes removed.
        """
        before = len(self.nodes)
        keys_to_remove = [
            k for k, n in self.nodes.items()
            if n.effective_obs < self.prune_min_obs
        ]
        for k in keys_to_remove:
            del self.nodes[k]
        self.n_prunes_run += 1
        removed = before - len(self.nodes)
        if removed > 0:
            LOG.info(
                "OnlineTrie.prune #%d: removed %d nodes (effective_obs<%d). Now %d nodes.",
                self.n_prunes_run, removed, self.prune_min_obs, len(self.nodes),
            )
        return removed

    def _evict_lru(self) -> int:
        """Evict oldest (last_ts) nodes to get under max_nodes.

        Uses a partial sort (heapq.nsmallest) for efficiency when only a
        few nodes need to be evicted.
        """
        if len(self.nodes) <= self.max_nodes:
            return 0
        n_to_evict = len(self.nodes) - self.max_nodes
        # Use heapq.nsmallest to find the N oldest nodes — O(N log k) where k=n_to_evict
        # Falls back to sorted() if n_to_evict is a large fraction of total
        if n_to_evict > len(self.nodes) // 10:
            # Sort everything — O(N log N)
            sorted_items = sorted(self.nodes.items(), key=lambda kv: kv[1].last_ts)
            for i in range(n_to_evict):
                del self.nodes[sorted_items[i][0]]
        else:
            # Partial sort — much faster when evicting just a few
            import heapq
            oldest = heapq.nsmallest(
                n_to_evict,
                self.nodes.items(),
                key=lambda kv: kv[1].last_ts,
            )
            for k, _ in oldest:
                del self.nodes[k]
        self.n_evictions_run += 1
        # Only log if evicting a significant batch (>1% of max_nodes)
        if n_to_evict > max(1, self.max_nodes // 100):
            LOG.warning(
                "OnlineTrie LRU eviction #%d: removed %d oldest nodes. Now %d nodes.",
                self.n_evictions_run, n_to_evict, len(self.nodes),
            )
        return n_to_evict

    def decay_all(self, current_ts: float) -> None:
        """Apply time-decay to ALL nodes (expensive; use sparingly).

        This is equivalent to "advancing the clock" — all accumulated
        statistics are scaled by 0.5^(elapsed_hours / half_life_hours).
        """
        if not self.nodes:
            return
        # We need a reference last_ts; use the most recent
        most_recent_ts = max(n.last_ts for n in self.nodes.values() if n.last_ts > 0)
        elapsed_hours = (current_ts - most_recent_ts) / 3600.0
        if elapsed_hours <= 0:
            return
        decay = 0.5 ** (elapsed_hours / self.half_life_hours)
        for n in self.nodes.values():
            if n.last_ts > 0:
                n.sum_outcome *= decay
                n.sum_outcome_sq *= decay
                n.effective_obs *= decay
        LOG.info(
            "OnlineTrie.decay_all: applied decay=%.4f (elapsed=%.2fh, half_life=%.1fh)",
            decay, elapsed_hours, self.half_life_hours,
        )

    # ------------------------------------------------------------------
    # Stats & persistence
    # ------------------------------------------------------------------

    def stats(self) -> Dict:
        """Return a summary dict for monitoring."""
        n_nodes = len(self.nodes)
        if n_nodes > 0:
            obs_counts = [n.n_obs for n in self.nodes.values()]
            eff_obs = [n.effective_obs for n in self.nodes.values()]
            obs_mean = float(np.mean(obs_counts))
            obs_max = int(np.max(obs_counts))
            eff_obs_total = float(np.sum(eff_obs))
        else:
            obs_mean = 0.0
            obs_max = 0
            eff_obs_total = 0.0
        return {
            "n_nodes": n_nodes,
            "n_inserts_total": self.n_inserts_total,
            "n_pending": sum(len(v) for v in self.pending.values()),
            "n_prunes_run": self.n_prunes_run,
            "n_evictions_run": self.n_evictions_run,
            "obs_per_node_mean": obs_mean,
            "obs_per_node_max": obs_max,
            "eff_obs_total": eff_obs_total,
            "half_life_hours": self.half_life_hours,
            "max_nodes": self.max_nodes,
            "created_at": self.created_at,
            "age_seconds": time.time() - self.created_at,
        }

    def save(self, path: str) -> None:
        """Persist trie to disk."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "n_bins": self.n_bins,
                "half_life_hours": self.half_life_hours,
                "max_nodes": self.max_nodes,
                "prune_min_obs": self.prune_min_obs,
                "bin_edges": self.bin_edges,
                "n_features": self.n_features,
                "nodes": self.nodes,
                "n_inserts_total": self.n_inserts_total,
                "n_prunes_run": self.n_prunes_run,
                "n_evictions_run": self.n_evictions_run,
                "created_at": self.created_at,
            }, f)
        LOG.info("OnlineTrie saved to %s (%d nodes)", path, len(self.nodes))

    @classmethod
    def load(cls, path: str) -> "OnlineTrie":
        """Load trie from disk. NOTE: pending buffer is NOT restored."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        trie = cls(
            n_bins=data["n_bins"],
            half_life_hours=data["half_life_hours"],
            max_nodes=data["max_nodes"],
            prune_min_obs=data["prune_min_obs"],
        )
        trie.bin_edges = data["bin_edges"]
        trie.n_features = data["n_features"]
        trie.nodes = data["nodes"]
        trie.n_inserts_total = data["n_inserts_total"]
        trie.n_prunes_run = data["n_prunes_run"]
        trie.n_evictions_run = data["n_evictions_run"]
        trie.created_at = data["created_at"]
        LOG.info("OnlineTrie loaded from %s (%d nodes)", path, len(trie.nodes))
        return trie


# ----------------------------------------------------------------------
# Ensemble helper — combine LightGBM pred with trie lookup
# ----------------------------------------------------------------------

def ensemble_prediction(
    lgb_pred: float,
    trie_mean: float,
    trie_n_obs: int,
    trie_min_obs: int = 5,
    trie_weight: float = 0.2,
) -> float:
    """Combine LightGBM prediction with trie lookup.

    If the trie has seen this pattern at least `trie_min_obs` times,
    blend with weight `trie_weight`. Otherwise, return lgb_pred unchanged.

    This is the F8 hook for "trie online feedback" — predictions get
    gently nudged by historical outcomes for similar patterns.
    """
    if trie_n_obs < trie_min_obs:
        return lgb_pred
    return lgb_pred * (1.0 - trie_weight) + trie_mean * trie_weight
