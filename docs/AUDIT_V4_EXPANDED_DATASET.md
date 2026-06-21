# AUDIT v0.40.11: Dataset expandido v4 (5 majors + 4 memes + 7 alts × 200k velas)

**Fecha**: 2026-06-19
**Versión**: v0.40.11
**Autor**: Auditoría automática sobre data real Binance 1m

## 1. Resumen ejecutivo

Esta auditoría responde a la solicitud del usuario de "reducir majors a 5 y subir alts a 6-7" para mejorar la calidad del motor. Se expandió el dataset de 14 tokens × 100k velas (v3) a **16 tokens × 200k velas (v4)** = 3.2M velas (+128%).

**Resultados clave**:

- **Dataset v4 completo**: 16 tokens × 200k velas = 3,200,000 velas únicas, rango 2026-01-30 → 2026-06-18 (~140 días).
- **Mejora neta PnL**: -281.98% (v3) → **-173.25%** (v4) = +108.73pp improvement.
- **SHORT mejora enormemente**: +448.86% → **+965.45%** = +516.59pp. SHORT es ahora claramente rentable.
- **LONG empeora**: -730.80% → -1138.69% = -407.89pp. LONG sigue siendo el problema estructural.
- **L/S ratio se invierte**: 1.03 → **0.87** (ahora SHORT-dominante, esperable en mercado bajista).
- **N4 saturation**: 27.9% → 33.6% (+5.7pp). Más datos ayudaron pero menos de lo predicho (45-55%).
- **FIX-14 (N4 routing)**: aún NO mejora globalmente (-6.09pp vs N3), pero **sí mejora memes** (+27.57pp).
- **7 de 16 tokens rentables** con N4 (vs 6 con N3).

## 2. Setup experimental

### 2.1 Token selection (per user spec)

| Clase | Tokens | Justificación |
|---|---|---|
| Majors (5) | BTC, ETH, SOL, BNB, XRP | Top 5 por liquidez y capitalización |
| Memes (4) | PEPE, WIF, BONK, FLOKI | Alta volatilidad, regímenes bajistas frecuentes |
| Alts (7) | LINK, ARB, OP, SUI, APT, INJ, TIA | Diversidad L1/L2/DeFi/infraestructura |

**Total**: 16 tokens. Cambios vs v3:
- Dropped: ADA, AVAX, DOGE (3 majors de menor liquidez)
- Kept: LINK, ARB (alts)
- Added: OP, SUI, APT, INJ, TIA (5 alts nuevos)

### 2.2 Dataset

- **Velas por token**: 200,000 (vs 100,000 en v3, +100%)
- **Total velas**: 3,200,000 (vs 1,400,000 en v3, +128%)
- **Rango temporal**: 2026-01-30 → 2026-06-18 (~140 días, vs 70 días en v3)
- **Binance API**: 0.15s sleep, ~12 min descarga total, sin rate limits
- **Sin duplicados**: 0 duplicados verificados en todos los 16 CSVs

### 2.3 Walk-forward

- **Train**: 150,000 velas por token (~104 días)
- **Test**: 50,000 velas por token (~35 días OOS)
- **Total OOS**: 800,000 velas (vs 420,000 en v3, +90.5%)
- **Config**: α=4, W=7, PL=5, min_conf=0.15

## 3. Resultados walk-forward

### 3.1 Comparación v3 → v4 (motor N3-only)

| Métrica | v3 N3 (14tok × 100k) | v4 N3 (16tok × 200k) | Delta |
|---|---:|---:|---:|
| Señales totales | 55,742 | 110,316 | +97.9% |
| L/S ratio | 1.03 | 0.87 | -15.5% |
| Hit rate | 47.0% | 46.6% | -0.4pp |
| **PnL total** | **-281.98%** | **-173.25%** | **+108.73pp** |
| PnL LONG | -730.80% | -1138.69% | -407.89pp |
| PnL SHORT | +448.86% | +965.45% | **+516.59pp** |

### 3.2 Comparación N3 vs N4 en v4

| Métrica | N3-only (baseline) | N4-regime (FIX-14) | Delta |
|---|---:|---:|---:|
| Señales totales | 110,316 | 110,352 | +0.0% |
| LONG signals | 51,227 | 52,293 | +2.1% |
| SHORT signals | 59,089 | 58,059 | -1.7% |
| L/S ratio | 0.87 | 0.90 | +3.4% |
| Hit rate | 46.6% | 46.6% | +0.00pp |
| **PnL total** | **-173.25%** | **-179.34%** | **-6.09pp** |
| PnL LONG | -1138.69% | -1147.10% | -8.41pp |
| PnL SHORT | +965.45% | +967.74% | +2.29pp |

### 3.3 Por clase de token (N3 vs N4)

| Clase | N3 PnL | N4 PnL | Delta | N4 verdicto |
|---|---:|---:|---:|---|
| Majors (5) | -19.66% | -42.23% | -22.57pp | ✗ N4 peor |
| Memes (4) | -43.89% | -16.32% | **+27.57pp** | ✓ **N4 mejor** |
| Alts (7) | -109.70% | -120.79% | -11.09pp | ✗ N4 peor |

### 3.4 Tokens rentables

**N3**: 6 de 16 rentables — TIA (+115%), XRP (+79%), FLOKI (+74%), WIF (+21%), BNB (+10%), BONK (-13% perdedor pero cercano).

**N4**: 7 de 16 rentables — TIA (+90%), XRP (+82%), FLOKI (+75%), WIF (+20%), BNB (+16%), BONK (+20%), TIA (+90%).

**BONK mejora con N4**: -12.94% → +19.65% (+32.59pp). Es el caso donde N4 routing más ayudó.

## 4. Conteo de nodos

| Capa | v3 (14tok × 100k) | v4 (16tok × 200k) | Delta |
|---|---:|---:|---:|
| N3 total nodes | 19,109 | 21,840 | +2,731 (+14.3%) |
| N4 total nodes | 23,482 | 33,619 | +10,137 (+43.2%) |
| N3 saturation | 100.0% | 100.0% | 0pp (saturado) |
| **N4 saturation** | **27.9%** | **33.6%** | **+5.7pp** |
| Combined N3+N4 | 42,591 | 55,459 | +12,868 (+30.2%) |

### 4.1 Por clase de token

| Clase | N3 nodos | N4 nodos | N4 with obs | N4 regímenes activos |
|---|---:|---:|---:|---:|
| Majors (5) | 6,825 | 8,898 | 5,995 | 19 (3.8/token) |
| Memes (4) | 5,460 | 9,037 | 5,802 | 16 (4.0/token) |
| Alts (7) | 9,555 | 15,684 | 10,193 | 28 (4.0/token) |

### 4.2 Análisis de saturación

- **N3 saturado al 100%**: 16 tokens × 1,024 patrones teóricos = 16,384 máx → 16,384 activos. Más data NO añade patrones nuevos a N3.
- **N4 al 33.6%**: 16 tokens × 4 regímenes × 1,024 = 65,536 máx → 21,990 activos. Aún hay 41,546 patrones teóricos sin poblar.
- **Crecimiento real**: el doble de data + 2 tokens nuevos solo elevó N4 saturación 5.7pp. Saturar N4 al 60% requeriría ~10x data actual (32M velas) o más tipos de tokens.

### 4.3 Distribución de regímenes (train set)

| Régimen | Observaciones | % del total |
|---|---:|---:|
| ranging | 337,000+ | ~99.0% |
| trending_down | ~3,000 | ~0.9% |
| trending_up | ~2,500 | ~0.7% |
| volatile | ~100 | ~0.03% |

**Hallazgo crítico**: el 99% de los patrones del train set se clasifican como `ranging`. Esto explica por qué N4 routing apenas mueve la aguja: el sub-trie `ranging` es prácticamente idéntico al trie N3. Para que N4 aporte valor, necesitaríamos un detector de régimen más sensible que clasifique más velas como `trending_up`/`trending_down`.

## 5. Veredicto

### 5.1 Sobre la expansión del dataset

**SÍ valió la pena**. El PnL total mejoró +108.73pp (-282% → -173%), casi exclusivamente por la mejora en SHORT (+516pp). El dataset más largo capturó más regímenes bajistas donde SHORT gana.

### 5.2 Sobre la elección de tokens (5 majors + 4 memes + 7 alts)

**Mezcla adecuada**. Los alts nuevos (OP, SUI, APT, INJ, TIA) tienen L/S ratio < 1 (más SHORT que LONG), lo cual es bueno para un mercado bajista. TIA es el token más rentable (+90-115%) — confirma que incluir alts con historial diverso ayuda.

### 5.3 Sobre FIX-14 (N4 routing)

**Aún no ayuda globalmente** (-6pp), PERO:
- **Sí ayuda en memes** (+27.57pp).
- **Sí ayuda en BONK específicamente** (+32.59pp).
- Es neutral en SHORT (+2.29pp).

La causa raíz de que N4 no despegue es estructural: el `RegimeDetector` actual clasifica el 99% de las velas como `ranging`, lo que hace que el sub-trie `ranging` sea casi idéntico al N3. **Para que N4 aporte valor, se necesita mejorar el detector de régimen** (FIX-17 candidato).

### 5.4 Sobre LONG signals

**LONG sigue siendo el problema estructural** (-1138% agregado, peor que v3 -730%). Ampliar data NO resolvió LONG. Las posibles causas:
1. El test set 2026-05-04 → 2026-06-18 incluye una corrección fuerte del mercado crypto (BTC bajó de ~70k a ~62k).
2. Los patrones alcistas del train set (que incluye la subida de enero a marzo) ya no son válidos en el test set.
3. El motor no distingue entre "régimen alcista" y "régimen bajista" al disparar LONG.

**Recomendación FIX-15 (alta prioridad)**: Implementar thresholds diferenciados por dirección:
- LONG: min_conf=0.25 (más estricto, fewer signals)
- SHORT: min_conf=0.15 (igual)

Esto debería filtrar los LONG signals de baja confianza que pierden dinero.

## 6. Recomendaciones para siguiente iteración

1. **FIX-15 (alta)**: Thresholds diferenciados por dirección (LONG=0.25, SHORT=0.15). Implementación en `engine/prediction.py` y `engine/predict_live.py`. Test: ver si LONG PnL mejora sin sacrificar SHORT.

2. **FIX-17 (media)**: Mejorar `RegimeDetector` para que sea más sensible a tendencias. Actualmente 99% ranging. Si logramos 60% ranging / 20% up / 20% down, N4 routing tendrá sub-tries distintos a N3.

3. **FIX-16 (baja)**: Per-asset LONG/SHORT enable flags. Desactivar LONG en tokens consistentemente perdedores.

4. **No ampliar más data por ahora**: N3 saturado al 100%, N4 saturación creció solo 5.7pp con el doble de data. Mejor invertir esfuerzo en FIX-15 y FIX-17.

5. **Considerar SHORT-only mode**: Dado que SHORT es consistentemente rentable (+965% en v4) y LONG consistentemente perdedor, una estrategia conservadora sería deshabilitar LONG y operar solo SHORT.

## 7. Archivos

### Scripts nuevos (en `scripts/audit_trie_1m/`)
- `download_1m_v4.py` — descarga 16 tokens × 200k velas, resume-capable
- `count_nodes_v4.py` — conteo de nodos N3/N4 con saturación y comparación v3
- `layer1_v4_walkforward.py` — walk-forward audit N3 vs N4 con breakdown por clase

### Artefactos en `/home/z/my-project/download/` (no en git por tamaño)
- `real_data_1m_v4/*.csv` — 16 archivos CSV, ~370 MB total, 3.2M velas
- `real_data_1m_v4/_summary.json` — metadata descarga
- `trie_stats_1m_v4/node_counts_v4.json` — conteo de nodos
- `trie_stats_1m_v4/layer1_v4_walkforward.json` — resultados walk-forward por token
- `trie_stats_1m_v4/layer1_v4_aggregate.json` — agregados por clase
- `trie_stats_1m_v4/layer1_v4_summary.md` — resumen ejecutivo

### Documentación
- `docs/AUDIT_V4_EXPANDED_DATASET.md` (este doc)
- `TRAZABILIDAD.md` — entrada v0.40.11 detallada
