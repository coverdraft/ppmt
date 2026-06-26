"""
V12 Paper Trader — Low-TF microstructure pipeline with optimized trading configs.

Uses 1m OHLCV data → aggregated to 5m bars → 80 features → LightGBM prediction
→ quantile-based signal generation with V12-optimized thresholds.

Architecture:
- Feed: Bybit 1m OHLCV → aggregate to 5m
- Features: 80 features (microstructure + MTF + BTC correlation)
- Model: V11 LightGBM binary classifier (data/v11/models/)
- Trading: V12 optimized Q thresholds, direction mode, trend filter
- Horizon: H=12 (1h forward)
"""
