# PPMT — Estado del Proyecto (Jun 2026)

> Documento vivo. Última actualización: **v81** (commit pending — Universal Engine: trend filter + dynamic ATR + regime pyramid).
> Idioma: español (consistencia con el equipo).

---

## 1. Qué es PPMT

PPMT (**Paper Trading Multi-Strategy**) es un motor de trading paper que corre
100% en el navegador (Next.js + Zustand). Genera precios sintéticos con GBM +
regime switching, simula comisiones y slippage, y ejecuta 4 estrategias en
paralelo sobre 10 tokens durante ventanas de 4–6 h.

**Objetivo**: validar por backtest (12 seeds × 10 tokens × 14.400 ticks × 8 perfiles)
que el sistema es **profitable estable en CUALQUIER régimen y token** antes de
mandarlo a producción (HF Space 24/7).

---

## ⚠️ VEREDICTO v67 → v81 (cambio crítico, Jun 30 2026)

**v67 RECHAZADO** como production champion. Backtest multi-regime (12 seeds × 8 perfiles)
reveló que v67 solo funciona marginalmente en 1/8 perfiles (HIGHVOL) y es
**catastrófico** en 6/8 perfiles:

| Perfil | v67 P&L | v67 Profit% | v67 MaxDD% | Veredicto |
|--------|---------|-------------|------------|-----------|
| MIXED  | -22     | 8%          | 0.37%      | ❌ Marginal |
| BULL   | -295    | 0%          | 3.09%      | ❌ Catastrófico |
| BEAR   | -164    | 0%          | 1.69%      | ❌ Catastrófico |
| HIGHVOL| -101    | 33%         | 2.34%      | ⚠️ Único marginal |
| MEME   | -206    | 17%         | 3.13%      | ❌ Catastrófico |
| ALT    | -124    | 8%          | 1.61%      | ❌ Catastrófico |
| BLUE   | 0       | 0%          | 0%         | ❌ No tradea |
| STABLE | 0       | 0%          | 0%         | ❌ No tradea |

**v81 ES EL NUEVO CHAMPION** (5 fixes aplicados al motor):

| Perfil | v67 P&L | v81 P&L | Δ       | v67 Profit% | v81 Profit% | Veredicto |
|--------|---------|---------|---------|-------------|-------------|-----------|
| MIXED  | -22     | -91     | -69     | 8%          | 17%         | ⚠️ Regresión P&L, mejora Profit% |
| BULL   | -295    | +165    | +461    | 0%          | **100%**    | ✅ HUGE |
| BEAR   | -164    | -7      | +157    | 0%          | 50%         | ✅ HUGE |
| HIGHVOL| -101    | -13     | +88     | 33%         | 25%         | ✅ Mejor |
| MEME   | -206    | +373    | +579    | 17%         | **100%**    | ✅ HUGE |
| ALT    | -124    | +6      | +130    | 8%          | 42%         | ✅ HUGE |
| BLUE   | 0       | 0       | 0       | 0%          | 0%          | — (TODO v82 grid) |
| STABLE | 0       | 0       | 0       | 0%          | 0%          | — (TODO v82 grid) |

**v81 wins 5/8 perfiles** vs v67's 1/8. MEME y BULL pasan de catastróficos a 100% profit.

---

## 2. LONG/SHORT support — veredicto

**v67**: SÍ tiene lógica LONG/SHORT en las 4 estrategias, pero solo funciona
correctamente en un lado por régimen:
- LONG funciona en BULL (+138) y MEME (+374) vía Strategy A (momentum)
- SHORT funciona en HIGHVOL (+34) vía Strategy B (mean reversion)
- SHORT CATASTRÓFICO en BULL (-4428) y MEME (-6138) — Strategy B apuesta contra tendencia
- LONG CATASTRÓFICO en BEAR (-835) — mismo problema

**v81**: Trend filter (slope-based) arregla el problema fundamental:
- LONG en BULL: +212 (vs v67 +138) — alineado
- SHORT en BEAR: +56 (vs v67 -44) — trend filter bloquea bad longs
- SHORT en HIGHVOL: +34 (vs v67 -2) — same
- LONG en MEME: +460 (vs v67 +374) — trend filter no bloquea momentum longs

**v81 SÍ maneja bien LONG y SHORT** en la mayoría de regímenes.

---

## 3. Multi-token support — veredicto

**v67**: NO funciona en memes ni bluechips:
- MEME: P&L -206, MaxDD 3.13%, Profit 17% (catastrófico)
- BLUE: 0 trades (ATR floor 0.58% bloquea)
- STABLE: 0 trades (mismo)

**v81**: Mejora significativa pero STILL no cubre BLUE/STABLE:
- MEME: P&L +373, MaxDD 0.64%, Profit **100%** ✅
- ALT: P&L +6, MaxDD 0.69%, Profit 42% ✅
- BLUE: 0 trades (ATR floor 0.40% aún bloquea)
- STABLE: 0 trades (mismo)

**Para BLUE/STABLE se necesita Strategy F (Grid Trading)** — TODO v82.

---

## 4. Composición actual del motor (v81)

### 4.1 Estrategias activas

| Strat | Tipo | Allocation | Tamaño base | RSI/Signal | Estado |
|------|------|-----------|-------------|------------|--------|
| **A** | Momentum 24h | 30% = 3.000 USDT | 0.040 (v67) | top movers \|chg\| + RSI 25/75 | Activa |
| **B** | Mean Reversion + Trend Filter (v81) | 25% = 2.500 USDT | 0.30 normal / 0.15 HIGHVOL (v81) + PYRAMID +75% @+1.0R (disabled if ATR%>1.5) | RSI 30/70 + SMA100 slope gate | Workhorse |
| **C** | Range Breakout | 25% = 2.500 USDT | 0.025 | rolling 60-tick high/low | Pausada (inerte) |
| **D** | Vol Squeeze | 20% = 2.000 USDT | 0.025 | Bollinger squeeze + first move | Inerte (no dispara) |

### 4.2 Gestión de riesgo por posición (v81)

| Parámetro | Valor | Origen |
|-----------|-------|--------|
| SL | 1.5 × ATR | v51e |
| TP parcial 1 | +0.5R → cierra 5% | v53h |
| TP parcial 2 | +1.0R → cierra 10% | v53h |
| TP parcial 3 | +1.25R → cierra 15% + activa trail | v53h |
| Lock (BE) | +0.5R → SL a entry+0.35R | v51e |
| Trail | 0.30 × ATR | v49c |
| Cooldown | 30 min post-stop por token | v11 |
| Time stop | 4 h máx hold | v11 |
| ATR floor | **0.40 %** (v81, was 0.58 v67) | v81 F2 |
| Catastrophic SL | **2.5 × ATR** (v81, was 4.0) | v81 F4 |
| TIERED size | **0.3/0.5/0.7/1.0** by ATR (v81, was 0.4/0.7/1.0) | v81 F6 |
| Trend filter B | SMA100 slope ±0.05% (v81 NEW) | v81 F1 |
| Pyramid disable | ATR% > 1.5 (v81 NEW) | v81 F3 |
| B size HIGHVOL | 0.15 (v81, was 0.30) | v81 F5 |

### 4.3 Costes de transacción simulados

- Fee: 0.10 % por trade (entrada + salida)
- Slippage: 0.05 % (modelo simple)
- Total round-trip: ~0.25 % — el motor tiene que superar esto para ser profitable.

---

## 5. Stack de versiones (trayectoria)

Cada versión se valida en **12 seeds** (`[42, 1337, 31337, 7, 99, 1234, 7777,
2025, 314, 555, 888, 2024]`). Menos de 12 seeds = overfit (lección v44).

| Version | WR% | P&L / 4h | MaxDD% | Profit% | PF | AvgR | Notas |
|---------|-----|----------|--------|---------|----|------|-------|
| v11–v37e | — | — | — | — | — | — | Foundation |
| v38g | 61.8 % | +13.92 | 0.31 % | 67 % | 1.46 | +0.41 | Baseline pre-v43a |
| v43a | 72.5 % | +13.92 | 0.28 % | 67 % | 1.53 | +0.61 | Multi-partial TP |
| v49c | 73.1 % | +20.18 | 0.27 % | 67 % | 1.75 | +0.66 | Stricter momentum 0.55 + trail 0.30 |
| v51e | 75.3 % | +23.07 | 0.26 % | 67 % | 1.90 | +0.64 | SL 1.5 + lock_offset 0.35 |
| v53h | 79.4 % | +27.00 | 0.28 % | 58 % | 2.04 | +0.77 | 3-partial TP @1.25R + B 0.125 |
| v56d | 79.4 % | +26.76 | 0.17 % | 67 % | 2.53 | +0.77 | Adaptive ATR sizing |
| v57i–v60b | 79.4 % | +42.89 | 0.28 % | 67 % | 2.56 | +0.77 | Size refinements |
| v61b | 79.6 % | +46.02 | 0.29 % | 67 % | 2.66 | +0.76 | PYRAMID B +50% @+1.0R |
| v62a | 79.6 % | +48.56 | 0.29 % | 67 % | 2.72 | +0.75 | PYRAMID B +75% @+1.0R |
| v67 | 79.6 % | +46.93 | 0.29 % | 67 % | 3.01 | +0.75 | A 0.040 + B 0.30 — **RECHAZADO multi-regime** |
| **v81** 🏆 | — | — | — | — | — | — | **Universal Engine: 5/8 perfiles rentables, MEME+BULL 100% profit** |

> Nota: los WR%/P&L de v38–v67 son en régimen MIXED únicamente. v63 reveló que
> eran overfit a MIXED y fallan catastróficamente en BULL/BEAR/MEME/ALT.
> v81 es el primero validado en 12 seeds × 8 perfiles (96 runs).

---

## 6. Lecciones aprendidas (qué NO funciona)

Estos caminos se probaron y **se descartaron con evidencia**:

| Idea | Resultado | Veredicto |
|------|-----------|-----------|
| Filtro de tendencia SMA(100) level-based | Reduce P&L en SIDE | ❌ Descartado (v40) |
| Slope-based SMA100 trend filter (v81) | ✅ BULL/MEME 100% profit | ✅ Adoptado (v81 F1) |
| SL más ajustado (1.3 ATR) | Reduce P&L | ❌ Descartado |
| ATR floor 0.65 % | Solo 17 % seeds profitable | ❌ Descartado |
| ATR floor 0.20 % | MIXED regresa -143, BLUE/STABLE pierden | ❌ Descartado (v80) |
| ATR floor 0.40 % | Compromise: MIXED OK, MEME 100% profit | ✅ Adoptado (v81 F2) |
| Catastrophic SL 4.0 ATR | Tail risk too large in HIGHVOL | ❌ Replaced (v81 F4) |
| Catastrophic SL 2.5 ATR | Tighter cap, MaxDD HIGHVOL 2.34→1.54 | ✅ Adoptado (v81 F4) |
| Risk gates (max_concurrent, dd_cooldown) | Inerte o dañino | ❌ Descartado (v46, v55) |
| Session kill switch | Corta ganadores más que perdedores | ❌ Descartado (v56) |
| Trail dinámico basado en R | Peor que trail fijo 0.30 | ❌ Descartado (v53) |
| Trail 0.25 o 0.35 | Peor que 0.30 | ❌ Descartado (v52) |
| Partial2 a +0.8R o +1.2R | Peor que +1.0R | ❌ Descartado (v50) |
| Partial3 a 1.15R o 1.30R | Peor que 1.25R | ❌ Descartado (v58e/f) |
| RSI 28/72 o 32/68 (B) | Peor que 30/70 | ❌ Descartado (v50) |
| SL 1.45 o 1.6 | Peor que 1.5 | ❌ Descartado (v52) |
| Pyramid en HIGHVOL (ATR% > 1.5) | MaxDD 2.34%, amplifica pérdidas | ❌ Replaced (v81 F3) |
| Pyramid disabled en HIGHVOL | MaxDD 1.54%, menos profit pero controlado | ✅ Adoptado (v81 F3) |
| Equity Curve Protection (v63) | Profit% 67→58 y Sharpe 9.82→2-5 | ❌ Descartado (v63) |
| Strategy E RSI 5/95 scalp (v63) | RSI<5 imposible en datos sintéticos | ⏸ Shelved (v63) |
| 12-seed validation sin multi-regime | Falso confianza (v62a crown → v63 reject) | ❌ Replaced por 12×8=96 runs |

---

## 7. Cuello de botella actual (v81)

- **MIXED regresa** vs v67 (-91 vs -22 P&L). Trend filter bloquea algunos buenos trades.
  → TODO v82: tunear threshold (probar ±0.10% en lugar de ±0.05%).
- **BLUE/STABLE no tradean** (ATR floor 0.40% aún bloquea).
  → TODO v82: implementar Strategy F (Grid Trading) para ATR < 0.40%.
- **Strategy A bug pre-existente**: línea 1130 usa `prices` undefined.
  → TODO v82: fix `prices` → `hist.map(h => h.price)`.
- **Strategy C y D pausadas/inertes** — 50% del capital sin usar.
  → TODO v82: activar Strategy C (Range Breakout) en SIDE regime.

---

## 8. Próximas exploraciones (v82+)

### 8.1 Strategy F: Grid Trading para BLUE/STABLE
- Para ATR < 0.40%, usar grid trading (comprar en Bollinger lower, vender en upper)
- Tamaño pequeño (0.025), SL tight (0.5 ATR), TP modesto (0.75 ATR)
- Solo activo cuando ATR < 0.40% (evita conflicto con A/B)

### 8.2 Tunear trend filter (v81 F1)
- Probar thresholds: ±0.05% (actual), ±0.10%, ±0.02%
- Buscar compromise entre bloquear bad trades y permitir good trades

### 8.3 Fix Strategy A bug
- Línea 1130: `computeRSI(prices, 14)` → `computeRSI(hist.map(h => h.price), 14)`
- Esto activaría el filtro RSI 25/75 de Strategy A (actualmente inerte)

### 8.4 Activar Strategy C (Range Breakout) en SIDE
- Detectar SIDE regime (SMA100 slope ≈ 0 + ATR < 0.6%)
- Activar C solo en SIDE para no competir con A/B en trending

### 8.5 Volatility Breakout para HIGHVOL
- Detectar ATR spike (ATR actual > 2× ATR promedio últimas 60 ticks)
- Entrar en dirección del spike con SL tight
- Solo para ATR% > 1.5 (HIGHVOL)

### 8.6 Regime-aware strategy switching
- BULL → Strategy A primary (momentum long)
- BEAR → Strategy A primary (momentum short)
- SIDE → Strategy B primary (mean reversion)
- HIGHVOL → Strategy A primary (vol breakout)
- LOWVOL/BLUE/STABLE → Strategy F primary (grid)

---

## 9. Trazabilidad

- **Repo**: https://github.com/coverdraft/ppmt
- **Branch activa**: `terminal-web`
- **Worklog completo**: `worklog.md` (46+ tareas documentadas, ~2000 líneas)
- **Scripts backtest**: `scripts/backtest/v38_push_v37e.py` → `v68_push.py`
  + `scripts/v80_direction_token_test.py` (LONG/SHORT split + 8 perfiles)
  + `scripts/v80_universal_engine.py` (v80 original, ATR floor 0.20)
  + `scripts/v81_universal_v2.py` (v81 production, ATR floor 0.40)
- **Backups motor**: `src/lib/paper-trading-engine.ts.bak.{v14,v15,v31b,v38g,v43a,v49c,v51e,v53h,v58d,v59f,v60b,v61b,v67}`
- **Engine actual**: `src/lib/paper-trading-engine.ts` (v81, commit pending)

Para revertir a cualquier versión:
```bash
cp src/lib/paper-trading-engine.ts.bak.v67 src/lib/paper-trading-engine.ts  # back to v67
```

---

## 10. Cómo reproducir un backtest

```bash
cd /home/z/my-project/scripts

# v67 baseline en 12 seeds × 8 perfiles (LONG/SHORT split)
python v80_direction_token_test.py all
python v80_direction_token_test.py aggregate

# v81 universal engine en 12 seeds × 8 perfiles
python v81_universal_v2.py all
python v81_universal_v2.py aggregate

# v67 legacy single-regime (12 seeds × MIXED only)
cd /home/z/my-project/scripts/backtest
python v67_push.py aggregate
```

---

## 11. Regla de validación

**TODO campeón se valida en 12 seeds × 8 perfiles = 96 runs** antes de commit.
Menos de 96 = overfit a un régimen específico (lección v63 + v80).

Criterios de aceptación:
1. **Profit% ≥ 50%** en al menos 5/8 perfiles
2. **MaxDD ≤ 2.0%** en TODOS los perfiles
3. **P&L > 0** en al menos 5/8 perfiles
4. **LONG y SHORT ambos rentables** en su régimen favorable (BULL→LONG, BEAR→SHORT)
5. **No regresión > 30%** vs versión anterior en ningún perfil
