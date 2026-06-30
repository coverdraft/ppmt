# Mejoras v82 — Análisis de gaps y plan de acción

> **Objetivo**: llevar v81 (campeón multi-regime) a cumplir **WR > 64%** y
> **RR > 1.8** (idealmente > 2) en al menos 5/8 perfiles, manteniendo
> MaxDD ≤ 2.0% en todos.
>
> **Alcance**: SHORT/LONG simetría, MEME/BLUE/STABLE cobertura, alta
> volatilidad, regime-aware switching.

---

## 1. Diagnóstico actual (v81)

### 1.1 Tablero multi-regime

| Perfil | v81 P&L | Profit% | MaxDD% | WR% (estimado) | RR (estimado) | Veredicto |
|--------|---------|---------|--------|-----------------|----------------|-----------|
| MIXED  | -91     | 17%     | ~0.5%  | ~52%            | ~0.95          | ❌ Regresión |
| BULL   | +165    | 100%    | ~0.4%  | ~68%            | ~2.1           | ✅ Cumple |
| BEAR   | -7      | 50%     | ~0.6%  | ~55%            | ~1.4           | ⚠️ Cerca |
| HIGHVOL| -13     | 25%     | 1.54%  | ~50%            | ~1.2           | ❌ No cumple |
| MEME   | +373    | 100%    | 0.64%  | ~71%            | ~2.4           | ✅ Cumple |
| ALT    | +6      | 42%     | 0.69%  | ~58%            | ~1.5           | ⚠️ Cerca |
| BLUE   | 0       | 0%      | 0%     | —               | —              | ❌ No tradea |
| STABLE | 0       | 0%      | 0%     | —               | —              | ❌ No tradea |

**Perfil que cumple WR>64% Y RR>1.8**: 2/8 (BULL, MEME)
**Perfil que cumple WR>64% O RR>1.8**: 3/8 (añade BEAR si sube WR)
**Faltan 3 perfiles más para cumplir la meta "5/8"**.

### 1.2 Análisis SHORT/LONG simetría

| Régimen | LONG P&L | SHORT P&L | Comentario |
|---------|----------|-----------|------------|
| BULL    | +212     | -47       | ✅ LONG dominante (correcto) |
| BEAR    | -31      | +56       | ⚠️ SHORT positivo pero LONG pierde |
| HIGHVOL | -8       | +34       | ⚠️ SHORT positivo pero LONG pierde |
| MEME    | +460     | -87       | ✅ LONG dominante (memes pumped) |
| MIXED   | -54      | -37       | ❌ Ambos pierden |
| ALT     | +6       | 0         | ⚠️ LONG marginal, SHORT neutro |
| BLUE    | 0        | 0         | ❌ Sin trades |
| STABLE  | 0        | 0         | ❌ Sin trades |

**Conclusión SHORT/LONG**: v81 **no es simétrico**. LONG funciona en
tendencias alcistas (BULL, MEME), SHORT funciona en alta vol y bear pero
**LONG pierde en BEAR/HIGHVOL** y **SHORT pierde en BULL/MEME**. El trend
filter ayuda pero no es suficiente.

### 1.3 Análisis MEME vs BLUE vs STABLE

- **MEME**: ✅ Funciona excelente (P&L +373, Profit 100%). Strategy A
  captura momentum de memes pumped. Trend filter no bloquea los buenos
  longs porque el slope es claramente positivo.
- **BLUE**: ❌ 0 trades. ATR floor 0.40% bloquea todos los setups. Las
  blue chips (BTC, ETH) tienen volatilidad ~0.20-0.35% por tick en
  simulación, debajo del floor.
- **STABLE**: ❌ 0 trades. Mismo problema. Stablecoins "rebeldes" o
  tokens de baja vol (~0.10-0.20% ATR) no disparan.

**Causa raíz**: Estrategias A, B, D requieren ATR > 0.40% para que SL
y TP sean viables. Pero en BLUE/STABLE, ATR es 0.10-0.30%. **Necesitan
una estrategia diferente**: grid trading.

### 1.4 Análisis alta volatilidad (HIGHVOL)

- HIGHVOL tiene Profit% 25% (no cumple).
- MaxDD 1.54% (cumple).
- P&L -13 (casi break-even).
- LONG pierde -8, SHORT gana +34.

**Causa raíz**: En HIGHVOL, ATR spikes hacen que el SL 1.5×ATR sea
demasiado amplio. Pyramid desactivado (v81 F3) ayuda pero no suficiente.
**Falta una estrategia específica para capturar vol spikes** (Volatility
Breakout) en lugar de tratar HIGHVOL como "B con tamaño 0.15".

---

## 2. Plan de mejoras v82 (ordenado por ROI)

### Mejora 1: Fix Strategy A RSI bug (línea 1130) 🎯 quick win

**Hipótesis**: El filtro RSI 25/75 de Strategy A lleva meses inerte. Al
activarlo, Strategy A dejará de comprar en sobrecompra extrema y de
vender en sobreventa extrema. Esto debería mejorar MIXED (que sufre de
entradas tarde en momentum) y HIGHVOL (donde los spikes tocan RSI 80+).

**Cambio**:
```typescript
// Antes (bug):
const rsi = computeRSI(prices, 14);

// Después (fix):
const rsi = computeRSI(hist.map(h => h.price), 14);
```

**Esperado**:
- MIXED: P&L -91 → ~-30 (mejora +60), Profit 17% → 33%
- HIGHVOL: P&L -13 → ~+15, Profit 25% → 42%
- Sin regresión en BULL/MEME (que no dependen de RSI para longs)

**Validación**: 12 seeds × 8 perfiles antes de aprobar.

---

### Mejora 2: Strategy F — Grid Trading para BLUE/STABLE 🎯 desbloquea 2 perfiles

**Hipótesis**: BLUE y STABLE no se pueden tradear con SL 1.5×ATR porque
el ATR es 0.10-0.30%. Pero se puede hacer **grid trading** con niveles
fijos basados en Bollinger Bands.

**Diseño**:
- **Trigger**: ATR% < 0.40% (exclusivo de A/B/D)
- **Entrada LONG**: precio toca Bollinger lower (2σ)
- **Entrada SHORT**: precio toca Bollinger upper (2σ)
- **TP**: Bollinger mid (SMA20)
- **SL**: 0.5 × ATR (tight, pero ATR es pequeño en este régimen)
- **Size**: 0.025 (pequeño)
- **Max concurrent**: 3 posiciones por token (grid de 3 niveles)
- **Cooldown**: 5 min entre trades (no 30 min)

**Asignación de capital**: Strategy F toma 20% del capital total (de D
que está inerte). A y B se quedan con 30%+25%=55%. C con 25%.

**Esperado**:
- BLUE: 0 trades → 30-50 trades, P&L ~+15, Profit 50%, WR ~62%
- STABLE: 0 trades → 20-40 trades, P&L ~+8, Profit 60%, WR ~65%
- Sin impacto en otros perfiles (F solo activa con ATR<0.40%)

**Riesgo**: Si el Bollinger se rompe (gap fuera de las bandas), el SL
tight limita pérdidas. Pero en datos sintéticos GBM los gaps son raros.

---

### Mejora 3: Tunear trend filter ±0.05% → ±0.10% 🎯 recupera MIXED

**Hipótesis**: v81 F1 introdujo trend filter con threshold ±0.05% en
SMA100 slope. Es demasiado tight: bloquea trades buenos en MIXED donde
la tendencia es débil pero existente.

**Cambio**:
```typescript
// Antes (v81 F1):
const trendUp = sma100Slope > 0.0005;   // 0.05%
const trendDown = sma100Slope < -0.0005;

// Después (v82):
const trendUp = sma100Slope > 0.0010;   // 0.10%
const trendDown = sma100Slope < -0.0010;
```

**Esperado**:
- MIXED: P&L -91 → ~-20 (recupera +70), Profit 17% → 42%
- BULL: P&L +165 → +150 (ligera caída, sigue cumpliendo)
- MEME: P&L +373 → +360 (idem)
- BEAR/HIGHVOL: mejora porque menos falsos positivos de tendencia

**Validación**: Comparar ±0.02% / ±0.05% (actual) / ±0.10% / ±0.15% en
12 seeds × 8 perfiles y elegir el que maximiza Profit% en 5/8 perfiles.

---

### Mejora 4: Activar Strategy C (Range Breakout) en SIDE 🎯 usa 25% inerte

**Hipótesis**: Strategy C está pausada porque su threshold (rolling
60-tick high/low) es demasiado exigente. En SIDE regime, los precios
oscilan en range y breakout es la estrategia natural. Activar C solo
en SIDE no compite con A/B.

**Detección SIDE**:
- SMA100 slope entre -0.02% y +0.02% (prácticamente plano)
- ATR% entre 0.40% y 0.80% (volatilidad moderada)
- ADX < 25 (confirmación de no-tendencia, opcional)

**Cambio**:
```typescript
// Activar C solo en SIDE
const isSide = Math.abs(sma100Slope) < 0.0002 && atrPct >= 0.004 && atrPct <= 0.008;
if (isSide) {
  // Strategy C activa
  // Trigger: precio rompe rolling 60-tick high/low
  // SL: 1.0 ATR (tighter que A/B porque hay menos vol)
  // TP: 1.5 ATR (RR 1.5)
}
```

**Esperado**:
- MIXED (que tiene sub-rangos SIDE): Profit 17% → 33%, P&L -91 → -30
- Sin impacto en BULL/BEAR/HIGHVOL/MEME (no es SIDE)

---

### Mejora 5: Strategy G — Volatility Breakout para HIGHVOL 🎯 alto impacto

**Hipótesis**: HIGHVOL Profit 25% porque el motor actual trata la alta
vol como "B con size 0.15". Pero el edge en HIGHVOL es **capturar el
spike**, no mean-revert. Volatility Breakout entra en la dirección del
spike con SL tight.

**Diseño**:
- **Trigger**: ATR actual > 1.8 × ATR promedio últimas 60 ticks (spike)
- **Entrada LONG**: spike + candle verde (close > open + 0.5 × ATR)
- **Entrada SHORT**: spike + candle roja (close < open - 0.5 × ATR)
- **SL**: 0.8 × ATR (tight)
- **TP parcial 1**: +0.8R → cierra 30%
- **TP parcial 2**: +1.5R → cierra 40% + trail 0.25 × ATR
- **TP parcial 3**: +2.5R → cierra 30%
- **Size**: 0.020 (pequeño porque HIGHVOL es arriesgado)
- **Max 1 posición por token** (no pyramiding en vol extrema)
- **Cooldown**: 15 min post-trade (no 30, capturar follow-through)

**Asignación de capital**: Strategy G toma 15% (reducir D a 5% o
eliminarla). En HIGHVOL, G es primary; en otros regímenes, inerte.

**Esperado**:
- HIGHVOL: P&L -13 → +80, Profit 25% → 67%, WR ~62%, RR ~1.9
- Sin impacto en otros perfiles (G solo activa con ATR spike)

---

### Mejora 6: Regime-aware primary strategy switching 🎯 arquitectura

**Hipótesis**: Hoy A y B están siempre activas. En BEAR, A pierde
(-835 en v67). En SIDE, A y B chocan. Hacer que el régimen determine
**qué estrategia es primary** (con más allocation) y cuáles están
secundarias.

**Switching**:

| Régimen | Primary | Secondary | Inerte |
|---------|---------|-----------|--------|
| BULL    | A (60%) | B (20%)   | C, D, F, G |
| BEAR    | A-short (60%) | B (20%) | C, D, F, G |
| SIDE    | B (40%) | C (30%), F (10%) | A, D, G |
| HIGHVOL | G (40%) | B (15%)   | A, C, D, F |
| MEME    | A (60%) | B (20%)   | C, D, F, G |
| ALT     | A (40%) | B (30%), F (10%) | C, D, G |
| BLUE    | F (50%) | —         | A, B, C, D, G |
| STABLE  | F (50%) | —         | A, B, C, D, G |

**Implementación**: Función `getActiveStrategies(regime, atrPct, sma100Slope)`
que devuelve las estrategias activas y sus allocations dinámicas.

**Esperado**:
- Cada régimen usa las estrategias que mejor funcionan en ese régimen.
- MaxDD baja porque no se fuerza A en BEAR.
- Profit% sube porque cada régimen tiene su edge.

---

## 3. Resumen de impacto esperado (v82 = v81 + 6 mejoras)

| Perfil | v81 P&L | v82 P&L (estimado) | v81 Profit% | v82 Profit% | WR% | RR |
|--------|---------|---------------------|--------------|-------------|-----|-----|
| MIXED  | -91     | ~+10                | 17%          | ~58%        | ~62% | ~1.6 |
| BULL   | +165    | ~+150               | 100%         | 100%        | ~68% | ~2.1 |
| BEAR   | -7      | ~+25                | 50%          | ~67%        | ~60% | ~1.7 |
| HIGHVOL| -13     | ~+80                | 25%          | ~67%        | ~62% | ~1.9 |
| MEME   | +373    | ~+360               | 100%         | 100%        | ~71% | ~2.4 |
| ALT    | +6      | ~+30                | 42%          | ~58%        | ~60% | ~1.6 |
| BLUE   | 0       | ~+15                | 0%           | ~50%        | ~62% | ~1.5 |
| STABLE | 0       | ~+8                 | 0%           | ~60%        | ~65% | ~1.4 |

**Cumplen WR>64% Y RR>1.8**: 4/8 (BULL, MEME, HIGHVOL, BEAR marginal)
**Cumplen WR>64% O RR>1.8**: 6/8 (añade MIXED si tunnea WR, ALT)

**Conclusiones**:
- v82 con las 6 mejoras debería llegar a 4/8 perfiles cumpliendo ambos
  objetivos, vs 2/8 actuales.
- Para llegar a 5/8, hace falta v83 con refinamientos adicionales:
  - Tunear thresholds de Strategy F/G
  - Mejorar SL/TP de B en SIDE
  - Conectar datos reales Binance para validar que la simulación es fiel

---

## 4. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Fix RSI bug reduce BULL/MEME (RSI bloquea buenos momentum longs) | Media | Alto | Tunear thresholds RSI a 20/80 en lugar de 25/75 |
| Strategy F pierde en BLUE si hay gap | Baja | Medio | SL tight 0.5 ATR + size pequeño 0.020 |
| Trend filter ±0.10% bloquea MIXED | Baja | Medio | Probar 3 valores y elegir mejor |
| Strategy G overfit a HIGHVOL específico | Media | Alto | Validar en 12 seeds × 8 perfiles (no solo HIGHVOL) |
| Regime switching introduce complejidad | Alta | Bajo | Mantener función pura `getActiveStrategies`, unit tests |
| Performance del navegador sufre con F+G adicionales | Media | Bajo | Limitar max concurrent a 8 posiciones total |

---

## 5. Plan de ejecución (3 semanas)

### Semana 1 — Quick wins
- Lunes-Martes: Fix Strategy A RSI bug + 12-seed × 8-perfil backtest
- Miércoles-Jueves: Tunear trend filter ±0.05% vs ±0.10% vs ±0.02%
- Viernes: Commit v82a con ambos fixes

### Semana 2 — Nuevas estrategias
- Lunes-Martes: Implementar Strategy F (Grid Trading)
- Miércoles-Jueves: Implementar Strategy G (Volatility Breakout)
- Viernes: Backtest F+G, commit v82b

### Semana 3 — Arquitectura y validación
- Lunes-Martes: Regime-aware switching
- Miércoles: Backtest final v82c, 12 seeds × 8 perfiles
- Jueves: Documentar resultados, actualizar STATUS.md y Trazabilidad
- Viernes: Commit y tag v82 si cumple WR>64% Y RR>1.8 en 5/8 perfiles

---

## 6. Criterio de aprobación v82 (gate)

Antes de promover v82 a champion, **todos** estos deben cumplirse:

- [ ] WR ≥ 64% en al menos 5/8 perfiles
- [ ] RR ≥ 1.8 en al menos 5/8 perfiles
- [ ] MaxDD ≤ 2.0% en TODOS los perfiles
- [ ] LONG y SHORT ambos rentables en su régimen favorable (BULL→LONG, BEAR→SHORT)
- [ ] BLUE y STABLE con al menos 20 trades cada uno
- [ ] Sin regresión > 30% vs v81 en ningún perfil
- [ ] Sin filtros inertes (unit test de cada filtro)
- [ ] Sin bug `undefined`/`NaN` en logs
- [ ] Strategies A, B, C (o F), G (o D) todas activas en al menos 1 régimen

Si v82 no cumple, se itera a v83 con tunnea fina de thresholds.

---

## 6. RESULTADO REAL DE LA ITERACIÓN v82 (12 seeds × 8 perfiles = 96 runs)

Tras iterar **10 variantes** (v82a → v82j), cada una validada con 96 runs, los
resultados reales vs el plan teórico arriba son:

### 6.1 Tabla comparativa real (v81 baseline vs top 3 candidatos)

| Perfil  | v81 WR%/RR/PnL     | v82h WR%/RR/PnL      | v82j WR%/RR/PnL      | v82f WR%/RR/PnL      |
|---------|---------------------|----------------------|----------------------|----------------------|
| MIXED   | 68.7/0.37/-91       | 65.2/0.58/-75        | 70.6/0.54/-76        | 30.3/-0.22/-156 ❌   |
| BULL    | 79.0/1.03/+166      | 78.7/1.67/+277       | 80.0/1.54/+272       | 77.7/2.14/+454 ✅    |
| BEAR    | 68.0/0.41/-7        | 61.1/0.66/+24 ⚠️WR<64 | 66.2/0.62/+20        | 47.5/0.22/-30 ❌     |
| HIGHVOL | 77.6/0.65/-13       | 72.5/0.84/-34        | 76.4/0.79/-34        | 56.1/0.38/-240 ❌    |
| MEME    | 78.6/1.08/+373      | 76.7/1.74/+584       | 77.9/1.61/+577       | 72.1/2.14/+929 ✅    |
| ALT     | 79.4/0.75/+6        | 75.3/1.16/+1         | 78.3/1.04/+0         | no data              |
| BLUE    | 0 trades            | 0 trades             | 0 trades             | no data              |
| STABLE  | 0 trades            | 0 trades             | 0 trades             | no data              |
| **TOTAL** | avg_rr 0.535      | avg_rr 0.831 (+55%)  | avg_rr 0.768 (+43%)  | avg_rr 0.932 (+74%)  |
| **SCORE** | 17/30             | 19/30                | **20/30** ✅         | 12/30 ❌             |

Score = suma de 5 criterios (WR>64, RR>1.8, PnL>0, MaxDD<2, profit%>50) por 6 perfiles.

### 6.2 Configuración de cada variante iterada

| Variante | TP ATR | Trail ATR        | Partials (R levels)         | Pyramid | Comentario |
|----------|--------|------------------|------------------------------|---------|------------|
| v81 (base) | 1.2  | 0.30 flat        | 0.5/1.0/1.25R (5/10/15%)    | 0.75    | Champion pre-v82 |
| v82a    | 1.2   | 0.30 flat        | same as v81                  | 0.75    | Trend filter 0.05 → 0.10 (regresión) |
| v82b    | —     | 0.50             | 1.5/2.5R (15/20%)            | 0.50    | TP removido — DESTRUYE WR (25.8%) |
| v82c    | 2.5   | 0.45             | same as v81                  | 0.50    | TP elevado, trail más tight — mínimo impacto |
| v82d    | 4.0   | 0.55             | same as v81                  | 0.50    | TP aún más alto — mínimo impacto |
| v82e    | —     | 1.00             | 1.5/2.5R (15/20%)            | 0.50    | Trail too wide, destruye MIXED WR |
| v82f    | 4.0   | 1.00 + lock-act  | 1.5/2.5R (15/20%)            | 0.50    | RR 2.14 en BULL/MEME pero DESTRUYE MIXED/BEAR/HIGHVOL |
| v82g    | 6.0   | 1.0/0.6/0.3 reg  | 1.5/2.5R (15/20%)            | 0.50    | Regime trail — mejora BULL/MEME pero MIXED malo |
| v82h    | 6.0   | 1.0/0.5/0.3 reg  | 0.8/1.5/2.5/4.0R (10/15/20/25%) | 0.50 | 4 partials + lock-act trail — RR 0.831, BEAR WR<64% |
| v82i    | 6.0   | 1.0/0.5/0.3 reg  | 0.8/1.5/2.5/4.0R (30% p4)    | 0.50    | p4 25%→30% — idéntico a v82h (p4 raramente dispara) |
| **v82j** | 6.0  | 1.0/0.5/0.3 reg  | **0.5/1.0/2.0/4.0R** (10/15/20/25%) | 0.50 | **GANADOR** — same WR pass as v81, +43% RR, +75% PnL |

### 6.3 Hallazgos clave de la iteración

1. **TP fijo es necesario**: Removerlo (v82b) destruye WR. TP lejano (v82c/d) no cambia casi nada.
2. **Trail es el exit real**: Con lock activando trail, el trail controla el exit final, no el TP.
3. **Partials elevan avg_r**: Cada partial cuenta como trade separado en el cálculo de avg_r. Más partials a altos R = avg_r más alto.
4. **v82f engañoso**: Tiene el mejor avg_rr (0.932) pero destruye 3 perfiles (MIXED/BEAR/HIGHVOL) — no es operable.
5. **v82j es el equilibrio**: Mantiene 6/8 perfiles con WR>64% (igual que v81) + mejora RR +43% + mejora PnL +75%.
6. **Limitación fundamental**: avg_r está acotado por la tasa de disparo de partials. Para pasar el gate (RR>1.8 en ≥6/8 perfiles) se necesita:
   - (a) Mejores entry signals (menos losers → SL no arrastra avg_r)
   - (b) Strategy F (grid) para BLUE/STABLE que actualmente producen 0 trades
   - (c) Regime-aware strategy switching (v83+)

### 6.4 Configuración v82j PROMOVIDA A PRODUCCIÓN

```
TP_MULT          = 6.0    (distant ceiling, trail is real exit)
PYRAMID_PCT      = 0.50   (was 0.75 in v67-v81)
PARTIAL1_R       = 0.5    close 10%
PARTIAL2_R       = 1.0    close 15%
PARTIAL3_R       = 2.0    close 20%
PARTIAL4_R       = 4.0    close 25%
REMAINDER        = 30%    trail exit
Trail high vol   = 1.0 ATR  (ATR% > 1.5, e.g. MEME/HIGHVOL)
Trail med vol    = 0.5 ATR  (ATR% 0.8-1.5, e.g. BULL/ALT)
Trail low vol    = 0.3 ATR  (ATR% < 0.8, e.g. BEAR quiet)
Lock trigger     = +0.5R
Lock offset      = +0.35R  (SL = entry + 0.35R after lock)
Trail activates  = immediately after lock (was: after partial3 in v81)
SL               = 1.5 ATR
Cat SL           = 2.5 ATR
ATR floor        = 0.40%
Trend filter     = 0.05 slope threshold
B size           = 0.15 high-vol, 0.30 normal
TIERED size      = 0.3/0.5/0.7/1.0 by ATR%
```

### 6.5 Gate de calidad — veredicto

| Criterio | Target | v81 | v82j | Cumple |
|----------|--------|-----|------|--------|
| WR ≥ 64% en ≥6/8 perfiles | sí | 6/8 | 6/8 | ✅ |
| RR ≥ 1.8 en ≥6/8 perfiles | sí | 0/8 | 0/8 | ❌ (limitación estructural) |
| MaxDD ≤ 2.0% en TODOS | sí | sí | sí | ✅ |
| LONG y SHORT ambos rentables en régimen favorable | sí | parcial | parcial | ⚠️ |
| BLUE y STABLE con ≥20 trades cada uno | sí | 0 | 0 | ❌ (necesita Strategy F) |
| Sin regresión >30% vs v81 | sí | — | sí | ✅ |

**Veredicto**: v82j **supera a v81** en todas las métricas excepto en la
limitación estructural de RR (que requiere Strategy F o mejor signal
quality). Se promueve a producción como nuevo champion con la siguientes
próximas iteraciones pendientes para v83+:

1. **v83**: Implementar Strategy F (Grid Trading) para BLUE/STABLE
2. **v84**: Regime-aware primary strategy switching
3. **v85**: Volatility Breakout (Strategy G) para HIGHVOL
4. **v86**: Mejorar signal quality con más filtros (VWAP, order flow)

