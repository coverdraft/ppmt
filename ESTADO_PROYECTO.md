# ESTADO DEL PROYECTO PPMT — v7 paper trader

**Última actualización:** 2026-06-26
**Responsable:** coverdraft + AI assistant

---

## 1. Mecánica del pipeline: OK ✅

- Path fix aplicado en `scripts/v7/paper_trader/model.py`:
  `MODEL_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "models"`
- `v7_layer2_rolling_retrain.py --dry-run` corre sin errores.
- Datos se descargan desde Bybit vía CCXT (público, sin API key).
- 59 features en `scripts/v7/paper_trader/features.py` (38 v5 + 21 v6).
- LightGBM entrena y guarda modelo con atomic swap (.tmp + fsync + rename).
- Acceptance gate funciona (FIRST_DEPLOY / ACCEPT / ACCEPT_WITH_WARNING / REJECT).
- Walk-forward split: train 83% / val 10% / test 7% (proporcional a ventana).
- CSV logs en `data/paper_trading/logs/retrain_<SYM>.csv`.

---

## 2. Señal predictiva: AUSENCIA TOTAL ❌

Resultados de dry-runs:

| Run        | val_dir_acc | val_corr | best_iter | n_trades |
|------------|-------------|----------|-----------|----------|
| BTC 7d     | 0.439       | -0.037   | 1         | 0        |
| BTC 30d    | 0.508       | +0.096   | 28        | 0        |
| BTC 90d    | 0.507       | +0.012   | 1         | 0        |
| ETH 30d r1 | 0.516       | -0.064   | 1         | 0        |
| ETH 30d r2 | 0.516       | -0.064   | 1         | 0        |

**Diagnóstico:**
- `best_iter=1` en 3/4 runs → LightGBM no encuentra nada que aprender.
- `val_corr ≈ 0` = sin correlación pred vs actual.
- `dir_acc ≈ 0.51` = sesgo de distribución de labels, no edge.
- `n_trades=0` en todos los runs = las predicciones nunca superan los thresholds (THR_LONG=0.20, THR_SHORT=0.50).

---

## 3. Causa raíz probable

Label `fwd_ret_3` = retorno forward a 3 velas = **15 minutos adelante** en timeframe de 5min.

15 min en crypto es ~99% ruido. El ratio señal/ruido (|mean|/std) del label es probablemente < 0.005, lo que significa que no hay señal para que LightGBM aprenda, por más features que tenga.

---

## 4. Features: NO son el problema

Las 59 features de `paper_trader/features.py` cubren:

- **Volumen relativo:** vol_ratio, vol_z, vol_delta_3, vol_acceleration
- **Microestructura:** wick_imbalance_3, body_consistency_5
- **Cross-asset:** btc_ret_1m/5m/15m, btc_vol_z, btc_trend_50, eth_corr_30, btc_alt_spread_15m
- **Régimen:** vol_regime, trend_50, regime_vol_trend, atr_percentile_50
- **Temporal:** hour_sin, hour_cos, hour_quantile, dow
- **Técnico:** RSI, EMA crosses, ATR, breakout, candlestick patterns

Falta order book real (bid/ask depth) pero no se puede con OHLCV puro. Las features actuales son razonablemente comprehensivas para lo que se puede sacar de candles.

---

## 5. Próximos pasos (EN ORDEN)

### PASO 1: Diagnóstico cuantitativo del label y features

Correr `scripts/v7/diagnose_signal.py` para medir:
- `|mean|/std` del label: si < 0.005 → confirmado que 15 min es ruido puro
- Cuántas features tienen `|corr| > 0.05` con el target: si < 5 → no hay señal
- Drift entre train y val: si alguna feature tiene `|drift_σ| > 0.5` → el split está sabotajeando

### PASO 2: Cambiar HORIZON de 3 a 12 (1 hora adelante)

Más horizonte = más señal. 1 hora debería:
- Aumentar |mean|/std del label (más movimiento para capturar)
- Revivir best_iter de 1 a 30+
- Hacer que val_corr sea consistentemente positiva

### PASO 3: Si HORIZON=12 no funciona, probar 288 (24 horas)

24 horas suele ser donde recién aparece edge real en crypto. Trade-off: más lag entre señal y resultado, pero la señal es mucho más fuerte.

### PASO 4: Ajustar thresholds de trading

Si cambiamos HORIZON, hay que ajustar los thresholds (THR_LONG, THR_SHORT) y el hold period para que sean consistentes con el nuevo horizonte.

---

## 6. Infraestructura pendiente (F9.x roadmap)

| Sub-fase | Descripción | Estado |
|----------|-------------|--------|
| F9.1 | Drift-based early trigger | Pendiente |
| F9.2 | Hot-reload en paper trader (mtime check) | Pendiente |
| F9.3 | Consecutive reject alerting (3 REJECT → alerta) | Pendiente |
| F9.4 | Features F4 (funding_rate, oi_change, sector) | Pendiente |
| F9.5 | Multi-symbol portfolio retrain | Pendiente |

---

## 7. Git status

- Repo: https://github.com/coverdraft/ppmt
- Branch: main
- Último commit bueno (antes del gitlink roto): `627cff2`
- Commit roto: `f8fc741` (solo metió gitlink `160000 ppmt`)
- **FIX APLICADO:** gitlink removido, archivos creados, commit limpio realizado.
