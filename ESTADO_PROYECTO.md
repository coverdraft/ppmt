# ESTADO DEL PROYECTO PPMT

Ultima actualizacion: 2026-06-26 (sesion 2 AI)
Repo: https://github.com/coverdraft/ppmt

## 1. Pipeline: OK
- LightGBM binary classification P(UP en 24h), HORIZON=288
- 58 features (dow eliminado), quantile-based trading
- Sequential backtest: hold=288 bars (24h), no overlap
- Walk-forward split: 83/10/7

## 2. Resultados ETH/USDT (mejor config)
- val_auc=0.570, test_auc=0.621, best_iter=25
- Backtest: Q80 L+S hold=288 → pnl=+5.01%, sharpe=0.24, 7 trades
- LONG-only pierde en todas las configs
- BTC = dead end (test_auc < 0.48 siempre)

## 3. Parametros actuales
- n_estimators=2000, early_stopping=150, lr=0.01, num_leaves=31
- min_data_in_leaf=30, lambda_l1=0.3, lambda_l2=3.0
- Q_LONG=80, Q_SHORT=20, hold=HORIZON(288), WINDOW=200

## 4. Sweep results (19 configs probadas)
- Mejor: Q75-80 L+S hold=288, pnl=+5%, sharpe=0.24
- LONG-only pierde siempre (crypto drift alcista no basta)
- Hold<288 pierde siempre (churning + costos)
- Solo 7 trades en 7 dias de test = estadisticamente debil

## 5. Estado honesto
- test_auc=0.62 es real pero debil
- 7 trades con pnl=+5% NO es significativo
- Necesitamos paper trading real 2-4 semanas para validar
- Script sweep_backtest.py permite testear configs rapidamente

## 6. Proximos pasos
1. Lanzar paper trading real solo ETH con config actual
2. Recolectar 2-4 semanas de datos out-of-sample
3. Si edge se confirma: ampliar a SOL y altcoins
4. Si edge desaparece: iterar en features (funding rate, OI)

## 7. Git
Ultimo commit: ver git log
Regla: todo cambio = commit + push