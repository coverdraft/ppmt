"""
V12 Paper Trader — Quantitative trading cycle with drift detection and rolling retrain.

Architecture:
- Feed: Bybit 5m OHLCV (direct API, no 1m→5m aggregation)
- Features: 80 features (microstructure + MTF + BTC correlation)
- Model: V11 LightGBM binary classifier (data/v11/models/)
- Trading: V12 optimized Q thresholds, direction mode, trend filter
- Database: SQLite — signals, trades, equity, predictions, model_versions, drift_events
- Metrics: Win Rate, Sharpe, Drawdown, Profit Factor, regime-aware performance
- Drift: Detection of model degradation (WR decline, prediction shift, regime change)
- Retrain: Rolling retrain with acceptance gate and atomic model swap
- Horizon: H=12 (1h forward)

Complete cycle:
  Market → Features → Model → Prediction → Execute → Store (SQLite)
      → Evaluate (Metrics) → Detect Drift → Retrain (if needed) → Deploy New Model
"""
