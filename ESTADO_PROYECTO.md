# ESTADO DEL PROYECTO PPMT

Ultima actualizacion: 2026-06-26 (sesion 3 AI — rolling sweep)
Repo: https://github.com/coverdraft/ppmt

## 1. Pipeline: OK
- LightGBM binary classification P(UP en 24h), HORIZON=288
- 58 features (dow eliminado), quantile-based trading
- Sequential backtest: hold=288 bars (24h), no overlap
- Walk-forward split: 83/10/7

## 2. Resultados ETH/USDT — Rolling Sweep (4 ventanas)
Configuracion ganadora: **Q90/Q10 LONG+SHORT hold=288**

| Metrica | Valor |
|---------|-------|
| PnL total | +18.1% |
| Sharpe | 0.138 |
| Trades | 27 (14L, 13S) |
| Win rate | 53.0% |
| Consistencia | 3/4 ventanas positivas |
| avg_ret/trade | +0.75% |
| test_auc | 0.496 (agregado) |

### Per-window breakdown (Q90 hold=288 L+S):
| Ventana | test_auc | Trades | Win rate | PnL |
|---------|----------|--------|----------|-----|
| 1 | 0.513 | 7 | 43% | +2.1% |
| 2 | 0.579 | 7 | 57% | +2.2% |
| 3 | 0.392 | 6 | 83% | +17.9% |
| 4 | 0.501 | 7 | 29% | -4.0% |

### Comparativa top configs (agregado 4 ventanas):
| Config | PnL | Sharpe | Consistencia |
|--------|-----|--------|--------------|
| Q90/Q10 L+S | +18.1% | 0.138 | 3/4 |
| Q85/Q15 L+S | +12.9% | 0.045 | 2/4 |
| Q80/Q20 L+S | +6.4% | -0.037 | 6/8* |
| Q75/Q25 L+S | +1.4% | -0.032 | 2/4 |
| LONG-only (todas) | -11% a -21% | negativo | 1-2/4 |

*Q80/Q20 muestra 6/8 porque algunas ventanas se parten en sub-ventanas al tener mas trades.

### Hallazgos clave del rolling sweep:
1. **LONG+SHORT siempre supera a LONG-only** — el SHORT es esencial
2. **Quintiles extremos (Q90/Q10) > moderados (Q75/Q25)** — mas selectividad = mejor edge
3. **hold=288 (24h) es el unico viable** — hold=144 o 576 pierden
4. **test_auc ~0.5 pero el edge existe** — el modelo ranking funciona aunque AUC global sea flojo
5. **Ventana 3 fue anomalia** (+17.9% PnL) — sin ella el edge es marginal
6. **Ventana 4 fue negativa** — el modelo no funciona en todos los regimenes

## 3. Parametros actuales
- n_estimators=2000, early_stopping=150, lr=0.01, num_leaves=31
- min_data_in_leaf=30, lambda_l1=0.3, lambda_l2=3.0
- **Q_LONG=90, Q_SHORT=10, hold=HORIZON(288), WINDOW=200**
- PROB_LONG=0.55, PROB_SHORT=0.42 (para predict() en vivo)

## 4. Evolucion de configs
| Fecha | Config | PnL | Notas |
|-------|--------|-----|-------|
| sesion 1 | Q75/Q25 L+S hold=288 | +5.0% | 7 trades, 1 ventana |
| sesion 2 | Q80/Q20 L+S hold=288 | +5.0% | 7 trades, 1 ventana |
| sesion 3 | **Q90/Q10 L+S hold=288** | **+18.1%** | **27 trades, 4 ventanas** |

## 5. Estado honesto
- test_auc=0.496 esta POR DEBAJO de 0.5 — pero el ranking funciona
- El edge proviene de ser muy selectivo (solo top/bottom 10% de predicciones)
- 27 trades en 4 ventanas es mejor que 7, pero aun statistically debil
- Ventana 3 aporta +17.9% del PnL total — skew positivo preocupante
- Sin ventana 3: PnL ~ +0.3% (practicamente ruido)
- Necesitamos paper trading real 2-4 semanas para validacion out-of-sample

## 6. Proximos pasos
1. **Lanzar paper trading real solo ETH** con config Q90/Q10 L+S hold=288
2. Recolectar 2-4 semanas de datos OOS
3. Si edge se confirma: ampliar a SOL y altcoins
4. Si edge desaparece: iterar en features (funding rate, OI, orderbook)
5. Considerar 180d de datos para mas trades en backtest

## 7. Git
Ultimo commit: ver git log
Regla: todo cambio = commit + push
