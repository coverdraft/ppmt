"""
Asset Classifier - Categorize Trading Pairs into Asset Classes

PPMT uses 6 asset classes that determine weight distribution
across the 4-level Trie architecture:

  1. blue_chip:  BTC, ETH — deepest data, highest N4 weight
  2. large_cap:  BNB, SOL, XRP — established assets
  3. mid_cap:    LINK, AVAX, DOT — moderate data
  4. defi:       UNI, AAVE, CRV — sector-specific patterns
  5. meme:       DOGE, SHIB, PEPE — high noise, low N3/N4 data
  6. new_launch: Recently listed — minimal data, rely on N1/N2

Classification determines:
  - Which weight profile to use (default, meme, blue_chip, new_launch)
  - How much to trust per-asset (N3) vs asset-class (N2) patterns
  - Position sizing constraints from the Risk Manager
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml


# Default classification rules (overridable via config)
DEFAULT_CLASSIFICATIONS = {
    "blue_chip": [
        "BTC/USDT", "ETH/USDT", "BTC/BUSD", "ETH/BUSD",
        "BTC/USDC", "ETH/USDC", "WBTC/USDT",
    ],
    "large_cap": [
        "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
        "AVAX/USDT", "DOT/USDT", "MATIC/USDT", "LINK/USDT",
        "TRX/USDT", "TON/USDT",
    ],
    "mid_cap": [
        "UNI/USDT", "AAVE/USDT", "CRV/USDT", "MKR/USDT",
        "ARB/USDT", "OP/USDT", "NEAR/USDT", "FTM/USDT",
        "ALGO/USDT", "ATOM/USDT", "FIL/USDT", "APT/USDT",
        "SUI/USDT", "SEI/USDT", "TIA/USDT",
    ],
    "defi": [
        "UNI/USDT", "AAVE/USDT", "CRV/USDT", "MKR/USDT",
        "COMP/USDT", "SNX/USDT", "DYDX/USDT", "GMX/USDT",
        "PENDLE/USDT", "LDO/USDT", "RPL/USDT",
    ],
    "meme": [
        "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "FLOKI/USDT",
        "BONK/USDT", "WIF/USDT", "BOME/USDT", "MEME/USDT",
    ],
    "new_launch": [],
}

# Market cap thresholds for auto-classification (in billions USD)
MARKET_CAP_THRESHOLDS = {
    "blue_chip": 100.0,   # > $100B
    "large_cap": 10.0,    # $10B - $100B
    "mid_cap": 1.0,       # $1B - $10B
}


@dataclass
class AssetInfo:
    """Classification result for a trading pair."""
    symbol: str
    asset_class: str
    weight_profile: str
    confidence: float = 1.0
    """How confident we are in this classification.
    Known symbols = 1.0, auto-classified = 0.5-0.8."""


class AssetClassifier:
    """
    Classifies trading pairs into asset classes.

    Classification is used by PPMT to:
    1. Select the correct weight profile for 4-level Trie search
    2. Determine which Trie levels to prioritize
    3. Set risk parameters (meme coins get tighter stops)

    The classifier first checks the known symbol list, then
    falls back to heuristic classification based on symbol patterns.

    Usage:
        classifier = AssetClassifier()
        info = classifier.classify("BTC/USDT")
        print(info.asset_class)   # 'blue_chip'
        print(info.weight_profile)  # 'blue_chip'
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the classifier.

        Args:
            config_path: Path to config YAML with asset_classes section.
                         If None, uses default classifications.
        """
        self.classifications = dict(DEFAULT_CLASSIFICATIONS)

        if config_path and os.path.exists(config_path):
            self._load_config(config_path)

    def _load_config(self, config_path: str) -> None:
        """Load custom classifications from config file."""
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}

            custom = config.get("asset_classes", {})
            for asset_class, symbols in custom.items():
                if isinstance(symbols, list):
                    self.classifications[asset_class] = symbols
        except (yaml.YAMLError, OSError):
            pass  # Fall back to defaults

    def classify_dynamic(self, symbol: str, age_hours: float = None,
                         max_volume_spike: float = None,
                         market_cap_usd: float = None) -> AssetInfo:
        """Classify a token with dynamic market data.

        v0.41.0 (FASE 3, Tarea 3.3): Extended classification that uses
        real-time market metadata (age, volume spikes, market cap) to
        produce more accurate classifications than the static lookup +
        heuristic approach.

        Priority order:
        1. Known symbols from the static lookup table (highest priority)
        2. Token < 24h old → force new_launch
        3. Token < 7 days old + volume spike > 20x → meme
        4. Market cap based classification (blue_chip/large_cap/mid_cap)
        5. Fallback to heuristic classification

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            age_hours: Hours since first listing (None = unknown)
            max_volume_spike: Maximum volume spike ratio vs average (None = unknown)
            market_cap_usd: Market cap in USD (None = unknown)

        Returns:
            AssetInfo with classification and weight profile
        """
        # Normalize symbol
        symbol = symbol.upper().strip()

        # 1. Check known symbols first (highest priority)
        for asset_class, symbols in self.classifications.items():
            if symbol in [s.upper() for s in symbols]:
                return AssetInfo(
                    symbol=symbol,
                    asset_class=asset_class,
                    weight_profile=self._get_weight_profile(asset_class),
                    confidence=1.0,
                )

        # 2. NEW RULE: Token < 24h → force new_launch
        if age_hours is not None and age_hours < 24:
            return AssetInfo(
                symbol=symbol,
                asset_class="new_launch",
                weight_profile="new_launch",
                confidence=0.9,  # High confidence in this classification
            )

        # 3. NEW RULE: Token < 7 days + volume spike > 20x → meme
        if age_hours is not None and age_hours < 168:  # 7 days = 168 hours
            if max_volume_spike is not None and max_volume_spike > 20.0:
                return AssetInfo(
                    symbol=symbol,
                    asset_class="meme",
                    weight_profile="meme",
                    confidence=0.8,
                )

        # 4. Market cap based classification
        if market_cap_usd is not None:
            if market_cap_usd >= 100e9:
                return AssetInfo(
                    symbol=symbol,
                    asset_class="blue_chip",
                    weight_profile="blue_chip",
                    confidence=0.7,
                )
            elif market_cap_usd >= 10e9:
                return AssetInfo(
                    symbol=symbol,
                    asset_class="large_cap",
                    weight_profile="default",
                    confidence=0.7,
                )
            elif market_cap_usd >= 1e9:
                return AssetInfo(
                    symbol=symbol,
                    asset_class="mid_cap",
                    weight_profile="default",
                    confidence=0.7,
                )

        # 5. Fallback to heuristic
        return self._heuristic_classify(symbol)

    def classify(self, symbol: str) -> AssetInfo:
        """
        Classify a trading pair into an asset class.

        Checks known symbols first, then applies heuristics:
        1. Known blue chip / large cap / etc. → direct classification
        2. Symbols with USD(T/C) stable pair → likely established
        3. Symbols with small quote pairs → possibly new/meme

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')

        Returns:
            AssetInfo with classification and weight profile
        """
        # Normalize symbol
        symbol = symbol.upper().strip()

        # Check each asset class
        for asset_class, symbols in self.classifications.items():
            if symbol in [s.upper() for s in symbols]:
                weight_profile = self._get_weight_profile(asset_class)
                return AssetInfo(
                    symbol=symbol,
                    asset_class=asset_class,
                    weight_profile=weight_profile,
                    confidence=1.0,
                )

        # Heuristic classification for unknown symbols
        return self._heuristic_classify(symbol)

    def _heuristic_classify(self, symbol: str) -> AssetInfo:
        """
        Heuristic classification for symbols not in the known list.

        Uses pattern matching on the symbol string:
        - Known base currencies → likely mid_cap
        - Meme-like names → meme
        - Everything else → new_launch
        """
        base = symbol.split("/")[0] if "/" in symbol else symbol

        # Common meme coin patterns
        meme_patterns = [
            "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF",
            "BOME", "TURBO", "WOJAK", "BRETT", "MOG",
            "BABYDOGE", "ELON", "MEME",
        ]
        if any(p in base.upper() for p in meme_patterns):
            return AssetInfo(
                symbol=symbol,
                asset_class="meme",
                weight_profile="meme",
                confidence=0.7,
            )

        # If it has a USDT/USDC/BUSD pair, it's probably at least mid_cap
        quote = symbol.split("/")[1] if "/" in symbol else ""
        if quote in ("USDT", "USDC", "BUSD"):
            return AssetInfo(
                symbol=symbol,
                asset_class="mid_cap",
                weight_profile="default",
                confidence=0.5,
            )

        # Default to new_launch for anything else
        return AssetInfo(
            symbol=symbol,
            asset_class="new_launch",
            weight_profile="new_launch",
            confidence=0.3,
        )

    @staticmethod
    def _get_weight_profile(asset_class: str) -> str:
        """Map asset class to weight profile."""
        profile_map = {
            "blue_chip": "blue_chip",
            "large_cap": "default",
            "mid_cap": "default",
            "defi": "default",
            "meme": "meme",
            "new_launch": "new_launch",
        }
        return profile_map.get(asset_class, "default")

    def get_all_classes(self) -> dict[str, list[str]]:
        """Return all asset classes and their symbols."""
        return dict(self.classifications)

    def add_symbol(self, symbol: str, asset_class: str) -> None:
        """Manually add a symbol to a specific asset class."""
        if asset_class not in self.classifications:
            self.classifications[asset_class] = []
        if symbol not in self.classifications[asset_class]:
            self.classifications[asset_class].append(symbol)
