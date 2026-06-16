# PPMT — Plan de Implementación y Trazabilidad

**Versión:** v0.31.0  
**Fecha:** 2026-06-16  
**Estado:** En progreso

---

## Arquitectura General

```
┌──────────────────────────────────────────────────────────────────┐
│                     PPMT Terminal (Dashboard)                     │
│                    index.html + WebSocket                         │
├──────────────┬──────────────┬──────────────┬─────────────────────┤
│  Chart Panel │  Trade Panel │  Validation  │  Money Management   │
│  (OHLCV+Sig) │  (History)   │  (BT + MC)   │  (Kelly + Nodes)    │
└──────┬───────┴──────┬───────┴──────┬───────┴──────────┬──────────┘
       │              │              │                  │
┌──────▼──────────────▼──────────────▼──────────────────▼──────────┐
│                      FastAPI Server (server.py)                   │
│  /api/status  /api/trades  /api/validate  /api/auto-setup       │
│  /api/nodes   /api/backtest  /api/start-trading  /ws            │
└──────┬──────────────┬──────────────┬─────────────────────────────┘
       │              │              │
┌──────▼──────────────▼──────────────▼─────────────────────────────┐
│                    Core Engine (realtime.py)                       │
│  SAX → Pattern Buffer → FuzzyMatcher → Prediction → Signal      │
│  Living Trie (N1+N2+N3+N4) → Regime → Risk → Position          │
├─────────────────┬──────────────────┬─────────────────────────────┤
│  MoneyManager   │  ParentNodeMgr   │  MonteCarloValidator        │
│  (Kelly+limits) │  (Multi-token)   │  (1000 sims, RoR, P95 DD)  │
└─────────────────┴──────────────────┴─────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────────┐
│                    PPMTStorage (SQLite)                           │
│  ohlcv | tries | trades | validations | signals | engine_states  │
│  (symbol, timeframe, timestamp) → Multi-symbol/Multi-TF          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4 Estilos de Token (TokenProfile)

| Estilo | Asset Class | SAX α | SAX W | Max Position | Catastrophic Loss | SL/TP Ratio |
|--------|-------------|-------|-------|-------------|-------------------|-------------|
| `blue_chip` | BTC, ETH | 5-7 | 8-15 | 4% | 5% | 2.0:1 |
| `default` | Large/Mid Cap | 5-6 | 6-12 | 3% | 8% | 1.8:1 |
| `meme` | DOGE, SHIB, PEPE | 4-5 | 4-8 | 2% | 15% | 1.5:1 |
| `new_launch` | New tokens | 3-4 | 3-6 | 1% | 25% | 1.2:1 |

Cada perfil tiene **21 parámetros auto-calibrables** via `TradingCalibrationEngine`.

---

## Fases de Implementación

### Phase 1 — Fundamentos Rotos ✅ COMPLETADO
- [x] Tabla `trades` en SQLite con campos: symbol, timeframe, direction, entry/exit, P&L, confidence, exit_reason, regime, leverage, kelly_fraction, node_id
- [x] Endpoint `/api/trades` — historial de trades cerrados
- [x] Endpoint `/api/trade-summary` — estadísticas agregadas
- [x] Tabla `validations` en SQLite
- [x] Endpoint `/api/validate` — ejecuta backtest + Monte Carlo, devuelve verdict PASS/FAIL
- [x] Gate pre-trade en `/api/start-trading` — si validate=FAIL → no opera
- [x] `storage.save_trade()`, `storage.get_trades()`, `storage.get_trade_summary()`
- [x] `storage.save_validation()`, `storage.get_latest_validation()`

### Phase 2 — Auto-setup ✅ COMPLETADO
- [x] Endpoint `/api/auto-setup` — ingest → build → calibrate → backtest → MC en una llamada
- [x] Progreso visual via `auto_setup_status` en TerminalState
- [x] Auto-ingest si no hay datos (500+ candles requeridas)
- [x] Auto-build si no hay trie (N3 requerido)

### Phase 3 — Panel de Operaciones ✅ COMPLETADO
- [x] TerminalState: campos `trade_history`, `validation_result`, `auto_setup_status`
- [x] **Trade History panel** — tabla de trades cerrados con P&L, exit reason, duración
- [x] **Money Management Detail** — position sizing, Kelly fraction, regime multiplier
- [x] **Validation Results panel** — backtest stats + MC verdict + recomendaciones
- [x] **UI/UX profesional** — rediseño completo estilo Bloomberg/TradingView (v0.32.0)
- [x] **Trade logging** — RealtimeTrader guarda trades en SQLite al cerrar

### Phase 4 — Multi-Token ✅ COMPLETADO
- [x] Endpoint `/api/portfolio-backtest` — backtest multi-token
- [x] Endpoint `/api/multi-setup` — auto-setup múltiples tokens + crear nodos
- [x] ParentNodeManager integration con child nodes por token
- [x] Portfolio backtest desde dashboard

### Phase 5 — Multi-Timeframe ✅ COMPLETADO
- [x] Endpoint `/api/multi-tf-analysis` — análisis multi-temporalidad
- [x] Confluence scoring entre temporalidades
- [x] Auto-ingest + auto-build por temporalidad

---

## Endpoints API Activos

| Método | Endpoint | Descripción | Estado |
|--------|----------|-------------|--------|
| GET | `/api/status` | Estado completo del terminal | ✅ |
| GET | `/api/snapshot` | Snapshot con uptime | ✅ |
| GET | `/api/portfolio` | Resumen de portfolio | ✅ |
| GET | `/api/signals` | Señales recientes | ✅ |
| GET | `/api/performance` | Métricas de rendimiento | ✅ |
| GET | `/api/risk` | Estado de riesgo | ✅ |
| GET | `/api/nodes` | Nodos hijos + parent | ✅ |
| POST | `/api/nodes/add` | Agregar nodo hijo | ✅ |
| POST | `/api/nodes/remove` | Eliminar nodo hijo | ✅ |
| POST | `/api/nodes/leverage` | Cambiar leverage | ✅ |
| POST | `/api/nodes/auto-mode` | Cambiar modo auto/manual | ✅ |
| POST | `/api/nodes/capital` | Cambiar capital total | ✅ |
| POST | `/api/nodes/kill-switch/activate` | Activar kill switch | ✅ |
| POST | `/api/nodes/kill-switch/deactivate` | Desactivar kill switch | ✅ |
| POST | `/api/nodes/redistribute` | Redistribuir capital | ✅ |
| POST | `/api/backtest` | Ejecutar backtest rápido | ✅ |
| GET | `/api/ohlcv` | Datos OHLCV para chart | ✅ |
| GET | `/api/market/price` | Precio actual del mercado | ✅ |
| GET | `/api/market/symbols` | Símbolos disponibles | ✅ |
| POST | `/api/ingest` | Descargar datos históricos | ✅ |
| POST | `/api/start-trading` | Iniciar sesión paper trading | ✅ |
| POST | `/api/stop-trading` | Detener sesión de trading | ✅ |
| GET | `/api/trading-status` | Estado de sesión activa | ✅ |
| GET | `/api/trades` | Historial de trades | ✅ |
| GET | `/api/trade-summary` | Estadísticas agregadas | ✅ |
| POST | `/api/validate` | Validar token (BT + MC) | ✅ |
| POST | `/api/auto-setup` | Pipeline completo automático | ✅ |
| POST | `/api/portfolio-backtest` | Backtest multi-token | ✅ |
| POST | `/api/multi-setup` | Auto-setup múltiples tokens | ✅ |
| POST | `/api/multi-tf-analysis` | Análisis multi-temporalidad | ✅ |
| WS | `/ws` | WebSocket tiempo real | ✅ |

---

## Pre-Trade Validation Gate

Antes de permitir trading autónomo, el sistema verifica:

| Check | Umbral | Descripción |
|-------|--------|-------------|
| Win Rate | > 40% | Tasa de acierto en backtest |
| Profit Factor | > 0.8 | Ganancias vs pérdidas |
| Risk of Ruin (MC) | < 20% | Probabilidad de ruina en 1000 simulaciones |
| MC Verdict | ≠ HIGH RISK | Veredicto de Monte Carlo |
| Min Trades | ≥ 5 | Mínimo de trades en backtest |

Si cualquier check falla → verdict = **FAIL** → trading no permitido.

---

## Living Trie (4 Niveles)

| Nivel | Patrón | Match | Descripción |
|-------|--------|-------|-------------|
| N1 | Último SAX symbol | Exacto | Match más rápido, menos contexto |
| N2 | Últimos 2 SAX symbols | Fuzzy | Balance velocidad/contexto |
| N3 | Últimos 3 SAX symbols | Fuzzy | Match principal |
| N4 | Últimos 4-5 SAX symbols | Fuzzy | Match más lento, más contexto |

Todos los niveles se persisten en SQLite (`tries` table) y se actualizan en vivo.

---

## Money Management Stack

```
Signal → RiskManager (per-trade sizing)
       → MoneyManager (portfolio-level)
           → Kelly Criterion (quarter-Kelly por defecto)
           → Circuit Breakers (daily loss, max drawdown)
           → Kill Switch (exposure > 95%)
       → ParentNodeManager (multi-token allocation)
           → ChildNodeConfig (per-token: alloc_pct, leverage, auto_mode)
```

---

## Base de Datos (SQLite ~/.ppmt/ppmt.db)

### Tablas

| Tabla | PK | Descripción |
|-------|-----|-------------|
| `assets` | symbol | Tokens rastreados con clasificación |
| `ohlcv` | (symbol, timeframe, timestamp) | Velas OHLCV multi-symbol/multi-TF |
| `tries` | (symbol, level) | Tries serializados (N1-N4) |
| `engine_states` | symbol | Estado del motor y TokenProfiles |
| `signals` | id (auto) | Historial de señales |
| `trades` | id (auto) | Historial de trades cerrados |
| `validations` | id (auto) | Resultados de validación |

---

## Changelog

### v0.32.0 (2026-06-16)
- Professional trading terminal UI/UX redesign (Bloomberg/TradingView style)
- CSS Grid layout with chart area, sidebar, blotter, status bar
- Trade logging: RealtimeTrader saves closed trades to SQLite
- Multi-token: /api/portfolio-backtest, /api/multi-setup endpoints
- Multi-timeframe: /api/multi-tf-analysis endpoint with confluence scoring
- All emojis removed from UI, sharp 4px border radius, compact professional density
- Header shows real-time P&L, regime, validation/trading badges
- Version bump to v0.32.0

### v0.31.0 (2026-06-16)
- Added `trades` and `validations` tables to SQLite
- Added `/api/trades`, `/api/trade-summary`, `/api/validate`, `/api/auto-setup` endpoints
- Pre-trade validation gate in `/api/start-trading`
- TerminalState: `trade_history`, `validation_result`, `auto_setup_status` fields
- Auto-ingest + auto-build in start-trading flow
- Monte Carlo validation with Risk of Ruin, P95 DD, verdict
- Living Trie persistence fix (saves all 4 levels N1-N4)
- Auto-build bug fix (PPMT.build() params)
- Backtest markers with entry/exit arrows and P&L colors

### v0.30.0 (2026-06-15)
- Fixed auto-build bug (PPMT.build() wrong params)
- Fixed DataCollector.fetch_historical → fetch_and_save()
- Fixed Living Trie persistence (all 4 levels)
- Implemented addBacktestMarkers() with entry/exit markers
- Removed unused FileResponse import
- Pushed to GitHub
