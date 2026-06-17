# ANÁLISIS CRÍTICO — Propuesta de Mejoras PPMT

> Fecha: 2026-06-17
> Sobre: Propuesta de mejoras técnicas (Zonas 2–7) enviada por el usuario
> Filosofía aplicada: **profesional, simple, funcional**. Nada de over-engineering.

---

## RESUMEN EJECUTIVO

La propuesta contiene ideas buenas, pero **varias ya existen en PPMT** y otras son **peligrosas de implementar ahora**. Tras auditar el código actual (`ppmt/src/ppmt/`), este es el veredicto por zona:

| Zona | Veredicto | Razón |
|------|-----------|-------|
| 2.1 Recalibración dinámica por TF | ✅ **Implementar** | Real mejora. Actualmente es fijo 2000 velas sin tener en cuenta TF. |
| 2.2 `min_dias=3` para Recién Listados | ✅ **Implementar** | Filtro de seguridad, 1 línea de config. |
| 2.3 Sweep con caché por símbolo | ✅ **Implementar** | La caché es la victoria real (si BTC está en 5 grupos, validarlo 1 vez). Progreso y cancelación ya funcionan. |
| 2.4 Columna "Fecha del test" | ✅ **Implementar** | Trazabilidad, tweak de UI. |
| 3.1–3.2 History SQLite + CLI | ⚠️ **Implementar versión mínima** | Solo `historical_scans` + `scan_results` + auto-save hook + `ppmt history --latest N`. NO crear `real_trades` (ya existe en `storage.save_trade()`). |
| 3.3 Insights automáticos | ❌ **Posponer a v0.35** | Bonito pero no crítico. Lo haremos cuando tengamos 50+ escaneos guardados. |
| 3.4 Guardado automático | ✅ **Implementar** | Es lo único de la Zona 3 que es realmente imprescindible. |
| 4.1–4.4 Portfolio Manager | 🛑 **YA EXISTE** | `risk/portfolio_manager.py` (1543 líneas), `portfolio_backtester.py` (814), `correlation_engine.py`, `regime_allocator.py`, `money_manager.py`. **NO duplicar.** |
| 5.1 Smart Selector con scoring | ⚠️ **Implementar scoring simple** | Un utility function de scoring + vista de tabla. NO crear módulo nuevo, integrar en history. |
| 5.2–5.4 Ejecución directa con CCXT | 🛑 **NO IMPLEMENTAR** | Peligroso sin validación paper trading previa. Posponer a v0.36+. |
| 6 Flujo completo | ✅ **Documentar** | Es diagrama, no código. |
| 7 Tests | ✅ **Implementar mínimos** | 5–8 tests cubriendo lo nuevo. |

---

## DETALLE POR ZONA

### ZONA 2 — Mejoras técnicas sobre lo existente

#### 2.1 Recalibración dinámica por TF ✅ APROBADO

**Problema real detectado en `engine/realtime.py:177`:**
```python
recalibration_interval: int = 2000  # FIJO, no depende de TF
```

En TF=4h, 2000 velas = 333 días de operación continua. En TF=1d, = 5.5 años. **Es absurdo recalibrar tan a menudo en TFs altos.** Tu propuesta es correcta.

**Implementación (pequeña, limpia):**
```python
def get_recalibration_interval(tf_minutes: int) -> int:
    """TF-aware recalibration interval.
    
    15m is the reference (2000 candles ≈ 20 days).
    Higher TFs → fewer recalibrations (less wasteful).
    Lower TFs → same as base (don't recalibrate too often either).
    """
    base = 2000
    factor = max(1.0, tf_minutes / 15.0)
    return int(base * factor)
```

Tabla resultante:
| TF | Intervalo (velas) | Tiempo real |
|----|-------------------|-------------|
| 1m | 2,000 | 33h |
| 5m | 2,000 | 7d |
| 15m | 2,000 | 21d |
| 1h | 8,000 | 333d |
| 4h | 32,000 | 5.3 años |
| 1d | 192,000 | 526 años (efectivamente nunca) |

⚠️ **Matización:** Para 4h y 1d, el cálculo da valores absurdos. Pongo un **techo de 50,000 velas** para no desactivar la recalibración del todo (mantiene Living Trie viva).

#### 2.2 Filtro `min_dias=3` para Recién Listados ✅ APROBADO

En `data/groups.py:236` ya tienes `recently_listed_30d` con `listing_days_max: 30`. Falta `listing_days_min: 3` para evitar la inestabilidad de las primeras 72h. **Una línea.**

#### 2.3 Sweep con caché por símbolo ✅ APROBADO (parcialmente)

**Lo que ya funciona** (ver `terminal/server.py` y `state.py`):
- ✅ Barra de progreso por token
- ✅ Cancelación vía flag `sweep_cancelled`
- ⚠️ Límite de tiempo: no existe, pero es opcional

**Lo que falta (lo importante):**
- ❌ **Caché por símbolo**: si BTC aparece en `top10_mcap`, `blue_chips`, `layer1`, `mi_cartera`, y el usuario hace Sweep All Groups, BTC se valida 4 veces. Esto es la victoria real.

**Implementación:** un dict `{symbol_tf: result}` con TTL de 5 min durante el sweep. ~30 líneas.

#### 2.4 Columna "Fecha del test" ✅ APROBADO

UI tweak, 5 líneas en el template HTML/JS del dashboard.

---

### ZONA 3 — Módulo History

#### 3.1 Estructura SQLite ⚠️ SIMPLIFICAR

Tu propuesta crea **3 tablas**:
- `historical_scan` ← ✅ nueva, necesaria
- `scan_results` ← ✅ nueva, necesaria
- `real_trades` ← ❌ **YA EXISTE** en `storage.py` (método `save_trade()`)

**Solo crear 2 tablas nuevas.** Las trades reales ya se guardan en `~/.ppmt/ppmt.db` cuando se ejecutan en paper trading.

#### 3.2 CLI `ppmt history` ⚠️ VERSIÓN MÍNIMA

Implementar solo:
```bash
ppmt history --latest 10           # últimos 10 escaneos
ppmt history --symbol DOGEUSDT     # historial de un token
ppmt history --today               # escaneos de hoy
ppmt history --export informe.csv  # exportar
```

**NO implementar `--export` en v0.34** — es trivial de añadir después, y los usuarios pueden usar `sqlite3` directamente mientras tanto.

#### 3.3 Insights automáticos ❌ POSPONER

```bash
ppmt insights  # mejores tokens, peores, nuevos PASS, consistentes
```

Es una buena idea, pero **requiere 50+ escaneos históricos** para ser útil. Lo hacemos en v0.35 cuando tengamos data real acumulada. Implementarlo ahora es escribir código que nadie va a probar con datos reales.

#### 3.4 Guardado automático ✅ IMPRESCINDIBLE

Este es el **único punto crítico de la Zona 3**. Cada escaneo (individual o Sweep All) debe guardarse automáticamente en SQLite, sin que el usuario haga nada.

**Implementación:** hook en `terminal/server.py` después de cada `_run_validation()` que llame a `history_manager.save_scan(...)`. ~20 líneas.

---

### ZONA 4 — Portfolio Manager 🛑 YA EXISTE — NO DUPLICAR

**El usuario no recuerda que esto ya está hecho.** Verifiquemos:

```
ppmt/src/ppmt/risk/
├── portfolio_manager.py        (1543 líneas) ← Gestión completa
├── portfolio_backtester.py     (814 líneas)  ← Backtest con rebalanceo
├── portfolio_api.py            ← API REST
├── portfolio_runner.py         ← Orquestador
├── correlation_engine.py       ← Diversificación
├── regime_allocator.py         ← Asignación por régimen
├── money_manager.py            ← Kelly, position sizing
└── position_sizing.py          ← Cálculo de tamaño
```

Tu propuesta Zona 4 crearía **código duplicado**. Lo único que falta es:
- ⚠️ **Tabla SQLite `portfolios` + `portfolio_positions`** para persistencia entre sesiones (actualmente está en memoria)
- ⚠️ **CLI `ppmt portfolio`** (no existe como CLI, solo como API REST)

→ Esto se hace en **v0.35**, no ahora. Esta release es para no romper lo que funciona.

---

### ZONA 5 — Smart Selector + Ejecución

#### 5.1 Smart Selector con scoring ⚠️ SOLO SCORING

El scoring es útil y barato. Implementar como **función utility** dentro de `history_manager.py`:

```python
def score_signal(metrics: dict, weights: dict) -> float:
    """Score 0–100 basado en PF, Sharpe, WR, DD, trades."""
    ...
```

**NO crear módulo nuevo** `smart_selector.py` para algo que son 30 líneas.

#### 5.2–5.4 Ejecución directa con CCXT 🛑 NO IMPLEMENTAR

**Razones:**
1. El usuario dice "simple pero funcional". Ejecución real no es simple.
2. No hay validación paper trading previa suficiente.
3. CCXT requiere API keys con permisos de trading — riesgo de pérdida real.
4. La filosofía PPMT actual es: **validar → paper trade → (mucho más tarde) ejecutar real**.

**Lo que sí hacemos:** paper trading ya funciona (`engine/paper_trader.py`). En v0.35 lo conectamos al Smart Selector para que el usuario pueda ver "si hubiera operado estas 3 señales, este sería el PnL". En v0.36+ se evalúa ejecución real.

---

### ZONA 6 — Flujo completo ✅ DOCUMENTAR

Es un diagrama de uso, no código. Lo añado a TRAZABILIDAD.md.

### ZONA 7 — Tests ✅ MÍNIMOS

5–8 tests cubriendo:
- `get_recalibration_interval()` para 1m/15m/1h/4h/1d
- Caché por símbolo en sweep (un símbolo en 2 grupos → 1 validación)
- `history_manager.save_scan()` inserta correctamente
- `history_manager.list_scans(limit=10)` devuelve lo guardado
- `score_signal()` scoring determinístico

---

## PLAN DE IMPLEMENTACIÓN POR FASES

### v0.34.0 (esta release) — 4 cambios pequeños + 1 módulo nuevo
1. ✅ `get_recalibration_interval(tf_minutes)` en `engine/realtime.py`
2. ✅ `listing_days_min: 3` en `recently_listed_30d` (`data/groups.py`)
3. ✅ Caché por símbolo en Sweep All Groups (`terminal/server.py`)
4. ✅ Columna "Fecha del test" en tabla de resultados (UI)
5. ✅ Módulo `history_manager.py` (SQLite + auto-save + scoring + CLI básico)

### v0.35.0 (próxima) — Portfolio + Selector display
6. Persistencia SQLite de `portfolio_manager.py` (tablas `portfolios`, `portfolio_positions`)
7. CLI `ppmt portfolio create/status/rebalance/risk-report`
8. CLI `ppmt selector --top N --group X` (display only, no execution)
9. Conectar Paper Trader al selector → "si hubiera operado top 3, este sería el PnL"

### v0.36.0+ — Insights + export
10. `ppmt insights` (mejores/peores tokens, nuevos PASS, consistentes)
11. `ppmt history --export CSV`
12. Insights visuales en dashboard FastAPI

### v1.0.0 (cuando haya data real acumulada) — Ejecución real
13. Integración CCXT opcional (OFF por defecto, con confirmación explícita)
14. Paper trading validation gate: 30+ días operando antes de permitir real

---

## CONCLUSIÓN

La propuesta tiene **ideas buenas mezcladas con código duplicado y características peligrosas**. Hemos elegido:

- **5 mejoras pequeñas y seguras** para v0.34.0
- **Posponer** Portfolio SQLite + Selector display para v0.35.0 (no duplica código existente)
- **Bloquear** ejecución real con CCXT hasta v1.0.0

Esto mantiene la promesa: **profesional, simple, funcional**. Nada de over-engineering, nada de duplicar lo que ya funciona, nada de poner dinero real en riesgo sin validación previa.
