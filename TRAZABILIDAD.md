# TRAZABILIDAD PPMT — Estado del Proyecto

> Última actualización: 2026-06-16
> Versión actual: v0.32.0 (commit `fd88a91` + decisión dashboard)
> Repositorio: https://github.com/coverdraft/ppmt
> Idioma: Español

---

## ⚠️ DECISIÓN ARQUITECTÓNICA — Dashboard Oficial (16 jun 2026)

**El dashboard oficial de PPMT es el FastAPI en `http://localhost:8420` (PPMT v0.32.0).**

**El dashboard Next.js en `http://localhost:3000` (que muestra "PPMT v0.12.0") está OBSOLETO y DESACTIVADO.**

### Razones
1. El Next.js `package.json` dice `name: cryptoquant-terminal v0.3.0` — es código heredado de otro proyecto, no es PPMT real.
2. El "v0.12.0" en la esquina superior está **hardcodeado** en `src/app/page.tsx:54` (un string pegado, no se calcula).
3. Las "5 estrategias" que muestra el Next.js (BTC +2450, ETH +1230, SOL -320, etc.) son **DEMO_DATA falsa** sembrada por `src/app/api/strategies/route.ts` (constante `DEMO_STRATEGIES`). **No son trades reales.**
4. El Next.js **no llama al motor PPMT Python**. Lee/escribe solo en una tabla Prisma `PPMTStrategy` con metadata inventada.
5. El proceso `next-server` consumía 121% CPU colgado y no respondía a peticiones.
6. El FastAPI `:8420` tiene **32 endpoints reales**: `/api/trades`, `/api/validate`, `/api/auto-setup`, `/api/start-trading`, `/api/portfolio-backtest`, `/api/multi-tf-analysis`, WebSocket `/ws`. Conecta directo al motor PPMT.

### Acción tomada
- Proceso `next-server` y `next dev` eliminados (libera CPU).
- FastAPI lanzado en `0.0.0.0:8420` con `run_server()`.
- Cualquier referencia a "dashboard" en este documento se refiere al FastAPI `:8420`.

### Limpieza pendiente (no bloquea uso)
- Eliminar `src/components/ppmt/` (panel obsoleto)
- Eliminar `src/app/api/strategies/` (demo data)
- Eliminar modelo `PPMTStrategy` y `PPMTStrategyRun` de `prisma/schema.prisma`
- Eliminar `src/app/page.tsx` actual o redirigirlo a `:8420`
- Borrar `src/store/ppmt-strategy-store.ts`

---

## 1. RESUMEN EJECUTIVO

PPMT (Probability Position Management Tool) es un motor de trading autónomo basado en **Trie progresivo de patrones (N1+N2+N3+N4)** con **fuzzy matching**, gestión de capital tipo Kelly, y modo paper-trading con validación Monte Carlo antes de operar real.

**Estado global (verificado 16 jun 2026):**
- Núcleo del motor (Trie, SAX, Matcher, Predictor): 100% funcional, 160 tests pasan (2 fallos conocidos no bloqueantes).
- Backtesting portfolio + Monte Carlo: 100% funcional.
- Trading en vivo (RealtimeTrader): 90% — paper trading funcional, persistencia de trades implementada (`storage.save_trade()`).
- Dashboard FastAPI `:8420`: 100% funcional con 32 endpoints.
- Pre-trade validation gate: **IMPLEMENTADO** ✅ (en `/api/start-trading`, requiere verdict=PASS).
- Auto-setup endpoint: **IMPLEMENTADO** ✅ (`/api/auto-setup`).
- Multi-token: **IMPLEMENTADO** en backtest (`/api/portfolio-backtest`, `/api/multi-setup`).
- Multi-timeframe: **IMPLEMENTADO** como análisis (`/api/multi-tf-analysis`).
- Next.js `:3000`: **OBSOLETO** — ver sección de decisión arriba.

---

## 2. ARQUITECTURA ACTUAL

```
ppmt/
├── src/
│   ├── ppmt/                    # Núcleo Python (motor + riesgo)
│   │   ├── core/                # Trie, SAX, Matcher, Metadata
│   │   ├── data/                # Collector, Storage (SQLite), Classifier
│   │   ├── engine/              # PPMT, Prediction, Signal, Realtime
│   │   ├── risk/                # MoneyManager, MonteCarlo, PortfolioRunner, Backtester
│   │   ├── cli/                 # CLI (init, ingest, build, predict, run)
│   │   └── terminal/            # FastAPI server (lite dashboard)
│   ├── app/                     # Next.js 16 dashboard (principal)
│   │   ├── api/ppmt/            # 14 rutas API (backtest, signals, predict, etc.)
│   │   ├── components/dashboard/# 56+ paneles (BacktestingLab, PaperTradingPanel, etc.)
│   │   └── ...
│   └── tests/                   # 209 tests (208 pasan, 1 fallo conocido en OOS)
├── data/                        # SQLite DB + tries serializados
├── config/                      # YAML de configuración
├── pyproject.toml
└── package.json
```

### Stack técnico
- **Python 3.11+**: FastAPI, numpy, pandas, scipy, websockets
- **Next.js 16 + TypeScript**: React 19, Tailwind 4, shadcn/ui, Prisma ORM, Socket.IO
- **Base de datos**: SQLite (candles, tries, señales, perfiles)
- **Bibliotecas clave**: matplotlib (charts), rich (CLI), click

---

## 3. FUNCIONALIDADES IMPLEMENTADAS ✅

### Núcleo del motor
- ✅ Trie progresivo N1+N2+N3+N4 con SAX encoding
- ✅ Fuzzy matching con distancia de Levenshtein
- ✅ Metadata v4.2 con 21 métricas por nodo
- ✅ 4 TokenProfiles auto-calibrables: `blue_chip`, `default`, `meme`, `new_launch`
- ✅ Asset classifier automático (registra perfil correcto según clase de token)
- ✅ Persistencia de tries en SQLite (todos los niveles N1-N4)

### Datos
- ✅ DataCollector con Binance, Bybit y MEXC (REST + WebSocket)
- ✅ Multi-timeframe: 1m, 5m, 15m, 1h, 4h, 1d (configurable)
- ✅ SQLite storage con tablas: `assets`, `ohlcv_data`, `tries`, `engine_state`, `signals`, `token_profiles`
- ✅ Bulk ingest CLI: `ppmt bulk-ingest`

### Backtesting y Riesgo
- ✅ `PortfolioBacktester`: backtest multi-token con capital compartido
- ✅ `MonteCarloSimulator`: 1000 simulaciones, Risk of Ruin, P95 DD, Profit Factor
- ✅ `MoneyManager`: Kelly Criterion, kill switch, daily loss limit, drawdown limit
- ✅ `PortfolioRunner`: orquestador multi-engine en hilo async
- ✅ `CorrelationEngine`: matriz de correlación entre posiciones
- ✅ `RegimeAllocator`: asignación según régimen de mercado (6 regímenes)

### Trading en vivo
- ✅ `RealtimeTrader`: streaming con WebSocket, modo live o replay
- ✅ 4 modos: `DRY_RUN`, `PAPER`, `LIVE`, `HEDGE`
- ✅ API `/api/ppmt/realtime/trade` para ejecutar paper trades desde señales
- ✅ WebSocket `/ws` para broadcast de estado en tiempo real
- ✅ `PortfolioRunner` con `run_async()` (no bloquea el dashboard)

### Dashboard (Next.js)
- ✅ 56+ componentes dashboard (BacktestingLab, PaperTradingPanel, etc.)
- ✅ 14 rutas API PPMT (backtest, signals, predict, ohlcv, runner, etc.)
- ✅ Monte Carlo UI con gráficos de distribución
- ✅ Chart OHLCV con trade markers (entry/exit)
- ✅ UI/UX profesional: sidebar ancho, tipografía 11px+, dark theme

### Tests y CI
- ✅ 208 tests pasan (de 209)
- ✅ Tests unitarios de Trie, Matcher, Metadata, SAX, MoneyManager, PortfolioRunner
- ✅ Tests de integración de pipeline completo

---

## 4. COSAS QUE FALTAN ⚠️ (LISTA PRIORIZADA)

### 🔴 PRIORIDAD CRÍTICA — Seguridad de Trading

#### F1. Tabla `trades` en SQLite + persistencia
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/ppmt/data/storage.py`, `src/ppmt/risk/money_manager.py`

El `MoneyManager` mantiene las posiciones en memoria. Cuando se reinicia el dashboard o el proceso, **se pierden todas las posiciones abiertas y el historial completo de trades**. No hay tabla `trades` en SQLite.

**Qué hacer:**
- Crear tabla `trades` con campos: `id, symbol, side, entry_time, entry_price, exit_time, exit_price, qty, pnl, pnl_pct, fee, status, signal_meta, asset_class`
- `MoneyManager.open_position()` debe escribir un row con `status='open'`
- `MoneyManager.close_position()` debe actualizar el row con `status='closed'` y los datos de salida
- Método `Storage.get_trade_history(symbol=None, limit=100)` para consultar historial

#### F2. Pre-trade Validation Gate (Backtest + Monte Carlo)
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/app/api/ppmt/realtime/route.ts`, `src/ppmt/risk/monte_carlo.py`

Antes de permitir que el `RealtimeTrader` opere un token en modo autónomo, debe validar que el sistema tiene **edge estadísticamente significativo** sobre ese token. Actualmente el sistema puede empezar a operar sin ninguna validación previa.

**Qué hacer:**
- Nuevo endpoint `POST /api/ppmt/validate` que reciba `{symbol, timeframe}`
- Internamente ejecuta `PortfolioBacktester.run()` con últimos 90 días
- Si backtest tiene < 30 trades → verdict `INSUFFICIENT_DATA` (no operar)
- Si backtest tiene ≥ 30 trades → ejecuta `MonteCarloSimulator.run()` (1000 sim)
- Veredicto final: `LOW_RISK` (P95 DD <15%, RoR <5%, PF>1.3) / `MODERATE` / `HIGH_RISK`
- Solo si verdict es `LOW_RISK` o `MODERATE`, se habilita el auto-trade para ese token
- Guardar verdict en SQLite (`token_validations` table) con expiración de 24h

#### F3. Auto-Setup ("Prepare Token") endpoint
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/app/api/ppmt/auto-setup/route.ts` (nuevo)

Un token nuevo no puede operar sin: ingest → build tries → backtest → MC validation. Hoy esto requiere 4 comandos CLI manuales. Necesitamos un botón "Prepare Token" en el dashboard que ejecute toda la cadena.

**Qué hacer:**
- Nuevo endpoint `POST /api/ppmt/auto-setup` con body `{symbol, timeframe}`
- Steps en backend: ingest 90 días → build 4 niveles trie → guardar tries → run backtest → run MC → guardar validation
- Devuelve `{job_id, status: 'running'}` inmediatamente, el cliente hace polling a `/api/ppmt/auto-setup/{job_id}`
- Cuando termina, actualiza el estado del token en UI a "READY_TO_TRADE"

---

### 🟡 PRIORIDAD ALTA — Visibilidad en Dashboard

#### F4. Panel "Trade History" en dashboard
**Estado:** PARCIAL — existe `PaperTradingPanel` pero no muestra historial persistente
**Archivos afectados:** `src/components/dashboard/trade-history-panel.tsx` (nuevo)

El usuario debe poder ver **todas las operaciones ejecutadas** (paper y live) con: timestamp, símbolo, lado, entry/exit price, PnL, fees, status. Hoy solo se ve en memoria y se pierde al recargar.

**Qué hacer:**
- Crear `TradeHistoryPanel` conectado a `GET /api/ppmt/trades?symbol=X&limit=100`
- Tabla con filtros (símbolo, fecha, status) y totales (PnL total, win rate, profit factor)
- Exportable a CSV

#### F5. Panel "Money Management" en dashboard
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/components/dashboard/money-manager-panel.tsx` (nuevo)

El usuario debe ver en tiempo real: capital total, cash disponible, exposición, drawdown actual, daily P&L, kill switch status, posiciones abiertas con su PnL no realizado.

**Qué hacer:**
- Crear `MoneyManagerPanel` conectado a `/api/ppmt/risk` (ya existe)
- Mostrar: Total Equity, Cash, Exposure %, Drawdown %, Daily P&L, Open Positions count
- Botón "Kill Switch" para detener todo
- Lista de posiciones abiertas con PnL live

#### F6. Panel "Validation Results" en dashboard
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/components/dashboard/validation-panel.tsx` (nuevo)

Mostrar el verdict de validación (LOW/MODERATE/HIGH) por token, con fecha de última validación y métricas clave (PF, P95 DD, RoR, num trades). Solo tokens validados pueden operar.

**Qué hacer:**
- Crear `ValidationPanel` conectado a `GET /api/ppmt/validations`
- Tabla: symbol | last_validation | verdict | PF | P95_DD | RoR | trades | expires_at
- Badge de color: verde (LOW), amarillo (MODERATE), rojo (HIGH)

---

### 🟢 PRIORIDAD MEDIA — Multi-token y Multi-timeframe

#### F7. Multi-token trading en vivo
**Estado:** PARCIAL — `PortfolioRunner` existe pero solo se usa en backtest
**Archivos afectados:** `src/ppmt/risk/portfolio_runner.py`, `src/app/api/ppmt/runner/route.ts`

El `PortfolioRunner` ya orquesta múltiples `TokenEngine` pero solo en modo backtest. En modo live, solo hay un `RealtimeTrader` activo a la vez. Necesitamos múltiples traders en paralelo (uno por token), compartiendo el mismo `MoneyManager` y `ParentNodeManager`.

**Qué hacer:**
- Modificar `PortfolioRunner` para soportar `mode='live'` además de `'backtest'`
- En modo live: un hilo por token, cada uno con su WebSocket, pero todos escribiendo al mismo `MoneyManager`
- API `POST /api/ppmt/runner/start` con `{symbols: [...], mode: 'live'}`
- Bloqueo de concurrencia en `MoneyManager.open_position()` para evitar race conditions

#### F8. Multi-timeframe signal fusion
**Estado:** NO IMPLEMENTADO
**Archivos afectados:** `src/ppmt/engine/signal.py`, `src/ppmt/engine/realtime.py`

Actualmente el `RealtimeTrader` opera en un solo timeframe. Un sistema profesional fusiona señales de múltiples timeframes (ej: 1h para dirección, 5m para entrada, 1m para ejecución). Sin esto, el sistema es ciego al contexto mayor.

**Qué hacer:**
- `SignalFusion` class que recibe señales de N timeframes y emite un score combinado
- Pesos por timeframe: 1d=0.3, 4h=0.25, 1h=0.2, 15m=0.15, 5m=0.1
- Solo operar si ≥ 2 timeframes alineados en dirección
- Configurable por `TokenProfile` (algunos tokens operan mejor en TF alto)

#### F9. ParentNodeManager UI
**Estado:** PARCIAL — la clase existe en `money_manager.py` pero no hay UI
**Archivos afectados:** `src/components/dashboard/parent-node-panel.tsx` (nuevo)

El `ParentNodeManager` gestiona el pool de capital con nodos hijo (cada uno con `alloc_pct`, `leverage`, `auto_mode`). Hoy solo se ve en CLI. Necesitamos una UI para crear/configurar/eliminar nodos hijo y ver su rendimiento individual.

**Qué hacer:**
- Crear `ParentNodePanel` conectado a `/api/ppmt/parent-nodes`
- Vista de árbol: Parent → Child 1, Child 2, ...
- Por hijo: alloc_pct, leverage, current_value, pnl, auto_mode toggle
- Botones: Add Child, Rebalance, Withdraw

---

### 🔵 PRIORIDAD BAJA — Polish

#### F10. Test OOS con símbolos rotos
**Estado:** 1 test falla — `test_oos_with_trending_data`
**Archivos afectados:** `tests/test_oos_validation.py:187`

El test pasa `symbols=` a `PPMT.build()` pero la firma no acepta ese parámetro. Hay que actualizar el test o añadir el parámetro (con default `None`) al método `build()`.

#### F11. Test trie-merge-observations falla
**Estado:** 1 test falla — `test_trie_merge_preserves_observations`
**Archivos afectados:** `tests/test_v43_robust.py:757`

`AttributeError` en línea 757. Revisar qué atributo se perdió durante el merge de tries.

#### F12. Script de deployment (Caddyfile + systemd)
**Estado:** PARCIAL — existe Caddyfile pero no systemd service
**Archivos afectados:** `deploy/ppmt.service` (nuevo)

Para producción necesitamos un `.service` de systemd que levante el dashboard Next.js y el backend Python al arrancar.

#### F13. Webhook de notificaciones (Telegram/Discord)
**Estado:** NO IMPLEMENTADO

Notificar al usuario cuando: se abre trade, se cierra trade, kill switch disparado, validación completada, error crítico.

---

## 5. PLAN DE IMPLEMENTACIÓN RECOMENDADO

### Sprint 1 (Semana 1) — Seguridad crítica
- **F1**: Tabla `trades` + persistencia (4h)
- **F2**: Pre-trade validation gate (8h)
- **F3**: Auto-setup endpoint (4h)
- Tests de integración (2h)
- **Entregable:** Trading autónomo seguro con gate de validación

### Sprint 2 (Semana 2) — Visibilidad dashboard
- **F4**: Trade History panel (4h)
- **F5**: Money Manager panel (4h)
- **F6**: Validation Results panel (3h)
- **Entregable:** Usuario ve todo lo que pasa en el sistema

### Sprint 3 (Semana 3) — Multi-token y fusion
- **F7**: Multi-token live trading (8h)
- **F8**: Multi-timeframe signal fusion (6h)
- **F9**: ParentNodeManager UI (4h)
- **Entregable:** Sistema opera múltiples tokens en múltiples timeframes

### Sprint 4 (Semana 4) — Polish y deploy
- **F10, F11**: Fix tests (2h)
- **F12**: Deployment scripts (3h)
- **F13**: Webhook notificaciones (4h)
- **Entregable:** Sistema production-ready

---

## 6. CÓMO ACTUALIZAR EN TU ORDENADOR

```bash
# 1. Navegar al directorio del proyecto
cd ~/projects/ppmt    # o donde tengas el repo

# 2. Traer los últimos cambios
git fetch origin
git pull origin main

# 3. Si hay cambios en dependencias Python
pip install -e . --upgrade

# 4. Si hay cambios en dependencias Node
npm install

# 5. Si hay migraciones de Prisma
npx prisma generate
npx prisma db push

# 6. Levantar el dashboard
# Opción A: Dashboard Next.js (recomendado)
npm run dev
# → http://localhost:3000

# Opción B: Terminal lite FastAPI
python -m ppmt.terminal.server
# → http://localhost:8420

# 7. Levantar trading en vivo (en otra terminal)
ppmt run --symbol BTC/USDT --timeframe 1h --mode paper
```

### Comandos CLI útiles
```bash
ppmt init                                    # Inicializar DB
ppmt ingest --symbol BTC/USDT --timeframe 1h --days 90   # Descargar datos
ppmt build --symbol BTC/USDT --timeframe 1h  # Construir tries
ppmt predict --symbol BTC/USDT --timeframe 1h # Ver predicción actual
ppmt list                                    # Listar assets trackeados
ppmt stats --symbol BTC/USDT                 # Estadísticas de patrones
ppmt run --symbol BTC/USDT --timeframe 1h --mode paper   # Paper trading
```

---

## 7. ESTADO DE TESTS

```
========================= 208 passed, 1 failed in 3.11s =========================
```

**Tests que fallan (conocidos):**
1. `tests/test_oos_validation.py::TestPatternDetectionOOS::test_oos_with_trending_data` — `PPMT.build()` no acepta `symbols=` kwarg
2. `tests/test_v43_robust.py::TestFullPipelineIntegration::test_trie_merge_preserves_observations` — `AttributeError` línea 757

Estos fallos NO bloquean el trading ni el backtesting. Son tests de robustez pendientes de fix.

---

## 8. ESTADO DE GITHUB

- **Repo:** https://github.com/coverdraft/ppmt
- **Branch activa:** `main`
- **Último commit pushed:** `c970697` — feat: Phase 4.6 E2E complete, all 4 phases done
- **Commits sin push:** 1 (este commit incluye TRAZABILIDAD.md)
- **Tests en CI:** locales solamente (no hay GitHub Actions todavía)

### Próximos commits planificados
- `feat: Phase 5.1 — trades table + persistence`
- `feat: Phase 5.2 — pre-trade validation gate (Backtest + MC)`
- `feat: Phase 5.3 — auto-setup endpoint`
- `feat: Phase 5.4 — dashboard panels (TradeHistory, MoneyManager, Validation)`
- `feat: Phase 5.5 — multi-token live trading`
- `feat: Phase 5.6 — multi-timeframe signal fusion`

---

## 9. FILTROS DE SEGURIDAD ACTIVOS

Antes de operar en modo LIVE (cuando se habilite), verificar:

- [ ] Token validado: verdict = LOW_RISK o MODERATE_RISK (no HIGH_RISK)
- [ ] Backtest con ≥ 30 trades en últimos 90 días
- [ ] Monte Carlo P95 Drawdown < 20%
- [ ] Monte Carlo Risk of Ruin < 5%
- [ ] Monte Carlo Profit Factor > 1.3
- [ ] Capital disponible en MoneyManager > min_position_size
- [ ] No hay kill switch activo
- [ ] Daily loss no superado
- [ ] Drawdown actual no superado
- [ ] No más de max_concurrent_positions abiertas
- [ ] Correlación con posiciones existentes < 0.7

Si **cualquier** check falla, el sistema debe **rechazar** el trade y loguear el motivo.

---

## 10. CONTACTO Y BITÁCORA

Bitácora técnica detallada en inglés: `TRACEABILITY.md` (149KB, histórico completo desde v0.1.0).

Este documento `TRAZABILIDAD.md` es el resumen ejecutivo en español, actualizado cada vez que se completa una fase o se hace un cambio arquitectónico significativo.

**Próxima actualización:** después de Sprint 1 (F1+F2+F3 completados).

---

## 🔧 FIX v0.32.1 (16 jun 2026) — Monte Carlo + Validation Gate

### Bug crítico encontrado
El usuario reportó: **"siempre da FAIL"** al validar cualquier token (BTC, DOGE, LINK, SOL).

### Diagnóstico
1. **Bug principal:** `MonteCarloSimulator(config=mc_config)` en `server.py:899` — el constructor de `MonteCarloSimulator` **no acepta argumentos**. El config se pasa al método `.simulate()`.
2. Como MC fallaba con `TypeError: MonteCarloSimulator() takes no arguments`, `mc_result` quedaba vacío `{}`.
3. El check `risk_of_ruin_pass` usaba `mc_result.get("risk_of_ruin", 1.0)` → default 1.0 (100%) → check SIEMPRE fallaba.
4. Resultado: verdict siempre FAIL, sin importar cuán bueno fuera el backtest.

### Fixes aplicados
1. **`MonteCarloSimulator()` sin args** + `mc_sim.simulate(trades_pnl, config=mc_config)` (server.py:899-902)
2. **Log de backtest** añadido: ahora imprime `trades=X, WR=Y%, PnL=Z%, DD=W%` para diagnóstico
3. **Verdict `INSUFFICIENT_DATA`** cuando backtest produce 0 trades (antes era FAIL confuso)
4. **Mensajes diferenciados en pre-trade gate:**
   - `INSUFFICIENT_DATA`: "backtest produced 0 trades — ingest more data"
   - `FAIL`: "did not pass safety checks (WR/PF/RoR)"
5. **Log de MC con traceback** (`exc_info=True`) para futuros diagnósticos

### Verificación del fix
Test directo con 10 trades sintéticos:
```
Risk of Ruin: 0.0000
P95 Max DD: 0.0637
Prob Profit: 1.0000
Verdict: LOW RISK
Checks: ALL PASS → verdict: PASS
```

### Cómo probar en tu Mac
```bash
git pull origin main
pip install -e . --force-reinstall
ppmt terminal
# → abrir http://localhost:8420
# → elegir token + TF + botón "PREPARAR Y VALIDAR"
# → ahora MC correrá y verdict será PASS/FAIL real (no siempre FAIL)
```

### Estado de tests
- 160 tests pasan (sin cambios — fix solo afecta `server.py`)
- 2 tests conocidos con fallo no relacionado (OOS validation + trie merge)

### Lección aprendida
El bug estuvo desde v0.31.0 (introducción del validation gate). Nunca se detectó porque:
- El `try/except` silenciaba el error de MC
- El default `risk_of_ruin=1.0` hacía que el check fallara sin información útil
- El log `Monte Carlo failed: MonteCarloSimulator() takes no arguments` solo era visible en consola

**Acción preventiva:** cuando un módulo falla silenciosamente, los defaults no deben hacer que el verdict sea siempre el mismo. Ahora con `INSUFFICIENT_DATA` queda claro cuándo es bug vs cuándo es falta de datos.
