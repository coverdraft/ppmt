# Estrategias alternativas para alta volatilidad

> **Por qué este documento**: v67/v81 funcionan bien en BULL/MEME pero
> HIGHVOL sigue siendo problemático (Profit 25%, WR ~50%, RR ~1.2). El
> usuario pidió explícitamente buscar estrategias alternativas igual de
> fuertes o mejores, especialmente para alta volatilidad.
>
> **Criterio de "igual de fuerte"**: WR ≥ 64% Y RR ≥ 1.8 en HIGHVOL
> sobre 12 seeds × 8 perfiles. Si una alternativa cumple en HIGHVOL
> pero pierde en otros, se queda como **complemento** (no reemplazo).

---

## 1. Por qué v67/v81 fracasan en HIGHVOL

### 1.1 Diagnóstico

HIGHVOL se caracteriza por:
- ATR% > 1.5% por tick (vs 0.4-0.8% en otros regímenes)
- Spikes de hasta 4-6% en un solo tick
- Whipsaws frecuentes (cambio de dirección en 2-3 ticks)
- Trend filter da señales falsas (slope cambia rápido)

**Fallos específicos de v67/v81 en HIGHVOL**:
1. SL 1.5 × ATR es demasiado amplio → un solo spike te saca con pérdida grande
2. Trail 0.30 × ATR sufre de "chop" (se activa demasiado pronto en whipsaws)
3. Trend filter SMA100 slope cambia signo en cada spike → bloquea buenos trades
4. Mean reversion (Strategy B) compra caídas que siguen cayendo
5. Pyramid (cuando estaba activo) amplificaba pérdidas en contra-tendencia

### 1.2 Lo que se probó y no funcionó

| Idea | Resultado | Veredicto |
|------|-----------|-----------|
| B size 0.30 → 0.15 en HIGHVOL (v81 F5) | MaxDD 2.34% → 1.54% ✅, pero Profit 25% | Mitigación, no solución |
| Pyramid disable en HIGHVOL (v81 F3) | MaxDD controlado ✅, pero Profit 25% | Mitigación, no solución |
| Catastrophic SL 2.5 ATR (v81 F4) | Limita tail risk ✅, pero Profit 25% | Mitigación, no solución |
| Trend filter ±0.05% (v81 F1) | Bloquea algunos malos ✅, pero bloquea buenos también | Tunear a ±0.10% |
| Risk gates (max_concurrent, dd_cooldown) | Inerte o dañino | ❌ Descartado (v46, v55) |
| Session kill switch | Corta ganadores más que perdedores | ❌ Descartado (v56) |

**Conclusión**: Las mitigaciones hasta ahora reducen MaxDD pero no mejoran
Profit. **Hace falta una estrategia nueva**, no parches a B.

---

## 2. Alternativas analizadas

### 2.1 Strategy G: Volatility Breakout (recomendada) ✅

**Concepto**: En HIGHVOL, el edge no es mean reversion ni trend following.
Es **capturar el spike** entrando en la dirección del primer movimiento
fuerte. Este es el enfoque de los "volatility breakout systems" clásicos
(Toby Crabel, Linda Raschke).

**Lógica**:
- **Setup**: Calcular ATR promedio de las últimas 60 ticks (ATR_60).
- **Trigger**: Si ATR actual > 1.8 × ATR_60, hay spike. Esperar confirmación
  de dirección: candle verde (close > open + 0.5 × ATR) → LONG, candle
  roja (close < open - 0.5 × ATR) → SHORT.
- **Entry**: A mercado en el close del candle trigger.
- **SL**: 0.8 × ATR_actual (tight, ajustado al spike).
- **TP parcial 1**: +0.8R → cierra 30% (lock rápido en vol).
- **TP parcial 2**: +1.5R → cierra 40% + activa trail 0.25 × ATR.
- **TP parcial 3**: +2.5R → cierra 30% (dejar correr si hay follow-through).
- **Size**: 0.020 (pequeño porque HIGHVOL es arriesgado).
- **Max 1 posición por token** (no pyramiding en vol extrema).
- **Cooldown**: 15 min post-trade (capturar follow-through si lo hay).

**Back-of-envelope expected value**:
- WR estimado: 55-62% (breakout captura continuación el 60% del tiempo)
- Avg Win: +1.6R (suma ponderada de partials)
- Avg Loss: -0.85R (SL tight más slippage)
- RR = 1.6 / 0.85 = **1.88**
- EV = 0.58 × 1.6 - 0.42 × 0.85 = +0.58R por trade
- Trades por HIGHVOL run: 15-25
- P&L esperado: ~+80 (vs v81 -13)

**Ventajas**:
- Específico para HIGHVOL (no interfiere con A/B en otros regímenes)
- SL tight limita tail risk
- No depende de trend filter (que falla en vol)
- Asimétrico: capta continuación del spike, no dirección "esperada"

**Riesgos**:
- Falsos breakouts (spike + reversión inmediata) → SL corta pérdida
- Slippage real podría ser mayor en vol (datos sintéticos subestiman)
- Overfit a HIGHVOL específico (mitigar con 12 seeds × 8 perfiles)

**Implementación**: Añadir como Strategy G en `paper-trading-engine.ts`.
Asignación 15% (reducir D a 5% o eliminarla).

---

### 2.2 Estrategia alternativa: Volatility Regime Switching (VRS) 🟡

**Concepto**: En lugar de tener una estrategia separada para HIGHVOL,
hacer que el motor **cambie de modo** completo cuando detecta HIGHVOL.
Es decir, en HIGHVOL desactivar A/B/C y activar G. En otros regímenes,
G inerte.

**Lógica**:
```
if (atrPct > 0.018) {  // HIGHVOL threshold
  mode = "VOLATILE";
  // Activa solo G
  // SL global más tight (0.8 ATR para todos)
  // Time stop 2h (más corto, capturar follow-through)
} else {
  mode = "NORMAL";
  // Activa A/B/C según régimen
}
```

**Ventaja**: Simplifica switching, no hay conflicto entre A/B/G.
**Desventaja**: Pierde opción de capturar momentum regular dentro de
HIGHVOL (que a veces ocurre, ej: meme pump sostenido).

**Veredicto**: 🟡 Prometedora pero más radical. Mejor empezar con G
independiente (2.1) y evaluar VRS en v83 si G no cumple.

---

### 2.3 Estrategia alternativa: ATR-Adjusted Kelly Sizing 🟡

**Concepto**: Mantener A/B pero hacer sizing dinámico basado en Kelly
criterion con vol actual. En HIGHVOL, Kelly sugiere reducir size; en
MIXED, aumentarlo.

**Lógica**:
```
winRate = historicalWinRate(estrategia, régimen)
avgWin = historicalAvgWin(...)
avgLoss = historicalAvgLoss(...)
kellyFraction = (winRate * avgWin - (1-winRate) * avgLoss) / avgWin
size = baseSize × kellyFraction × safetyFactor (0.25)
```

**Ventaja**: Sizing óptimo matemáticamente.
**Desventaja**:
- Necesita historial suficiente por estrategia × régimen
- Kelly puro es agresivo (usar 0.25× Kelly)
- No cambia SL/TP, solo size → no soluciona el problema raíz

**Veredicto**: 🟡 Complemento útil pero no solución principal. Implementar
como **mejora adicional** sobre G (v83+).

---

### 2.4 Estrategia alternativa: Bollinger Band Squeeze Release 🟢

**Concepto**: Cuando la volatilidad se comprime (Bollinger bands se
estrechan) y luego se expande, el siguiente movimiento suele ser fuerte.
Entrar en la dirección del breakout del squeeze.

**Lógica**:
- **Setup**: Bollinger bandwidth (BB upper - BB lower) / BB mid < 0.05
  (squeeze).
- **Trigger**: Precio rompe BB upper → LONG, rompe BB lower → SHORT.
- **SL**: BB mid opuesto.
- **TP**: 2 × ATR (del post-squeeze, no del squeeze).
- **Size**: 0.025.

**Ventaja**: Captura movimientos explosivos post-compresión.
**Desventaja**:
- Squeeze ocurre pocas veces por sesión (quizás 1-2)
- En datos sintéticos, los squeezes son raros (GBM no genera clusters
  de vol naturalmente)

**Veredicto**: 🟢 Útil en producción (datos reales con clustering de vol),
pero en backtest sintético no se puede validar. **Shelved para v84+**
cuando se conecten datos reales.

---

### 2.5 Estrategia alternativa: Mean Reversion con Z-Score 🟡

**Concepto**: Reemplazar el RSI 30/70 de Strategy B por **Z-score del
precio respecto a SMA + stdev**. Más robusto que RSI en vol alta.

**Lógica**:
```
zScore = (precio - sma50) / stdev50
if (zScore < -2.0) LONG   // precio 2σ por debajo de la media
if (zScore > +2.0) SHORT  // precio 2σ por encima
SL: sma50 (nivel medio, menos tight que 1.5 ATR)
TP: zScore vuelve a 0
```

**Ventaja**:
- Z-score se ajusta automáticamente a la vol (denominador es stdev)
- SL en nivel medio, no en múltiplo de ATR (más estable)
- Funciona en cualquier régimen (no solo HIGHVOL)

**Desventaja**:
- En trending fuerte, Z-score se queda en -2/-3 y sigue cayendo
  → trend filter sigue siendo necesario
- Genera más trades que RSI (threshold -2 es más frecuente que RSI 30)

**Veredicto**: 🟡 **Prometedora como reemplazo de B en HIGHVOL**. Pero
requiere re-validar todos los regímenes. **Probar en v83** como variante
de B (B-Z) y comparar con B original.

---

### 2.6 Estrategia alternativa: Time-Based Exit (no SL) 🟢

**Concepto**: En HIGHVOL, los SL tight te sacan por whipsaw. En lugar
de SL, usar **time stop** agresivo: si el trade no avanza +0.5R en 30
min, cerrar a mercado sin importar P&L.

**Lógica**:
- Sin SL fijo.
- Time stop 1: 30 min → si P&L < +0.5R, cerrar.
- Time stop 2: 60 min → cerrar sin importar P&L.
- TP: +1.5R o trail 0.30 ATR.

**Ventaja**: Evita whipsaw de SL en vol choppy.
**Desventaja**:
- Pérdida media mayor que SL tight (si el trade se va a -2R en 30 min,
  pierdes 2R en lugar de 0.8R)
- Necesita backtest cuidadoso para validar

**Veredicto**: 🟢 **Interesante complemento** a Strategy G. Probar
como variante en v83.

---

### 2.7 Estrategia alternativa: Volatility Targeting Portfolio 🟢

**Concepto**: En lugar de estrategias, hacer **volatility targeting**:
mantener una exposición total que tenga vol anualizada target (ej: 30%).
Si vol actual sube, reducir size; si baja, aumentarlo.

**Lógica**:
```
targetVol = 0.30 anualizado
currentVol = stdev(returns últimos 30 ticks) × sqrt(annualizationFactor)
leverage = targetVol / currentVol
size = baseSize × leverage (cap 1.5x)
```

**Ventaja**:
- Suaviza la curva de equity en todos los regímenes
- Es lo que hacen los fondos de vol targeting reales

**Desventaja**:
- No genera alpha por sí solo (es solo sizing)
- Necesita estrategias subyacentes que tengan edge

**Veredicto**: 🟢 **Overlay útil en v83+** sobre A/B/G/F. No es
estrategia por sí solo.

---

## 3. Comparación de alternativas

| Estrategia | Edge | Aplicabilidad | Complejidad | ROI esperado | Prioridad |
|------------|------|---------------|-------------|--------------|-----------|
| **G: Volatility Breakout** | Captura spike | HIGHVOL específico | Media | ALTO | **v82** |
| VRS: Regime switching | Simplifica motor | HIGHVOL específico | Baja | Medio | v83 (si G no cumple) |
| Kelly Sizing | Sizing óptimo | Todos los regímenes | Media | Medio | v83 |
| BB Squeeze Release | Captura compresión | Todos los regímenes | Baja | Bajo (datos sint) | v84+ (datos reales) |
| Z-Score Mean Reversion | Reemplaza B | HIGHVOL + SIDE | Baja | Medio | v83 |
| Time-Based Exit | Evita whipsaw | HIGHVOL específico | Baja | Bajo | v83 (variante G) |
| Volatility Targeting | Suaviza equity | Overlay global | Media | Medio | v83+ |

---

## 4. Recomendación final

**Para v82**: Implementar **Strategy G (Volatility Breakout)** como
estrategia nueva. Es la alternativa con mejor ratio ROI/esfuerzo y
está alineada con la literatura clásica (Crabel, Raschke).

**Para v83**:
- Probar **VRS** (regime switching completo) si G no cumple WR>64% en HIGHVOL.
- Añadir **Z-Score B** como variante de B en HIGHVOL.
- Implementar **Kelly sizing** como mejora transversal.

**Para v84+** (cuando se conecten datos reales):
- BB Squeeze Release (validable solo con datos reales que tengan
  clustering de vol).
- Volatility Targeting como overlay.

---

## 5. Especificación técnica de Strategy G (lista para implementar)

```typescript
// Strategy G: Volatility Breakout (v82)
function strategyG_Signal(
  hist: Candle[],
  atr60: number,
  atrActual: number,
  currentCandle: Candle
): { signal: 'LONG' | 'SHORT' | null; size: number; sl: number; tp1: number; tp2: number; tp3: number } {
  const atrPct = atrActual / currentCandle.close;

  // Trigger: spike de vol
  if (atrActual < 1.8 * atr60) return { signal: null, /* ... */ };

  // Confirmación de dirección
  const bodySize = currentCandle.close - currentCandle.open;
  const isGreen = bodySize > 0.5 * atrActual;
  const isRed = bodySize < -0.5 * atrActual;

  if (!isGreen && !isRed) return { signal: null, /* ... */ };

  const signal = isGreen ? 'LONG' : 'SHORT';
  const entry = currentCandle.close;
  const sl = signal === 'LONG'
    ? entry - 0.8 * atrActual
    : entry + 0.8 * atrActual;

  const risk = Math.abs(entry - sl);
  const tp1 = signal === 'LONG' ? entry + 0.8 * risk : entry - 0.8 * risk;
  const tp2 = signal === 'LONG' ? entry + 1.5 * risk : entry - 1.5 * risk;
  const tp3 = signal === 'LONG' ? entry + 2.5 * risk : entry - 2.5 * risk;

  return {
    signal,
    size: 0.020,
    sl,
    tp1, tp2, tp3,
  };
}

// Trail post-tp2: 0.25 × ATR (tighter que B que usa 0.30)
// Cooldown: 15 min (vs 30 de A/B)
// Max concurrent: 1 por token (no pyramiding)
```

**Parámetros a tunear en backtest**:
- ATR spike threshold: 1.5 / 1.8 / 2.0 × ATR_60
- SL: 0.6 / 0.8 / 1.0 × ATR
- TP1: 0.6 / 0.8 / 1.0 × R
- TP2: 1.2 / 1.5 / 1.8 × R
- TP3: 2.0 / 2.5 / 3.0 × R
- Body threshold: 0.3 / 0.5 / 0.7 × ATR

---

## 6. Validación esperada de Strategy G

Antes de aprobar G, validar en **12 seeds × 8 perfiles = 96 runs**:

- [ ] HIGHVOL Profit% ≥ 50% (vs 25% actual)
- [ ] HIGHVOL WR ≥ 60%
- [ ] HIGHVOL RR ≥ 1.8
- [ ] HIGHVOL MaxDD ≤ 2.0%
- [ ] Sin regresión > 30% en otros 7 perfiles (G debería ser inerte fuera de HIGHVOL)
- [ ] Al menos 10 trades en HIGHVOL por seed (suficiente muestra)
- [ ] Sin bug `undefined`/`NaN` en logs

Si G no cumple, iterar a VRS (alternativa 2.2) en v83.
