"""
V10 — Exit-Aware Classifier + Enhanced Multi-Timeframe

Key changes vs V9:
  1. MFE/MAE labels: Predict trade quality, not just win/lose
     - mfe_pct: Max favorable excursion (% from entry)
     - mae_pct: Max adverse excursion (% from entry)
     - mfe_mae_ratio: Entry efficiency (high = good entry, bad exit)
     - time_to_mfe: Minutes to peak favorable price
  2. BTC correlation features (restored from V8)
     - btc_ret_1m, btc_ret_5m: BTC momentum
     - btc_impulse_score: Sudden BTC move detection
     - alt_btc_lag_1: Does the alt lag BTC?
     - alt_btc_corr_30: Rolling correlation
  3. 1h timeframe context (aggregated from 1m)
     - mtf1h_ema_align, mtf1h_trend_str, mtf1h_ret_3, mtf1h_atr_pct
     - mtf_trend_agree now includes 1h (1m/5m/15m/1h)
  4. Dual model training:
     - Model A: Binary classifier (same as V9 but with new features)
     - Model B: MFE/MAE regressor — predicts "entry quality"
  5. Adaptive backtest exits:
     - Use predicted MFE to set dynamic TP/SL
     - Use predicted MAE to set trailing stop distance
"""
