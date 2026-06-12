# PPMT v0.6.2 — TRACEABILITY

## Registro de cambios, hallazgos y decisiones

---

## 2026-06-12 — Investigación del Overlap OHLCV vs Close (1.03x vs 13.54x)

### Contexto
Se detectó que la estrategia SAX "ohlcv" produce 1.03x overlap (casi cada patrón es único) mientras que la estrategia "close" produce 13.54x overlap. Se investigaron ambas hipótesis: (a) es un bug, (b) es comportamiento esperado por la arquitectura multi-nodo de PPMT.

### Hallazgo CRÍTICO: Bug en la fórmula del composite OHLCV

**El composite OHLCV tiene una distribución degenerada.** La fórmula actual:

```python
composite = body_center * direction * (0.5 + 0.5 * vol_ratio)
```

Produce los siguientes problemas matemáticos:

1. **55.1% de los valores están dentro de ±0.10 de cero** — pico masivo en 0
2. **74.0% de los valores están dentro de ±0.20** — casi toda la data concentrada cerca de 0
3. **Rango extremo: [-1348.7, 0.7]** — outliers por multiplicación
4. **Con alphabet_size=8, el 92.5% de los símbolos caen en el símbolo central** → destrucción de información

**Causa raíz:** La multiplicación `body_center × direction` hace que cuando `direction ≈ 0` (59.6% de las velas tienen |direction| < 0.3), el composite colapsa a ~0 independientemente del body_center o el volumen. Esto destruye la información de posición del cuerpo y volumen.

**Por qué "close" funciona mejor:** Los precios de cierre tienen autocorrelación natural, creando PAA values más suaves que, tras z-scoring, se distribuyen mejor entre los breakpoints Gaussianos de SAX.

### Evaluación de la crítica de otra IA

Se recibió crítica externa con 4 puntos. Evaluación contra nuestra arquitectura:

| Punto de la IA | Validez | Aplica a PPMT? | Decisión |
|---|---|---|---|
| "El objetivo no es maximizar match rate, sino Información × Repetición" | ✅ Correcto | Sí, framework correcto | ADOPTAR como principio guía |
| "Elegir 3 es arbitrario, probar 3-6" | ✅ Correcto | Sí, pero no es solo el tamaño | ADOPTAR — testear múltiples tamaños |
| "Mapeo bullish-neutral-bearish es muy simple" | ⚠️ Parcialmente | No usamos B/N/B, usamos SAX estándar | NO APLICA — nuestra codificación es SAX, no B/N/B |
| "Codificar body/wick/volume por separado (Versión 3)" | ✅ Correcto en principio | Sí, pero requiere cambio arquitectónico | ADOPTAR para V0.7 — el análisis demuestra que el composite multiplicativo destruye información |

### Lo que SÍ vale de la crítica

1. **Information × Repetition** como métrica de optimización — no maximizar match rate
2. **Testear múltiples alphabet_sizes** (3, 4, 5, 6, 8) con datos reales
3. **Codificar features por separado** — el composite multiplicativo es la causa raíz del problema
4. **Percentiles adaptativos** — los breakpoints Gaussianos no funcionan bien con distribuciones no-Gaussianas

### Lo que NO aplica de la crítica

1. La IA asume que usamos "bullish/neutral/bearish" — PPMT usa SAX con z-score breakpoints
2. La IA no conoce nuestra arquitectura de 4 niveles (N1-N4) con AdaptiveWeights
3. La IA no conoce nuestro FuzzyMatcher que ya provee tolerancia a ruido
4. La IA sugiere "estados adaptativos" pero no especifica cómo integrarlos con SAX

### Solución propuesta (3 fases)

#### Fase 1 — Fix inmediato (V0.6.2)
- **Reemplazar composite multiplicativo por aditivo** en `_extract_series()`:
  ```python
  # ANTES (degenerado):
  composite = body_center * direction * vol_composite

  # DESPUÉS (preserva información):
  composite = body_center * 0.4 + direction * 0.35 + vol_norm * 0.25
  ```
- Esto mantiene la arquitectura de un solo stream SAX
- La distribución aditiva es casi-Gaussiana (33.5% dentro de |z|<0.5 vs 38% teórico)

#### Fase 2 — Mejora (V0.6.3)
- **Añadir breakpoints adaptativos** (empirical quantiles) como opción en SAXConfig
- **Testear alphabet_size 3-8** con datos reales de Binance
- **Métrica de evaluación:** Information × Repetition (entropía × overlap)

#### Fase 3 — Evolución (V0.7)
- **Multi-feature encoding:** body_size (3 estados) + direction (3 estados) + volume (3 estados) = 27 símbolos compuestos
- Cada feature se discretiza independientemente con percentiles adaptativos
- Los símbolos compuestos se integran en el Trie existente sin cambios arquitectónicos
- Esto es equivalente a la "Versión 3" de la otra IA pero adaptada a nuestra arquitectura

---

## 2026-06-12 — Fixes en trie.py

### Cambios aplicados

1. **Añadido `trading_observations: int = 0`** a `PPMTTrie.__init__()`
   - Distingue observaciones de build-time vs trading-time
   - Permite escalar confianza para tries frescos

2. **Añadido `propagate_metadata()`** a `PPMTTrie`
   - Propaga metadata desde hojas hasta la raíz
   - Agrega: historical_count, win_rate, expected_move_pct, max_drawdown_pct, max_favorable_pct, avg_duration, continuation_nodes
   - Los nodos internos ahora tienen estadísticas significativas

3. **Añadido `_propagate_node()`** helper recursivo
   - Los nodos HOJA retornan su propia metadata
   - Los nodos INTERNOS agregan la de sus hijos con promedios ponderados
   - Las observaciones PROPIAS del nodo tienen precedencia

4. **Actualizado `to_dict()` y `from_dict()`** para `trading_observations`

### Pendiente en trie.py
- BlockLifecycleMetadata carece de campos de regime tracking
- Falta integración con RegimeDetector

---

## Pendientes (Backlog)

| # | Tarea | Prioridad | Estado |
|---|---|---|---|
| 1 | Fix composite OHLCV (multiplicativo → aditivo) | ALTA | **FIXED** (commit 47b34e2) |
| 2 | Copiar regime.py de ppmt/ppmt/ a src/ppmt/core/ | ALTA | Pendiente (V4 lo tiene en remote) |
| 3 | Añadir regime tracking a BlockLifecycleMetadata | MEDIA | Pendiente |
| 4 | Rediseñar SHORT confidence gate (regime-aware) | MEDIA | Pendiente |
| 5 | Re-habilitar catastrophic_loss_pct con hard stop 8% | MEDIA | Pendiente |
| 6 | Sincronizar directorios duplicados | BAJA | Pendiente |
| 7 | Tests no-distorsionantes con datos reales (Binance) | ALTA | Pendiente |
| 8 | Validación OOS cross-token (4 niveles) | ALTA | Pendiente |
| 9 | Testear alphabet_sizes 3-8 con datos reales | ALTA | Pendiente (requiere datos Binance) |
| 10 | Añadir breakpoints adaptativos (empirical quantiles) | MEDIA | Pendiente (V0.6.3) |
| 11 | Multi-feature encoding (body/wick/volume separados) | MEDIA | Pendiente (V0.7) |

---

## Principios del Proyecto

1. **Solo datos reales** — Nunca datos sintéticos. Siempre Binance.
2. **Repo local + DB local** — Sin dependencias externas persistentes
3. **Trazabilidad** — Todo cambio documentado aquí
4. **GitHub** — Commit después de cada paso
5. **Information × Repetition** — Métrica guía, no maximizar match rate
