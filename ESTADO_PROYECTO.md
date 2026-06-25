# ESTADO DEL PROYECTO PPMT

Ultima actualizacion: 2026-06-26 (sesion 2 AI)
Repo: https://github.com/coverdraft/ppmt

## 1. Mecanica del pipeline: OK
- 58 features (dow eliminado)
- LightGBM binary classification P(UP en 24h)
- Walk-forward split: train 83% / val 10% / test 7%

## 2. Resultados actuales (90d, H=288 binary)
Metrica | BTC | ETH
val_auc | 0.430 | 0.568
test_auc | 0.479 | 0.630
best_iter | 1 | 29
n_trades | 0 | 0

ETH tiene senal real. BTC = dead end.

## 3. Parametros actuales
HORIZON=288, THR_LONG=0.51, THR_SHORT=0.46
lr=0.01, n_estimators=2000, early_stopping=150
num_leaves=31, min_data_in_leaf=30
lambda_l1=0.3, lambda_l2=3.0

## 4. Problema: n_trades=0 en ETH
Predicciones cerca de 0.50, no cruzan umbrales.

## 5. Proximos pasos
1. Resolver n_trades=0
2. Paper trading solo ETH
3. Descartar BTC
4. Ampliar a SOL y altcoins

## 6. Git
Ultimo commit: 2e6e19b
Regla: todo cambio = commit + push