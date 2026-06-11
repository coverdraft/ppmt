#!/usr/bin/env python3
"""
PPMT Synthetic Market Data Generator
======================================

Generates realistic synthetic OHLCV data for 3 crypto tokens using
Geometric Brownian Motion with regime shifts:

  1. BTC/USDT - Blue chip:  price ~65000, ATR ~1.5%, trending with regime shifts
  2. ETH/USDT - Large cap:  price ~3500,  ATR ~2.0%, moderate volatility
  3. SOL/USDT - Mid cap:    price ~150,   ATR ~3.5%, higher volatility with meme-like spikes

Each token gets 8760 candles (1 year of 1h data) with:
  - Realistic OHLCV relationships (high >= max(open,close), low <= min(open,close))
  - Regime shifts every 200-500 candles (trending_up, ranging, trending_down, volatile)
  - Volume spikes correlated with volatility
  - Meme-like pump/dump spikes for SOL

Usage:
    cd ~/my-project/ppmt
    python scripts/generate_synthetic_market_data.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

# Ensure ppmt modules can be imported when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pandas as pd

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

# ── Configuration ────────────────────────────────────────────────────────

NUM_CANDLES = 8760  # 1 year of 1h candles
RANDOM_SEED = 42
HOURS_PER_YEAR = 8760.0

# Regime types and their characteristics
# drift_annual: expected annual return in the regime
# vol_mult: multiplier on base volatility
REGIME_PARAMS = {
    "trending_up":   {"drift_annual": 0.60, "vol_mult": 0.9},   # +60%/yr, slightly lower vol
    "ranging":       {"drift_annual": 0.02, "vol_mult": 0.6},    # flat, low vol
    "trending_down": {"drift_annual":-0.50, "vol_mult": 1.3},    # -50%/yr, higher vol
    "volatile":      {"drift_annual": 0.00, "vol_mult": 1.8},    # flat, very high vol
}

# Token definitions
@dataclass
class TokenConfig:
    """Configuration for a synthetic token."""
    symbol: str
    asset_class: str
    initial_price: float
    annual_vol: float             # Target annualized volatility (e.g., 0.60 = 60%)
    target_atr_pct: float         # Target average ATR as % of price (hourly bar)
    base_volume: float            # Base hourly volume in USDT
    spike_probability: float     # Probability of a meme-like spike per candle
    spike_magnitude_atr: float   # Spike magnitude as multiple of target ATR


TOKEN_CONFIGS = [
    TokenConfig(
        symbol="BTC/USDT",
        asset_class="blue_chip",
        initial_price=65000.0,
        annual_vol=0.55,             # ~55% annual vol → ~0.59% hourly
        target_atr_pct=0.015,        # ~1.5% ATR
        base_volume=500_000_000,     # 500M USDT
        spike_probability=0.002,     # Rare spikes
        spike_magnitude_atr=2.0,
    ),
    TokenConfig(
        symbol="ETH/USDT",
        asset_class="large_cap",
        initial_price=3500.0,
        annual_vol=0.70,             # ~70% annual vol → ~0.75% hourly
        target_atr_pct=0.020,        # ~2.0% ATR
        base_volume=250_000_000,     # 250M USDT
        spike_probability=0.005,     # Occasional spikes
        spike_magnitude_atr=2.5,
    ),
    TokenConfig(
        symbol="SOL/USDT",
        asset_class="mid_cap",
        initial_price=150.0,
        annual_vol=0.90,             # ~90% annual vol → ~0.96% hourly
        target_atr_pct=0.035,        # ~3.5% ATR
        base_volume=80_000_000,      # 80M USDT
        spike_probability=0.012,     # Frequent meme-like spikes
        spike_magnitude_atr=3.0,
    ),
]


# ── Regime Shift Generator ──────────────────────────────────────────────

def generate_regime_sequence(
    n_candles: int,
    rng: np.random.Generator,
    min_length: int = 200,
    max_length: int = 500,
) -> list[tuple[str, int]]:
    """
    Generate a sequence of (regime_name, duration) tuples covering n_candles.

    Regime durations are uniformly sampled between min_length and max_length.
    The last regime is truncated to exactly fill n_candles.
    """
    regimes = list(REGIME_PARAMS.keys())
    sequence: list[tuple[str, int]] = []
    remaining = n_candles

    # Start with a random regime
    current_regime = str(rng.choice(regimes))

    while remaining > 0:
        duration = int(rng.integers(min_length, max_length + 1))
        duration = min(duration, remaining)
        sequence.append((current_regime, duration))
        remaining -= duration

        # Transition: pick next regime (avoid repeating same regime)
        next_choices = [r for r in regimes if r != current_regime]
        current_regime = str(rng.choice(next_choices))

    return sequence


# ── OHLCV Generator ────────────────────────────────────────────────────

def generate_ohlcv(
    config: TokenConfig,
    n_candles: int = NUM_CANDLES,
    rng: Optional[np.random.Generator] = None,
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data using Geometric Brownian Motion
    with regime-dependent drift and volatility.

    The process:
    1. Generate a regime sequence covering all candles
    2. For each candle, simulate a GBM step with regime-dependent parameters
    3. Construct realistic OHLCV from the intrabar Brownian bridge
    4. Add volume correlated with volatility
    5. Inject occasional spikes (more frequent for mid-cap / meme-like)

    Key relationships:
    - hourly_vol = annual_vol / sqrt(hours_per_year)
    - ATR is generated from the high-low range, which emerges from the
      intra-bar Brownian bridge simulation plus wick extensions
    - The target_atr_pct is used for spike sizing only

    Args:
        config: TokenConfig with symbol, initial price, vol, ATR target, etc.
        n_candles: Number of hourly candles to generate
        rng: Numpy random generator for reproducibility

    Returns:
        DataFrame with columns: open, high, low, close, volume
        and DatetimeIndex at 1h frequency
    """
    if rng is None:
        rng = np.random.default_rng(RANDOM_SEED)

    # Base hourly volatility from annualized target
    base_hourly_vol = config.annual_vol / np.sqrt(HOURS_PER_YEAR)

    # Generate regime sequence
    regime_seq = generate_regime_sequence(n_candles, rng)

    # Expand regimes into per-candle array
    regime_per_candle: list[str] = []
    for regime_name, duration in regime_seq:
        regime_per_candle.extend([regime_name] * duration)
    regime_per_candle = regime_per_candle[:n_candles]

    # Pre-allocate arrays
    opens = np.empty(n_candles)
    highs = np.empty(n_candles)
    lows = np.empty(n_candles)
    closes = np.empty(n_candles)
    volumes = np.empty(n_candles)

    dt = 1.0 / HOURS_PER_YEAR
    price = config.initial_price

    for i in range(n_candles):
        regime = regime_per_candle[i]
        params = REGIME_PARAMS[regime]

        # Regime-dependent drift and volatility
        drift_annual = params["drift_annual"]
        vol_mult = params["vol_mult"]

        hourly_vol = base_hourly_vol * vol_mult
        hourly_drift = drift_annual * dt

        # ── GBM close price ────────────────────────────────────────
        z = rng.standard_normal()
        log_return = hourly_drift - 0.5 * hourly_vol**2 + hourly_vol * z
        # (using full GBM with Itô correction: μ - σ²/2)

        open_price = price
        close_price = price * np.exp(log_return)

        # ── Intra-bar path (Brownian bridge) for H/L ───────────────
        # Simulate substeps within the bar to find realistic high/low
        n_substeps = 6
        sub_vol = hourly_vol / np.sqrt(n_substeps)
        sub_drift = (log_return) / n_substeps  # Spread total drift evenly

        sub_prices = [open_price]
        current = open_price
        for _ in range(n_substeps):
            z_sub = rng.standard_normal()
            sub_return = sub_drift - 0.5 * sub_vol**2 + sub_vol * z_sub
            current = current * np.exp(sub_return)
            sub_prices.append(current)

        # Replace last substep with the actual close (Brownian bridge endpoint)
        sub_prices[-1] = close_price
        all_prices = np.array(sub_prices)

        high_price = np.max(all_prices)
        low_price = np.min(all_prices)

        # Add small wick extensions (shadows typical in crypto candles)
        wick_frac = rng.exponential(0.15)  # Fraction of hourly_vol
        wick_up = wick_frac * hourly_vol * max(open_price, close_price)
        wick_down = wick_frac * hourly_vol * min(open_price, close_price)
        high_price = max(high_price, max(open_price, close_price) + wick_up)
        low_price = min(low_price, min(open_price, close_price) - wick_down)

        # ── Spike injection ────────────────────────────────────────
        if rng.random() < config.spike_probability:
            spike_dir = rng.choice([-1, 1])
            # Spike magnitude as fraction of price
            spike_size = config.target_atr_pct * config.spike_magnitude_atr * rng.uniform(0.3, 1.0)
            spike_move = spike_dir * spike_size

            # Override close with spike
            close_price = price * np.exp(log_return + spike_move)

            if spike_dir > 0:
                # Pump: extend high
                high_price = close_price * (1 + rng.uniform(0.001, 0.003))
                high_price = max(high_price, open_price)
            else:
                # Dump: extend low
                low_price = close_price * (1 - rng.uniform(0.001, 0.003))
                low_price = min(low_price, open_price)
                low_price = max(low_price, 0.0001)

        # ── OHLCV consistency ──────────────────────────────────────
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)
        low_price = max(low_price, 0.0001)  # Safety floor

        # ── Volume ─────────────────────────────────────────────────
        # Volume correlates with |return| and regime volatility
        abs_return_pct = abs(close_price - open_price) / open_price
        vol_regime_mult = vol_mult
        vol_spike_mult = 1.0 + abs_return_pct * 30.0  # Big moves → big volume

        base_vol = config.base_volume * rng.uniform(0.5, 1.5)
        volume = base_vol * vol_regime_mult * vol_spike_mult
        volume *= rng.lognormal(0, 0.25)  # Noise

        # Store
        opens[i] = open_price
        highs[i] = high_price
        lows[i] = low_price
        closes[i] = close_price
        volumes[i] = volume

        # Advance price
        price = close_price

    # Build DataFrame with DatetimeIndex
    # Start from 1 year ago
    end_ts = pd.Timestamp.now().floor("h")
    start_ts = end_ts - pd.Timedelta(hours=n_candles - 1)
    index = pd.date_range(start=start_ts, periods=n_candles, freq="h")

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )

    return df


# ── Main Pipeline ──────────────────────────────────────────────────────

def run_generation() -> dict:
    """
    Generate synthetic OHLCV data for all configured tokens and
    save to PPMTStorage.

    Returns:
        Summary dict with per-token statistics.
    """
    start_time = time.time()
    rng = np.random.default_rng(RANDOM_SEED)

    # Initialize storage and classifier
    storage = PPMTStorage()
    classifier = AssetClassifier()

    print(f"\n{'='*65}")
    print(f"  PPMT Synthetic Market Data Generator")
    print(f"  {len(TOKEN_CONFIGS)} tokens x {NUM_CANDLES} candles (1h)")
    print(f"  Random seed: {RANDOM_SEED}")
    print(f"{'='*65}\n")

    summary = {}

    for config in TOKEN_CONFIGS:
        print(f"  Generating {config.symbol} ({config.asset_class}) ... ", end="", flush=True)
        t0 = time.time()

        # Generate data
        df = generate_ohlcv(config, NUM_CANDLES, rng)

        # Register asset with classifier
        info = classifier.classify(config.symbol)
        storage.register_asset(config.symbol, info.asset_class)

        # Save to storage
        inserted = storage.save_ohlcv(config.symbol, "1h", df)

        elapsed = time.time() - t0

        # Compute statistics
        returns = df["close"].pct_change().dropna()
        atr_pct = (df["high"] - df["low"]) / df["close"]
        stats = {
            "symbol": config.symbol,
            "asset_class": info.asset_class,
            "weight_profile": info.weight_profile,
            "candles_requested": NUM_CANDLES,
            "candles_inserted": inserted,
            "initial_price": df["open"].iloc[0],
            "final_price": df["close"].iloc[-1],
            "price_return_pct": (df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100,
            "min_price": df["low"].min(),
            "max_price": df["high"].max(),
            "target_atr_pct": config.target_atr_pct * 100,
            "actual_mean_atr_pct": atr_pct.mean() * 100,
            "std_atr_pct": atr_pct.std() * 100,
            "mean_hourly_return_pct": returns.mean() * 100,
            "std_hourly_return_pct": returns.std() * 100,
            "annualized_vol_pct": returns.std() * np.sqrt(HOURS_PER_YEAR) * 100,
            "mean_volume": df["volume"].mean(),
            "max_volume": df["volume"].max(),
            "generation_time_s": round(elapsed, 2),
        }

        summary[config.symbol] = stats
        print(f"done ({elapsed:.2f}s) — {inserted} candles inserted")

    # ── Print Summary Statistics ────────────────────────────────────
    total_time = time.time() - start_time

    print(f"\n{'='*65}")
    print(f"  GENERATION COMPLETE — Summary Statistics")
    print(f"{'='*65}\n")

    for symbol, s in summary.items():
        print(f"  ┌─ {symbol} ({s['asset_class']}/{s['weight_profile']}) ─────────")
        print(f"  │  Candles:       {s['candles_inserted']:,} / {s['candles_requested']:,}")
        print(f"  │  Price:         ${s['initial_price']:,.2f} → ${s['final_price']:,.2f} ({s['price_return_pct']:+.1f}%)")
        print(f"  │  Price Range:   ${s['min_price']:,.2f} — ${s['max_price']:,.2f}")
        print(f"  │  ATR:           {s['actual_mean_atr_pct']:.2f}% (target: {s['target_atr_pct']:.1f}%, std: {s['std_atr_pct']:.2f}%)")
        print(f"  │  Hourly Return: {s['mean_hourly_return_pct']:+.5f}% (std: {s['std_hourly_return_pct']:.3f}%)")
        print(f"  │  Ann. Vol:      {s['annualized_vol_pct']:.1f}%")
        print(f"  │  Mean Volume:   ${s['mean_volume']:,.0f}")
        print(f"  │  Max Volume:    ${s['max_volume']:,.0f}")
        print(f"  └─────────────────────────────────────")

    print(f"\n  Total generation time: {total_time:.2f}s")

    # ── Verify Data in Storage ──────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Verification — Querying PPMTStorage")
    print(f"{'='*65}\n")

    assets = storage.get_assets()
    print(f"  Registered assets: {len(assets)}")
    for a in assets:
        print(f"    {a['symbol']:12s}  class={a['asset_class']:12s}  profile={a['weight_profile']:12s}  candles={a['candle_count']}")

    print()
    for config in TOKEN_CONFIGS:
        count = storage.get_candle_count(config.symbol, "1h")
        df_loaded = storage.load_ohlcv(config.symbol, "1h")
        print(f"  {config.symbol:12s}  storage count={count:,}  loaded shape={df_loaded.shape}")
        if not df_loaded.empty:
            # Quick sanity check: OHLCV consistency
            violations_high = (df_loaded["high"] < df_loaded[["open", "close"]].max(axis=1)).sum()
            violations_low = (df_loaded["low"] > df_loaded[["open", "close"]].min(axis=1)).sum()
            print(f"               OHLCV violations: high={violations_high}, low={violations_low}")

    storage.close()

    print(f"\n{'='*65}")
    print(f"  ✅ All synthetic data ingested successfully")
    print(f"{'='*65}\n")

    return summary


def main():
    summary = run_generation()
    # Exit with error if any token failed
    for symbol, s in summary.items():
        if s["candles_inserted"] == 0:
            print(f"  ❌ ERROR: No candles inserted for {symbol}")
            sys.exit(1)


if __name__ == "__main__":
    main()
