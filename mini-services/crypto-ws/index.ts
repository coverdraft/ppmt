// @ts-nocheck
import { Server } from "socket.io";

const PORT = 3003;

// ============================================================
// DATA SOURCE CONFIGURATION
// ============================================================

type DataSourceMode = "live" | "simulated" | "hybrid";

const DATA_SOURCE: DataSourceMode =
  (process.env.DATA_SOURCE as DataSourceMode) || "hybrid";

console.log(`🔧 DATA_SOURCE mode: ${DATA_SOURCE}`);

const io = new Server(PORT, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"],
  },
});

// ============================================================
// TYPES
// ============================================================

interface TokenData {
  symbol: string;
  name: string;
  chain: string;
  price: number;
  priceChange5m: number;
  priceChange1h: number;
  priceChange24h: number;
  volume24h: number;
  liquidity: number;
  riskScore: number;
  botActivityPct: number;
  smartMoneyPct: number;
  /** Token address on chain, used for DexScreener lookups */
  address?: string;
  /** DexScreener pair address */
  pairAddress?: string;
  /** FDV from DexScreener */
  fdv?: number;
  /** Market cap from DexScreener */
  marketCap?: number;
}

interface DexScreenerPair {
  chainId: string;
  dexId: string;
  pairAddress: string;
  baseToken: { address: string; symbol: string; name: string };
  quoteToken: { address: string; symbol: string; name: string };
  priceNative: string;
  priceUsd: string;
  txns: {
    h24: { buys: number; sells: number };
    h6: { buys: number; sells: number };
    h1: { buys: number; sells: number };
  };
  volume: { h24: number; h6: number; h1: number };
  liquidity: { usd: number; base: number; quote: number };
  fdv: number;
  marketCap: number;
  pairCreatedAt: number;
  priceChange?: {
    m5: number;
    h1: number;
    h6: number;
    h24: number;
  };
}

// ============================================================
// SIMULATED TOKEN DATA (kept as fallback)
// ============================================================

const simulatedTokens: TokenData[] = [
  { symbol: "BONK", name: "Bonk", chain: "SOL", price: 0.00002847, priceChange5m: 2.3, priceChange1h: 5.1, priceChange24h: -3.2, volume24h: 23400000, liquidity: 8900000, riskScore: 35, botActivityPct: 22, smartMoneyPct: 8 },
  { symbol: "WIF", name: "dogwifhat", chain: "SOL", price: 2.34, priceChange5m: -0.8, priceChange1h: 3.4, priceChange24h: 12.5, volume24h: 89000000, liquidity: 45000000, riskScore: 25, botActivityPct: 15, smartMoneyPct: 18 },
  { symbol: "JUP", name: "Jupiter", chain: "SOL", price: 1.12, priceChange5m: 0.5, priceChange1h: -1.2, priceChange24h: 4.7, volume24h: 56000000, liquidity: 23000000, riskScore: 15, botActivityPct: 30, smartMoneyPct: 12 },
  { symbol: "PEPE", name: "Pepe", chain: "ETH", price: 0.00001234, priceChange5m: 4.5, priceChange1h: 8.9, priceChange24h: 23.1, volume24h: 345000000, liquidity: 89000000, riskScore: 40, botActivityPct: 35, smartMoneyPct: 10 },
  { symbol: "POPCAT", name: "Popcat", chain: "SOL", price: 1.45, priceChange5m: -2.1, priceChange1h: 1.3, priceChange24h: -5.6, volume24h: 12000000, liquidity: 6700000, riskScore: 45, botActivityPct: 28, smartMoneyPct: 5 },
  { symbol: "ORCA", name: "Orca", chain: "SOL", price: 3.87, priceChange5m: 0.3, priceChange1h: 2.1, priceChange24h: 7.8, volume24h: 34000000, liquidity: 12000000, riskScore: 20, botActivityPct: 18, smartMoneyPct: 22 },
  { symbol: "RAY", name: "Raydium", chain: "SOL", price: 2.45, priceChange5m: 1.2, priceChange1h: -0.5, priceChange24h: 3.4, volume24h: 28000000, liquidity: 15000000, riskScore: 18, botActivityPct: 20, smartMoneyPct: 15 },
  { symbol: "FLOKI", name: "Floki Inu", chain: "ETH", price: 0.000234, priceChange5m: -3.2, priceChange1h: -5.8, priceChange24h: -12.3, volume24h: 67000000, liquidity: 34000000, riskScore: 55, botActivityPct: 40, smartMoneyPct: 3 },
  { symbol: "BOME", name: "Book of Meme", chain: "SOL", price: 0.0112, priceChange5m: 6.7, priceChange1h: 15.3, priceChange24h: 34.5, volume24h: 45000000, liquidity: 12000000, riskScore: 60, botActivityPct: 45, smartMoneyPct: 4 },
  { symbol: "SLERF", name: "Slerf", chain: "SOL", price: 0.234, priceChange5m: -1.5, priceChange1h: -3.2, priceChange24h: -8.9, volume24h: 5600000, liquidity: 2300000, riskScore: 72, botActivityPct: 38, smartMoneyPct: 2 },
  { symbol: "JTO", name: "Jito", chain: "SOL", price: 3.21, priceChange5m: 0.8, priceChange1h: 1.9, priceChange24h: 5.6, volume24h: 19000000, liquidity: 8900000, riskScore: 22, botActivityPct: 12, smartMoneyPct: 20 },
  { symbol: "UNI", name: "Uniswap", chain: "ETH", price: 7.89, priceChange5m: -0.3, priceChange1h: 0.7, priceChange24h: 2.1, volume24h: 123000000, liquidity: 56000000, riskScore: 10, botActivityPct: 25, smartMoneyPct: 25 },
  { symbol: "AAVE", name: "Aave", chain: "ETH", price: 89.12, priceChange5m: 0.2, priceChange1h: 1.1, priceChange24h: 3.8, volume24h: 78000000, liquidity: 34000000, riskScore: 8, botActivityPct: 15, smartMoneyPct: 30 },
  { symbol: "LINK", name: "Chainlink", chain: "ETH", price: 14.56, priceChange5m: 1.1, priceChange1h: 2.3, priceChange24h: 5.1, volume24h: 95000000, liquidity: 42000000, riskScore: 12, botActivityPct: 20, smartMoneyPct: 22 },
  { symbol: "SHIB", name: "Shiba Inu", chain: "ETH", price: 0.00002567, priceChange5m: 3.4, priceChange1h: 7.8, priceChange24h: 15.6, volume24h: 234000000, liquidity: 78000000, riskScore: 38, botActivityPct: 32, smartMoneyPct: 8 },
  { symbol: "DRIFT", name: "Drift Protocol", chain: "SOL", price: 0.89, priceChange5m: 0.9, priceChange1h: -1.4, priceChange24h: 6.2, volume24h: 8900000, liquidity: 5600000, riskScore: 28, botActivityPct: 22, smartMoneyPct: 16 },
  { symbol: "PUMP", name: "Pump.fun", chain: "SOL", price: 0.00567, priceChange5m: -5.6, priceChange1h: -12.3, priceChange24h: -28.9, volume24h: 3400000, liquidity: 1200000, riskScore: 85, botActivityPct: 55, smartMoneyPct: 1 },
  { symbol: "TURBO", name: "Turbo", chain: "SOL", price: 0.00891, priceChange5m: 2.1, priceChange1h: 4.5, priceChange24h: 9.3, volume24h: 5600000, liquidity: 2300000, riskScore: 65, botActivityPct: 42, smartMoneyPct: 3 },
  { symbol: "GMX", name: "GMX", chain: "ETH", price: 34.56, priceChange5m: -0.4, priceChange1h: 0.9, priceChange24h: 1.5, volume24h: 45000000, liquidity: 23000000, riskScore: 14, botActivityPct: 18, smartMoneyPct: 24 },
  { symbol: "ARB", name: "Arbitrum", chain: "ETH", price: 1.12, priceChange5m: 0.7, priceChange1h: -0.3, priceChange24h: 4.2, volume24h: 67000000, liquidity: 34000000, riskScore: 16, botActivityPct: 22, smartMoneyPct: 20 },
];

// ============================================================
// SIGNAL TEMPLATES (kept as-is, no real signal API)
// ============================================================

const signalTemplates = [
  { type: "RUG_PULL", direction: "AVOID", descriptions: [
    "Liquidity removal detected - 40% of pool drained in last 5 blocks",
    "Creator wallet moving tokens to exchange - high probability exit scam",
    "Dev wallet distributing to multiple wallets - rug preparation pattern",
  ]},
  { type: "SMART_MONEY_ENTRY", direction: "LONG", descriptions: [
    "Top-10 smart money wallet accumulated 2.3% of supply",
    "3 wallets with >85% win rate entered within 10 minutes",
    "Institutional-grade accumulation pattern detected",
  ]},
  { type: "LIQUIDITY_TRAP", direction: "SHORT", descriptions: [
    "False breakout pattern - liquidity above range, stops likely to be hunted",
    "Concentrated liquidity at key level - stop hunt imminent",
  ]},
  { type: "V_SHAPE", direction: "LONG", descriptions: [
    "Sharp rejection from support with volume spike - V-shape forming",
    "Liquidation cascade complete - strong bid wall absorbing sells",
  ]},
  { type: "DIVERGENCE", direction: "LONG", descriptions: [
    "Price making lower lows while RSI making higher lows - bullish divergence",
    "On-chain divergence - price up but smart money exiting",
  ]},
  { type: "BOT_ACTIVITY_SPIKE", direction: "AVOID", descriptions: [
    "MEV bot activity increased 300% in last hour - frontrunning risk elevated",
    "Sniper bot detected entering within block 0 - 8 bots identified in first 3 blocks",
    "Sandwich attack pattern detected - 5 attacks in last 10 minutes",
  ]},
  { type: "WASH_TRADING_ALERT", direction: "AVOID", descriptions: [
    "Circular trading detected between 3 wallets - artificial volume inflation",
    "Self-trading pattern identified - wallet buying own sells to create fake volume",
  ]},
  { type: "WHALE_MOVEMENT", direction: "LONG", descriptions: [
    "Whale wallet transferred 500K tokens to exchange - potential sell pressure",
    "Large accumulation detected - 2M USD buy in single transaction",
  ]},
];

// ============================================================
// TRADER WALLETS (kept as-is)
// ============================================================

const smartMoneyWallets = [
  { label: "Galaxy Whale", address: "7xKX...gAsU", chain: "SOL", score: 85 },
  { label: "Institutional Hub", address: "0x742d...2bD18", chain: "ETH", score: 78 },
  { label: "Smart Accumulator", address: "7mK3...5jW", chain: "SOL", score: 72 },
  { label: "Alpha Wallet", address: "0xd8dA...6045", chain: "ETH", score: 88 },
  { label: "Institutional SOL Fund", address: "G7nJ...6iW", chain: "SOL", score: 91 },
];

const botWallets = [
  { label: "Jito MEV Bot #1", address: "9WzD...mkR7", chain: "SOL", botType: "MEV_EXTRACTOR", confidence: 0.92 },
  { label: "Sandwich Bot Sol", address: "2ZHJ...8sK", chain: "SOL", botType: "SANDWICH_BOT", confidence: 0.87 },
  { label: "MEV Builder Flashbots", address: "0x6b75...9c9c", chain: "ETH", botType: "MEV_EXTRACTOR", confidence: 0.95 },
  { label: "PEPE Sniper Bot", address: "0xA69b...6D62", chain: "ETH", botType: "SNIPER_BOT", confidence: 0.89 },
  { label: "Wash Trading Bot #1", address: "B1g5...3gV", chain: "SOL", botType: "WASH_TRADING_BOT", confidence: 0.78 },
  { label: "Arbitrage Scanner ETH", address: "0x3Ddf...c9f1", chain: "ETH", botType: "ARBITRAGE_BOT", confidence: 0.91 },
  { label: "JIT LP Bot", address: "Hk8d...6iR", chain: "SOL", botType: "JIT_LP_BOT", confidence: 0.85 },
  { label: "Copy Trader Pro", address: "CwiH...XhN", chain: "SOL", botType: "COPY_BOT", confidence: 0.73 },
];

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function randomBetween(min: number, max: number) {
  return Math.random() * (max - min) + min;
}

function randomChoice<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ============================================================
// LIVE TOKEN STATE
// ============================================================

/** The active token list - starts with simulated, may be replaced by live data */
let tokens: TokenData[] = [...simulatedTokens];

/** Whether we currently have live DexScreener data */
let usingLiveData = false;

/** Timestamp of last successful DexScreener fetch */
let lastLiveFetchTime = 0;

/** Price history for sparklines */
const priceHistories: Record<string, number[]> = {};
for (const t of tokens) {
  priceHistories[t.symbol] = Array.from({ length: 20 }, () =>
    t.price * randomBetween(0.92, 1.08)
  );
}

// ============================================================
// DEXSCREENER API CLIENT (standalone, no imports from Next.js)
// ============================================================

const DEXSCREENER_BASE = "https://api.dexscreener.com";

/** Search tokens on DexScreener */
async function dexScreenerSearch(query: string): Promise<DexScreenerPair[]> {
  try {
    const url = `${DEXSCREENER_BASE}/latest/dex/search?q=${encodeURIComponent(query)}`;
    const res = await fetch(url, {
      signal: AbortSignal.timeout(10000), // 10s timeout
    });
    if (!res.ok) throw new Error(`DexScreener search failed: ${res.status}`);
    const data = (await res.json()) as { pairs?: DexScreenerPair[] };
    return data.pairs || [];
  } catch (error) {
    console.error("❌ DexScreener search error:", error);
    return [];
  }
}

/** Get token data by address from DexScreener */
async function dexScreenerTokenLookup(address: string): Promise<DexScreenerPair[]> {
  try {
    const url = `${DEXSCREENER_BASE}/latest/dex/tokens/${encodeURIComponent(address)}`;
    const res = await fetch(url, {
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error(`DexScreener token lookup failed: ${res.status}`);
    const data = (await res.json()) as { pairs?: DexScreenerPair[] };
    return data.pairs || [];
  } catch (error) {
    console.error("❌ DexScreener token lookup error:", error);
    return [];
  }
}

/**
 * Fetch trending tokens from DexScreener and convert to our TokenData format.
 * Returns an array of TokenData with real prices from the API.
 */
async function fetchLiveTokenData(): Promise<TokenData[]> {
  try {
    const pairs = await dexScreenerSearch("trending");

    if (!pairs || pairs.length === 0) {
      console.warn("⚠️  DexScreener returned no trending pairs");
      return [];
    }

    // Deduplicate by symbol — keep the pair with highest liquidity
    const bestBySymbol = new Map<string, DexScreenerPair>();
    for (const pair of pairs) {
      const sym = pair.baseToken.symbol;
      const existing = bestBySymbol.get(sym);
      if (!existing || (pair.liquidity?.usd || 0) > (existing.liquidity?.usd || 0)) {
        bestBySymbol.set(sym, pair);
      }
    }

    const liveTokens: TokenData[] = [];
    let rank = 0;

    for (const pair of bestBySymbol.values()) {
      // Limit to 25 tokens max
      if (rank >= 25) break;

      const priceUsd = parseFloat(pair.priceUsd || "0");
      if (priceUsd <= 0) continue; // skip invalid prices

      // Map DexScreener chainId to our chain format
      const chainMap: Record<string, string> = {
        solana: "SOL",
        ethereum: "ETH",
        base: "ETH",
        arbitrum: "ETH",
        optimism: "ETH",
        polygon: "ETH",
        bsc: "BSC",
      };
      const chain = chainMap[pair.chainId] || pair.chainId.toUpperCase();

      // Compute risk score heuristic: lower liquidity + newer = higher risk
      const ageHours = pair.pairCreatedAt
        ? (Date.now() - pair.pairCreatedAt) / (1000 * 60 * 60)
        : 999;
      const liquidityUsd = pair.liquidity?.usd || 0;
      const riskScore = Math.min(99, Math.max(1, Math.round(
        80 - (liquidityUsd / 1e6) * 10 - Math.min(ageHours / 24, 20)
      )));

      // Bot/smart money percentages are estimated heuristics since DexScreener
      // doesn't provide these directly
      const botActivityPct = Math.min(60, Math.max(5, Math.round(
        30 - (liquidityUsd / 1e6) * 2 + (riskScore / 5)
      )));
      const smartMoneyPct = Math.min(35, Math.max(1, Math.round(
        20 - riskScore / 5 + (liquidityUsd > 1e7 ? 5 : 0)
      )));

      const token: TokenData = {
        symbol: pair.baseToken.symbol,
        name: pair.baseToken.name,
        chain,
        price: priceUsd,
        priceChange5m: pair.priceChange?.m5 ?? randomBetween(-2, 2),
        priceChange1h: pair.priceChange?.h1 ?? randomBetween(-5, 5),
        priceChange24h: pair.priceChange?.h24 ?? randomBetween(-15, 15),
        volume24h: pair.volume?.h24 ?? 0,
        liquidity: pair.liquidity?.usd ?? 0,
        riskScore,
        botActivityPct,
        smartMoneyPct,
        address: pair.baseToken.address,
        pairAddress: pair.pairAddress,
        fdv: pair.fdv,
        marketCap: pair.marketCap,
      };

      liveTokens.push(token);
      rank++;
    }

    return liveTokens;
  } catch (error) {
    console.error("❌ fetchLiveTokenData error:", error);
    return [];
  }
}

// ============================================================
// DATA MODE RESOLVER
// ============================================================

/**
 * Returns the dataMode that should be tagged on emitted events.
 * - If DATA_SOURCE is 'simulated' → always 'simulated'
 * - If DATA_SOURCE is 'live' → 'live' if we have live data, else 'simulated' (fallback)
 * - If DATA_SOURCE is 'hybrid' → 'live' if we have live data, else 'simulated' (fallback)
 */
function currentDataMode(): "live" | "simulated" {
  if (DATA_SOURCE === "simulated") return "simulated";
  // For 'live' and 'hybrid': if we actually have live data, tag as 'live'
  return usingLiveData ? "live" : "simulated";
}

// ============================================================
// REFRESH LIVE DATA FROM DEXSCREENER
// ============================================================

async function refreshLiveData(): Promise<boolean> {
  // Skip if in simulated-only mode
  if (DATA_SOURCE === "simulated") {
    usingLiveData = false;
    return false;
  }

  try {
    console.log("🔄 Refreshing live token data from DexScreener...");
    const liveTokens = await fetchLiveTokenData();

    if (liveTokens.length > 0) {
      // Merge live data into our token list:
      // - Replace existing tokens that match by symbol
      // - Add new tokens from DexScreener
      // - Keep simulated tokens that have no live equivalent
      const liveBySymbol = new Map<string, TokenData>();
      for (const t of liveTokens) {
        liveBySymbol.set(t.symbol.toUpperCase(), t);
      }

      const mergedTokens: TokenData[] = [];

      // First, go through simulated tokens and replace with live where available
      for (const sim of simulatedTokens) {
        const live = liveBySymbol.get(sim.symbol.toUpperCase());
        if (live) {
          mergedTokens.push(live);
          liveBySymbol.delete(sim.symbol.toUpperCase());
        } else {
          mergedTokens.push({ ...sim });
        }
      }

      // Then add any live tokens not in the simulated set
      for (const live of liveBySymbol.values()) {
        mergedTokens.push(live);
      }

      tokens = mergedTokens;

      // Initialize price histories for any new tokens
      for (const t of tokens) {
        if (!priceHistories[t.symbol]) {
          priceHistories[t.symbol] = Array.from({ length: 20 }, () =>
            t.price * randomBetween(0.97, 1.03)
          );
        }
      }

      usingLiveData = true;
      lastLiveFetchTime = Date.now();
      console.log(`✅ Refreshed ${liveTokens.length} live tokens from DexScreener (total: ${tokens.length})`);
      return true;
    } else {
      console.warn("⚠️  DexScreener returned empty data, keeping current token list");
      // If we've never had live data, stay on simulated
      if (!usingLiveData) {
        console.warn("⚠️  Falling back to simulated data");
      }
      return false;
    }
  } catch (error) {
    console.error("❌ refreshLiveData error:", error);
    console.warn("⚠️  Keeping current token data (live or simulated fallback)");
    return false;
  }
}

// ============================================================
// SIMULATED DATA GENERATORS (unchanged, kept as fallback)
// ============================================================

function simulatePriceUpdate() {
  const token = randomChoice(tokens);
  const changePercent = randomBetween(-2, 2);
  token.price *= (1 + changePercent / 100);
  token.priceChange5m += randomBetween(-1, 1);
  token.priceChange1h += randomBetween(-0.5, 0.5);
  token.priceChange24h += randomBetween(-0.2, 0.2);
  token.volume24h += randomBetween(-50000, 100000);

  if (priceHistories[token.symbol]) {
    priceHistories[token.symbol].push(token.price);
    if (priceHistories[token.symbol].length > 20) {
      priceHistories[token.symbol].shift();
    }
  }

  return {
    ...token,
    priceHistory: priceHistories[token.symbol],
  };
}

function simulateSignal() {
  const template = randomChoice(signalTemplates);
  const token = randomChoice(tokens);
  return {
    id: `sig_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`,
    type: template.type,
    tokenId: token.symbol,
    tokenSymbol: token.symbol,
    tokenPrice: token.price,
    chain: token.chain,
    confidence: Math.floor(randomBetween(35, 98)),
    direction: template.direction,
    description: randomChoice(template.descriptions),
    timestamp: Date.now(),
    priceTarget: token.price * randomBetween(0.8, 1.4),
    botInvolvement: template.type.includes('BOT') || template.type.includes('WASH'),
  };
}

function simulateSmartMoneyAlert() {
  const token = randomChoice(tokens);
  const wallet = randomChoice(smartMoneyWallets);
  return {
    id: `sma_${Date.now()}`,
    walletLabel: wallet.label,
    walletAddress: wallet.address,
    tokenSymbol: token.symbol,
    chain: token.chain,
    action: randomChoice(["BUY", "SELL"]),
    amount: randomBetween(10000, 500000),
    price: token.price,
    timestamp: Date.now(),
    smartMoneyScore: wallet.score,
    walletType: "SMART_MONEY",
  };
}

function simulateBotAlert() {
  const token = randomChoice(tokens);
  const bot = randomChoice(botWallets);
  const actions = bot.botType === 'SNIPER_BOT' ? ['BUY'] :
    bot.botType === 'WASH_TRADING_BOT' ? ['BUY', 'SELL'] :
    bot.botType === 'SANDWICH_BOT' ? ['BUY', 'SELL'] :
    bot.botType === 'ARBITRAGE_BOT' ? ['SWAP'] :
    ['BUY', 'SELL'];

  return {
    id: `bot_${Date.now()}`,
    botLabel: bot.label,
    botAddress: bot.address,
    botType: bot.botType,
    confidence: bot.confidence,
    tokenSymbol: token.symbol,
    chain: token.chain,
    action: randomChoice(actions),
    amount: randomBetween(5000, 200000),
    price: token.price,
    timestamp: Date.now(),
    mevExtracted: bot.botType === 'MEV_EXTRACTOR' ? randomBetween(10, 500) : 0,
    isFrontrun: bot.botType === 'MEV_EXTRACTOR' || bot.botType === 'SANDWICH_BOT',
    isSandwich: bot.botType === 'SANDWICH_BOT',
    isWashTrade: bot.botType === 'WASH_TRADING_BOT',
    slippageBps: bot.botType === 'ARBITRAGE_BOT' ? Math.floor(randomBetween(1, 5)) : Math.floor(randomBetween(20, 300)),
  };
}

function simulatePriceAlert() {
  const token = randomChoice(tokens);
  return {
    id: `pa_${Date.now()}`,
    tokenSymbol: token.symbol,
    chain: token.chain,
    price: token.price,
    change: randomBetween(-10, 10),
    alertType: randomChoice(["BREAKOUT", "BREAKDOWN", "SUPPORT_TEST", "RESISTANCE_TEST"]),
    timestamp: Date.now(),
  };
}

function simulateTraderStats() {
  const totalTraders = 50 + Math.floor(randomBetween(-2, 5));
  const totalBots = botWallets.length + Math.floor(randomBetween(0, 3));
  const totalSmartMoney = smartMoneyWallets.length;

  return {
    totalTraders,
    totalBots,
    totalSmartMoney,
    totalWhales: 4,
    totalSnipers: Math.floor(randomBetween(3, 8)),
    avgWinRate: randomBetween(0.42, 0.58),
    totalVolume: randomBetween(15000000, 25000000),
    totalMevExtracted: randomBetween(80000, 150000),
    totalFrontruns: Math.floor(randomBetween(200, 500)),
    totalSandwiches: Math.floor(randomBetween(50, 150)),
    botTypeBreakdown: {
      MEV_EXTRACTOR: Math.floor(randomBetween(4, 8)),
      SNIPER_BOT: Math.floor(randomBetween(5, 12)),
      SANDWICH_BOT: Math.floor(randomBetween(3, 6)),
      COPY_BOT: Math.floor(randomBetween(2, 5)),
      WASH_TRADING_BOT: Math.floor(randomBetween(1, 3)),
      ARBITRAGE_BOT: Math.floor(randomBetween(2, 4)),
      JIT_LP_BOT: Math.floor(randomBetween(1, 3)),
    },
    chainBreakdown: {
      SOL: { traders: Math.floor(totalTraders * 0.52), bots: Math.floor(totalBots * 0.6) },
      ETH: { traders: Math.floor(totalTraders * 0.38), bots: Math.floor(totalBots * 0.35) },
      BASE: { traders: Math.floor(totalTraders * 0.06), bots: Math.floor(totalBots * 0.03) },
      ARB: { traders: Math.floor(totalTraders * 0.04), bots: Math.floor(totalBots * 0.02) },
    },
  };
}

// ============================================================
// HYBRID PRICE UPDATE
// ============================================================

/**
 * In hybrid/live mode, applies small noise to real base prices.
 * In simulated mode, uses the original simulatePriceUpdate logic.
 */
function hybridPriceUpdate() {
  const token = randomChoice(tokens);

  if (usingLiveData && DATA_SOURCE !== "simulated") {
    // Use real price as base, apply tiny noise (±0.3%)
    const noisePercent = randomBetween(-0.3, 0.3);
    token.price *= (1 + noisePercent / 100);
    token.priceChange5m += randomBetween(-0.3, 0.3);
    token.priceChange1h += randomBetween(-0.15, 0.15);
    token.priceChange24h += randomBetween(-0.05, 0.05);
    token.volume24h += randomBetween(-10000, 20000);
  } else {
    // Full simulation mode
    const changePercent = randomBetween(-2, 2);
    token.price *= (1 + changePercent / 100);
    token.priceChange5m += randomBetween(-1, 1);
    token.priceChange1h += randomBetween(-0.5, 0.5);
    token.priceChange24h += randomBetween(-0.2, 0.2);
    token.volume24h += randomBetween(-50000, 100000);
  }

  if (priceHistories[token.symbol]) {
    priceHistories[token.symbol].push(token.price);
    if (priceHistories[token.symbol].length > 20) {
      priceHistories[token.symbol].shift();
    }
  }

  return {
    ...token,
    priceHistory: priceHistories[token.symbol],
  };
}

// ============================================================
// HYBRID MARKET SUMMARY
// ============================================================

/**
 * In hybrid/live mode, try to compute market summary from real token data.
 * Falls back to simulated values if no live data.
 */
function hybridMarketSummary() {
  if (usingLiveData && DATA_SOURCE !== "simulated") {
    // Try to extract BTC/ETH from live token data
    const btcToken = tokens.find(t => t.symbol.toUpperCase() === "BTC" || t.symbol.toUpperCase() === "WBTC");
    const ethToken = tokens.find(t => t.symbol.toUpperCase() === "ETH" || t.symbol.toUpperCase() === "WETH");

    const btcPrice = btcToken?.price || 67500 + randomBetween(-500, 500);
    const ethPrice = ethToken?.price || 3450 + randomBetween(-50, 50);

    // Compute total volume from all live tokens
    const totalVolume = tokens.reduce((sum, t) => sum + (t.volume24h || 0), 0);

    // Estimate total market cap (very rough: sum of FDVs)
    const totalMarketCap = tokens.reduce((sum, t) => sum + (t.fdv || t.marketCap || 0), 0);

    return {
      btcPrice: typeof btcPrice === "number" ? btcPrice : 67500,
      ethPrice: typeof ethPrice === "number" ? ethPrice : 3450,
      totalMarketCap: totalMarketCap > 0 ? totalMarketCap : 2.45e12 + randomBetween(-1e10, 1e10),
      totalVolume24h: totalVolume,
      fearGreedIndex: Math.floor(randomBetween(25, 75)),
      liveTokenCount: tokens.length,
    };
  }

  // Simulated fallback
  return {
    btcPrice: 67500 + randomBetween(-500, 500),
    ethPrice: 3450 + randomBetween(-50, 50),
    totalMarketCap: 2.45e12 + randomBetween(-1e10, 1e10),
    fearGreedIndex: Math.floor(randomBetween(25, 75)),
  };
}

// ============================================================
// STARTUP: INITIAL DATA FETCH
// ============================================================

(async () => {
  console.log(`🚀 Crypto WebSocket server running on port ${PORT}`);
  console.log(`📡 Events: token-update, new-signal, smart-money-alert, bot-alert, trader-stats, price-alert, market-summary`);
  console.log(`🔧 DATA_SOURCE: ${DATA_SOURCE}`);

  if (DATA_SOURCE !== "simulated") {
    console.log("🌐 Attempting initial DexScreener data fetch...");
    const success = await refreshLiveData();
    if (success) {
      console.log("✅ Initial live data loaded successfully");
    } else {
      console.log("⚠️  Could not load live data, using simulated data as fallback");
    }
  } else {
    console.log("📊 Running in simulated-only mode — no external API calls");
  }
})();

// ============================================================
// PERIODIC REFRESH: Fetch DexScreener data every 30 seconds
// ============================================================

if (DATA_SOURCE !== "simulated") {
  setInterval(async () => {
    try {
      await refreshLiveData();
    } catch (error) {
      console.error("❌ Periodic refresh error (non-fatal):", error);
    }
  }, 30000);
}

// ============================================================
// SOCKET.IO CONNECTIONS
// ============================================================

io.on("connection", (socket) => {
  console.log(`📱 Client connected: ${socket.id}`);

  // Send initial data with dataMode tag
  socket.emit("initial-data", {
    tokens: tokens.map(t => ({
      ...t,
      priceHistory: priceHistories[t.symbol],
    })),
    traderStats: simulateTraderStats(),
    dataMode: currentDataMode(),
    dataSource: DATA_SOURCE,
    lastLiveFetchTime,
  });

  socket.on("disconnect", () => {
    console.log(`📱 Client disconnected: ${socket.id}`);
  });
});

// ============================================================
// BRAIN EVENT BRIDGE
// ============================================================

/**
 * Push a real Brain signal to all connected clients.
 * Called by the Brain Scheduler when a cycle produces signals.
 */
function emitBrainSignal(signal: { type: string; tokenSymbol?: string; tokenAddress?: string; chain?: string; confidence: number; direction: string; description: string; [key: string]: unknown }) {
  io.emit("new-signal", {
    id: `brain_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`,
    ...signal,
    timestamp: Date.now(),
    dataMode: "brain" as const,
    source: "brain",
  });
  console.log(`🧠 Brain signal emitted: ${signal.type} [${signal.direction}]`);
}

/**
 * Push a Brain cycle completion event.
 */
function emitBrainCycleCompleted(data: { cyclesCompleted: number; tokensScanned: number; signalsGenerated: number; [key: string]: unknown }) {
  io.emit("brain-cycle", {
    ...data,
    timestamp: Date.now(),
  });
  console.log(`🧠 Brain cycle #${data.cyclesCompleted} completed: ${data.tokensScanned} scanned, ${data.signalsGenerated} signals`);
}

/**
 * Push a scheduler status change event.
 */
function emitSchedulerStatus(data: { status: string; uptime?: number; [key: string]: unknown }) {
  io.emit("scheduler-status", {
    ...data,
    timestamp: Date.now(),
  });
}

// Expose brain event emitters via a simple HTTP API
// so the Next.js backend can push events to WS clients
import { createServer } from "http";

const INTERNAL_PORT = 3010;

const internalServer = createServer(async (req, res) => {
  if (req.method !== "POST") {
    res.writeHead(405);
    res.end("Method Not Allowed");
    return;
  }

  let body = "";
  for await (const chunk of req) {
    body += chunk;
  }

  try {
    const event = JSON.parse(body);
    
    switch (event.type) {
      case "brain-signal":
        emitBrainSignal(event.data);
        break;
      case "brain-cycle":
        emitBrainCycleCompleted(event.data);
        break;
      case "scheduler-status":
        emitSchedulerStatus(event.data);
        break;
      case "alert":
        io.emit("alert", event.data);
        console.log(`🔔 Alert emitted: ${event.data?.title || 'unknown'}`);
        break;
      default:
        console.warn(`⚠️  Unknown internal event type: ${event.type}`);
    }
    
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
  } catch (error) {
    console.error("❌ Internal event parse error:", error);
    res.writeHead(400);
    res.end("Bad Request");
  }
});

internalServer.listen(INTERNAL_PORT, () => {
  console.log(`🔌 Brain event bridge listening on port ${INTERNAL_PORT}`);
});

// ============================================================
// EMIT INTERVALS
// ============================================================

// Token updates every 1.5 seconds
setInterval(() => {
  const update = hybridPriceUpdate();
  io.emit("token-update", {
    ...update,
    dataMode: currentDataMode(),
  });
}, 1500);

// New signals every 8 seconds (always simulated — no real signal API)
setInterval(() => {
  const signal = simulateSignal();
  io.emit("new-signal", {
    ...signal,
    dataMode: "simulated" as const,
  });
}, 8000);

// Smart money alerts every 12 seconds (always simulated)
setInterval(() => {
  const alert = simulateSmartMoneyAlert();
  io.emit("smart-money-alert", {
    ...alert,
    dataMode: "simulated" as const,
  });
}, 12000);

// Bot activity alerts every 10 seconds (always simulated)
setInterval(() => {
  const alert = simulateBotAlert();
  io.emit("bot-alert", {
    ...alert,
    dataMode: "simulated" as const,
  });
}, 10000);

// Price alerts every 10 seconds
setInterval(() => {
  const alert = simulatePriceAlert();
  io.emit("price-alert", {
    ...alert,
    dataMode: currentDataMode(),
  });
}, 10000);

// Market summary updates every 5 seconds
setInterval(() => {
  const summary = hybridMarketSummary();
  io.emit("market-summary", {
    ...summary,
    dataMode: currentDataMode(),
  });
}, 5000);

// Trader stats updates every 15 seconds (always simulated)
setInterval(() => {
  const stats = simulateTraderStats();
  io.emit("trader-stats", {
    ...stats,
    dataMode: "simulated" as const,
  });
}, 15000);
