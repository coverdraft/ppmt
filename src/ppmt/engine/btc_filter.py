"""
BTC Context Filter - FASE 2, Tarea 2.3

Post-prediction filter based on BTC market regime.

In crypto, BTC dictates market direction. A LONG signal on DOGE
while BTC is trending_down has significantly lower probability.
This filter adjusts confidence based on BTC's current state.

The filter is OPT-IN: if no BTC context is provided, signals pass
through unchanged. This ensures existing flows that don't provide
BTC data are not broken.

Usage:
    from ppmt.engine.btc_filter import BTCContextFilter

    filter = BTCContextFilter()
    filter.update_btc_context(btc_prices=np.array([...]))

    result = filter.filter_signal('LONG', 0.65)
    if result['rejected']:
        # Skip this signal
    else:
        adjusted_confidence = result['adjusted_confidence']
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np


class BTCContextFilter:
    """Post-prediction filter based on BTC market regime.

    In crypto, BTC dictates market direction. A LONG signal on DOGE
    while BTC is trending_down has significantly lower probability.
    This filter adjusts confidence based on BTC's current state.
    """

    # Multipliers for confidence based on BTC regime and signal direction
    REGIME_MULTIPLIERS = {
        # (signal_direction, btc_regime) -> confidence_multiplier
        ('LONG', 'trending_up'): 1.2,    # Aligned: boost
        ('LONG', 'trending_down'): 0.3,  # Counter-trend: penalize
        ('LONG', 'ranging'): 0.8,         # Neutral: slight reduction
        ('LONG', 'volatile'): 0.5,        # Risky: significant reduction
        ('SHORT', 'trending_up'): 0.3,    # Counter-trend: penalize
        ('SHORT', 'trending_down'): 1.2,  # Aligned: boost
        ('SHORT', 'ranging'): 0.8,        # Neutral: slight reduction
        ('SHORT', 'volatile'): 0.5,       # Risky: significant reduction
    }

    # Hard rejection: in extreme BTC volatility, reject all altcoin signals
    REJECT_ON_EXTREME_VOL = True
    EXTREME_VOL_THRESHOLD = 0.60  # If BTC annualized vol > 60%

    def __init__(self, storage=None):
        self.storage = storage
        self._btc_regime: Optional[str] = None
        self._btc_volatility: Optional[float] = None
        self._last_update: float = 0

    def update_btc_context(self, btc_prices: np.ndarray = None):
        """Update BTC regime from price data.

        Can be called periodically (e.g., every 100 candles) or
        when new BTC data is available.
        """
        if btc_prices is not None and len(btc_prices) >= 50:
            from ppmt.core.regime import RegimeDetector
            detector = RegimeDetector()
            info = detector.detect_detailed(btc_prices)
            self._btc_regime = info.regime
            self._btc_volatility = info.volatility
            self._last_update = time.time()

    def filter_signal(self, signal_direction: str, confidence: float) -> dict:
        """Apply BTC context filter to a signal.

        Returns dict with:
            'adjusted_confidence': float
            'rejected': bool
            'reason': str
            'btc_regime': str
        """
        if self._btc_regime is None:
            return {
                'adjusted_confidence': confidence,
                'rejected': False,
                'reason': 'no_btc_context',
                'btc_regime': 'unknown',
            }

        # Check extreme volatility rejection
        if self.REJECT_ON_EXTREME_VOL and self._btc_volatility and self._btc_volatility > self.EXTREME_VOL_THRESHOLD:
            return {
                'adjusted_confidence': 0.0,
                'rejected': True,
                'reason': f'btc_extreme_volatility ({self._btc_volatility:.1%})',
                'btc_regime': self._btc_regime,
            }

        # Apply regime multiplier
        key = (signal_direction, self._btc_regime)
        multiplier = self.REGIME_MULTIPLIERS.get(key, 0.8)  # Default: slight reduction
        adjusted = confidence * multiplier

        return {
            'adjusted_confidence': adjusted,
            'rejected': adjusted < 0.10,  # Reject if too low
            'reason': f'btc_{self._btc_regime}_multiplier_{multiplier:.1f}',
            'btc_regime': self._btc_regime,
        }
