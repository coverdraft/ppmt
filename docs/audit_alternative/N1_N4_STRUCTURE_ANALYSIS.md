# PPMT — Análisis Estructural N1-N4 (Capa 1)

**Versión analizada:** v0.39.6 (commit 1038dad)
**Fecha:** 2026-06-19
**Autor:** super-z (auditoría capa por capa)
**Objetivo:** Determinar si los 4 tries jerárquicos N1/N2/N3/N4 están correctamente diferenciados o si son réplicas estructurales que anulan el beneficio del "hierarchical specificity".

---

## 1. Diseño intencional (lo que el código declara)

### 1.1 Cuatro tries con nombres semánticos

`src/ppmt/engine/ppmt.py:141-144`:
```python
self.trie_n1 = PPMTTrie(name=f"universal")
self.trie_n2 = PPMTTrie(name=f"asset_class:{asset_class}")
self.trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
self.trie_n4 = PPMTTrie(name=f"per_asset_regime:{symbol}")
```

### 1.2 Pesos declarados en `src/ppmt/engine/weights.py:31-56`

| Perfil       | N1 (universal) | N2 (asset_class) | N3 (per_asset) | N4 (per_asset_regime) |
|--------------|---------------:|-----------------:|---------------:|----------------------:|
| default      | 10%            | 30%              | 30%            | 30%                   |
| meme         | 10%            | 60%              | 20%            | 10%                   |
| new_launch   | 15%            | 55%              | 20%            | 10%                   |
| blue_chip    | 5%             | 20%              | 35%            | 40%                   |

### 1.3 Principio declarado (`weights.py:78-81`)

> "More specific levels (N3, N4) get more weight when data is rich.
> Less specific levels (N1, N2) compensate when data is sparse.
> Dead asset knowledge transfers through N2 persistence."

### 1.4 Flujo declarado de `match_raw()` (`ppmt.py:331-349`)

Cada uno de los 4 tries se consulta con `FuzzyMatcher.best_match()`, se extrae el `confidence` de cada uno, y se combinan vía `AdaptiveWeights.compute_weighted_confidence()` con la matriz de pesos del perfil.

---

## 2. Realidad actual (lo que el código hace)

### 2.1 El build inserta EXACTAMENTE lo mismo en los 4 tries

`ppmt.py:291-302`:
```python
# Insert into all 4 levels
for trie in [self.trie_n1, self.trie_n2, self.trie_n3, self.trie_n4]:
    trie.insert_with_observations(
        symbols=pattern,
        move_pct=move_pct,
        drawdown_pct=drawdown_pct,
        favorable_pct=favorable_pct,
        duration=duration,
        won=won,
        next_symbol=next_sym,
        regime=regime,          # Mismo régimen para los 4
    )
```

**Consecuencia matemática:** dado que los 4 tries reciben el mismo stream de patrones con los mismos metadatos, después de `propagate_metadata()` los 4 árboles son **idénticos en estructura y en metadata**.

### 2.2 El match_raw devuelve 4 confidences idénticas

`ppmt.py:332-341`:
```python
n1_match = self.matcher.best_match(self.trie_n1, current_symbols)
n2_match = self.matcher.best_match(self.trie_n2, current_symbols)
n3_match = self.matcher.best_match(self.trie_n3, current_symbols)
n4_match = self.matcher.best_match(self.trie_n4, current_symbols)

n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0
```

Como los 4 tries son estructuralmente idénticos, en cualquier consulta simultánea:
- O los 4 devuelven el mismo `node` con el mismo `confidence`
- O los 4 devuelven `None` con confidence 0.0

### 2.3 El weighted_confidence es por tanto redundante

`weights.py:220-235`:
```python
confidences = np.array([n1_confidence, n2_confidence, n3_confidence, n4_confidence])
weights = self.to_array()
total_weight = 0.0
weighted_sum = 0.0
for w, c in zip(weights, confidences):
    if c > 0:
        weighted_sum += w * c
        total_weight += w
if total_weight == 0:
    return 0.0
return weighted_sum / total_weight
```

Si `n1_conf == n2_conf == n3_conf == n4_conf == c`, entonces:
```
weighted_sum = c * (w1 + w2 + w3 + w4) = c * 1.0
total_weight = w1 + w2 + w3 + w4 = 1.0
result = c * 1.0 / 1.0 = c
```

**El weighted_confidence es exactamente igual al confidence individual de cualquiera de los 4 tries.** La jerarquía N1-N4 aporta **cero bits de información**.

### 2.4 `AdaptiveWeights.adapt()` nunca se invoca

`grep` confirma: `adapt()` solo se llama desde `weights.py` mismo (su propia definición). Nunca desde `ppmt.py`, `realtime.py`, `paper_trader.py`, ni el terminal. Los pesos son **estáticos del perfil**, no se adaptan a la data real.

### 2.5 `set_regime()` y `_current_regime` existen pero no se usan

`ppmt.py:231-233`:
```python
def set_regime(self, regime: str) -> None:
    """Set the current market regime for N4 Trie selection."""
    self._current_regime = regime
```

`grep` confirma que `set_regime()` solo se llama desde tests, nunca desde el flujo de producción. El "N4 Trie selection" descrito en el docstring **no existe en runtime**.

### 2.6 Conclusión de la realidad

Los 4 tries jerárquicos son una **abstracción ausente**: existen los objetos, existen los pesos, existe la función `compute_weighted_confidence`, pero en producción todo se reduce a "consultar 4 veces el mismo trie y promediar con pesos que dan el mismo número".

CPU: 4 × O(k²·α²) por consulta (innecesario).
Memoria: 4 × la necesaria para almacenar los patrones.
Información aportada por la jerarquía: 0.

---

## 3. Implicación para TF 1m/5m (objetivo del usuario)

El usuario indicó explícitamente: *"principalmente operaremos bajos TF 1m, 5m, por tanto la mayor de veces que podamos entrar a sacarle beneficio al mercado mejor"*.

### 3.1 Magnitud del desperdicio en low TF

| TF  | 90 días = candles | Patrones aprox (α=5, W=7, k=5) | Tries × 4 = patrones almacenados | CPU match × 4 |
|-----|------------------:|-------------------------------:|---------------------------------:|--------------:|
| 1m  | 129,600           | ~12,960                        | ~51,840                          | 4×            |
| 5m  | 25,920            | ~2,592                         | ~10,368                          | 4×            |
| 15m | 8,640             | ~864                           | ~3,456                           | 4×            |
| 1h  | 2,160             | ~216                           | ~864                             | 4×            |

En 1m: **51,840 nodos trie duplicados**. Memoria ~4×, CPU de consulta ~4×. La duplicación no aporta nada.

### 3.2 ¿Por qué el usuario NO ve más entradas en low TF?

El usuario quiere máxima frecuencia de entrada. Tres efectos bloquean:

1. **Confidence baja por diseño (no por jerarquía)**: `confidence` en metadata.py:253-290 usa Bayesian shrinkage con `prior_strength=10`. Para un patrón nuevo con `historical_count=20` y `win_rate=0.55`, la confidence es `(0.55·20 + 0.5·10)/(20+10) · log_bonus ≈ 0.533·0.535 ≈ 0.285`. En regímenes "ranging" o "volatile", el `adaptive_min_conf` típico es 0.35-0.60 → señal rechazada.

2. **Reglas de signal.py demasiado estrictas**: `historical_count < 3` rechaza patrones jóvenes (la mayoría en low TF). `risk_reward_ratio < adaptive_min_rr` con `min_rr` típicamente 1.5-2.0 descarta patrones con drawdown > move.

3. **N1-N4 no diferenciados**: aunque los pesos blue_chip dicen N4=40%, ese 40% va al mismo confidence que el 5% de N1. No aporta diferenciación.

**Conclusión:** Diferenciar los 4 tries NO es la palanca principal para más entradas en low TF. Las palancas son (a) relajar signal.py, (b) ajustar `prior_strength`, (c) mejorar SL/TP para subir win_rate efectivo, (d) **aprovechar `regime_stats` ya embebido en metadata** para que N3 haga el trabajo que N4 debería hacer pero no hace.

---

## 4. Tres opciones de rediseño

### 4.1 Opción A — Diferenciación estricta (cada trie guarda solo su scope)

- N1: solo patrones de **otros** tokens (cross-asset universal)
- N2: solo patrones de **otros tokens del mismo asset_class**
- N3: solo patrones del **token actual sin distinguir régimen**
- N4: solo patrones del **token actual en el régimen actual**

**Requiere**: `build()` multi-asset (el engine hoy es single-symbol en `build()`).
**Costo**: Cada trie tendría ~25% de la data → confidence destruida por Bayesian shrinkage.
**Riesgo**: En TF 1m con 90 días, N4_volatile tendría ~15% × 12,960 ≈ 1,944 patrones, pero si `detect_simple()` marca 70% ranging, N4_ranging tendría ~9,000 y los demás <1,000. La asimetría rompe el "knowledge transfer".
**Veredicto**: NO recomendado para low TF. Reescribe el contrato del engine.

### 4.2 Opción B — Solo diferenciar N4 por régimen

- N1, N2, N3: quedan como réplicas (mantener compatibilidad)
- N4: solo patrones del token en el régimen detectado en el momento del build

**Requiere**: Filtrar en `build()` cuándo insertar en N4 según `regime`.
**Costo**: N4 tiene entre 15% y 70% de la data según distribución de regímenes.
**Riesgo**: N4 sparse → confidence baja → peso 40% en blue_chip arrastra el weighted_confidence hacia abajo.
**Ventaja**: Implementación mínima (solo un `if` en `build()`).
**Veredicto**: Mejora marginal pero **contradictorio**: si N4 pesa 40% y tiene baja confidence, **reduce** entradas (lo opuesto al objetivo del usuario).

### 4.3 Opción C — Eliminar la jerarquía física, usar regime_stats ya embebida

**Observación clave**: `BlockLifecycleMetadata` ya tiene:
- `regime_distribution: dict[str, int]` (línea 610-612 en metadata.py)
- `regime_stats: dict[str, RegimeStats]` con `count`, `wins`, `total_move_pct` por régimen
- `confidence_for_regime(current_regime, ...)` (línea ~459-531) que ajusta la confidence por régimen con multiplicador entre 0.5 y 1.2

**Es decir: N3 YA contiene la información de régimen dentro de cada nodo.** N4 no aporta nada nuevo; es solo una vista filtrada de N3 que pierde data.

**Opción C concreta:**

1. **Mantener el build con inserción única** (no en loop) en un solo trie `trie_n3` (per_asset). Eliminar físicamente `trie_n1`, `trie_n2`, `trie_n4` o dejarlos como aliases `= trie_n3` para no romper la API existente.

2. **En `match_raw()`**:
   - Consultar solo `trie_n3`
   - Aplicar `meta.confidence_for_regime(self._current_regime, ...)` para obtener `regime_adjusted_confidence`
   - Ese es el `weighted_confidence` final
   - Mantener `n1_match = n2_match = n4_match = n3_match` por compatibilidad con `PPMTResult`

3. **En `realtime.py` / `paper_trader.py`**: Asegurar que `set_regime()` se llama con el régimen detectado antes de cada `match_raw()`. El `RegimeDetector` ya existe.

4. **En `weights.py`**: Marcar `WEIGHT_PROFILES` como `DEPRECATED`. La adaptación por régimen ahora vive en `metadata.confidence_for_regime()`.

**Costo**: 1 trie en vez de 4. CPU ÷ 4. Memoria ÷ 4.
**Beneficio**: 
- Confidence diferenciada por régimen (no por nivel jerárquico)
- Misma cantidad de data → no destruye Bayesian shrinkage
- Aprovecha infraestructura ya implementada
- Para TF 1m: 4× menos CPU → más consultas por segundo → más oportunidades de entrada
- Para low TF con regímenes cambiantes: la confidence sube/baja según régimen actual → adapta frecuencia de entrada

**Veredicto**: **RECOMENDADO.** Es la opción que mejor cumple el objetivo del usuario (máxima frecuencia de entrada en low TF) sin romper la data ni la API.

---

## 5. Predicción de impacto por capa

### Capa 1 (este análisis)
- **Cambio**: Opción C implementada.
- **Métrica esperada**: CPU de match_raw ÷ 4. Memoria ÷ 4. Confidence ahora varía por régimen dentro del mismo trie.

### Capa 2 (matcher)
- **Cambio propagado**: El matcher sigue consultando 1 trie en vez de 4. El threshold de fuzzy (0.85 default) sigue igual. FIX-2 (separar similitud y confidence) sigue siendo necesario y ortogonal.
- **Métrica esperada**: matcher 4× más rápido en low TF → más oportunidades de matching por minuto.

### Capa 3 (signal)
- **Cambio propagado**: Las 4 confidences idénticas de antes se vuelven 1 confidence ajustada por régimen. FIX-3 (relajar `historical_count < 3` → `< 1`, cap de `adaptive_min_conf` a 0.20) sigue siendo necesario porque la confianza del régimen puede ser alta pero el count bajo.
- **Métrica esperada**: Más señales aprobadas en regímenes favorables, menos en desfavorables. Neto: +señales de calidad.

### Capa 4 (Living Trie feedback loop)
- **Cambio propagado**: El loop de feedback ahora actualiza 1 trie en vez de 4 → 4× más rápido. Regímenes nuevos se incorporan vía `regime_distribution` automáticamente.
- **Métrica esperada**: Living trie más reactivo, recalibración más frecuente viable.

---

## 6. Riesgos y mitigaciones

| Riesgo | Probabilidad | Mitigación |
|--------|-------------|------------|
| Tests existentes asumen 4 tries | Media | Mantener `trie_n1`/`n2`/`n4` como propiedades que devuelven `trie_n3` |
| PaperTrader serializa 4 tries | Media | En storage, serializar solo `trie_n3`; al cargar, asignar los 4 al mismo objeto |
| Pérdida de "universal knowledge" (N1) | Baja | El conocimiento universal real requeriría multi-asset build; hoy N1 = N3, así que no hay pérdida real |
| `set_regime()` no se llama en runtime | Alta | Documentar y garantizar llamada desde `realtime.py` antes de cada `match_raw()` |

---

## 7. Decisión

**Implementar Opción C.** Justificación:

1. Cumple el objetivo del usuario (máxima frecuencia en low TF) sin perder data.
2. Aprovecha `regime_stats` ya implementado en `BlockLifecycleMetadata` (líneas 610-628 en metadata.py) — infraestructura pagada pero no usada.
3. Reduce CPU y memoria 4× → habilita más consultas/minuto.
4. No rompe la API externa (mantiene `trie_n1`/`n2`/`n3`/`n4` y `PPMTResult.n1_confidence` etc.).
5. Combinable con FIX-2 (separar sim/conf en matcher), FIX-3 (relajar signal.py), FIX-4 (SL/TP rule) que se re-aplicarán en conjunto.

**Próximos pasos:**

1. Implementar FIX-1 Opción C en `ppmt.py` (build y match_raw), `realtime.py` (set_regime call).
2. Re-aplicar FIX-2 en `matcher.py` (separar similitud y confidence thresholds).
3. Re-aplicar FIX-3 en `signal.py` (relajar gates en paper mode).
4. Re-aplicar FIX-4 en `metadata.py` (SL/TP rule).
5. Re-correr audits capa 1, 2, 3.
6. Auditar capa 4 (Living Trie).
7. Push a GitHub.

---

## 8. Estado de archivos analizados

| Archivo | Líneas clave | Hallazgo |
|---------|--------------|----------|
| `engine/ppmt.py` | 141-144 (init), 291-302 (build), 331-349 (match_raw) | 4 tries réplicas confirmado |
| `engine/weights.py` | 31-56 (perfiles), 197-235 (compute_weighted) | Pesos estáticos, adapt() no se llama |
| `core/metadata.py` | 253-290 (confidence), 459-531 (confidence_for_regime), 610-628 (regime tracking) | `regime_stats` ya implementado pero sin uso en match_raw |
| `core/matcher.py` | 71-102 (init), 274-323 (best_match) | Threshold único 0.85, sin separación sim/conf |
| `engine/signal.py` | 492-584 (generate_entry_signal) | `historical_count < 3` + `adaptive_min_rr` estricto |
| `core/profiles.py` | 48-63 (TIMEFRAME_ALPHA_DEFAULTS) | 1m: α=5 W=7; 5m: α=4 W=7 — correcto para low TF |

---

**Fin del análisis estructural N1-N4.** Continúa con implementación de FIX-1 Opción C + re-aplicar FIX-2/3/4.
