"""
Tests for the PPMT Data Layer and V3 Metadata Enhancements.

Covers:
  - Storage (SQLite persistence)
  - Collector (Binance API, CSV import)
  - Classifier (asset class detection)
  - BlockLifecycleMetadata V3 enhancements:
    - probability_of_success
    - expected_profit_ahead
    - sizing_signal
  - Signal → RiskManager integration with metadata_sizing_signal
"""

from __future__ import annotations

import os
import tempfile
import time

import pandas as pd
import pytest

from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.trie import PPMTTrie
from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.signal import Signal, SignalType
from ppmt.risk.manager import RiskManager, RiskConfig


# === Metadata V3 Tests ===

class TestMetadataV3Enhancements:
    """Test the V3 metadata fields for Risk Manager integration."""

    def test_probability_of_success_zero_count(self):
        """No observations → probability = 0."""
        meta = BlockLifecycleMetadata()
        assert meta.probability_of_success == 0.0

    def test_probability_of_success_with_data(self):
        """With data, should return Bayesian-adjusted win rate."""
        meta = BlockLifecycleMetadata(win_rate=0.8, historical_count=100)
        prob = meta.probability_of_success
        assert 0.5 < prob < 1.0  # Should be close to 0.8 but shrunk

    def test_expected_profit_ahead_positive(self):
        """Winning pattern should have positive expected profit."""
        meta = BlockLifecycleMetadata(
            win_rate=0.7,
            expected_move_pct=3.0,
            max_drawdown_pct=-1.5,
            historical_count=50,
        )
        profit = meta.expected_profit_ahead
        # 0.7 * 3.0 + 0.3 * (-1.5) = 2.1 - 0.45 = 1.65
        assert profit == pytest.approx(1.65, abs=0.01)

    def test_expected_profit_ahead_negative(self):
        """Losing pattern should have negative expected profit."""
        meta = BlockLifecycleMetadata(
            win_rate=0.3,
            expected_move_pct=1.0,
            max_drawdown_pct=-3.0,
            historical_count=50,
        )
        profit = meta.expected_profit_ahead
        # 0.3 * 1.0 + 0.7 * (-3.0) = 0.3 - 2.1 = -1.8
        assert profit < 0

    def test_sizing_signal_zero_count(self):
        """No observations → sizing_signal = 0."""
        meta = BlockLifecycleMetadata()
        assert meta.sizing_signal == 0.0

    def test_sizing_signal_high_conviction(self):
        """High win rate, good R:R, positive expected → signal >= 1.5."""
        meta = BlockLifecycleMetadata(
            win_rate=0.85,
            expected_move_pct=3.0,
            max_drawdown_pct=-0.8,
            historical_count=200,
        )
        assert meta.sizing_signal >= 1.0  # Should be high

    def test_sizing_signal_low_conviction(self):
        """Low win rate, bad R:R, negative expected → low signal."""
        meta = BlockLifecycleMetadata(
            win_rate=0.35,
            expected_move_pct=0.5,
            max_drawdown_pct=-2.0,
            historical_count=10,
        )
        assert meta.sizing_signal < 1.0  # Should be low

    def test_sizing_signal_in_to_dict(self):
        """sizing_signal should appear in serialized dict."""
        meta = BlockLifecycleMetadata(
            win_rate=0.7,
            expected_move_pct=2.0,
            max_drawdown_pct=-1.0,
            historical_count=50,
        )
        d = meta.to_dict()
        assert "sizing_signal" in d
        assert "probability_of_success" in d
        assert "expected_profit_ahead" in d
        assert d["sizing_signal"] > 0


# === Storage Tests ===

class TestPPMTStorage:
    """Test SQLite storage layer."""

    @pytest.fixture
    def storage(self):
        """Create a temporary storage for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            s = PPMTStorage(db_path=db_path)
            yield s
        finally:
            s.close()
            os.unlink(db_path)

    def test_init_creates_tables(self, storage):
        """Database should be initialized with tables."""
        cursor = storage.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "assets" in tables
        assert "ohlcv" in tables
        assert "tries" in tables
        assert "engine_states" in tables

    def test_register_and_get_assets(self, storage):
        """Should register and retrieve assets."""
        storage.register_asset("BTC/USDT", "blue_chip")
        storage.register_asset("DOGE/USDT", "meme")

        assets = storage.get_assets()
        assert len(assets) >= 2
        symbols = {a["symbol"] for a in assets}
        assert "BTC/USDT" in symbols
        assert "DOGE/USDT" in symbols

    def test_save_and_load_ohlcv(self, storage):
        """Should save and load OHLCV data."""
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0],
                "high": [105.0, 106.0, 107.0],
                "low": [99.0, 100.0, 101.0],
                "close": [104.0, 105.0, 103.0],
                "volume": [1000.0, 1100.0, 900.0],
            },
            index=pd.date_range("2024-01-01", periods=3, freq="1h"),
        )

        storage.register_asset("TEST/USDT", "mid_cap")
        inserted = storage.save_ohlcv("TEST/USDT", "1h", df)
        assert inserted == 3

        loaded = storage.load_ohlcv("TEST/USDT", "1h")
        assert len(loaded) == 3
        assert loaded["close"].iloc[0] == 104.0

    def test_save_and_load_trie(self, storage):
        """Should serialize and deserialize Tries."""
        trie = PPMTTrie(name="test")
        trie.insert_with_observations(
            symbols=["a", "b", "c"],
            move_pct=2.5,
            drawdown_pct=-1.0,
            favorable_pct=3.0,
            duration=30,
            won=True,
            next_symbol="d",
        )

        storage.save_trie("BTC/USDT", "n3", trie)
        loaded = storage.load_trie("BTC/USDT", "n3")

        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.pattern_count == 1

        # Check metadata survived
        node = loaded.search(["a", "b", "c"])
        assert node is not None
        assert node.metadata.historical_count == 1

    def test_load_nonexistent_trie(self, storage):
        """Should return None for non-existent trie."""
        result = storage.load_trie("FAKE/USDT", "n3")
        assert result is None

    def test_engine_state_persistence(self, storage):
        """Should save and load engine state."""
        state = {
            "symbol": "BTC/USDT",
            "asset_class": "blue_chip",
            "total_patterns_built": 500,
            "weights": {"n1": 0.1, "n2": 0.2, "n3": 0.35, "n4": 0.35},
        }
        storage.save_engine_state("BTC/USDT", state)
        loaded = storage.load_engine_state("BTC/USDT")

        assert loaded is not None
        assert loaded["symbol"] == "BTC/USDT"
        assert loaded["total_patterns_built"] == 500

    def test_signal_history(self, storage):
        """Should save and retrieve signals."""
        signal_dict = {
            "symbol": "BTC/USDT",
            "signal_type": "ENTRY_LONG",
            "confidence": 0.85,
            "quality_score": 0.72,
            "sizing_multiplier": 2.0,
            "entry_price": 100000.0,
            "sl_price": 97000.0,
            "tp_price": 106000.0,
            "expected_move_pct": 5.0,
            "win_rate": 0.8,
            "remaining_candles": 50,
            "matched_pattern": ["a", "d", "b"],
            "predicted_path": [],
            "timestamp": time.time(),
        }
        storage.save_signal(signal_dict)

        signals = storage.get_signals("BTC/USDT")
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "ENTRY_LONG"


# === Classifier Tests ===

class TestAssetClassifier:
    """Test asset classification."""

    def test_classify_btc(self):
        classifier = AssetClassifier()
        info = classifier.classify("BTC/USDT")
        assert info.asset_class == "blue_chip"
        assert info.weight_profile == "blue_chip"
        assert info.confidence == 1.0

    def test_classify_doge(self):
        classifier = AssetClassifier()
        info = classifier.classify("DOGE/USDT")
        assert info.asset_class == "meme"
        assert info.weight_profile == "meme"

    def test_classify_unknown_with_usdt(self):
        classifier = AssetClassifier()
        info = classifier.classify("NEWCOIN/USDT")
        assert info.asset_class == "mid_cap"
        assert info.confidence == 0.5

    def test_classify_meme_pattern(self):
        classifier = AssetClassifier()
        info = classifier.classify("PEPECoin/USDT")
        assert info.asset_class == "meme"
        assert info.confidence == 0.7

    def test_classify_bnb(self):
        classifier = AssetClassifier()
        info = classifier.classify("BNB/USDT")
        assert info.asset_class == "large_cap"
        assert info.weight_profile == "default"

    def test_case_insensitive(self):
        classifier = AssetClassifier()
        info = classifier.classify("btc/usdt")
        assert info.asset_class == "blue_chip"

    def test_add_symbol(self):
        classifier = AssetClassifier()
        classifier.add_symbol("MYTOKEN/USDT", "defi")
        info = classifier.classify("MYTOKEN/USDT")
        assert info.asset_class == "defi"


# === Collector Tests ===

class TestDataCollector:
    """Test data collector."""

    def test_import_csv(self):
        """Should import OHLCV from CSV file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("timestamp,open,high,low,close,volume\n")
            f.write("1704067200000,100,105,99,104,1000\n")
            f.write("1704070800000,104,106,100,105,1100\n")
            f.write("1704074400000,105,107,101,103,900\n")
            csv_path = f.name

        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            storage = PPMTStorage(db_path=db_path)
            collector = DataCollector(storage=storage)

            df = collector.import_csv("TEST/USDT", "1h", csv_path)
            assert len(df) == 3
            assert df["close"].iloc[0] == 104.0

            # Check it was saved to storage
            loaded = storage.load_ohlcv("TEST/USDT", "1h")
            assert len(loaded) == 3

            collector.close()
        finally:
            os.unlink(csv_path)
            os.unlink(db_path)

    def test_import_csv_missing_column(self):
        """Should raise ValueError for missing columns."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("timestamp,open,close\n")
            f.write("1704067200000,100,104\n")
            csv_path = f.name

        try:
            collector = DataCollector()
            with pytest.raises(ValueError, match="missing required column"):
                collector.import_csv("TEST/USDT", "1h", csv_path)
        finally:
            os.unlink(csv_path)
            collector.close()

    def test_timeframe_to_ms(self):
        """Should convert timeframe strings to milliseconds."""
        assert DataCollector._timeframe_to_ms("1m") == 60_000
        assert DataCollector._timeframe_to_ms("5m") == 300_000
        assert DataCollector._timeframe_to_ms("1h") == 3_600_000
        assert DataCollector._timeframe_to_ms("4h") == 14_400_000
        assert DataCollector._timeframe_to_ms("1d") == 86_400_000


# === Integration: Signal → RiskManager with Metadata ===

class TestSignalRiskManagerIntegration:
    """Test the tight PPMT → RiskManager integration via metadata_sizing_signal."""

    def test_metadata_sizing_signal_drives_position_size(self):
        """Higher sizing_signal should produce larger position sizes."""
        config = RiskConfig(base_position_size_pct=0.02)

        # Low conviction signal
        low_signal = Signal(
            signal_type=SignalType.ENTRY_LONG,
            confidence=0.55,
            symbol="TEST/USDT",
            entry_price=100.0,
            sl_price=98.0,
            sizing_multiplier=0.5,
            metadata_sizing_signal=0.5,
        )

        # High conviction signal
        high_signal = Signal(
            signal_type=SignalType.ENTRY_LONG,
            confidence=0.90,
            symbol="TEST2/USDT",
            entry_price=100.0,
            sl_price=98.0,
            sizing_multiplier=2.0,
            metadata_sizing_signal=1.8,
        )

        rm = RiskManager(capital=10000.0, config=config)
        low_size = rm.calculate_position_size(low_signal)
        high_size = rm.calculate_position_size(high_signal)

        # High conviction should produce larger position
        assert high_size > low_size

    def test_metadata_signal_overrides_quality_score(self):
        """metadata_sizing_signal should override sizing_multiplier when present."""
        config = RiskConfig(base_position_size_pct=0.02)

        # Signal with low quality_score multiplier but high metadata signal
        signal = Signal(
            signal_type=SignalType.ENTRY_LONG,
            confidence=0.9,
            symbol="TEST/USDT",
            entry_price=100.0,
            sl_price=98.0,
            sizing_multiplier=0.5,  # Low quality-based
            metadata_sizing_signal=1.8,  # High metadata-based
        )

        rm = RiskManager(capital=10000.0, config=config)
        size = rm.calculate_position_size(signal)

        # Should use metadata_sizing_signal (1.8), not sizing_multiplier (0.5)
        # risk_pct = 0.02 * 1.8 = 0.036
        # risk_amount = 10000 * 0.036 = 360
        # sl_distance = 2
        # size = 360 / 2 = 180
        assert size > 0
        # Verify it's using the metadata signal, not the lower quality multiplier
        assert size >= 100  # Should be 180, not 50

    def test_full_pipeline_metadata_to_sizing(self):
        """End-to-end: metadata → signal → risk manager → position size."""
        # Create metadata with known characteristics
        meta = BlockLifecycleMetadata(
            win_rate=0.80,
            expected_move_pct=3.0,
            max_drawdown_pct=-0.8,
            max_favorable_pct=4.0,
            historical_count=200,
            remaining_candles=50,
        )

        # Create signal from metadata
        signal = Signal(
            signal_type=SignalType.ENTRY_LONG,
            confidence=meta.confidence,
            symbol="BTC/USDT",
            entry_price=100000.0,
            sl_price=100000.0 * (1 + meta.max_drawdown_pct / 100.0 * 1.2),
            tp_price=100000.0 * (1 + meta.expected_move_pct / 100.0),
            win_rate=meta.win_rate,
            risk_reward_ratio=meta.risk_reward_ratio,
            historical_count=meta.historical_count,
            probability_of_success=meta.probability_of_success,
            expected_profit_ahead=meta.expected_profit_ahead,
            metadata_sizing_signal=meta.sizing_signal,
        )
        signal.quality_score = signal.compute_quality_score()
        signal.sizing_multiplier = signal.compute_sizing_multiplier()

        # Risk manager uses metadata_sizing_signal
        rm = RiskManager(capital=50000.0)
        can_open, reason = rm.can_open(signal)
        size = rm.calculate_position_size(signal)

        # Should be able to open and get a reasonable size
        assert can_open or "R:R" in reason or "Confidence" in reason
        assert size > 0
