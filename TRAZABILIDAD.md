# Trazabilidad PPMT — v11 → v81

> Documento de lecciones aprendidas. Qué se hizo **bien**, qué se hizo **mal**,
> y por dónde seguir. Útil para no repetir errores y para onboarding rápido.
>
> Última actualización: v81 (Jun 30 2026)

---

## 1. Resumen ejecutivo

PPMT es un motor de paper trading (Next.js + Zustand, 100% browser) que prueba
4 estrategias en paralelo sobre 10 tokens sintéticos (GBM + regime switching)
durante ventanas de 4–6 h. La meta es **WR > 64% y RR > 1.8 (idealmente > 2)**
validado en **12 seeds × 8 perfiles = 96 runs** antes de producción.

**Estado actual**: v81 es el primer campeón que sobrevive multi-regime (5/8
perfiles rentables), pero **no cumple los objetivos de WR/RR** todavía porque
la validación se mueve en una dirección nueva (universalidad) y no en optimizar
el WR del régimen MIXED en aislado.

**Conclusión clave**: v67 (campeón previo) era **un falso campeón**. Ganaba en
MIXED con WR 79.6% y PF 3.01, pero perdía -295 en BULL y -206 en MEME. La
validación 12-seed-sin-multi-regime generó confianza falsa. v81 arregla esto
con un trend filter + 5 fixes estructurales.

---

## 2. Lo que se hizo BIEN (aciertos)

### 2.1 Arquitectura y disciplina de proceso

- **Backups de cada versión** (`paper-trading-engine.ts.bak.{v14,...,v67}`):
  Permiten revertir a cualquier punto sin git archaeology. Invaluable cuando
  un cambio "obvio" rompe algo.
- **Validación 12 seeds** (`[42, 1337, 31337, 7, 99, 1234, 7777, 2025, 314, 555, 888, 2024]`):
  Menos de 12 seeds = overfit. Lección cobrada cara en v44.
- **Worklog persistente** (`worklog.md`, 46+ tareas, ~2000 líneas):
  Cada cambio queda documentado con hipótesis → ejecución → resultado.
- **STATUS.md como dashboard vivo**: Una sola fuente de verdad para ver qué
  versión es champion, qué probó cada versión, qué descartó.

### 2.2 Decisiones técnicas acertadas

| Decisión | Por qué funcionó | Versión |
|----------|------------------|---------|
| SL = 1.5 × ATR (no fijo) | Se adapta a la volatilidad del token | v51e |
| Lock (BE) a +0.5R → SL a entry+0.35R | Bloquea ganadores sin cortarlos demasiado pronto | v51e |
| Trail = 0.30 × ATR (no R-based) | Trail fijo probado mejor que dinámico | v49c |
| 3 partials @0.5R/1.0R/1.25R | Distribución óptima entre lock y ride | v53h |
| Pyramid B +75% @+1.0R | Solo pyramidear en ganadores confirmados | v62a |
| TIERED size 0.3/0.5/0.7/1.0 by ATR | Más tamaño en baja vol, menos en alta | v81 F6 |
| Trend filter slope-based (no level) | SMA100 level cortaba SIDE, slope no | v81 F1 |
| ATR floor 0.40% (compromise) | 0.65 mataba MIXED, 0.20 mataba BLUE | v81 F2 |
| Catastrophic SL 2.5 × ATR (no 4.0) | Cap tighter en HIGHVOL | v81 F4 |
| Pyramid disable en HIGHVOL | Evita amplificar pérdidas en vol extrema | v81 F3 |

### 2.3 Disciplina de "no-overfit"

- v63 intentó Equity Curve Protection (ECP) → Profit% cayó 67→58 y Sharpe
  9.82→2-5. **Se descartó con evidencia**, no por intuición.
- v67 siguió el camino "más parámetros más fino" → WR 79.6% en MIXED pero
  fracaso multi-regime. **Se descartó como champion** aunque tuviera mejor WR.
- Cada hipótesis se prueba en **al menos 3 valores** (ej: SL 1.45/1.5/1.6)
  antes de declarar winner.

---

## 3. Lo que se hizo MAL (errores caros)

### 3.1 Overfit a MIXED (el error más caro del proyecto)

**Qué pasó**: v38–v67 se optimizaron solo en régimen MIXED. Todos los
backtests mostraban WR creciente y MaxDD bajo. Se proclamó campeón a v62a y
luego v67 con PF 3.01.

**Error real**: MIXED es el régimen "easy mode" (GBM con volatilidad moderada
y sin tendencia fuerte). Optimizar solo ahí generó un motor que **no sabía
qué hacer en BULL/BEAR/MEME**.

**Evidencia del fracaso**: cuando se corrió v80_direction_token_test.py
(12 seeds × 8 perfiles), v67 mostró:
- BULL: P&L -295, Profit% 0% (perdedor 100%)
- MEME: P&L -206, Profit% 17% (perdedor 83%)
- BLUE/STABLE: 0 trades (ATR floor bloqueaba entrada)

**Lección cobrada**: ~25 versiones (v38–v67) gastadas optimizando un caso
de uso que no es representativo del mundo real. **Siempre validar multi-regime
desde el día 1.**

### 3.2 Bug de Strategy A (filtro RSI inerte desde hace ~30 versiones)

**Qué pasó**: En `paper-trading-engine.ts:1130`, la llamada
`computeRSI(prices, 14)` usa `prices` que **no existe en ese scope**. El
filtro RSI 25/75 de Strategy A (momentum) lleva meses inerte. Los trades de
A se ejecutan solo por `|chg|` top movers, sin filtro de sobrecompra/sobreventa.

**Por qué no se detectó**: Porque MIXED es indulgente con momentum puro. El
RSI no estaba bloqueando trades buenos en MIXED, así que el backtest seguía
dando verde. En HIGHVOL/BULL/MEME donde sí importaría filtrar extremos, el
motor compraba sin freno.

**Lección**: **Los filtros "inertes" son peores que no tenerlos** porque dan
falsa sensación de control. Hay que agregar unit tests que verifiquen que
cada filtro efectivamente bloquea señales.

**Fix**: `computeRSI(hist.map(h => h.price), 14)` (TODO v82).

### 3.3 Strategies C y D inertes (50% del capital sin trabajar)

**Qué pasó**: Strategy C (Range Breakout) y Strategy D (Vol Squeeze) están
configuradas con allocation 25%+20%=45% del capital pero **nunca disparan**
en backtest. Los thresholds de rolling 60-tick high/low y Bollinger squeeze
son demasiado exigentes para los datos sintéticos.

**Por qué se mantuvieron**: Porque "no perdían dinero" (inerte = 0 P&L).
Parecía inofensivo. Pero el capital asignado a ellas **no está generando
retorno**. El motor opera de facto solo con A+B (55% del capital).

**Lección**: **Una estrategia inerte no es neutral, es dañina**: consume
allocation que podría ir a estrategias activas. Flag de "estrategia activa
en los últimos N ticks" obligatorio.

### 3.4 Equity Curve Protection (v63) — sobreingeniería

**Qué pasó**: v63 introdujo ECP (pausar trading si equity cae >X% en ventana
móvil). En teoría sonaba bien. En práctica:
- Profit%: 67 → 58 (cayó)
- Sharpe: 9.82 → 2-5 (se desplomó)
- Cortaba ganadores más que perdedores porque las ventanas móviles eran
  ruidosas en datos sintéticos.

**Lección**: **No añadir lógica de control sin validar que mejora el objetivo
principal**. ECP suena bien en paper pero el "trailing stop del equity" es
notoriamente ruidoso en series cortas (4 h).

### 3.5 ATR floor binario (0.20 vs 0.40 vs 0.65)

**Qué pasó**:
- v80 probó ATR floor 0.20% → MIXED regresa a -143, BLUE/STABLE pierden
- v81 bajó a 0.40% → MIXED OK, MEME 100% profit, pero BLUE/STABLE siguen sin tradear
- v67 usaba 0.58% → bloqueaba MEME

**Lección**: Un ATR floor **binario** no es la respuesta. Lo correcto es
**tier-aware**: Strategies A/B/D requieren ATR > 0.40%, pero Strategy F
(grid) debería poder operar con ATR < 0.40%. El floor actual mata BLUE/STABLE.

### 3.6 Strategy E (RSI 5/95 scalp) — pitch inútil

**Qué pasó**: v63 propuso un scalp con RSI 5/95. En datos sintéticos GBM, RSI
casi nunca toca 5 ni 95. La estrategia jamás dispara.

**Lección**: **Validar la distribución del signal antes de construir la
estrategia**. Para cada nuevo filtro, primero correr un histograma del
indicator sobre los datos sintéticos y verificar que el threshold es
alcanzable con frecuencia razonable (>5% del tiempo).

---

## 4. Decisiones que se revertirán pronto (deudas técnicas)

| Deuda | Origen | Plan | Versión |
|-------|--------|------|---------|
| Strategy A RSI bug | v38 (silencioso desde entonces) | Fix `prices` → `hist.map(h=>h.price)` | v82 |
| Strategies C/D inertes | v38 | Activar C en SIDE, reemplazar D | v82 |
| ATR floor binario | v81 | Strategy F (grid) para ATR<0.40% | v82 |
| Trend filter ±0.05% muy tight | v81 F1 (MIXED regresa) | Probar ±0.10% / ±0.02% | v82 |
| Sin validación de tokens reales | siempre | Conectar Binance API en sandbox | v83 |
| Sin slippage realista (0.05% fijo) | siempre | Modelar slippage como función de size + depth | v83 |

---

## 5. Cronología de versiones (cambios significativos)

```
v11 → v37e  Foundation: 4 estrategias, 10 tokens, 12 seeds, GBM+regime.
v38g        Baseline pre-v43a. WR 61.8%, PF 1.46.
v43a        Multi-partial TP. WR 72.5%, PF 1.53.
v49c        Momentum 0.55 + trail 0.30. WR 73.1%, PF 1.75.
v51e        SL 1.5 ATR + lock_offset 0.35. WR 75.3%, PF 1.90.
v53h        3-partial TP @1.25R + B 0.125. WR 79.4%, PF 2.04.
v56d        Adaptive ATR sizing. MaxDD 0.17% (mínimo histórico).
v60b        Size refinements + tiered. P&L +42.89.
v61b        PYRAMID B +50% @+1.0R. P&L +46.02.
v62a        PYRAMID B +75% @+1.0R. P&L +48.56, PF 2.72. Campeón aparente.
v63         ECP framework + 12-seed validation. RECHAZADO (Profit 67→58).
v67         A 0.040 + B 0.30 (Kill Loser + Push B). WR 79.6%, PF 3.01.
            RECHAZADO multi-regime (BULL -295, MEME -206).
v80         Universal engine (ATR floor 0.20). MIXED regresa -143.
v81         Universal engine v2 (5 fixes). 5/8 perfiles rentables. 🏆
```

---

## 6. Cómo no repetir los errores (checklist para nuevos cambios)

Antes de aprobar cualquier nueva versión como champion:

- [ ] Validada en **12 seeds × 8 perfiles = 96 runs** (no solo MIXED).
- [ ] Profit% ≥ 50% en al menos 5/8 perfiles.
- [ ] MaxDD ≤ 2.0% en TODOS los perfiles.
- [ ] P&L > 0 en al menos 5/8 perfiles.
- [ ] LONG y SHORT ambos rentables en su régimen favorable.
- [ ] Sin regresión > 30% vs versión anterior en ningún perfil.
- [ ] Estrategias A, B, C, D (o F) **todas activas** en al menos 1 régimen.
- [ ] Sin filtros inertes (correr unit test que verifique que cada filtro
      bloquea señales que deberían bloquearse).
- [ ] WR ≥ 64% en al menos 3 perfiles (no solo MIXED).
- [ ] RR (Avg Win / Avg Loss) ≥ 1.8 en al menos 5 perfiles.
- [ ] Sin bug `undefined`/`NaN` en logs.
- [ ] Trend filter threshold justificado (no "porque sí").

---

## 7. Por dónde seguir (prioridades v82+)

1. **Fix Strategy A RSI bug** (línea 1130) — quick win, desbloquea el filtro
   que llevaba meses inerte. Puede mejorar MIXED sin tocar nada más.
2. **Strategy F: Grid Trading** para BLUE/STABLE — desbloquea 2 perfiles que
   hoy están en 0 trades. Añade 2/8 perfiles al contador de "profitables".
3. **Tunear trend filter ±0.05% → ±0.10%** — recupera P&L en MIXED que cayó
   de -22 a -91 sin sacrificar BULL/MEME.
4. **Activar Strategy C en SIDE** — usa el 25% del capital inerte.
5. **Volatility Breakout (Strategy G)** para HIGHVOL — actualmente HIGHVOL
   sigue en Profit% 25%. Detectar ATR spike y entrar en dirección del spike.
6. **Regime-aware primary strategy switching** — en lugar de A+B siempre
   activas, hacer que BULL→A primary, BEAR→A primary (short), SIDE→B primary,
   HIGHVOL→G primary, BLUE/STABLE→F primary.
