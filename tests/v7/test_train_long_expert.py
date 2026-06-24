"""
Tests for v7_train_long_expert.py (F5a — LightGBM LONG expert).

Coverage:
  1. Feature list integrity (71 = 59 v6 + 12 F4)
  2. Walk-forward splits: train.ts < test.ts_min (anti-leakage)
  3. Sample weights: 2x for top-25% pumps + 2x for BEAR_2022 + 4x compound
  4. Anti-leakage guards: #3 (top_feat < 30%), #4 (train_corr < 0.85), #5 (std < 0.05)
  5. Long threshold sweep: produces dict with n_signals, wr, pf for each threshold
  6. End-to-end smoke: small synthetic dataset → train → results dict has all required keys

Run:
    python -m pytest tests/v7/test_train_long_expert.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# Make scripts/v7 importable
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "v7")
sys.path.insert(0, SCRIPT_DIR)

import v7_train_long_expert as trainer


# ----------------------------------------------------------------------------
# 1. Feature list integrity
# ----------------------------------------------------------------------------

class TestFeatureList:
    def test_total_feature_count_is_71(self):
        assert len(trainer.FEATURE_NAMES) == 71, \
            f"Expected 71 features (59 v6 + 12 F4), got {len(trainer.FEATURE_NAMES)}"

    def test_v6_feature_count_is_59(self):
        assert len(trainer.FEATURE_NAMES_V6) == 59

    def test_f4_feature_count_is_12(self):
        assert len(trainer.FEATURE_NAMES_F4) == 12

    def test_no_duplicate_feature_names(self):
        names = trainer.FEATURE_NAMES
        assert len(set(names)) == len(names), \
            f"Duplicate feature names: {[n for n in names if names.count(n) > 1]}"

    def test_f4_features_are_expected_set(self):
        expected = {
            "funding_rate", "funding_rate_z",
            "oi_change_1h", "oi_change_4h",
            "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
            "sector_idx",
            "day_of_week_sin", "day_of_week_cos", "day_of_week",
        }
        assert set(trainer.FEATURE_NAMES_F4) == expected

    def test_label_is_fwd_ret_3(self):
        assert trainer.LABEL == "fwd_ret_3"


# ----------------------------------------------------------------------------
# 2. Walk-forward splits — anti-leakage (train.ts < test.ts_min)
# ----------------------------------------------------------------------------

class TestWalkForwardSplits:
    def _make_synthetic_df(self, n_per_window: int = 2000) -> pd.DataFrame:
        """Create a DataFrame spanning 2025-01 to 2025-10, each month has 2000+ rows.
        Pre-2025-04 data serves as training context for the first walk-forward window."""
        rng = np.random.default_rng(seed=42)
        rows = []
        # Include Jan, Feb, Mar 2025 as pre-window training data + 5 WF months
        for year, month in [(2025, 1), (2025, 2), (2025, 3),
                            (2025, 4), (2025, 5), (2025, 6), (2025, 9), (2025, 10)]:
            for i in range(2000):
                day = (i % 28) + 1
                hour = (i % 4) * 6
                ts = pd.Timestamp(year=year, month=month, day=day, hour=hour, tz="UTC")
                rows.append({
                    "symbol": "BTCUSDT",
                    "ts": ts,
                    "window": "RECENT_2026" if month <= 6 else "RANGE_2025",
                    trainer.LABEL: float(rng.uniform(0.01, 1.0)),  # LONG only
                    **{f: float(rng.standard_normal()) for f in trainer.FEATURE_NAMES},
                })
        return pd.DataFrame(rows)

    def test_split_count(self):
        df = self._make_synthetic_df()
        splits = trainer.walk_forward_splits(df)
        assert len(splits) == 5, f"Expected 5 splits, got {len(splits)}"

    def test_train_before_test(self):
        """Anti-leakage: every train row must have ts < test month start."""
        df = self._make_synthetic_df()
        splits = trainer.walk_forward_splits(df)
        for name, train_df, test_df in splits:
            yr, mo = name.split("-")
            cutoff = pd.Timestamp(year=int(yr), month=int(mo), day=1, tz="UTC")
            assert train_df["ts"].max() < cutoff, \
                f"Split {name}: train max ts {train_df['ts'].max()} >= cutoff {cutoff}"
            assert test_df["ts"].min() >= cutoff, \
                f"Split {name}: test min ts {test_df['ts'].min()} < cutoff {cutoff}"
            assert test_df["ts"].max() < cutoff + pd.Timedelta(days=31), \
                f"Split {name}: test max ts {test_df['ts'].max()} outside test month"

    def test_train_grows_monotonically(self):
        """Walk-forward: each window's train set is larger than the previous."""
        df = self._make_synthetic_df()
        splits = trainer.walk_forward_splits(df)
        sizes = [len(train_df) for _, train_df, _ in splits]
        for i in range(1, len(sizes)):
            assert sizes[i] > sizes[i - 1], \
                f"Train size did not grow: window {i-1}={sizes[i-1]}, window {i}={sizes[i]}"

    def test_no_overlap_train_test(self):
        """Train and test must not share any rows."""
        df = self._make_synthetic_df()
        splits = trainer.walk_forward_splits(df)
        for name, train_df, test_df in splits:
            # Check by (symbol, ts) tuples
            train_keys = set(zip(train_df["symbol"], train_df["ts"]))
            test_keys = set(zip(test_df["symbol"], test_df["ts"]))
            overlap = train_keys & test_keys
            assert not overlap, f"Split {name}: {len(overlap)} overlapping rows"

    def test_split_names_match_wf_windows(self):
        df = self._make_synthetic_df()
        splits = trainer.walk_forward_splits(df)
        names = [name for name, _, _ in splits]
        assert names == trainer.WF_WINDOWS


# ----------------------------------------------------------------------------
# 3. Sample weights — 2x pumps + 2x BEAR_2022 + 4x compound
# ----------------------------------------------------------------------------

class TestSampleWeights:
    def test_uniform_weights_when_no_pumps_no_bear(self):
        """All weights should be 1.0 if no pumps and no BEAR_2022."""
        # Use 8 values so 75th percentile is unambiguous
        # Values [0.1..0.8], 75th percentile = 0.7 (numpy interpolation), so >=0.7 gets pump weight
        y = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], dtype=np.float32)
        window = np.array(["BULL_2024"] * 8)
        w = trainer.compute_sample_weights(y, window)
        # Indices 0-5 (0.1-0.6): not pumps → 1x
        # Indices 6-7 (0.7-0.8): pumps → 2x
        for i in range(6):
            assert w[i] == 1.0, f"Index {i} (y={y[i]}): expected 1.0, got {w[i]}"
        for i in range(6, 8):
            assert w[i] == 2.0, f"Index {i} (y={y[i]}): expected 2.0, got {w[i]}"

    def test_bear_weight_doubles(self):
        """BEAR_2022 rows should get 2x weight."""
        y = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], dtype=np.float32)
        # Only the last 2 are pumps (>= 0.7)
        window = np.array(["BEAR_2022", "BULL_2024", "BULL_2024", "BULL_2024",
                           "BULL_2024", "BULL_2024", "BULL_2024", "BEAR_2022"])
        w = trainer.compute_sample_weights(y, window)
        # Index 0: BEAR_2022, not pump → 2x
        assert w[0] == 2.0
        # Index 1: BULL_2024, not pump → 1x
        assert w[1] == 1.0
        # Index 7: BEAR_2022 AND pump → 4x
        assert w[7] == 4.0

    def test_compound_weight_4x_for_bear_pump(self):
        """BEAR_2022 + pump should give 4x weight (2x * 2x compound)."""
        y = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], dtype=np.float32)
        # Make all rows BEAR_2022
        window = np.array(["BEAR_2022"] * 8)
        w = trainer.compute_sample_weights(y, window)
        # All rows are BEAR → all get 2x base
        # Top 25% (0.7, 0.8) are pumps → 4x
        # Bottom 75% (0.1-0.6) → 2x
        assert w[0] == 2.0  # 0.1 BEAR but not pump
        assert w[6] == 4.0  # 0.7 BEAR + pump
        assert w[7] == 4.0  # 0.8 BEAR + pump

    def test_weights_are_positive(self):
        """All weights must be > 0 (no zero or negative weights)."""
        rng = np.random.default_rng(42)
        y = rng.uniform(0.01, 1.0, size=1000).astype(np.float32)
        windows = rng.choice(["BULL_2024", "BEAR_2022", "RANGE_2025"], size=1000)
        w = trainer.compute_sample_weights(y, windows)
        assert (w > 0).all(), "Found non-positive weights"

    def test_pump_threshold_is_75th_percentile(self):
        """PUMP_PERCENTILE should be 75."""
        assert trainer.PUMP_PERCENTILE == 75

    def test_pump_weight_is_2(self):
        assert trainer.PUMP_WEIGHT == 2.0

    def test_bear_weight_is_2(self):
        assert trainer.BEAR_WEIGHT == 2.0


# ----------------------------------------------------------------------------
# 4. Anti-leakage guards
# ----------------------------------------------------------------------------

class TestAntiLeakageGuards:
    def _make_result(self, window="2025-04", top_feat_pct=0.20, train_corr=0.50,
                     test_corr=0.45):
        return {
            "window": window,
            "top_feat_pct": top_feat_pct,
            "top_feat_name": "atr_pct",
            "corr_train": train_corr,
            "corr_test": test_corr,
            "guards": {
                "top_feat_under_30pct": top_feat_pct < 0.30,
                "train_corr_under_085": train_corr < 0.85,
            },
        }

    def test_guard_3_passes_when_top_feat_under_30pct(self):
        results = [self._make_result(top_feat_pct=0.20)]
        checks = trainer.run_anti_leakage_checks(results)
        assert checks["guard_3_max_top_feat_pct"] == 0.20
        assert not any("GUARD #3" in a for a in checks["alerts"])

    def test_guard_3_fails_when_top_feat_over_30pct(self):
        results = [self._make_result(top_feat_pct=0.55, window="2025-04")]
        checks = trainer.run_anti_leakage_checks(results)
        assert checks["guard_3_max_top_feat_pct"] == 0.55
        assert any("GUARD #3" in a and "2025-04" in a for a in checks["alerts"])

    def test_guard_4_passes_when_train_corr_under_085(self):
        results = [self._make_result(train_corr=0.50)]
        checks = trainer.run_anti_leakage_checks(results)
        assert not any("GUARD #4" in a for a in checks["alerts"])

    def test_guard_4_fails_when_train_corr_over_085(self):
        results = [self._make_result(train_corr=0.90, window="2025-05")]
        checks = trainer.run_anti_leakage_checks(results)
        assert any("GUARD #4" in a and "2025-05" in a for a in checks["alerts"])

    def test_guard_5_passes_when_std_under_005(self):
        results = [
            self._make_result(test_corr=0.45, window="2025-04"),
            self._make_result(test_corr=0.47, window="2025-05"),
            self._make_result(test_corr=0.46, window="2025-06"),
        ]
        checks = trainer.run_anti_leakage_checks(results)
        assert checks["guard_5_corr_std"] < 0.05
        assert not any("GUARD #5" in a for a in checks["alerts"])

    def test_guard_5_fails_when_std_over_005(self):
        results = [
            self._make_result(test_corr=0.30, window="2025-04"),
            self._make_result(test_corr=0.50, window="2025-05"),
            self._make_result(test_corr=0.40, window="2025-06"),
        ]
        checks = trainer.run_anti_leakage_checks(results)
        # std of [0.30, 0.50, 0.40] = 0.0816 > 0.05
        assert checks["guard_5_corr_std"] > 0.05
        assert any("GUARD #5" in a for a in checks["alerts"])

    def test_summary_dict_has_all_required_fields(self):
        results = [self._make_result()]
        checks = trainer.run_anti_leakage_checks(results)
        required = {"alerts", "guard_3_max_top_feat_pct", "guard_4_max_train_corr",
                    "guard_5_corr_std", "test_corr_mean", "n_windows_trained"}
        assert set(checks.keys()) == required


# ----------------------------------------------------------------------------
# 5. Long threshold sweep
# ----------------------------------------------------------------------------

class TestLongThresholdSweep:
    def test_threshold_sweep_produces_all_keys(self):
        """Each threshold should produce a dict with n_signals, wr, pf, avg_pnl_pct, tot_dollars."""
        rng = np.random.default_rng(42)
        n = 1000
        # Synthesize: pred and y both positive, well-correlated
        y_test = rng.uniform(0.0, 1.0, size=n).astype(np.float32)
        pred_test = y_test * 0.8 + rng.normal(0, 0.1, size=n).astype(np.float32)

        # Inline the threshold sweep logic (mirror what train_one_window does)
        thresholds = [0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
        results_by_thr = {}
        for thr in thresholds:
            long_mask = pred_test > thr
            n_long = int(long_mask.sum())
            if n_long > 0:
                actuals_long = y_test[long_mask]
                net_long = actuals_long - 0.14
                wins = float(net_long[net_long > 0].sum())
                losses = float(-net_long[net_long < 0].sum())
                pf = wins / losses if losses > 0 else 99.0
                wr = float((net_long > 0).mean())
                avg_pnl = float(net_long.mean())
                tot_dollars = float(net_long.sum() / 100 * 700)
            else:
                pf = wr = avg_pnl = tot_dollars = 0.0
            results_by_thr[f"thr_{thr:.2f}"] = {
                "n_signals": n_long, "wr": float(wr), "pf": float(pf),
                "avg_pnl_pct": float(avg_pnl), "tot_dollars": float(tot_dollars),
            }

        # Verify all required keys present
        required_keys = {"n_signals", "wr", "pf", "avg_pnl_pct", "tot_dollars"}
        for thr_key, stats in results_by_thr.items():
            assert set(stats.keys()) == required_keys, \
                f"{thr_key} missing keys: {required_keys - set(stats.keys())}"

    def test_higher_threshold_fewer_signals(self):
        """As threshold increases, n_signals should monotonically decrease."""
        rng = np.random.default_rng(42)
        n = 5000
        y_test = rng.uniform(0.0, 2.0, size=n).astype(np.float32)
        pred_test = y_test + rng.normal(0, 0.2, size=n).astype(np.float32)

        n_signals = []
        for thr in [0.20, 0.30, 0.40, 0.50, 0.75, 1.00]:
            n_signals.append(int((pred_test > thr).sum()))

        for i in range(1, len(n_signals)):
            assert n_signals[i] <= n_signals[i - 1], \
                f"Threshold {i}: {n_signals[i]} > {n_signals[i-1]} (should be non-increasing)"


# ----------------------------------------------------------------------------
# 6. End-to-end smoke test (small synthetic dataset)
# ----------------------------------------------------------------------------

class TestEndToEndSmoke:
    def test_train_one_window_returns_valid_dict(self, tmp_path, monkeypatch):
        """Train on tiny synthetic data, verify result dict has all required keys."""
        # Override OUTPUT_DIR to use tmp_path
        monkeypatch.setattr(trainer, "OUTPUT_DIR", tmp_path)

        rng = np.random.default_rng(42)
        n = 5000
        # Make synthetic features and labels
        X = rng.standard_normal(size=(n, len(trainer.FEATURE_NAMES))).astype(np.float32)
        # Label: positive (LONG filter) and correlated with first feature
        y = (X[:, 0] * 0.3 + rng.standard_normal(n) * 0.1 + 0.5).astype(np.float32)
        y = np.clip(y, 0.01, 5.0)  # ensure positive

        train_df = pd.DataFrame({
            "symbol": "BTCUSDT",
            "ts": pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
            "window": "BULL_2024",
            trainer.LABEL: y,
            **{fname: X[:, i] for i, fname in enumerate(trainer.FEATURE_NAMES)},
        })
        test_df = train_df.iloc[:1000].copy()
        train_df = train_df.iloc[1000:].copy()

        # Lower LGB params for fast test
        monkeypatch.setattr(trainer, "N_BOOST_ROUND", 30)
        monkeypatch.setattr(trainer, "EARLY_STOPPING", 5)

        result = trainer.train_one_window("smoke_test", train_df, test_df)

        # Verify required keys
        required_keys = {
            "window", "model_type", "label", "n_features", "n_train", "n_val", "n_test",
            "effective_sample_size", "n_pumps_train", "n_bear_train", "best_iteration",
            "rmse_train", "rmse_test", "mae_test", "corr_train", "corr_val", "corr_test",
            "dir_acc_test", "dir_acc_train", "long_thresholds", "top_feat_pct",
            "top_feat_name", "top_20_features", "guards", "model_path", "train_time_seconds",
        }
        missing = required_keys - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

        # Verify model file was saved
        assert Path(result["model_path"]).exists()
        # Verify model_type
        assert result["model_type"] == "v7_long_expert"
        # Verify label
        assert result["label"] == "fwd_ret_3"
        # Verify feature count
        assert result["n_features"] == 71
        # Verify all thresholds present
        assert set(result["long_thresholds"].keys()) == {
            "thr_0.20", "thr_0.30", "thr_0.40", "thr_0.50", "thr_0.75", "thr_1.00"
        }
        # Verify top_20_features has 20 entries
        assert len(result["top_20_features"]) == 20
        # Verify guards
        assert "top_feat_under_30pct" in result["guards"]
        assert "train_corr_under_085" in result["guards"]

    def test_train_results_json_round_trip(self, tmp_path, monkeypatch):
        """Verify the result dict is JSON-serializable (no numpy types)."""
        monkeypatch.setattr(trainer, "OUTPUT_DIR", tmp_path)

        rng = np.random.default_rng(42)
        n = 1000
        X = rng.standard_normal(size=(n, len(trainer.FEATURE_NAMES))).astype(np.float32)
        y = np.clip(X[:, 0] * 0.3 + 0.5 + rng.standard_normal(n) * 0.1, 0.01, 5.0).astype(np.float32)

        train_df = pd.DataFrame({
            "symbol": "BTCUSDT",
            "ts": pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
            "window": "BULL_2024",
            trainer.LABEL: y,
            **{fname: X[:, i] for i, fname in enumerate(trainer.FEATURE_NAMES)},
        })
        test_df = train_df.iloc[:200].copy()
        train_df = train_df.iloc[200:].copy()

        monkeypatch.setattr(trainer, "N_BOOST_ROUND", 20)
        monkeypatch.setattr(trainer, "EARLY_STOPPING", 5)

        result = trainer.train_one_window("json_test", train_df, test_df)

        # Must be JSON-serializable
        import json
        json_str = json.dumps(result)
        restored = json.loads(json_str)
        assert restored["window"] == "json_test"
        assert isinstance(restored["n_train"], int)
        assert isinstance(restored["corr_test"], float)
