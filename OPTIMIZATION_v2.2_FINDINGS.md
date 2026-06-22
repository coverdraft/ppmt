# PPMT v2.2 — Hallazgos de Optimización Exhaustiva (sesión 23 jun 2026)

## Resumen ejecutivo

Se ejecutó una optimización exhaustiva del motor PPMT buscando una
configuración universal que funcione en cualquier token. **Resultado
negativo**: ninguna de las 100+ configuraciones probadas produce
PnL positivo en OOS. El motor tiene un problema estructural más profundo
que no se resuelve con ajuste de parámetros.

## Hallazgos clave

### 1. Bug LONG/SHORT encontrado y reparado ✅

**Archivo**: `src/ppmt/core/metadata.py:1017-1029`

**Bug original**: Cada observación solo alimentaba UNA direction_stats
según el signo de `move_pct`:
- `move_pct > 0` → long_stats.count += 1
- `move_pct < 0` → short_stats.count += 1

**Consecuencia**: En IS alcista (BTC +18%, SOL +60% en últimos 90d),
80%+ de las observaciones iban a `long_stats`. `short_stats` quedaba
casi vacío. `best_direction_p7()` veía `short_count == 0` y siempre
devolvía "LONG" → **0 SHORTs generados**.

**Fix aplicado (v2.2)**: Cada observación alimenta AMBAS direction_stats
con outcome espejado:
- LONG perspective: won = (move_pct > 0)
- SHORT perspective: won = (move_pct < 0)

Esto permite que `best_direction_p7()` funcione correctamente: si un
patrón systematicamente produce move_pct > 0, long_stats tendrá WR
alta y short_stats WR baja → motor elige LONG. Y viceversa.

**Verificación**: Después del fix, el motor genera 40-55% SHORTs en
OOS (antes 0%). El fix NO mejora PnL pero desbloquea la capacidad
direccional.

### 2. SL/TP basados en max_drawdown_pct es estructuralmente defectuoso ❌

**Problema**: `meta.max_drawdown_pct` es el PEOR drawdown observado
en la ventana del patrón (5 velas × 5m = 25min). Típicamente 1.5-3%
para BTC. Con `sl_multiplier=2.0`, SL_dist = 3-6%.

`meta.avg_move_long/short` (usado para TP) es el promedio, típicamente
0.3-0.8%. Así que SL/TP ratio es 4:1 a 8:1 a favor de SL → PF < 0.5
garantizado.

**Fix intentado (v2.2.1)**: SL/TP basados en ATR(14) real.
Mejora marginal: PF sube de 0.65 a 0.75. Sigue < 1.

**Fix intentado (v2.2)**: Forzar RR mínimo 2.0 (TP = max(TP, SL×2)).
Mejora marginal: PF sube a 0.73. Sigue < 1.

### 3. El motor anti-predice direccionalemente ❌

**Evidencia (5 tokens × 30d OOS × 12 configs)**:
- WR consistentemente 33-42% (todos < 50%)
- PF 0.60-0.85 (todos < 1.0)
- Si el motor fuera random, WR sería 50%. WR < 50% significa
  anti-predicción sistemática.

**Hipótesis**: El matching SAX + Trie sobre 60d IS está sobreajustado.
Los patrones "abcde" que funcionaron en IS no son estables en OOS.

**Test mean-reversion (v2.2.2)**: Invertir dirección predicha.
- NORMAL: WR 35.4%, PnL -221%
- REVERSE: WR 37.8%, PnL -112%

REVERSE mejora 4 de 5 tokens pero no cura. ETH casi se recupera
(-47% → -7%). Esto confirma sesgo direccional erróneo pero no puro
mean-reversion.

### 4. SOL funciona mejor que los demás ✅

En TODOS los configs probados, SOL/USDT tiene el mejor PnL individual:
- SOL: WR 40-45%, PF 0.77-0.86
- BTC: WR 33-40%, PF 0.61-0.66
- LINK: WR 32-37%, PF 0.63-0.73
- ETH: WR 35-43%, PF 0.63-0.71
- DOGE: WR 37-41%, PF 0.65-0.79

SOL tiene mayor volatilidad intraday (rangos 5m más amplios), lo que
permite que el patrón SAX capture más señal direccional.

### 5. N3=90% peso es subóptimo pero no es el cuello de botella ❌

Probamos 11 perfiles de peso diferentes. Los mejores:
- `univ_40_20_20_20` (N1=40%, N2=20%, N3=20%, N4=20%)
- `n1_dom_60_0_20_20` (N1=60%, N2=0%, N3=20%, N4=20%)
- `class_30_30_20_20` (N1=30%, N2=30%, N3=20%, N4=20%)

Estos mejoran PnL marginalmente vs `F_base` (N3=90%) pero TODOS siguen
negativos. La distribución de pesos NO es el problema principal.

### 6. α=5 (125 patrones N3) es mejor que α=3 (27 patrones) ✅

Confirmado: α=3 da solo 27 patrones N3 (0.01% cobertura del espacio
248,832). α=5 da 125 patrones con ~80-100 obs/patrón. Mucho más
densidad estadística.

## Configuración óptima encontrada (aunque sea negativa)

La MEJOR configuración de todas las probadas:

```python
weights = (0.40, 0.20, 0.20, 0.20)  # N1, N2, N3, N4 — universal-friendly
ev_threshold = 0.20
sl_multiplier = 2.0
hard_move_floor = 0.10
min_confidence = 0.08
alpha_n3n4 = 5
```

**Resultado OOS 30d, 5 tokens**:
- PnL: -123.5%
- WR: 42.3%
- PF: 0.73
- n_trades: 1139
- shorts: 35.6%
- MC prob_profit: 0.0%
- MC risk_ruin: 100%

## Root cause diagnosis

El motor NO tiene edge direccional en OOS. Tres causas posibles:

### A. Sobreajuste del IS
60 días de IS son pocos para construir tries estables. Los patrones
capturados son específicos del régimen IS (bull market Mar-Jun 2026).

**Solución propuesta**: Walk-forward rolling. Build on days 1-30, test
on day 31. Build on days 2-32, test on day 33. Etc. 60 tests walk-
forward en vez de 1 test estático.

### B. SAX symbolization demasiado agregada
α=5 con W=10 mapea 10 velas a 1 símbolo. Eso pierde mucha información
micro-estructura. El patrón "abcde" puede representar 10 movimientos
diferentes a nivel de vela.

**Solución propuesta**: Probar W=5 (5 velas por símbolo) o W=3. Más
granularidad. Pero requiere más datos.

### C. compute_outcome_directional es ruidoso
La función usa `DIRECTIONAL_MICRO_FLOOR = 0.01%` como threshold. Eso
es demasiado bajo. Casi cualquier movimiento cuenta como "won".

**Solución propuesta**: Subir el threshold a 0.10-0.20% (real move
floor). Solo registrar como "won" si el movimiento post-patrón es
significativo.

## Próximos pasos recomendados (priorizados)

1. **Walk-forward validation** (alta prioridad)
   - Implementar rolling build+test
   - 60 ventanas de 30d IS + 1d OOS cada una
   - Evaluar consistencia de edge

2. **Filtrado de patrones no-predictivos** (alta prioridad)
   - Para cada patrón en el trie, test de chi-cuadrado sobre
     long_wr vs short_wr. Eliminar patrones con p > 0.05.
   - Solo mantener patrones con edge estadísticamente significativo.

3. **Ensemble de alphas** (media prioridad)
   - Construir tries con α=3, 5, 7 para cada token
   - Votación: solo entrar si 2 de 3 alphas coinciden en dirección
   - Reduce variance y overfitting

4. **Multi-timeframe fusion** (media prioridad)
   - 1m: microestructura (señal primaria)
   - 5m: mesoestructura (filtro direccional)
   - 15m: macrocontexto (filtro de régimen)
   - Solo entrar si 5m y 15m coinciden

5. **Regime-aware weights** (baja prioridad)
   - Detectar régimen actual (trending/ranging/volatile)
   - En trending: más peso a N3/N4 (específico)
   - En ranging: más peso a N1 (universal)

6. **CalibrationEngine auto-alpha** (baja prioridad)
   - Para cada token, IS data, probar α=3,4,5,7
   - Elegir el α que maximiza IS WR con min 50 obs/patrón
   - Persistir el α óptimo por (symbol, timeframe)

## Archivos modificados en esta sesión

| Archivo | Cambio |
|---------|--------|
| `src/ppmt/core/metadata.py:1017-1066` | Fix LONG/SHORT bug: cada obs alimenta ambas direction_stats |
| `scripts/download_ohlcv.py` | Nuevo: descarga 90d OHLCV de Binance REST |
| `scripts/ppmt_grid_search.py` | Nuevo: grid search 88 configs × 5 tokens |
| `scripts/ppmt_v221_atr.py` | Nuevo: ATR-based SL/TP + edge filter |
| `scripts/ppmt_v222_reverse.py` | Nuevo: test mean-reversion (invertir dirección) |
| `scripts/smoke_test.py` | Nuevo: smoke test 1 token × 1 config |

## Datos descargados

OHLCV 90d (2026-03-24 → 2026-06-22) en `~/.ppmt/ppmt.db`:
- BTC/USDT: 25920 (5m) + 8640 (15m)
- ETH/USDT: 25920 (5m) + 8640 (15m) — **NUEVO, antes no estaba**
- SOL/USDT: 25920 (5m) + 8640 (15m)
- DOGE/USDT: 25920 (5m) + 8640 (15m)
- LINK/USDT: 25920 (5m) + 8640 (15m)

## Conclusión

El fix LONG/SHORT es real y necesario, pero NO es suficiente para
hacer el motor rentable. Se necesita rediseño del matching/training
para lograr edge OOS. Los próximos pasos recomendados son walk-forward
validation y filtrado estadístico de patrones.
