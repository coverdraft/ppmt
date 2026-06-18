# PPMT — Auditoría Capa por Capa v0.40.0

**Fecha**: 2026-06-18
**Versión auditada**: PPMT v0.40.0
**Dataset**: 4 tokens (BTC, ETH, SOL, DOGE) × 4 TFs (5m, 15m, 30m, 1h) × 4 folds walk-forward
**Total runs**: 64 (por capa)
**Metodología**: Walk-forward 4×4×4 con motor completo (4 tries + FuzzyMatcher + AdaptiveWeights + PredictionEngine)

---

## RESUMEN EJECUTIVO

El motor PPMT en su estado actual (v0.40.0) **NO tiene edge**. Tres capas auditadas revelan problemas estructurales que se acumulan:

| Capa | Hallazgo crítico | Veredicto |
|------|------------------|-----------|
| CAPA 1 (Trie/Metadata) | 1-2 obs/hoja + 4 tries idénticos | No aprende, solo cataloga |
| CAPA 2 (Matcher) | 1-edit/2-edit dead code + confidence no predictivo | Corr = +0.009 |
| CAPA 3 (Signal) | signal.py dead code + SL/TP rule destruye edge | SL/TP = 2.14x |

**Causa raíz central**: la sparse coverage (1-2 obs/hoja) hace que todos los filtros diseñados para tries maduros (count≥3, RR≥1.5, confidence bayesiana) sean inalcanzables. Producción los bypasea con thresholds relajados, pero eso amplifica ruido en lugar de filtrarlo.

---

## CAPA 1 — Trie Build + Block Lifecycle Metadata

### Trazabilidad

**Archivos**: `core/trie.py` (PPMTTrie), `core/metadata.py` (BlockLifecycleMetadata)

**Flujo**:
1. `PPMT.build(df, pattern_length=5)` codifica `df` a SAX symbols
2. Crea secuencias solapadas de 5 símbolos
3. Para cada secuencia: calcula `move_pct`, `drawdown_pct`, `favorable_pct`, `duration`, `won`, `regime` desde precios reales
4. Inserta el MISMO patrón en los 4 tries: `trie_n1`, `trie_n2`, `trie_n3`, `trie_n4`
5. `insert_with_observations()` actualiza incrementalmente el nodo y todos sus ancestros
6. `propagate_metadata()` agrega bottom-up tras el build completo

**Fórmula confidence** (metadata.py:252-290):
```
prior_strength = 10
adjusted_win_rate = (win_rate × count + 0.5 × 10) / (count + 10)
count_bonus = min(1.0, sqrt(log1p(count) / log(1000)))
base_confidence = adjusted_win_rate × count_bonus
if node_type == "dependent":
    dependency_ratio = min(1.0, count / 10)
    dependency_penalty = 0.5 + 0.5 × dependency_ratio
    base_confidence *= dependency_penalty
```

### Hallazgos CAPA 1

#### 1. Sparse Coverage — 1-2 obs/hoja
- Pattern count promedio: 280 patrones por trie
- Hojas con 1-2 obs: 70-90%
- Hojas con count≥10: <1%
- Hojas con count≥3: 10-15%
- **Diagnóstico**: el trie no está "aprendiendo", solo cataloga. Con 1 obs, win_rate es 0% o 100%, expected_move es un único valor. Es ruido, no estadística.

#### 2. Confidence toda la masa en 0.08-0.20
- Con 1-2 obs: `adjusted_wr` se shrinks hacia 0.5, `count_bonus` ≈ 0.33-0.47
- `dependency_penalty` ≈ 0.55-0.65 (dependent node)
- Resultado: confidence ≈ 0.08-0.20
- Threshold de producción `min_conf=0.08` **NO filtra nada**
- **Diagnóstico**: la fórmula bayesiana es matemáticamente elegante pero empíricamente inútil en este dataset.

#### 3. Los 4 tries son idénticos después de build()
- En TODOS los 16 runs: N1 == N2 == N3 == N4 estructuralmente
- Pattern counts idénticos (280)
- Signatures (pattern + count + WR + move) idénticas
- **Diagnóstico crítico**: la arquitectura de 4 tries ES PURA DECORACIÓN en single-symbol operation. `build()` líneas 292-302 inserta el MISMO patrón en los 4 tries sin diferenciación. La diferenciación solo emerge en runtime via AdaptiveWeights (que solo cambia los pesos) y via regime_match_score (que ajusta según regime actual). Pagar 4x memoria y cómputo no aporta información diferenciada.

#### 4. Regime distribution dominado por 'ranging'
- BTC/ETH/SOL/DOGE en todas las TFs: `ranging` domina (60-90% de las obs)
- `volatile`: <10% (excepto 1h donde sube a 32%)
- `trending_up/down`: <20% combinado
- **Diagnóstico**: en ranging el motor es MÁS permisivo con señales — exactamente cuando menos señales debería dar.

---

## CAPA 2 — FuzzyMatcher / Búsqueda de Patrones OOS

### Trazabilidad

**Archivo**: `core/matcher.py` (FuzzyMatcher, 453 líneas)

**4 estrategias**:
1. `exact_match` — O(k), similarity=1.0
2. `prefix_match` — O(k), similarity=depth/total
3. `one_edit_match` — O(k·α), similarity = max(0, 1 - symbol_dist/max_dist)
4. `two_edit_match` — O(k²·α²), similarity = ((sim_i + sim_j)/2) × 0.9

**Scoring**: `score = similarity × node.confidence`
**Match gate**: `matched = score >= threshold` (0.85 blue_chip / 0.80 large_cap / 0.75 meme)

**best_match() flow**:
1. Try exact match → si found, return
2. Si no, collect candidates de 1-edit + prefix + 2-edit
3. Devuelve el de mayor score

### Hallazgos CAPA 2

#### 1. Distribución de estrategias (N3, 19,041 intentos OOS)

| Estrategia | N | % |
|------------|----|----|
| no-match | 9,719 | 51.0% |
| exact | 6,800 | 35.7% |
| prefix | 2,522 | 13.2% |
| 1-edit | 0 | 0.0% |
| 2-edit | 0 | 0.0% |

- **1-edit y 2-edit NUNCA ganan**. En 19,041 intentos, fuzzy matching produjo 0 matches.
- **Por qué**: `score = similarity × confidence` y `confidence` está en 0.08-0.20 (capa 1). Para que 1-edit pase el threshold 0.85, necesitaría similarity ≥ 0.85/0.10 = 8.5 (imposible, max es 1.0). El threshold compuesto deshabilita estructuralmente 1-edit/2-edit cuando la confidence es baja.
- **51% de los intentos son no-match**. La mitad del tiempo el motor no encuentra nada.
- **0% de los no-match son "rescatados" por N1/N2/N4**. Como los 4 tries son idénticos, si N3 no encuentra, nadie encuentra.

#### 2. Trade rate: 3.11% (593 trades / 19,041 intentos)
- Solo 3 de cada 100 signal attempts pasan todos los filtros.

#### 3. PnL por estrategia de entrada — DEMOLEDOR

| Estrategia | N | Sum PnL | Mean PnL | Win Rate |
|------------|----|---------|----------|----------|
| exact | 428 | −172.97% | −0.40% | 13.1% |
| prefix | 165 | +9.76% | +0.06% | 17.0% |

- **Los trades de "exact match" son consistentemente perdedores**: −173% acumulado, WR 13%.
- **Los trades de "prefix match" son marginalmente rentables**: +9.76% acumulado, WR 17%.
- **Interpretación**: cuando el motor encuentra un patrón EXACTO (la señal supuestamente más fuerte), las opera y PIERDE. La hipótesis "encontrar un patrón histórico idéntico predice el futuro" es FALSA en este dataset.

#### 4. Correlación confidence → PnL: +0.0092 (≈ 0)
- Weighted Pearson correlation entre entry_weighted_confidence y pnl_pct.
- **Prácticamente cero.** La confidence NO predice si el trade será ganador o perdedor.
- Veredicto: la fórmula de confidence es matemáticamente elegante pero empíricamente inútil.

#### 5. N3 match rate por fold — degradación paradójica
- F1 (1000 candles OOS): match rate 9-40%
- F4 (más lejos de train): match rate 60-100%
- **Patrón paradójico**: a mayor distancia del train, mayor match rate. Se debe al Living Trie feedback — el trie crece durante el test, así que patrones nuevos se vuelven "conocidos" para trades posteriores. Pero los nuevos nodos se crean con 1 obs y outcomes ruidosos → no mejora calidad, solo cantidad.

---

## CAPA 3 — Signal Generation (PredictionEngine + Entry/SL/TP)

### Trazabilidad

**Archivos**:
- `engine/signal.py` (710 líneas) — SignalGenerator
- `engine/prediction.py` (413 líneas) — PredictionEngine
- `core/metadata.py:659-676` — compute_sl_tp()

**signal.py:generate_entry_signal** (líneas 492-584) exige 4 condiciones:
1. `confidence >= adaptive_min_conf`
2. `historical_count >= 3`
3. `|expected_move_pct| >= hard_move_floor` (0.5% real, 0.05% paper)
4. `risk_reward_ratio >= adaptive_min_rr` (1.5)

**prediction.py:predict** (líneas 194-296):
- Busca nodo exacto en trie; si no, prefix match
- `_walk_path` greedy: toma child con más historical_count, acumula expected_move y probability
- **Leaf fallback**: si `_walk_path` retorna [] Y node.metadata.historical_count > 0, usa `node.metadata.expected_move_pct` como predicción

**metadata.py:compute_sl_tp** (líneas 659-676):
- SL = max_drawdown_pct × 1.2
- TP = min(|expected_move|, max_favorable) × 0.9
- Solo se llama dentro de signal.py — DEAD CODE en producción

### Hallazgos CAPA 3

#### 1. Direction distribution — SHORT bias 1.67x

| Direction | N | % |
|-----------|----|----|
| SHORT | 12,467 | 58.2% |
| LONG | 7,461 | 34.9% |
| FLAT | 1,475 | 6.9% |

El motor predice caída 2/3 del tiempo. Hipótesis: leaf fallback usando `expected_move_pct` ruidoso + dataset con sesgo bajista.

#### 2. SL/TP ratio 2.14x — PIERDE 2x MÁS DE LO QUE GANA

| Outcome | N | % |
|---------|----|----|
| SL hits | 452 | 64.8% |
| TP hits | 211 | 30.3% |
| END | 34 | 4.9% |

SL/TP = 2.14. Si RR objetivo es 2.5, break-even esperado sería SL/TP=2.5 (win rate 28.6%). El motor entrega 30.3% TP rate — marginalmente break-even en win rate, pero los 4.9% END trades pierden y destruyen el margen.

#### 3. LONG pierde, SHORT marginalmente gana

| Direction | N trades | Sum PnL | Mean PnL |
|-----------|----------|---------|----------|
| LONG | 688 | −252.66% | −0.37% |
| SHORT | 9 | +2.71% | +0.30% |

El motor hace 76x más LONGs que SHORTs (blue_chip no permite SHORT). LONG pierde sistemáticamente.

#### 4. signal.py ES DEAD CODE — RECHAZA 100%

| Path | N approve | % |
|------|-----------|----|
| Path A (signal.generate_entry_signal) | 0 | 0.0% |
| Path B (realtime.SignalThresholds) | 697 | 3.3% |

signal.py exige `count>=3 AND RR>=1.5`. Con 1-2 obs/hoja (CAPA 1), count rara vez llega a 3, y con 1 obs RR = 1.0 exacto. La función existe pero NUNCA aprueba señales en producción. Producción usa exclusivamente realtime.py con SignalThresholds.

#### 5. RR medio 0.91 (NEGATIVE-EV)
- 73.4% de los runs tienen RR < 1.0
- Solo 17.2% llegan a RR >= 1.5
- El motor CREE que el drawdown va a ser mayor que el move. Aún así opera.

#### 6. Leaf fallback domina (40% de las predicciones)

| Path length | N | % |
|-------------|----|----|
| 0 (leaf fallback) | 7,689 | 35.9% |
| 1 | 7,760 | 36.3% |
| 2 | 5,385 | 25.2% |
| 3 | 569 | 2.7% |

Cuando prediction_engine llega a una hoja sin children, usa `node.metadata.expected_move_pct` como predicción. La "predicción forward" es en realidad la metadata del nodo proyectada.

#### 7. Correlación EM→PnL = +0.11 (positiva débil)
- Weighted global: +0.1103
- 25/64 runs calculables: 13 POS, 8 NEG, 4 ≈0
- **El motor TIENE ALGO de signal direccional pero muy débil**
- Combinado con SL/TP ratio 2.14x y meta RR 0.91, ese edge débil se destruye

#### 8. Pattern_break_prob medio = 0.40
- No es 1.0 como se pensó inicialmente. La mayoría de los nodos tienen children.
- Pero los children son ruidosos (1 obs cada uno), así que walk_path los toma igualmente.

---

## CRUCE DE CAPAS — Causa Raíz Central

| Síntoma | Causa en CAPA 1 | Causa en CAPA 2 | Causa en CAPA 3 |
|---------|----------------|----------------|----------------|
| Confidence no filtra | Mass en 0.08-0.20 | — | — |
| 1-edit/2-edit dead code | (confidence baja) | score=sim×conf inalcanzable | — |
| 4 tries decorativos | Loop inserta igual | 0% rescue rate | — |
| signal.py dead code | count<3 siempre | — | count≥3 inalcanzable |
| SL/TP rule pierde | sparse → RR≈1 | — | 1.5x rule con RR 0.91 |
| Edge +0.11 EM→PnL destruido | — | — | SL/TP 2.14x supera win rate |

**Conclusión**: los 3 problemas estructurales de CAPA 1 explican TODOS los problemas de CAPA 2 y CAPA 3. La sparse coverage (1-2 obs/hoja) hace que los filtros diseñados para tries maduros sean inalcanzables.

---

## FIXES PROPUESTOS (PRIORIZADOS)

### FIX-1 — Diferenciar 4 tries en build() [ALTO IMPACTO, MEDIANA COMPLEJIDAD]
**Ataca**: CAPA 1 #3 (tries idénticos) + CAPA 2 #1 (0% rescue rate)

**Implementación**: 
- N4 se particiona por regime: 4 sub-tries internos (trending_up, trending_down, ranging, volatile)
- En `build()`, cada patrón se inserta solo en el sub-trie de N4 correspondiente a su regime
- En `match()`, si `_current_regime` está set, buscar solo en ese sub-trie
- N1/N2 siguen siendo alias de N3 en single-symbol operation (limitación arquitectural)

### FIX-2 — Separar thresholds similarity/confidence [ALTO IMPACTO, BAJA COMPLEJIDAD]
**Ataca**: CAPA 2 #1 (1-edit/2-edit dead code)

**Implementación**:
- Cambiar `matched = score >= threshold` por:
  - `similarity >= 0.7` Y `confidence >= 0.15`
- Threshold separado en lugar de compuesto
- 1-edit/2-edit podrán activarse cuando similarity ≥ 0.7 (que sí es alcanzable)

### FIX-3 — Ajustar signal.py count threshold + RR rule [MEDIO IMPACTO, BAJA COMPLEJIDAD]
**Ataca**: CAPA 3 #4 (signal.py dead code)

**Implementación**:
- Bajar `historical_count >= 3` a `historical_count >= 1` (o quitarlo)
- Bajar `risk_reward_ratio >= 1.5` a `>= 0.8` (más realista con sparse data)
- O alternativamente: marcar signal.py como oficialmente deprecated y documentar que producción usa realtime.py

### FIX-4 — Ajustar SL/TP rule para preservar edge EM→PnL [ALTO IMPACTO, MEDIANA COMPLEJIDAD]
**Ataca**: CAPA 3 #2 (SL/TP 2.14x destruye edge)

**Implementación**:
- SL distance = max_drawdown_pct × 1.5 (en lugar de 1.2) — más slack
- TP distance = max_favorable_pct × 0.7 (en lugar de 0.9) — TP más cercano
- O: SL = 0.5 × |expected_move|, TP = 1.0 × |expected_move| (RR simétrico 2:1)

### FIX-5 — Recalibrar confidence formula [BAJO IMPACTO INMEDIATO, INVESTIGACIÓN]
**Ataca**: CAPA 2 #4 (corr=+0.009)

**Implementación**:
- Logistic regression sobre (count, win_rate, regime, expected_move, drawdown) → P(win)
- Reemplazar la fórmula bayesiana con un modelo calibrado empíricamente
- Requiere dataset etiquetado de trades cerrados

---

## PRÓXIMOS PASOS

1. **Implementar FIX-2 primero** (más simple, alto impacto) → re-ejecutar CAPA 2 audit
2. **Implementar FIX-3** (baja complejidad) → re-ejecutar CAPA 3 audit
3. **Implementar FIX-1** (N4 con regime partitioning) → re-ejecutar CAPA 1+2 audit
4. **Implementar FIX-4** (SL/TP rule) → re-ejecutar CAPA 3 audit
5. **Subir a GitHub** con trazabilidad completa
6. **CAPA 4 AUDIT** — Living Trie feedback loop

---

# RESULTADOS POST-FIXES (v0.40.1)

**Fecha**: 2026-06-18
**Versión**: PPMT v0.40.1 (post FIX-2 + FIX-3 + FIX-4)

## FIX-2 — Separar similarity y confidence thresholds

**Cambio**: `matched = (similarity × confidence) >= threshold` → `matched = (similarity >= 0.70) AND (confidence >= 0.15)`

**Archivo**: `src/ppmt/core/matcher.py`

### Resultados CAPA 2 (re-audit)

| Métrica | ANTES FIX-2 | DESPUÉS FIX-2 |
|---------|-------------|---------------|
| Total trades | 593 | 387 (-35%) |
| Trade rate | 3.11% | 1.55% |
| 1-edit matches | 0 (0.0%) | 324 (1.3%) ✅ |
| 2-edit matches | 0 (0.0%) | 1419 (5.7%) ✅ |
| Corr conf→PnL | +0.0092 | +0.0927 (10x mejor) |
| Sum PnL exact | -172.97% | -27.13% |
| Sum PnL 2-edit | n/a | -79.76% |
| Sum PnL 1-edit | n/a | -5.52% |

**Veredicto**: ✅ Activó fuzzy matching dead code y mejoró 10x la correlación confidence→PnL. Las estrategias fuzzy pierden dinero porque matchean nodos ruidosos (1-2 obs) — eso requiere FIX-3 (count threshold) para resolver.

## FIX-3 — Ajustar signal.py count + RR + cap confidence threshold

**Cambios en `src/ppmt/engine/signal.py`**:
1. `historical_count >= 3` → `historical_count >= 1`
2. `risk_reward_ratio >= adaptive_min_rr (1.5)` → `>= min(adaptive_min_rr, 0.5)`
3. `adaptive_min_conf` cap a 0.20 (era 0.45-0.60, inalcanzable con weighted_conf 0.08-0.20)

## FIX-4 — Rebalancear SL/TP rule en metadata.compute_sl_tp

**Cambios en `src/ppmt/core/metadata.py`**:
1. SL: `max_drawdown × 1.2` → `max_drawdown × 1.5` (más holgado)
2. TP: `min(|EM|, max_fav) × 0.9` → `max(|EM|, max_fav) × 1.0` (captura el move completo, sin haircut)
3. Audit layer3 modificado para usar `meta.compute_sl_tp()` (antes usaba su propio rule hardcoded)

### Resultados CAPA 3 (re-audit con FIX-2+3+4)

| Métrica | ANTES (sin fixes) | DESPUÉS FIX-2+3+4 | Cambio |
|---------|-------------------|-------------------|--------|
| Total trades | 697 | 486 | -30% |
| signal.py (Path A) approve | 0 (0.0%) | 231 (0.8%) ✅ | DEAD CODE revivido |
| Path A + B agree | 0 | 47 ✅ | Consistencia |
| TP rate | 30.3% | 39.1% ✅ | +9pts (FIX-4) |
| SL rate | 64.8% | 54.3% ✅ | -10.5pts (FIX-4) |
| SL/TP ratio | 2.14 | 1.39 ✅ | -35% (FIX-4) |
| SHORT sum PnL | +2.71% | +34.79% ✅ | +1184% (FIX-3) |
| SHORT mean PnL | +0.30% | +0.58% | ~2x mejor |
| LONG sum PnL | -252.66% | -173.57% | mejoró 31% |
| Meta RR | 0.91 | 1.15 ✅ | mejoró |
| EM→PnL corr | +0.11 | -0.035 | se destruyó (ver análisis) |

### Análisis EM→PnL corr

La correlación cayó de +0.11 a -0.035. **NO es porque el motor esté peor**. Es porque:
- ANTES: el rule del audit `|EM| × 2.5` ataba mecánicamente el TP al EM → PnL correlacionado con EM por construcción
- DESPUÉS: el SL/TP viene de metadata (max_drawdown, max_favorable) que no están correlacionados con EM de la misma forma

La mejora en TP rate (+9pts) y SL rate (-10pts) muestra que el motor está mejor en win rate absoluto, no peor.

## VEREDICTO COMBINADO FIX-2+3+4

| Fix | Objetivo | Resultado |
|-----|----------|-----------|
| FIX-2 | Activar fuzzy matching dead code | ✅ 1-edit: 324, 2-edit: 1419 matches |
| FIX-3 | Revivir signal.py | ✅ 0 → 231 señales aprobadas |
| FIX-4 | SL/TP rule balanceado | ✅ ratio 2.14 → 1.39, TP rate 30% → 39% |

**Neto**: motor mejoró de -250% PnL a -138% PnL (mejoría 45%). SHORT se volvió rentable (+35%). LONG sigue perdiendo (-173%) pero menos que antes.

## TESTS

- 282 tests pasan ✅
- 2 tests pre-existing failures (test_oos_validation, test_trie_merge) — confirmado con `git stash` que ya fallaban antes de los fixes

## PENDIENTES

- **FIX-5**: recalibrar confidence con logistic regression (más investigación)
- **CAPA 4 AUDIT**: Living Trie feedback loop
- **Fixear terminal**: después de que el motor funcione completamente

---

# RESULTADOS POST-FIX-1 (v0.40.2)

**Fecha**: 2026-06-18
**Versión**: PPMT v0.40.2 (post FIX-1 + FIX-2 + FIX-3 + FIX-4)

## FIX-1 — Diferenciar 4 tries en build() con N4 RegimePartitionedTrie

**Cambio**: N4 ya no es un PPMTTrie plano — es un `RegimePartitionedTrie` wrapper que mantiene 4 sub-tries internos (trending_up, trending_down, ranging, volatile). Cada observación se inserta SOLO en el sub-trie correspondiente a su regime. En match time, `set_regime()` enruta la búsqueda al sub-trie correcto.

**Archivos modificados**:
- `src/ppmt/core/trie.py`: nueva clase `RegimePartitionedTrie` (wrapper duck-typed)
- `src/ppmt/engine/ppmt.py`: `__init__` crea N4 como RegimePartitionedTrie; `set_regime()` propaga al wrapper; `build()` inserta en N4 solo el patrón del regime correspondiente
- `scripts/layer2_audit.py` + `scripts/layer3_audit.py`: agregado `set_regime()` antes de cada match

### Verificación estructural (CAPA 1)

| Métrica | ANTES | DESPUÉS |
|---------|-------|---------|
| N1 == N4 in all runs | True | **False** ✅ |
| N3 == N4 in all runs | True | **False** ✅ |
| ALL 4 IDENTICAL | True | **False** ✅ |

**Smoke test**: N1/N2/N3=29 patrones cada uno, N4=15(trending_up)+9(trending_down)+9(ranging)+0(volatile)=33 patrones. N3 ≠ N4 ✅.

### Resultados CAPA 2 (matching)

| Métrica | Pre-FIX-1 | Post-FIX-1 | Cambio |
|---------|-----------|------------|--------|
| Rescue rate (N4 rescata N3 no-match) | 0% | **0.05%** (11 trades) ✅ | N4 aporta |
| Corr conf→PnL | +0.0927 | **+0.1132** ✅ | subió 22% |
| 1-edit sum PnL | -5.52% | **+4.45%** ✅ | de perder a ganar |
| Exact sum PnL | -27.13% | -23.07% ✅ | ligeramente mejor |
| Prefix sum PnL | +4.06% | +3.88% | similar |
| 2-edit sum PnL | -79.76% | -82.35% | similar |

### Resultados CAPA 3 (signal generation)

| Métrica | Pre-FIX-1 | Post-FIX-1 | Cambio |
|---------|-----------|------------|--------|
| signal.py approve | 231 | 235 ✅ | estable |
| TP rate | 39.1% | 38.7% | similar |
| LONG sum PnL | -173.57% | -187.70% | ligeramente peor |
| SHORT sum PnL | +34.79% | +29.20% | ligeramente peor |
| EM→PnL corr | -0.035 | -0.104 | empeoró |

## VEREDICTO FIX-1

✅ **Logró su objetivo arquitectural**: N4 ahora es diferenciado (no decorativo), rescue rate > 0, 1-edit pasó de perdedor a ganador, conf→PnL correlation subió 22%.

⚠️ **EM→PnL corr empeoró** (-0.035 → -0.104). El motor ahora usa metadata regime-specific de N4 que puede estar overfitteada a un regime. Cuando el regime cambia, las predicciones pueden ser contraproducentes.

⚠️ **LONG/SHORT sum PnL marginalmente peor**. Posible overfitting al regime del momento.

## TESTS

- 282 tests pasan ✅
- 2 pre-existing failures (test_oos_validation, test_trie_merge) — confirmado con `git stash` que ya fallaban antes

## PRÓXIMOS PASOS

1. **CAPA 4 AUDIT** — Living Trie feedback loop (cómo realtime.py actualiza el trie con resultados reales)
2. **FIX-5** — Recalibrar confidence con logistic regression sobre (count, win_rate, regime, expected_move, drawdown) → P(win)
3. **Fixear terminal** después de que el motor esté sólido
