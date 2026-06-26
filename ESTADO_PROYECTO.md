# ESTADO DEL PROYECTO PPMT

Ultima actualizacion: 2026-06-27 (sesion 5 — V12 paper trading)
Repo: https://github.com/coverdraft/ppmt

## 0. Resumen ejecutivo

El proyecto tiene **dos pipelines activos y validados**:

| Pipeline | Horizonte | Base TF | Features | WR | Consistencia | Estado |
|----------|-----------|---------|----------|-----|-------------|--------|
| **V7** (24h) | H=288 (24h) | 5m | 58 | 64-72% | 3-4/4 | Paper trading listo |
| **V12** (1h) | H=12 (1h) | 1m->5m | 80 | 65-69% | 4-6/6 | Paper trading en progreso |

**V12 es el pipeline actual** - temporalidad baja con microestructura, mejor WR y consistencia.

---

## 1. Pipeline V7: 24h Horizon (COMPLETADO)

- LightGBM binary classification P(UP en 24h), HORIZON=288
- 58 features (dow eliminado), quantile-based trading
- Sequential backtest: hold=288 bars (24h), no overlap
- Walk-forward split: 83/10/7
- Per-symbol config: Q, window_size, cost_pct, HP preset (SYMBOL_CONFIG)

## 2. V7 DEEP OPTIMIZATION (180d, 5040 configs)

### 2a. Setup
- **4 tokens**: DOGE, AVAX, SOL, ETH (los 4 con edge confirmado)
- **5 HP presets**: default, more_reg, less_reg, very_reg, slow_deep
- **7 Q configs**: Q95/5, Q92/8, Q90/10, Q87/13, Q85/15, Q82/18, Q80/20
- **3 window sizes**: 100, 200, 400
- **3 cost models**: maker (0.04%), mid (0.09%), taker (0.14%)
- **4 rolling windows**: walk-forward cross-validation
- **Total**: 2 x 5 x 7 x 3 x 3 x 4 x 4 = 5040 configs (2 runs de 2520)

### 2b. Mejor config por token (180d, maker fees)

| Token | Q Config | HP | Window | PnL | Sharpe | WR | MaxDD | PF | Consistencia |
|-------|----------|-----|--------|------|--------|------|-------|------|-------------|
| **AVAX/USDT** | Q82/18 | more_reg | 200 | **+44.76%** | +0.292 | 64.4% | -10.37% | 4.69 | **4/4** |
| **SOL/USDT** | Q85/15 | very_reg | 200 | **+41.46%** | +0.325 | 61.9% | -12.88% | 3.86 | **4/4** |
| **DOGE/USDT** | Q95/5 | default | 400 | **+41.55%** | +0.725 | 72.3% | -4.61% | 30.78 | **4/4** |
| **ETH/USDT** | Q87/13 | default | 400 | **+36.56%** | +0.324 | 70.2% | -9.15% | 2.55 | 3/4 |

### 2c. Horizontes V7 - SOLO H=288 funciona (con 5m features)

| Horizonte | Duracion | Avg PnL | % Positivos | Veredicto |
|-----------|----------|---------|-------------|-----------|
| H=6       | 30min    | -100.8% | 0%          | CATASTROFICO |
| H=12      | 1h       | -56.5%  | 0%          | DESASTRE |
| **H=288** | **24h**  | **+5.6%** | **66.7%** | **UNICO VIABLE** |

**NOTA IMPORTANTE**: Esta conclusion aplica SOLO al pipeline V7 (5m features, sin microestructura).
El pipeline V12 demostro que H=12 (1h) ES viable con 1m microestructura features.

---

## 3. Pipeline V12: 1h Horizon Low-TF Microstructure (ACTUAL)

### 3a. Arquitectura

- **Base data**: 1m OHLCV candles -> agregados a 5m bars
- **Features**: 80 (incluye microestructura: CVD, vol_delta, price_impact)
- **MTF features**: 5m/15m/1h timeframes
- **BTC correlation**: eth_corr_30, btc_corr_30
- **Modelo**: LightGBM binary classifier, P(UP en 1h)
- **HORIZON**: H=12 (1h forward, 12 x 5min bars)
- **Trading**: Quantile-based, rolling window=200, maker cost=0.04%

### 3b. Evolucion V10 -> V11 -> V12

| Version | Cambio | WR | Consistencia |
|---------|--------|-----|-------------|
| V10 | 1m microstructure dataset, 80 features | 0.39 | 0/4 |
| V11 | Pipeline completo, train+backtest, H=12 | 0.61 | 2/4 |
| **V12** | Optimized Q thresholds, direction+trend filters | **0.65-0.69** | **4-6/6** |

**Mejora V12 vs V10**: WR +77% (0.39 -> 0.693)

### 3c. V12 Optimization - 108 configs

- **9 Q configs**: Q80/20, Q82/18, Q85/15, Q87/13, Q90/10, Q92/8, Q95/5, Q97/3, Q98/2
- **2 direction modes**: both, long_only
- **2 trend filters**: none, aligned
- **3 symbols**: SOL, DOGE, AVAX
- **Total**: 9 x 2 x 2 x 3 = 108 configs

### 3d. Best Configs V12 (walk-forward validated, 6 windows)

| Token | Profile | Q Config | Direction | Trend | WR | PnL% | PF | Sharpe | Windows |
|-------|---------|----------|-----------|-------|------|------|------|--------|---------|
| **SOL** | Balanced | Q95/5 | both | none | **0.693** | +4369% | 3.35 | +0.385 | **4/4** |
| **DOGE** | Conservative | Q98/2 | both | none | **0.681** | +2548% | 3.03 | +0.343 | **6/6** |
| **DOGE** | Balanced | Q95/5 | both | none | **0.649** | +3064% | 2.40 | +0.277 | **6/6** |
| **AVAX** | Conservative | Q97/3 | long_only | aligned | **0.625** | +1062% | 3.35 | +0.383 | **6/6** |
| **AVAX** | Balanced | Q95/5 | both | aligned | **0.622** | +2186% | 2.62 | +0.301 | **6/6** |

### 3e. Hallazgos clave V12

1. **Selectividad del quantile = mejor WR**: Q95/5 (WR 0.70) >> Q85/15 (WR 0.62)
2. **Menos trades pero mejor calidad**: frecuencia baja = menos ruido + menos costos
3. **Trend alignment es ambivalente**: SOL long_only+aligned da WR 0.738, pero "both+none" tiene mejor PnL
4. **Cost-aware labels NO mejoran el modelo**: threshold 0.08% empeoro resultados
5. **Signal strength es el filtro mas poderoso** (analisis de bins)
6. **Todos los tokens robustos**: 100% de windows rentables
7. **Microestructura es clave**: CVD_5m, vol_delta, price_impact son top features

### 3f. V12 SYMBOL_CONFIG

```python
V12_SYMBOL_CONFIG = {
    "SOL": {
        "balanced":  {"q_long": 95, "q_short": 5,  "direction": "both",     "trend_filter": "none"},
        "conservative": {"q_long": 95, "q_short": 5, "direction": "long_only", "trend_filter": "aligned"},
    },
    "DOGE": {
        "balanced":  {"q_long": 95, "q_short": 5,  "direction": "both",     "trend_filter": "none"},
        "conservative": {"q_long": 98, "q_short": 2, "direction": "both",     "trend_filter": "none"},
    },
    "AVAX": {
        "balanced":  {"q_long": 95, "q_short": 5,  "direction": "both",     "trend_filter": "aligned"},
        "conservative": {"q_long": 97, "q_short": 3, "direction": "long_only", "trend_filter": "aligned"},
    },
}
```

---

## 4. Hallazgos criticos (ambos pipelines)

1. **Temporalidad baja FUNCIONA con microestructura** (V12) - H=12 viable con 1m features
2. **Temporalidad baja NO funciona sin microestructura** (V7) - H=12 catastrofico con 5m features
3. **Selectividad > frecuencia** - Q95/5 > Q80/20 en ambos pipelines
4. **Altcoins > ETH > BTC** - mercados menos eficientes = mas edge
5. **SHORT es esencial** - LONG-only pierde en V7; en V12 both > long_only
6. **Per-symbol tuning es critico** - hasta +37pp de diferencia
7. **Maker fees necesarias** - 0.04% RT vs 0.14% taker = +5pp PnL
8. **Mas operaciones NO = mejor** - frecuencia alta = ruido + costos
9. **BTC = dead end confirmado** - mercado demasiado eficiente
10. **Edge no es un punto aislado** - 193+ configs con PnL>10% en V7

---

## 5. Estado honesto

### V7 (24h)
- **3/4 tokens con 4/4 consistencia y PnL > 40%** - resultado excelente
- PnLs agregados positivos y robustos PERO backtest IS -> necesita paper trading OOS

### V12 (1h)
- **3/3 tokens con 4-6/6 consistencia** - muy robusto
- WR 0.625-0.693 - excelente para 1h horizon
- PnLs en backtest muy altos (perf de paper trading sera menor)
- **PENDIENTE**: validacion en paper trading OOS

### Riesgos
- Backtest no es OOS puro - paper trading es obligatorio
- V12 PnLs de backtest son irrealmente altos - esperamos ~50% de degradacion OOS
- AVAX WR 0.625 es el mas bajo - margen menor
- Trend alignment puede cambiar de efectividad en mercados diferentes

---

## 6. Proximos pasos

1. **Paper trading V12** con configs validados (SOL, DOGE, AVAX) - EN PROGRESO
2. Comparar resultados V12 paper trading vs V7 paper trading
3. Si edge se confirma: aumentar allocation y anadir mas tokens
4. Si edge falla OOS: considerar features adicionales (funding rate, OI, orderbook)
5. Test H=6 (30min) con microestructura features
6. Anadir XRP, LINK, SUI al pipeline V12

---

## 7. Git
Ultimo commit: ver git log
Regla: todo cambio = commit + push

---

## 8. Archivos clave

### V12 Pipeline
- `scripts/v11/v11_build_dataset.py` - 80 features from 1m data
- `scripts/v11/v11_train.py` - Binary classifier training
- `scripts/v11/v11_backtest.py` - Fixed + adaptive exits backtest
- `scripts/v12/v12_optimize.py` - Exhaustive parameter optimization (108 configs)
- `scripts/v12/v12_validate.py` - Walk-forward validation (6 windows)
- `scripts/v12/v12_analyze.py` - Feature/filter analysis
- `scripts/v12/v12_adaptive_exit.py` - Trailing stop testing
- `data/v12/V12_SUMMARY.md` - V12 results summary
- `data/v12/v12_optimization_results.csv` - All optimization results
- `data/v12/v12_validation_results.json` - Walk-forward validation results

### V7 Pipeline
- `scripts/v7/deep_optimize.py` - deep optimization (180d, 5040 configs)
- `scripts/v7/comprehensive_sweep.py` - sweep multi-token x multi-horizon x rolling
- `scripts/v7/paper_trader/model.py` - SYMBOL_CONFIG centralizada
- `scripts/v7/v7_layer2_rolling_retrain.py` - evaluate_test con per-symbol config
- `data/sweep_results/deep_opt_test.csv` - DOGE/AVAX per-window (2520 rows)
- `data/sweep_results/deep_opt_eth_sol.csv` - ETH/SOL per-window (2520 rows)
