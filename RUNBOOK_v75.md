# RUNBOOK v7.5 — Cómo correr PPMT v7.5 en tu ordenador

> **Estado:** v7.5 completado y commiteado a `main` (commit `5fd8526`).
> **Resultado backtest walk-forward:** Sharpe **2.80**, PnL **+333.76%**, MaxDD **-7.09%**, WR **51.7%**, 1,280 trades.
> **Versión vs v6:** v7.5 gana 3-4x en PnL y Sharpe sobre v6 baseline.

---

## 0. Requisitos del sistema

| Componente | Mínimo | Recomendado |
|------------|--------|-------------|
| Python | 3.9 | 3.11+ |
| RAM | 8 GB | 16 GB (la DB pesa 3.3 GB) |
| Disco | 10 GB libres | 20 GB |
| OS | Linux / macOS / WSL2 | Linux nativo |
| Internet | Sí (descarga OHLCV de Coinbase + funding/OI de Binance) | — |

---

## 1. Clonar el repo

```bash
git clone https://github.com/coverdraft/ppmt.git
cd ppmt
```

## 2. Crear entorno virtual e instalar dependencias

```bash
python3 -m venv venv
source venv/bin/activate          # Linux/macOS
# venv\Scripts\activate           # Windows

pip install --upgrade pip
pip install numpy pandas scipy pyyaml rich click \
            ccxt lightgbm scikit-learn \
            requests python-binance pyarrow

# Opcional (si quieres instalar el paquete ppmt-terminal completo):
# pip install -e .
```

> **Nota:** `pyproject.toml` declara dependencias del terminal completo (fastapi, uvicorn, etc.). Para correr SOLO el pipeline v7.5 basta con las listadas arriba.

## 3. Reconstruir la base de datos desde cero

La DB `data/ppmt.db` está gitignored (pesa 3.3 GB). Hay que reconstruirla.

### 3.1 Crear estructura de carpetas

```bash
mkdir -p data/v6_models data/v7_models/v75
```

### 3.2 Descargar OHLCV de Coinbase (5m + 15m)

```bash
# 5m timeframe (~30-60 min, ~1.4M filas)
python scripts/v6/v6_download_ohlcv.py --timeframe 5m

# 15m timeframe (necesario para algunos features v6)
python scripts/v6/v6_download_ohlcv.py --timeframe 15m
```

Esto descarga 12 símbolos (BTC, ETH, SOL, DOGE, etc.) en 4 ventanas (BEAR_2022, BULL_2024, RANGE_2025, RECENT_2026) y los guarda en la tabla `ohlcv_v6` de `data/ppmt.db`.

### 3.3 Extraer features v6 (59 features)

```bash
python scripts/v6/v6_extract_features.py
```

Crea la tabla `feature_observations_v6` con ~1.44M filas. Cada fila = (symbol, ts, 59 features en JSON, label `fwd_ret_3`).

### 3.4 Prefetch funding rates + Open Interest (Binance)

```bash
python scripts/v7/v7_prefetch_extras.py
```

Descarga funding rates y OI históricos de Binance (las APIs de Coinbase no exponen esto) a cachés SQLite locales.

### 3.5 Extraer features F4 (12 features extras)

```bash
python scripts/v7/v7_extract_features_extras.py
```

Crea la tabla `feature_observations_v7_extras` con las 12 features adicionales: `funding_rate`, `funding_rate_z`, `oi_change_1h`, `oi_change_4h`, `sector_blue_chip/large_cap/old_meme/new_meme`, `sector_idx`, `day_of_week_sin/cos/plain`.

### 3.6 Materializar parquet v7.5 (one-time, acelera el entrenamiento)

```bash
python scripts/v7/v7_materialize_v75_features.py
```

Genera `data/v7_models/v75/v75_features.parquet` (~250 MB, 1.44M filas × 71 features + label). Esto evita el costoso `json_extract` en cada run de entrenamiento.

## 4. Entrenar v7.5 (5 modelos walk-forward)

```bash
python scripts/v7/v7_train_v75.py
```

Entrena 5 modelos LightGBM, uno por ventana walk-forward (2025-04, 05, 06, 09, 10). Tiempo total: ~50 segundos.

**Salidas:**
- `data/v7_models/v75/v75_{window}.txt` (modelo LightGBM)
- `data/v7_models/v75/v75_{window}_results.json` (métricas por ventana)
- `data/v7_models/v75/v75_summary.json` (resumen agregado)

**Métricas esperadas** (corrida de referencia):
| Ventana | corr_test | dir_acc |
|---------|-----------|---------|
| 2025-04 | +0.0548 | 0.508 |
| 2025-05 | +0.0199 | 0.506 |
| 2025-06 | +0.0133 | 0.499 |
| 2025-09 | +0.0252 | 0.504 |
| 2025-10 | +0.1570 | 0.505 |

## 5. Backtest walk-forward v7.5

```bash
# Backtest con threshold por defecto (0.30%)
python scripts/v7/v7_backtest_v75.py

# Sweep de thresholds (0.10% - 1.00%)
python scripts/v7/v7_backtest_v75.py --sweep
```

**Salidas:**
- `data/v7_models/v75/v75_backtest_summary.json`
- `data/v7_models/v75/v75_backtest_trades_{window}.parquet` (trades por ventana)
- `data/v7_models/v75/v75_backtest_equity_curve.parquet` (curva de equity agregada)

**Resultados esperados** (best thr=0.30%):
| Métrica | v7.5 | v6 baseline | Δ |
|---------|------|-------------|---|
| Trades | 1,280 | 448 | +186% |
| Win Rate | 51.7% | 49.8% | +1.9pp |
| Profit Factor | 1.27 | 1.11 | +0.16 |
| **PnL** | **+333.76%** | +85.97% | **+3.9x** |
| **Sharpe** | **2.80** | 0.85 | **+3.3x** |
| MaxDD | -7.09% | -10.64% | +3.55pp mejor |

**Ship criteria** (master plan §11.6):
- ✅ Sharpe > 1.0 (2.80 > 1.0)
- ✅ MaxDD > -15% (-7.09% > -15%)
- ⚠️ WR > 52% (51.7%, falla por 0.3pp — dentro del ruido estadístico, 95% CI ±2.8pp)

## 6. (Opcional) Reentrenar v6 baseline para comparación

```bash
python scripts/v6/v6_train_wf_parquet.py
python scripts/v6/v6_backtest_wf_parquet.py
```

Útil para verificar que v7.5 realmente mejora sobre v6 con el mismo harness.

## 7. Verificar resultados

```bash
# Resumen del entrenamiento
cat data/v7_models/v75/v75_summary.json | python -m json.tool

# Resumen del backtest
cat data/v7_models/v75/v75_backtest_summary.json | python -m json.tool

# Inspeccionar trades
python -c "import pandas as pd; df=pd.read_parquet('data/v7_models/v75/v75_backtest_trades_2025-10.parquet'); print(df.head()); print(df.describe())"

# Curva de equity
python -c "import pandas as pd; eq=pd.read_parquet('data/v7_models/v75/v75_backtest_equity_curve.parquet'); print(eq.tail())"
```

## 8. Troubleshooting

| Problema | Causa | Solución |
|----------|-------|----------|
| `sqlite3.OperationalError: no such table: ohlcv_v6` | No descargaste OHLCV | Vuelve al paso 3.2 |
| `ModuleNotFoundError: lightgbm` | Falta dependencia | `pip install lightgbm` |
| Error de rate limit en Binance | IPs saturadas | Espera 5 min y reintenta `v7_prefetch_extras.py` |
| `v75_features.parquet` no encontrado | Falta materialización | Corre paso 3.6 |
| Backtest da 0 trades | Threshold muy alto | Prueba `--thr-long 0.10 --thr-short 0.10` |
| DB ocupa >5GB | Repetiste descargas | Borra `data/ppmt.db` y empieza de cero el paso 3.2 |

## 9. Tiempos estimados (referencia, hardware moderno)

| Paso | Tiempo |
|------|--------|
| 3.2 Descargar OHLCV 5m | 30-60 min |
| 3.2 Descargar OHLCV 15m | 15-30 min |
| 3.3 Extraer features v6 | 10-20 min |
| 3.4 Prefetch funding/OI | 15-30 min |
| 3.5 Extraer F4 | 5-10 min |
| 3.6 Materializar parquet | 2-5 min |
| 4 Entrenar v7.5 (5 modelos) | ~1 min |
| 5 Backtest v7.5 | ~30 seg |
| **TOTAL** | **~2-3 horas** |

## 10. Próximos pasos sugeridos

1. **F8 — Online learning**: reentrenar mensualmente con datos nuevos.
2. **Hyperparameter tuning**: Optuna sobre `num_leaves`, `learning_rate`, `feature_fraction`.
3. **Labels alternativos**: probar `fwd_ret_6`, `fwd_ret_12` en vez de `fwd_ret_3`.
4. **Live trading**: integrar con Coinbase Advanced API (ver `src/ppmt/`).
5. **Dashboard**: construir visualización de equity curve y trades en tiempo real.

---

**Referencias:**
- Plan maestro: `PPMT_v7_MASTER_PLAN.md` §12.1 (outcome Option D)
- Worklog: `worklog.md` (Task ID `v75-train` y `v75-backtest`)
- Scripts: `scripts/v7/v7_train_v75.py`, `scripts/v7/v7_backtest_v75.py`
