# CryptoQuant Terminal — Arquitectura del Sistema

## Diagrama de Arquitectura

Ver imagen: `cryptoquant-architecture.png`

```
🌐 DATA SOURCES → 📥 PIPELINE → 🧠 BRAIN (12 Engines) → 🎯 STRATEGY → ⚡ EXECUTION
                                    ↕                      ↕              ↕
                                  🛡️ RISK              💾 STORAGE      🖥️ UI
```

---

## Capas del Sistema

### 1. Data Sources (APIs Externas)
| Fuente | Uso | Estado |
|--------|-----|--------|
| CoinGecko | Volume + OHLCV histórico | ✅ Funcional |
| DexScreener | Precios live + Pares + Batch | ✅ Funcional (batch 30) |
| DexPaprika | Descubrimiento de tokens | ✅ Funcional |
| Etherscan | Datos on-chain ETH | ✅ Funcional |
| Birdeye | — | ❌ ELIMINADO |

### 2. Data Pipeline
- **Data Ingestion Pipeline**: Orquesta todas las fuentes
- **OHLCV Pipeline**: Multi-timeframe (5m, 1h, 4h, 1d) con backfill
- **Unified Cache**: Evita llamadas duplicadas
- **Rate Limiter + Semaphore**: Protege contra rate limits
- **Data Quality Gate**: Valida calidad de datos antes de uso

### 3. Brain Orchestrator (12 Motores Analíticos)
| # | Motor | Función | Líneas |
|---|-------|---------|--------|
| 1 | Big Data Engine | Régimen, anomalías, whale forecast | ~800 |
| 2 | Token Lifecycle | Detección de fase (6 fases) | ~500 |
| 3 | Behavioral Model | Predicción de comportamiento | ~400 |
| 4 | Wallet Profiler | Scoring smart money/whale | ~500 |
| 5 | Bot Detection | 8 clasificadores de bots | ~300 |
| 6 | Candlestick Patterns | 30+ patrones multi-timeframe | ~600 |
| 7 | Deep Analysis | LLM + rule-based fallback | ~400 |
| 8 | Cross-Correlation | P(outcome|trader+pattern+phase) | ~400 |
| 9 | Operability Score | Filtro fee-aware | ~300 |
| 10 | Smart Money Tracker | Flujo de smart money | ~300 |
| 11 | Buy/Sell Pressure | Presión compradora/vendedora | ~300 |
| 12 | Feedback Loop | Aprendizaje continuo | ~400 |

**Flujo del Brain**:
1. Recolección de datos multi-timeframe
2. Contexto de mercado (régimen + volatilidad)
3. Ciclo de vida del token (fase)
4. Comportamiento de traders
5. Inteligencia bot/whale
6. Score de operabilidad (fee-aware)
7. Señales predictivas
8. Patrones de velas
9. Análisis profundo (LLM)
10. Correlación cruzada
11. Decisión: TRADE / WATCH / AVOID / SKIP

### 4. Strategy Layer
- **Trading System Engine**: 7 templates predefinidos (Alpha Hunter, Smart Entry Mirror, etc.)
- **Signal Generators**: Generación de señales de entrada/salida
- **Strategy Evolution**: Auto-refinamiento con feedback loop
- **Capital Allocation**: **16 métodos** de dimensionamiento
- **Decision Engine**: Toma de decisión final

### 5. Risk Management
- **Risk Management Panel**: Dashboard con exposición, drawdown, P&L, concentración
- **Statistical Validation**: CI 95%, t-tests, p-values, power analysis, decay temporal
- **Walk-Forward Engine**: Anti-overfitting con validación out-of-sample
- **Operability Filter**: Filtro fee-aware antes de operar

### 6. Execution Layer
- **Paper Trading Engine**: Simulación con precios live, persistencia en DB
- **Backtest Engine**: Métricas completas (Sharpe, Sortino, Calmar, etc.)
- **Trade Execution Engine**: Preparado para ejecución real
- **Autonomous Execution Engine**: Ejecución autónoma

### 7. Storage (SQLite + Prisma 7)
- 25+ modelos de datos
- Tokens + DNA + Candles
- Traders + Transactions + Behavior
- Sessions + Positions + Trades
- Backtests + Operations
- Signals + Predictions + Feedback

### 8. UI Dashboard (React + shadcn/ui)
- AI Strategy Manager (componente principal ~3000 líneas)
- 20+ paneles de dashboard
- Datos en vivo via React Query
- Animaciones con Framer Motion

---

## Evaluación de Risk Management

### ✅ Lo que ESTÁ BIEN

1. **Risk Management Panel** (1199 líneas) — MUY COMPLETO
   - Risk Score Gauge (exposición + drawdown + concentración + dirección)
   - P&L Metrics (realized, unrealized, win rate, profit factor, expectancy)
   - Drawdown Analysis (current, max, recovery factor, time to recovery)
   - Concentration Analysis (by chain, by direction)
   - Trade Analysis (best/worst trade, avg hold time, MFE/MAE)
   - Equity Curve SVG con drawdown overlay
   - Risk Controls (max position, max portfolio risk, stop loss, daily loss limit)

2. **Capital Allocation Engine** (1096 líneas) — PROFESIONAL
   - 16 métodos con fórmulas reales documentadas
   - FIXED_FRACTIONAL, KELLY_MODIFIED, RISK_PARITY, etc.
   - Fee/slippage awareness post-processing
   - Portfolio optimization (Markowitz, Min Variance)
   - Custom Composite (combinar métodos con pesos)

3. **Statistical Validation** (921 líneas) — RIGUROSO
   - Wilson Score CI para proporciones
   - t-distribución para muestras pequeñas
   - Welch's t-test, Chi-square test
   - Power analysis, sample size calculation
   - Temporal decay (half-life configurable)
   - Validation gate obligatorio antes de predicciones

4. **Walk-Forward Engine** (655 líneas) — COMPLETO
   - Rolling y Anchored WFA
   - WFE calculation con weighted average
   - Parameter stability assessment
   - Robustness classification (ROBUST/MARGINAL/OVERFIT)
   - Scorecard generation

### ⚠️ Lo que NECESITA MEJORA

1. **Risk Controls Save** — Es un stub:
   ```typescript
   const saveControlsMutation = useMutation({
     mutationFn: async () => {
       await new Promise((r) => setTimeout(r, 500)); // FAKE!
       return true;
     },
   });
   ```
   **Fix**: Persistir a TradingSystem o configuración de usuario en DB.

2. **Risk Panel sin trades** — Muestra "empty state" si no hay trades:
   - No muestra métricas de riesgo preventivo sin actividad
   - Debería mostrar configuración de risk controls aunque no haya trades

3. **Solo LONG** — El sistema solo opera en LONG actualmente:
   - M3 bug: No hay soporte real para SHORT en paper trading
   - Risk panel muestra "100% LONG bias" sin advertencia útil

4. **No hay Monte Carlo** — No existe simulación Monte Carlo para riesgo

### Veredicto Risk Management: **7.5/10**

La capa de risk management está **bien estructurada y es profesional** en sus cálculos. Los 16 métodos de capital allocation son reales con fórmulas documentadas. La validación estadística es rigurosa. Lo que falta es:
- Persistir los risk controls
- Monte Carlo simulation
- Soporte SHORT real
- Risk preventivo sin trades

---

## Evaluación de Features Faltantes

### 1. Broker Connectors — ❌ NO EXISTE

**Estado actual**: No hay ninguna integración con exchanges reales.

**Lo que existe**:
- `trade-execution-engine.ts` (1019 líneas) — Arquitectura de ejecución
- `autonomous-execution-engine.ts` — Motor autónomo (preparado pero no conectado)
- `paper-trading-engine.ts` — Solo simula

**Lo que FALTA**:
- Conector Binance/OKX/Bybit para ejecución real
- Wallet management (Phantom, MetaMask, etc.)
- Order signing y broadcast
- Balance checking real
- Transaction confirmation

**Prioridad**: BAJA — La ejecución real va al FINAL según lo acordado

---

### 2. Portfolio Manager Multi-Cuenta — ❌ NO EXISTE

**Estado actual**: Un solo portfolio por usuario, sin multi-cuenta.

**Lo que existe**:
- Capital allocation por estrategia
- Paper trading con un solo capital
- Risk overview de un solo portfolio

**Lo que FALTA**:
- Múltiples wallets/cuentas por usuario
- Agregación cross-chain
- P&L consolidado multi-cuenta
- Asignación de capital entre cuentas
- Transferencias entre cuentas

**Prioridad**: MEDIA — Útil pero no crítico para MVP

---

### 3. Walk-Forward Optimization — ✅ YA EXISTE

**Estado**: COMPLETO y profesional (655 líneas)

- Rolling y Anchored WFA
- WFE con weighted average por trades
- Parameter stability score
- Classification: ROBUST / MARGINAL / OVERFIT / INSUFFICIENT_DATA
- Scorecard generation detallada
- API endpoint: `/api/backtest/walk-forward`

**Lo que podría mejorarse**:
- Integración con UI (no hay botón "Run WFA" visible en el panel)
- Persistencia de resultados WFA en DB
- Visualización gráfica de WFE por ventana

---

### 4. Monte Carlo Risk Simulator — ❌ NO EXISTE

**Estado actual**: No hay ninguna implementación de Monte Carlo.

**Lo que SÍ existe** que se puede aprovechar:
- Statistical Validation con distribuciones
- Backtesting Engine con equity curves
- Trade history con MFE/MAE

**Lo que FALTA crear**:
- Monte Carlo Engine que:
  1. Tome trades históricos como input
  2. Resamplear aleatoriamente N simulaciones (1000-10000)
  3. Calcular distribución de:
     - Max Drawdown (VaR y CVaR)
     - Ruin probability
     - Return distribution
     - Time to recovery
  4. Generar percentiles (5th, 25th, 50th, 75th, 95th)
  5. Calcular Probability of Ruin
  6. UI con fan chart de equity paths

**Prioridad**: ALTA — Esencial para gestión de riesgo profesional

---

## Resumen de Estado por Módulo

| Módulo | Estado | Completitud | Notas |
|--------|--------|-------------|-------|
| Data Sources | ✅ | 85% | Birdeye eliminado, resto funciona |
| Data Pipeline | ✅ | 90% | Multi-TF, backfill, cache |
| Brain Orchestrator | ✅ | 95% | 12 engines completos |
| Trading Systems | ✅ | 85% | 7 templates, falta UI de creación |
| Capital Allocation | ✅ | 95% | 16 métodos profesionales |
| Risk Management | ✅ | 75% | Falta Monte Carlo + persistir controls |
| Walk-Forward | ✅ | 90% | Completo, falta UI integration |
| Statistical Validation | ✅ | 95% | Riguroso |
| Paper Trading | ✅ | 80% | Funciona, solo LONG |
| Backtesting | ✅ | 85% | Métricas completas |
| Broker Connectors | ❌ | 0% | No existe, va al final |
| Portfolio Multi-Cuenta | ❌ | 0% | No existe |
| Monte Carlo | ❌ | 0% | Alta prioridad crear |
| UI Dashboard | ✅ | 80% | 20+ paneles, falta responsive |

---

## Bugs Pendientes

| ID | Bug | Severidad | Estado |
|----|-----|-----------|--------|
| M1 | Chain hardcodeado como SOL | Media | Pendiente |
| M2 | MAE calculado incorrectamente | Alta | Pendiente |
| M3 | Solo LONG, sin SHORT real | Alta | Pendiente |
| M4 | Math.random en DNA scores | Media | Pendiente |
| M5 | Risk controls no se persisten | Media | Pendiente |
| M6 | WFA sin botón en UI | Baja | Pendiente |
| M7 | Equity curve SVG puede colapsar con muchos puntos | Baja | Pendiente |

---

## Próximos Pasos (Orden de Prioridad)

1. **Crear Monte Carlo Risk Simulator** — Feature faltante de alta prioridad
2. **Fix M2: MAE incorrecto** — Afecta métricas de riesgo
3. **Fix M3: Soporte SHORT** — Limita estrategia
4. **Fix M5: Persistir Risk Controls** — Configuración se pierde
5. **Fix M1: Chain dinámico** — Multi-chain real
6. **Integrar WFA en UI** — Ya existe, solo necesita botón
7. **Broker Connectors** — Al FINAL
8. **Portfolio Multi-Cuenta** — Post-MVP
