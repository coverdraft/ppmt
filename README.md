# CryptoQuant Terminal

Terminal profesional de trading cuantitativo para criptomonedas con datos en tiempo real, señales IA, gestión de riesgo y backtesting.

Built with **Next.js 16** + **React 19** + **TypeScript** + **Tailwind CSS 4** + **shadcn/ui** + **Prisma** + **SQLite**.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/coverdraft/cryptoquant-terminal.git
cd cryptoquant-terminal
npm install        # postinstall auto-generates Prisma client

# Setup database
npx prisma db push --schema=./prisma/schema.prisma

# Start dev server
npm run dev
```

Open **http://localhost:3000**

---

## Dos Modos de Operación

El terminal funciona en **dos modos** distintos, cada uno con su pipeline de 6 pasos:

### 🔬 Modo Investigación (Backtesting & Validación)

Pipeline para investigar, diseñar, validar y optimizar estrategias con datos históricos. **No se opera con dinero real.**

| Paso | Nombre | Qué haces | Pestañas que usas |
|------|--------|-----------|-------------------|
| 1 | **ESCANEAR** | Descubrir tokens y entender el mercado | Dashboard, Multi-Chain, Market Regime |
| 2 | **ANALIZAR** | Brain analiza tokens, genera señales | Brain, Signals, Alpha Rank, Deep Analysis |
| 3 | **FILTRAR** | Risk pre-filter elimina señales peligrosas | Pre-Filter, Kill Switches |
| 4 | **DISEÑAR** | Crear trading systems | Strategy Lab, Patterns |
| 5 | **BACKTEST** | Validar con datos históricos | Backtesting, Walk-Forward, Monte Carlo |
| 6 | **OPTIMIZAR** | Evolucionar y ajustar estrategias | Meta-Model, Evolution Tree |

### 🚀 Modo Operación (Paper Trading & Ejecución)

Pipeline para poner a trabajar las estrategias validadas con precios reales.

| Paso | Nombre | Qué haces | Pestañas que usas |
|------|--------|-----------|-------------------|
| 1 | **SELECCIONAR** | Elegir estrategias validadas | Strategy Lab |
| 2 | **CONFIGURAR** | Definir risk controls y capital | Kill Switches, Capital Alloc, Risk |
| 3 | **PAPER TRADE** | Simular con precios reales | Paper Trading |
| 4 | **MONITOREAR** | Vigilar posiciones y riesgo | Portfolio, Portfolio AI, Risk |
| 5 | **EJECUTAR** | Ejecutar trades | Execution Cost, Execution |
| 6 | **CONTROLAR** | Controles de emergencia | Event Bus, SDE Decisions |

**Regla de oro:** Nunca pases al Modo Operación sin completar el Backtesting (Paso 5) con resultados positivos.

---

## Data Seeding (First Time)

```bash
# 1. Load tokens from CoinGecko + DexScreener (~5,000 tokens)
curl -X POST http://localhost:3000/api/seed

# 2. Initialize Brain
curl http://localhost:3000/api/brain/init

# 3. Start Brain Cycle (generates signals + analysis)
curl -X POST http://localhost:3000/api/brain/pipeline -H "Content-Type: application/json" -d '{"chain":"SOL","limit":50}'
```

---

## 26 Tabs Across 5 Groups

### 📡 MARKET
| Tab | Shortcut | Description |
|-----|----------|-------------|
| Dashboard | 1 | Live token feed & prices |
| Charts | 2 | OHLCV candlestick charts |
| Multi-Chain | 3 | Cross-chain comparison |
| Market Regime | g | Regime detection (BULL/BEAR/SIDEWAYS/CRISIS) |

### 🧠 INTELLIGENCE
| Tab | Shortcut | Description |
|-----|----------|-------------|
| Signals | 4 | Live signal feed |
| Brain | 5 | Control center (start/stop cycles) |
| Meta-Model | m | Engine performance & weights |
| Alpha Rank | a | Top alpha opportunities |
| Smart Money | 6 | Trader intelligence |
| Deep Analysis | 7 | Deep token analysis |
| DNA Scanner | 8 | Token DNA analysis |
| Predictive | 9 | AI predictions |

### 🛡️ RISK & PORTFOLIO
| Tab | Shortcut | Description |
|-----|----------|-------------|
| Pre-Filter | f | Risk pre-filter for signals |
| Kill Switches | r | Emergency kill switches |
| Risk | u | Risk management & Monte Carlo |
| Portfolio | y | Portfolio view |
| Portfolio AI | b | Impact analysis & optimization |
| Capital Alloc | t | Capital allocation (Kelly Modified) |
| SDE Decisions | i | Strategic decision engine |
| Exec Cost | p | Execution cost estimator |

### ⚙️ STRATEGY
| Tab | Shortcut | Description |
|-----|----------|-------------|
| Strategy Lab | 0 | Trading system lab & AI optimizer |
| Backtesting | q | Strategy backtesting |
| Paper Trading | w | Simulated trading with real prices |
| Patterns | e | Pattern builder & detection |

### 🔧 SYSTEM
| Tab | Shortcut | Description |
|-----|----------|-------------|
| Event Bus | v | Real-time event monitor |
| Export/Import | o | Export & import data |

---

## API Endpoints (70+)

All endpoints tested and verified:

| Category | Endpoints | Status |
|----------|-----------|--------|
| Brain | `/api/brain/*` (30+ actions) | ✅ Working |
| Market | `/api/market/summary`, `/api/market/tokens`, `/api/market/ohlcv` | ✅ Live data |
| Signals | `/api/signals`, `/api/alpha/ranking`, `/api/predictive` | ✅ Working |
| Risk | `/api/risk/pre-filter`, `/api/risk/overview`, `/api/kill-switch` | ✅ Working |
| Portfolio | `/api/portfolio/intelligence`, `/api/portfolio/optimize` | ✅ Working |
| Strategy | `/api/trading-systems`, `/api/strategy-states`, `/api/strategy-evolution` | ✅ Working |
| Backtesting | `/api/backtest`, `/api/backtest/walk-forward` | ✅ Working |
| Execution | `/api/execution/cost`, `/api/execution/positions` | ✅ Working |
| Capital | `/api/capital-allocation`, `/api/capital-allocation/dashboard` | ✅ Working |
| Regime | `/api/regime/assess` | ✅ Returns TRENDING_BULL (73% confidence) |

---

## Environment Variables

```env
# Database (auto-configured)
DATABASE_URL="file:./db/custom.db"

# Free APIs (no key needed)
# DexScreener - no API key required
# DexPaprika - no API key required
# CoinGecko - free tier available

# Optional API keys
COINGECKO_API_KEY=""
ETHERSCAN_API_KEY=""           # Ethereum wallet data
HELIUS_API_KEY=""              # Solana wallet transactions

# Auth (currently disabled)
NEXTAUTH_SECRET=""
NEXTAUTH_URL="http://localhost:3000"
```

---

## Key Commands

```bash
# Development
npm run dev                    # Start dev server on port 3000

# Database
npx prisma generate --schema=./prisma/schema.prisma   # Generate Prisma client
npx prisma db push --schema=./prisma/schema.prisma    # Push schema to DB
npx prisma studio              # Open DB browser

# Setup (one command)
npm run setup                  # Install + generate + push

# Production
npm run build && npm start     # Build and serve
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Could not find Prisma Schema` | Run `npx prisma generate --schema=./prisma/schema.prisma` |
| `EADDRINUSE: port 3000` | `lsof -ti:3000 \| xargs kill -9` then restart |
| Empty token table | Run `curl -X POST http://localhost:3000/api/seed` |
| 0 signals | Start Brain cycle: `curl -X POST http://localhost:3000/api/brain/pipeline -H "Content-Type: application/json" -d '{"chain":"SOL","limit":50}'` |
| npm peer dependency warnings | Safe to ignore — TypeScript 6 vs ESLint peer dep conflict |
| 19 npm vulnerabilities | Run `npm audit` to check — most are dev dependency warnings |

---

## Tech Stack

- **Frontend**: Next.js 16.1.3, React 19, TypeScript 6, Tailwind CSS 4, shadcn/ui, Recharts, Framer Motion, Zustand, TanStack Query
- **Backend**: Next.js API Routes, Prisma 6.19, SQLite
- **Real-time**: Socket.IO (WebSocket bridge)
- **Data**: CoinGecko, DexScreener, DexPaprika, Binance (all free)
- **Services**: 40+ service modules (Brain, Strategy, Risk, Execution, Portfolio, Feature Store)
- **Database**: 47 Prisma models

---

## License

Private — All rights reserved.
