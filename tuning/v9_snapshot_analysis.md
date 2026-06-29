# PPMT Snapshot v9 — Diagnostic Report

- **Snapshot timestamp**: 2026-06-28 18:45:23 (local) / 2026-06-29 00:45:23 UTC
- **Export version**: `ppmt-export-v9`
- **Engine mode**: paper / auto / running
- **Session length**: 12.5 min
- **Selected token**: BTC/USDT (89 active)
- **Exchange**: COINBASE
- **Ticks processed**: 269,524 (~40 ticks/min)

---

## 1. Engine Health (Operational)

| Subsystem | Status | Notes |
|---|---|---|
| WebSocket | ✅ connected | `seconds_since_last_tick=1` |
| Auto mode | ✅ ON | `is_running=true` |
| Kill switch | ✅ OFF | `is_trading_allowed=true` |
| Circuit breakers | ✅ all OFF | max_drawdown / daily_loss / volatility |
| Loop health | ✅ alive | equity curve length 500, delta +0.32 |

**Lectura**: motor sane, feed sin lag, loop estable.

---

## 2. Capital & Exposure

| Métrica | Valor | Lectura |
|---|---|---|
| Cash balance | 9,415.25 USDT | 581.62 USDT comprometidos |
| Portfolio value | 9,996.87 | −3.13 vs baseline 10,000 |
| Realized PnL | +0.53 | casi plano |
| Unrealized PnL | 0 | sin exposición marcada |
| Total PnL % | −0.03 % | dentro del ruido |
| Exposure | 5.8 % | muy bajo (capital subutilizado) |
| Leverage | 1 | sin apalancamiento |
| Max DD | 0.03 % | sin estrés |
| Kelly | 0.19 → size sugerido 960.7 USDT | recomendación modesta |
| Monte Carlo verdict | PASS | ⚠️ prob_profit 45.4 % (ver §8) |

---

## 3. Estrategias — Desempeño Individual

| ID | Nombre | Trades | Win% | Realized | Unrealized | Cash/Alloc | Status |
|---|---|---|---|---|---|---|---|
| A | Momentum | **0** | — | 0 | 0 | 3,000/3,000 | ⚠️ INACTIVO |
| B | Mean Reversion | 14 | **60 %** | +2.63 | −0.23 | 2,262.54/2,500 | ✅ Única que aporta |
| C | Breakout | 10 | **30 %** | −1.25 | +0.14 | 2,253.80/2,500 | ⚠️ Underperforming |
| D | Squeeze | 2 | **0 %** | −0.85 | +0.09 | 1,898.91/2,000 | ⚠️ Muestra insuficiente |

**Síntesis**: B sostiene al motor. C y D destruyen valor. A nunca disparó → 3,000 USDT inactivos.

---

## 4. Trades Cerrados — Estadística

- 20 cierres: **8 W / 12 L → 40 % win rate**.
- **Profit Factor = 0.92** (perdiendo dinero).
- Avg win +0.68 / Avg loss −0.49 → ratio pago **1.39**.
- R:R configurado **2.5** vs ratio pago real **1.39** → los TP no se alcanzan; los SL cortan antes.
- Best +0.91 / Worst −0.98 → simetría, sin colas extremas.
- Cierres: **SL 12, TP 8**, cero trailing / time-stop / manual.
- Avg hold **13 min** → scalp puro.
- **Streaks recientes**: `L4 → W3 → L1 → W3 → L7` → cierra con **racha perdedora de 7**.

---

## 5. Patterns / Living Trie

- Buffer actual: `D U F F F U D F F F D U` → alternancia choppy.
- **Entropía 0.846** (alto, casi aleatorio).
- **Regímenes**: 41 volatile / 9 trending_down → mercado sin direccionalidad.
- Trie: 5,028 patrones, profundidad máx 6, 269,524 observaciones.
- Match score reciente: 0.151 → 0.154 (bajísimo y estable) → el histórico no reconoce bien el contexto actual.

---

## 6. Machine Learning

- Etapa **BOOTSTRAP**, `drift_detected=false`, `last_retrain_time=null`.
- Confianza clavada en **0.9 SHORT** con EV 0.27 durante toda la ventana → **sobreconfianza sistemática en SHORT**.
- Win rate trend estable en 40 % (26 trades, 11 W) → no hay aprendizaje porque no hay retrain.

---

## 7. Signals

- 20 señales en la sesión, **20/h** (alto ritmo).
- Última: `FIL/USDT SHORT, conf 0.9, EV 0.27, MEANREV_RSI88_SHORT, move 0.6 %`.
- Distribución: mayormente **SHORT** (15+) con algunos LONG puntuales.
- Tipos: MEANREV_RSI_* dominan, después BREAKOUT_*, dos SQUEEZE_*.

---

## 8. Risk Config y Monte Carlo

```
riskPerTradePct:              3
maxConcurrentPositions:       8
maxCorrelatedPositions:       3
maxDrawdownPct:               25
dailyLossLimitPct:            8
positionSizingMethod:         risk_parity
kellyFraction:                0.5
defaultLeverage:              1
maxLeverage:                  3
takeProfitMultiplier:         2.5
stopLossATR:                  1.5
trailingStopEnabled:          true
trailingStopActivationPct:    1
trailingStopDistancePct:      0.5
breakEvenEnabled:             true
breakEvenActivationPct:       0.5
```

**Monte Carlo**:
- `risk_of_ruin`: 0.0001
- `probability_of_profit`: **45.4 %** ← bajo
- `p95_dd`: **0** ← sospechoso
- `verdict`: **PASS**

⚠️ **Inconsistencia**: `prob_profit 45.4 %` + `PF 0.92` + `p95_dd=0` en sesión de 12.5 min → el verdict PASS es **falso positivo** por dataset corto. El `p95_dd=0` indica paths insuficientes o dataset demasiado chico. **No confiar en PASS hasta ≥500 trades y ≥24h de sesión**.

---

## 9. Tokens Activos con Posición

5 tokens operando: **PEPE, ZEC, FIL, ETC, RNDR** (todos SHORT o lateral, sin BTC/ETH/SOL). BTC/ETH/SOL marcados `is_trading=false` pese a tener histórico → el motor los desactivó.

---

## 10. Loop Health

Equity curve reciente oscila en **9,996.48 – 9,997.06** (rango 0.58 USDT), delta +0.32 → motor estable pero plano, sin dirección.

---

## Diagnóstico Sintetizado

1. **PF < 1 + racha de 7L** → el motor está perdiendo, no es ruido.
2. **R:R configurado 2,5 vs ratio pago real 1,39** → los TP no se alcanzan; el SL pega primero sistemáticamente.
3. **ML en BOOTSTRAP y sobreconfiado (0,9 SHORT)** → señales débiles tratadas como fuertes.
4. **Capital ocioso**: Strategy A (3,000 USDT) sin operar; sólo 5,8 % de exposición.
5. **Monte Carlo PASS con prob_profit 45 %** → falso positivo por sesión corta.

---

## Recomendaciones (aplicadas en `v9_to_v10_tuning_patch.yaml`)

1. **Ajustar TP/SL**: bajar TP de ×2.5 a **×1.8** y SL de 1.5 ATR a **1.2 ATR** → ratio pago objetivo ≥1.7.
2. **Reducir capital a Strategy C** (Breakout) de 2,500 a **1,000** hasta más datos.
3. **Forzar diagnóstico de Strategy A**: añadir log detallado de rechazo de señales Momentum.
4. **Subir umbral de confianza** de 0.65 → **0.75** mínimo; filtrar `ev_score < 0.30`.
5. **Pausar subida de exposición** hasta ML salga de BOOTSTRAP y ≥500 trades acumulados.
6. **Circuit breaker de racha**: pausar auto_mode si racha perdedora ≥ **10**.

Ver `v9_to_v10_tuning_patch.yaml` para los valores exactos antes/después y `TRACEABILITY.md` para registrar el resultado post-aplicación.
