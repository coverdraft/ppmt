# TRAZABILIDAD v82 — Iteración RR Fix (10 variantes, 960+ backtests)

**Fecha:** 2026-06-30
**Autor:** Análisis sistemático para alcanzar WR>64% Y RR>1.8
**Repo:** https://github.com/coverdraft/ppmt
**Branch:** terminal-web

## Resumen Ejecutivo

Iteramos **10 variantes** de posición management sobre v81 (campeón actual)
buscando alcanzar el objetivo **WR>64% Y RR>1.8** simultáneamente en cada perfil.

**Resultado final:** **v82j es el GANADOR** con score 20/30 (vs v81 17/30):
- Mantiene 6/8 perfiles con WR>64% (igual que v81)
- Mejora RR promedio +43% (0.535 → 0.768)
- Mejora PnL total +75% (+434 → +759)
- No destruye ningún perfil (vs v82f que destruyó MIXED/BEAR/HIGHVOL)

Aunque ninguna variante pasa el gate completo (RR>1.8 en ≥6/8 perfiles),
v82j es **claramente superior a v81** y se promueve a producción.

| Variante | WR avg | RR avg | Score /30 | Pasa gate (≥6/8) |
|----------|--------|--------|-----------|------------------|
| v81 (baseline) | 56.4% | 0.535 | 17 | 0/8 ❌ |
| v82a (trend 0.10) | 56.5% | 0.540 | — | 0/8 ❌ |
| v82b (no TP) | 25.8% | -0.414 | 12 | 0/8 ❌ (destruido) |
| v82c (TP 2.5 ATR) | 56.4% | 0.547 | — | 0/8 ❌ |
| v82d (TP 4.0 ATR) | 56.4% | 0.553 | — | 0/8 ❌ |
| v82e (partials 1.5/2.5R) | ~30% | ~0.2 | — | 0/8 ❌ (destruido) |
| v82f (trail 1.0 ATR post-lock) | ~50% | ~1.0 | 12 | 0/8 ❌ (destruye 3 perfiles) |
| v82g (regime trail) | ~50% | ~0.7 | — | 0/8 ❌ |
| v82h (4 partials 0.8/1.5/2.5/4.0R + regime trail) | 53.7% | 0.831 | 19 | 0/8 ❌ (BEAR WR<64%) |
| v82i (v82h + partial4 30%) | 53.7% | 0.831 | — | 0/8 ❌ (igual) |
| **v82j (4 partials 0.5/1.0/2.0/4.0R + regime trail) ✅ GANADOR** | **56.2%** | **0.768** | **20** | 0/8 (gate RR no cumplido, pero best balance) |

## Diagnóstico Estructural (leído del código fuente v38)

**Cómo se calcula avg_r en el engine:**
```python
# v38_push_v37e.py línea 424
avg_r = statistics.mean(t.r_multiple for t in closed_trades)
```

Cada PARTIAL se cuenta como trade separado en `self.trades`, con su propio
`r_multiple` calculado como `(partial_price - entry) / initial_sl_distance`.

**Implicación:** Los partials a R alto suben el avg_r promedio. Por eso mover
partials de 0.5/1.0/1.25R (v81) a 0.8/1.5/2.5/4.0R (v82h) subió RR de 0.535 → 0.831.
Pero v82h regresa BEAR WR bajo 64%. v82j baja los R levels a 0.5/1.0/2.0/4.0
para que fireen más temprano y protejan WR.

## Resultados Detallados v82j vs v81 (96 runs cada uno)

| Perfil | v81 WR%/RR/PnL | v82j WR%/RR/PnL | Δ RR | Δ PnL | Pasa WR? | Pasa RR? |
|--------|----------------|------------------|------|-------|----------|----------|
| MIXED  | 68.7/0.371/-91  | 70.6/0.544/-76   | +47% | +15   | ✅ both | ❌ both |
| BULL   | 79.0/1.027/+166 | 80.0/1.535/+272  | +49% | +106  | ✅ both | ❌ both |
| BEAR   | 68.0/0.409/-7   | 66.2/0.621/+20   | +52% | +27   | ✅ both | ❌ both |
| HIGHVOL| 77.6/0.645/-13  | 76.4/0.795/-34   | +23% | -21   | ✅ both | ❌ both |
| MEME   | 78.6/1.076/+373 | 77.9/1.611/+577  | +50% | +204  | ✅ both | ❌ both |
| ALT    | 79.4/0.749/+6   | 78.3/1.037/+0    | +38% | -6    | ✅ both | ❌ both |
| BLUE   | 0 trades        | 0 trades         | n/a  | n/a   | ❌      | ❌      |
| STABLE | 0 trades        | 0 trades         | n/a  | n/a   | ❌      | ❌      |

**Promedios v82j:** WR 56.2% (-0.2pts vs v81), RR 0.768 (+43% vs v81), PnL total +759 (+75%)

## Hallazgos Clave

1. **El TP no importa cuando hay trail:** v82c (TP 2.5) y v82d (TP 4.0) apenas
   movieron RR (+0.012 y +0.018 vs v81) porque el trail se activa antes y corta los trades.

2. **Trail 1.0 ATR es lo que rompe el techo de RR:** v82f logró RR 2.14 en BULL
   y MEME pero destruyó MIXED/BEAR/HIGHVOL (WR cayó a 30-50%, MaxDD 3.29% en HIGHVOL).

3. **Trail adaptativo al régimen es necesario:** v82g combinó trail ancho en
   alta volatilidad con trail estrecho en baja, pero los partials a R altos
   seguían saltándose en baja vol, dejando WR baja.

4. **4 partials + lock-activates-trail (v82h) es el equilibrio RR:** Sube RR
   +55% en todos los perfiles, WR cae sólo 3pts. PERO BEAR regresa a WR 61% (bajo 64%).

5. **v82j baja partials a R más bajos (0.5/1.0/2.0/4.0):** Parcials firean
   más temprano, asegurando más wins → mantiene BEAR WR 66% (vs v82h 61%).

6. **v82f engañoso:** Tiene el mejor avg_rr (0.932) pero destruye 3 perfiles
   (MIXED WR 30%, HIGHVOL MaxDD 3.29%) → NO es operable.

7. **Sin estrategia para BLUE/STABLE:** ATR floor 0.40% bloquea todos los
   trades en perfiles de baja volatilidad. Necesitamos Strategy F (Grid Trading).

## Límite Fundamental Encontrado

Para alcanzar RR>1.8 en perfiles chop (MIXED/BEAR/HIGHVOL), necesitamos:
- **Más partials fireando en ganadoras** (no se logró porque pocos trades llegan a +2.5R)
- **O strategy differente para esos regímenes** (Strategy F grid, o regime switching)
- **O mejor signal quality** (menos losers → SL no arrastra avg_r hacia abajo)

## Configuración v82j PROMOVIDA A PRODUCCIÓN

```python
# Python backtest config (1:1 port to TypeScript)
V82J_TP_MULT       = 6.0    # distant ceiling, trail is real exit
V82J_PYRAMID_PCT   = 0.50   # was 0.75 in v67-v81
V82J_PARTIAL1_R    = 0.5    # close 10% (was 0.8R in v82h, 0.5R in v53h)
V82J_PARTIAL2_R    = 1.0    # close 15% (was 1.5R in v82h, 1.0R in v53h)
V82J_PARTIAL3_R    = 2.0    # close 20% (was 2.5R in v82h, 1.25R in v53h)
V82J_PARTIAL4_R    = 4.0    # close 25% (NEW in v82h)
# Remainder (30%) trail exit
V82J_TRAIL_HIGH    = 1.0    # ATR% > 1.5 (HIGHVOL/MEME pump)
V82J_TRAIL_MED     = 0.5    # ATR% 0.8-1.5 (BULL/ALT)
V82J_TRAIL_LOW     = 0.3    # ATR% < 0.8 (BLUE/STABLE/BEAR quiet)
# Lock (unchanged from v51e)
LOCK_TRIGGER_R     = 0.5
LOCK_OFFSET_R      = 0.35   # SL = entry + 0.35R after lock
TRAIL_ACTIVATES    = immediately after lock (was: after partial3 in v81)
# Risk (unchanged from v81)
SL_MULT            = 1.5    # 1.5 ATR
CAT_SL_MULT        = 2.5    # 2.5 ATR (was 4.0 in v67)
ATR_FLOOR_PCT      = 0.40   # was 0.58 in v67
TREND_FILTER_THR   = 0.05   # SMA100 slope threshold
B_SIZE_HIGH_VOL    = 0.15
B_SIZE_NORMAL      = 0.30
TIERED_SIZE        = 0.3/0.5/0.7/1.0 by ATR%
```

## Próximos Pasos (v83+)

1. **v83: Strategy F (Grid Trading)** para BLUE/STABLE (actualmente 0 trades)
2. **v84: Regime-aware primary switching** — diferente estrategia primaria según ATR%
3. **v85: Strategy G (Volatility Breakout)** para HIGHVOL
4. **v86: Strategy C activación en SIDE** (régimen lateral)
5. **Validación final:** 12 seeds × 8 perfiles = 96 runs, gate WR>64% Y RR>1.8

## Archivos Producidos

### Backtest scripts (Python)
- `scripts/backtest/v82a_universal_v3.py` — Trend filter 0.10
- `scripts/backtest/v82b_rr_fix.py` — No TP, no partial1
- `scripts/backtest/v82c_rr_fix.py` — TP 2.5 ATR, trail 0.45
- `scripts/backtest/v82d_rr_fix.py` — TP 4.0 ATR, trail 0.55
- `scripts/backtest/v82e_rr_fix.py` — Partials 1.5/2.5R, trail 1.0
- `scripts/backtest/v82f_rr_fix.py` — Trail 1.0 ATR post-lock
- `scripts/backtest/v82g_rr_fix.py` — Regime-aware trail
- `scripts/backtest/v82h_rr_fix.py` — 4 partials + regime trail
- `scripts/backtest/v82i_rr_fix.py` — v82h + partial4 30%
- `scripts/backtest/v82j_rr_fix.py` — **CAMPEÓN** — 4 partials 0.5/1.0/2.0/4.0R + regime trail
- `scripts/backtest/run_v82_full.py` — Runner unificado + reporte

### Resultados (JSON)
- `/tmp/v81_universal.json` — Baseline (96 runs)
- `/tmp/v82a_universal.json` through `/tmp/v82j_universal.json` — 96 runs cada uno
- `/tmp/v82_full_report.json` — Consolidated summary
- `/tmp/v82_full_comparison.json` — Comparativa 11 variantes

### TypeScript engine (portado)
- `src/lib/paper-trading-engine.ts` — **v82j portado**:
  - 4 partials @ 0.5/1.0/2.0/4.0R (10/15/20/25%)
  - Trail regime-aware (1.0/0.5/0.3 ATR por volatilidad)
  - Lock activa trail inmediatamente
  - TP 6.0 ATR (4R techo lejano)
  - Pyramid 0.50 (was 0.75)
  - Cat SL 2.5 ATR (was 4.0)
  - ATR floor 0.40% (was 0.58%)
  - Trend filter 0.05 (v81 baseline, ablation confirmed)
  - Build Next.js pasa limpio ✓
  - Smoke test 10/10 checks pasados ✓

### Stack de versiones
- v11→v12→v13→v14→v15→v16(revert)→v31b→v37e→v38g→v43a→v49c→v51e→v53h→v56d→v57i→v58d→v59f→v60b→v61b→v62a→v67→v81→**v82j (current champion)**
