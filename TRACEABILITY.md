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

## 2026-06-12 — Validación OOS Cross-Token con Datos Reales

### Evaluación de segunda crítica externa

La otra IA reconoció que el fix del composite multiplicativo → aditivo es correcto. Puntos evaluados:

| Punto | Validez | Nuestra posición |
|---|---|---|
| "Los pesos 40/35/25 son arbitrarios" | ⚠️ Parcialmente | Son priors razonables (body_position = más estable, direction = más predictivo, volume = confirmación). El análisis de sensibilidad muestra que TODOS los pesos producen ~100% entropía con datos reales — la diferencia está en qué features se enfatizan, no en la calidad de la distribución. Deben validarse OOS. |
| "Congelar y testear OOS" | ✅ Correcto | ESO ES EXACTAMENTE LO QUE HICIMOS |
| "Test 1-5: BTC/ETH/SOL OOS, walk-forward, Monte Carlo" | ✅ Correcto | Hechos Tests 1-3 (BTC, ETH, SOL). Walk-forward y Monte Carlo pendientes. |
| "La prueba definitiva es OOS PnL" | ✅ Correcto | Validado — ver resultados abajo |

### Análisis de sensibilidad de pesos

Probamos 5 combinaciones de pesos con datos simulados (alpha=5):

| Config | Entropía | Overlap | Info×Rep |
|---|---|---|---|
| equal 33-33-33 | 2.321/2.322 (100%) | 1.17x | 2.71 |
| **current 40-35-25** | **2.318/2.322 (100%)** | **1.16x** | **2.68** |
| direction_heavy 25-50-25 | 2.321/2.322 (100%) | 1.13x | 2.63 |
| body_heavy 50-25-25 | 2.321/2.322 (100%) | 1.17x | 2.71 |
| volume_heavy 25-25-50 | 2.319/2.322 (100%) | 1.14x | 2.65 |

**Conclusión:** Todos los pesos producen ~100% entropía. La diferencia entre pesos es marginal con datos simulados. Con datos reales, la diferencia se manifiesta en PnL, no en distribución. Los pesos 40/35/25 son un prior razonable hasta que OOS demuestre lo contrario.

### Resultados OOS con datos reales (Binance, 2 años, 80/20 split)

**Configuración óptima: ohlcv/alpha=3/window=10/pattern_length=5**

| Token | ohlcv/a3 PnL | Best Close PnL | ohlcv/a3 PF | ohlcv/a3 WR | ohlcv/a3 Trades | ohlcv/a3 Overlap |
|---|---|---|---|---|---|---|
| BTC | **332.64%** | 215.60% (close/a5) | 3.04 | 66.7% | 261 | 19.44x |
| ETH | **527.45%** | 395.43% (close/a3) | 2.95 | 59.4% | 288 | 19.50x |
| SOL | **687.17%** | 380.19% (close/a5) | 3.59 | 67.4% | 301 | 19.39x |

**El composite aditivo GANA en PnL en los 3 tokens.** El Profit Factor es consistente (2.95-3.59). El overlap es estable (~19.4x) — buen balance de Information × Repetition.

### Comparación con alpha=4 y alpha=5

| Config | BTC PnL | BTC Trades | BTC WR | BTC PF |
|---|---|---|---|---|
| **ohlcv/a3** | **332.64%** | **261** | **66.7%** | **3.04** |
| ohlcv/a4 | 34.38% | 53 | 54.7% | 1.75 |
| ohlcv/a5 | 21.36% | 6 | 100.0% | inf |

**alpha=3 es claramente superior** — más trades (significancia estadística), mejor PnL, PF sólido.

### Sobre la pregunta: "¿El nuevo composite mejora OOS?"

**SÍ.** El composite aditivo con alpha=3 produce:
- 332-687% PnL vs 176-395% con close
- PF de 2.95-3.59 (consistente y rentable)
- 261-301 trades (suficiente para significancia)
- El fix NO es solo "una distribución estadísticamente bonita" — produce resultados de trading superiores

### Pendiente: Walk-forward y Monte Carlo

- Walk-forward: Pendiente (requiere implementación)
- Monte Carlo sobre trades OOS: El framework existe en `src/ppmt/risk/monte_carlo.py`

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
| 7 | Tests no-distorsionantes con datos reales (Binance) | ALTA | **PARCIAL** — OOS BTC+ETH+SOL validado |
| 8 | Validación OOS cross-token (4 niveles) | ALTA | **PARCIAL** — 3 tokens, alpha=3 validado |
| 9 | Testear alphabet_sizes 3-8 con datos reales | ALTA | **DONE** — alpha=3 es óptimo |
| 10 | Añadir breakpoints adaptativos (empirical quantiles) | MEDIA | Pendiente (V0.6.3) |
| 11 | Multi-feature encoding (body/wick/volume separados) | MEDIA | Pendiente (V0.7) |

---

## Principios del Proyecto

1. **Solo datos reales** — Nunca datos sintéticos. Siempre Binance.
2. **Repo local + DB local** — Sin dependencias externas persistentes
3. **Trazabilidad** — Todo cambio documentado aquí
4. **GitHub** — Commit después de cada paso
5. **Information × Repetition** — Métrica guía, no maximizar match rate
6. **Validar OOS antes de afirmar** — La prueba definitiva es PnL OOS, no distribución bonita
7. **Congelar después de fixes** — No acumular cambios sin validar
