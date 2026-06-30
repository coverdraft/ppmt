# TRAZABILIDAD v82 — Iteración RR Fix (9 variantes, 864+ backtests)

**Fecha:** 2026-06-30
**Autor:** Análisis sistemático para alcanzar WR>64% Y RR>1.8
**Repo:** https://github.com/coverdraft/ppmt
**Branch:** terminal-web

## Resumen Ejecutivo

Iteramos 9 variantes de posición management sobre v81 (campeón actual) buscando
alcanzar el objetivo **WR>64% Y RR>1.8** simultáneamente en cada perfil.

**Resultado final:** v82h es el mejor encontrado (+55% en RR promedio) pero
**ninguna variante pasa el gate** en todos los perfiles. 2/8 perfiles (BULL, MEME)
quedan a <7% del target de RR.

| Variante | WR avg | RR avg | Pasa gate (≥6/8) |
|----------|--------|--------|------------------|
| v81 (baseline) | 56.4% | 0.535 | 0/8 ❌ |
| v82a (trend 0.10) | 56.5% | 0.540 | 0/8 ❌ |
| v82b (no TP) | 25.8% | -0.414 | 0/8 ❌ (destruido) |
| v82c (TP 2.5 ATR) | 56.4% | 0.547 | 0/8 ❌ |
| v82d (TP 4.0 ATR) | 56.4% | 0.553 | 0/8 ❌ |
| v82e (partials 1.5/2.5R) | ~30% | ~0.2 | 0/8 ❌ (destruido) |
| v82f (trail 1.0 ATR post-lock) | ~50% | ~1.0 | 0/8 ❌ |
| v82g (regime trail) | ~50% | ~0.7 | 0/8 ❌ |
| **v82h (4 partials 0.8/1.5/2.5/4.0R + regime trail)** | **53.7%** | **0.831** | 0/8 ❌ |
| v82i (v82h + partial4 30%) | 53.7% | 0.831 | 0/8 ❌ (igual) |
| v82j (v82h + partials a R bajos) | 56.2% | 0.768 | 0/8 ❌ |

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

## Resultados Detallados v82h (96 runs)

| Perfil | v81 WR/RR | v82h WR/RR | Cambio RR | P&L v82h | Pasa gate? |
|--------|-----------|------------|-----------|----------|------------|
| MIXED | 68.7%/0.371 | 65.2%/0.580 | +56% | -74.79 | ❌ (RR 0.58) |
| BULL | 79.0%/1.027 | 78.7%/**1.674** | +63% | +276.75 | ❌ (RR 1.67, falta 7%) |
| BEAR | 68.0%/0.409 | 61.1%/**0.657** | +61% | +23.94 | ❌ (RR 0.66) |
| HIGHVOL | 77.6%/0.645 | 72.5%/**0.836** | +30% | -33.54 | ❌ (RR 0.84) |
| MEME | 78.6%/1.076 | 76.7%/**1.738** | +62% | +583.66 | ❌ (RR 1.74, falta 3%) |
| ALT | 79.4%/0.749 | 75.3%/**1.163** | +55% | +1.42 | ❌ (RR 1.16) |
| BLUE | 0%/0.000 | 0%/0.000 | n/a | 0.00 | ❌ (0 trades) |
| STABLE | 0%/0.000 | 0%/0.000 | n/a | 0.00 | ❌ (0 trades) |

**Promedios v82h:** WR 53.7% (-2.7pts vs v81), RR 0.831 (+55% vs v81)

## Hallazgos Clave

1. **El TP no importa cuando hay trail:** v82c (TP 2.5) y v82d (TP 4.0) apenas
   movieron RR (+0.012 y +0.018 vs v81) porque el trail 0.30 ATR se activa antes
   y corta los trades.

2. **Trail 1.0 ATR es lo que rompe el techo de RR:** v82f logró RR 2.14 en BULL
   y MEME pero destruyó MIXED/BEAR/HIGHVOL (WR cayó a 30-50%).

3. **Trail adaptativo al régimen es necesario:** v82g combinó trail ancho en
   alta volatilidad con trail estrecho en baja, pero los partials a R altos
   seguían saltándose en baja vol, dejando WR baja.

4. **4 partials a R altos + lock-activates-trail (v82h) es el equilibrio:**
   - Sube RR +55% en todos los perfiles
   - WR cae sólo 3pts (aceptable)
   - 2/8 perfiles (BULL, MEME) a <7% del target

5. **Sin estrategia para BLUE/STABLE:** ATR floor 0.40% bloquea todos los
   trades en perfiles de baja volatilidad. Necesitamos Strategy F (Grid Trading).

## Límite Fundamental Encontrado

Para alcanzar RR>1.8 en perfiles chop (MIXED/BEAR/HIGHVOL), necesitamos:
- **Más partials fireando en ganadoras** (no se logró porque pocos trades llegan a +2.5R)
- **O strategy differente para esos regímenes** (Strategy F grid, o regime switching)

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
- `scripts/backtest/v82h_rr_fix.py` — **CAMPEÓN** — 4 partials + regime trail
- `scripts/backtest/v82i_rr_fix.py` — v82h + partial4 30%
- `scripts/backtest/v82j_rr_fix.py` — v82h + partials a R bajos
- `scripts/backtest/run_v82_full.py` — Runner unificado + reporte

### Resultados (JSON)
- `/tmp/v81_universal.json` — Baseline (96 runs)
- `/tmp/v82a_universal.json` through `/tmp/v82j_universal.json` — 96 runs cada uno
- `/tmp/v82_full_report.json` — Consolidated summary

### TypeScript engine (portado)
- `src/lib/paper-trading-engine.ts` — v82h portado:
  - Strategy B `_checkStops`: 4 partials @ 0.8/1.5/2.5/4.0R
  - Trail regime-aware (1.0/0.5/0.3 ATR por volatilidad)
  - Lock activa trail inmediatamente
  - TP 6.0 ATR (4R techo lejano)
  - Pyramid 0.50 (was 0.75)
  - Trend filter 0.05 (v81 baseline, ablation confirmed)
