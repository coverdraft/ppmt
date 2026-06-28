/**
 * LivePriceFeed — Multi-exchange real-time crypto prices.
 *
 * Chain of fallbacks (Spain-friendly — no Binance.com geo-block issues):
 *   1. Coinbase WebSocket (wss://ws-feed.exchange.coinbase.com) — primary,
 *      gives us real-time ticker updates for any USD pair. Not geo-blocked.
 *   2. Kraken REST polling (api.kraken.com/0/public/Ticker) — fallback #1,
 *      polls top liquid pairs every 10s when WS is down.
 *   3. CoinGecko REST polling (api.coingecko.com/api/v3/coins/markets) —
 *      fallback #2, gives us 24h change % and volume for ALL tokens in one
 *      call. Also not geo-blocked.
 *
 * The engine calls getData(symbol) and expects:
 *   - price: last trade price
 *   - changePct: 24h change %
 *   - quoteVolume: 24h quote volume in USD
 *
 * Symbol convention:
 *   - Internal: "BTC/USDT" (kept for backwards compat with engine + UI)
 *   - Coinbase: "BTC-USD"
 *   - Kraken:   "XXBTZUSD"  (sigh) — handled via AssetPairs endpoint
 *   - CoinGecko: identified by coin id ("bitcoin", "ethereum", ...)
 */

export interface TickerData {
  symbol: string        // normalized: "BTC/USDT"
  rawSymbol: string     // exchange format
  price: number
  changePct: number     // 24h price change %
  volume: number        // 24h base volume
  quoteVolume: number   // 24h quote volume (USD)
  high: number          // 24h high
  low: number           // 24h low
  timestamp: number
}

// ─── Token metadata: internal symbol → exchange identifiers ───
interface TokenMeta {
  internal: string      // "BTC/USDT"
  coinbase: string | null  // "BTC-USD" or null
  kraken: string | null    // "XXBTZUSD" or null
  coingecko: string | null // "bitcoin" or null
  name: string
}

// 80 liquid USD pairs available across Coinbase + Kraken + CoinGecko
// (we call it "USDT" internally for backward-compat with existing UI)
const TOKEN_META: TokenMeta[] = [
  // ─── Mega cap ───
  { internal: 'BTC/USDT',   coinbase: 'BTC-USD',    kraken: 'XXBTZUSD',  coingecko: 'bitcoin',                name: 'Bitcoin' },
  { internal: 'ETH/USDT',   coinbase: 'ETH-USD',    kraken: 'XETHZUSD',  coingecko: 'ethereum',               name: 'Ethereum' },
  { internal: 'BNB/USDT',   coinbase: null,         kraken: null,        coingecko: 'binancecoin',            name: 'BNB' },
  { internal: 'SOL/USDT',   coinbase: 'SOL-USD',    kraken: 'SOLUSD',    coingecko: 'solana',                 name: 'Solana' },
  { internal: 'XRP/USDT',   coinbase: 'XRP-USD',    kraken: 'XXRPZUSD',  coingecko: 'ripple',                 name: 'Ripple' },
  { internal: 'ADA/USDT',   coinbase: 'ADA-USD',    kraken: 'ADAUSD',    coingecko: 'cardano',                name: 'Cardano' },
  { internal: 'AVAX/USDT',  coinbase: 'AVAX-USD',   kraken: 'AVAXUSD',   coingecko: 'avalanche-2',            name: 'Avalanche' },
  { internal: 'DOGE/USDT',  coinbase: 'DOGE-USD',   kraken: 'XDGUSD',    coingecko: 'dogecoin',               name: 'Dogecoin' },
  { internal: 'DOT/USDT',   coinbase: 'DOT-USD',    kraken: 'DOTUSD',    coingecko: 'polkadot',               name: 'Polkadot' },
  { internal: 'LINK/USDT',  coinbase: 'LINK-USD',   kraken: 'LINKUSD',   coingecko: 'chainlink',              name: 'Chainlink' },
  { internal: 'MATIC/USDT', coinbase: 'MATIC-USD',  kraken: 'MATICUSD',  coingecko: 'matic-network',          name: 'Polygon' },
  { internal: 'TRX/USDT',   coinbase: null,         kraken: null,        coingecko: 'tron',                   name: 'TRON' },
  { internal: 'LTC/USDT',   coinbase: 'LTC-USD',    kraken: 'XLTCZUSD',  coingecko: 'litecoin',               name: 'Litecoin' },
  { internal: 'BCH/USDT',   coinbase: 'BCH-USD',    kraken: 'BCHUSD',    coingecko: 'bitcoin-cash',           name: 'Bitcoin Cash' },
  { internal: 'ATOM/USDT',  coinbase: 'ATOM-USD',   kraken: 'ATOMUSD',   coingecko: 'cosmos',                 name: 'Cosmos' },
  { internal: 'XLM/USDT',   coinbase: 'XLM-USD',    kraken: 'XXLMZUSD',  coingecko: 'stellar',                name: 'Stellar' },
  { internal: 'NEAR/USDT',  coinbase: 'NEAR-USD',   kraken: 'NEARUSD',   coingecko: 'near',                   name: 'Near' },
  { internal: 'APT/USDT',   coinbase: 'APT-USD',    kraken: 'APTUSD',    coingecko: 'aptos',                  name: 'Aptos' },
  { internal: 'ARB/USDT',   coinbase: 'ARB-USD',    kraken: 'ARBUSD',    coingecko: 'arbitrum',               name: 'Arbitrum' },
  { internal: 'OP/USDT',    coinbase: 'OP-USD',     kraken: 'OPUSD',     coingecko: 'optimism',               name: 'Optimism' },
  { internal: 'INJ/USDT',   coinbase: 'INJ-USD',    kraken: 'INJUSD',    coingecko: 'injective-protocol',     name: 'Injective' },
  { internal: 'FIL/USDT',   coinbase: 'FIL-USD',    kraken: 'FILUSD',    coingecko: 'filecoin',               name: 'Filecoin' },
  { internal: 'AAVE/USDT',  coinbase: 'AAVE-USD',   kraken: 'AAVEUSD',   coingecko: 'aave',                   name: 'Aave' },
  { internal: 'MKR/USDT',   coinbase: 'MKR-USD',    kraken: 'MKRUSD',    coingecko: 'maker',                  name: 'Maker' },
  { internal: 'SUI/USDT',   coinbase: 'SUI-USD',    kraken: 'SUIUSD',    coingecko: 'sui',                    name: 'Sui' },
  { internal: 'TIA/USDT',   coinbase: 'TIA-USD',    kraken: 'TIAUSD',    coingecko: 'celestia',               name: 'Celestia' },
  { internal: 'RUNE/USDT',  coinbase: null,         kraken: 'RUNEUSD',   coingecko: 'thorchain',              name: 'Thorchain' },
  { internal: 'FTM/USDT',   coinbase: null,         kraken: 'FTMUSD',    coingecko: 'fantom',                 name: 'Fantom' },
  { internal: 'SEI/USDT',   coinbase: 'SEI-USD',    kraken: 'SEIUSD',    coingecko: 'sei-network',            name: 'Sei' },
  { internal: 'STX/USDT',   coinbase: 'STX-USD',    kraken: null,        coingecko: 'blockstack',             name: 'Stacks' },
  { internal: 'IMX/USDT',   coinbase: 'IMX-USD',    kraken: 'IMXUSD',    coingecko: 'immutable-x',            name: 'Immutable' },
  { internal: 'GRT/USDT',   coinbase: 'GRT-USD',    kraken: 'GRTUSD',    coingecko: 'the-graph',              name: 'The Graph' },
  { internal: 'LDO/USDT',   coinbase: 'LDO-USD',    kraken: 'LDOUSD',    coingecko: 'lido-dao',               name: 'Lido DAO' },
  { internal: 'SAND/USDT',  coinbase: 'SAND-USD',   kraken: 'SANDUSD',   coingecko: 'the-sandbox',            name: 'Sandbox' },
  { internal: 'MANA/USDT',  coinbase: 'MANA-USD',   kraken: 'MANAUSD',   coingecko: 'decentraland',           name: 'Decentraland' },
  { internal: 'AXS/USDT',   coinbase: 'AXS-USD',    kraken: 'AXSUSD',    coingecko: 'axie-infinity',          name: 'Axie Infinity' },
  { internal: 'GALA/USDT',  coinbase: null,         kraken: null,        coingecko: 'gala',                   name: 'Gala' },
  { internal: 'CHZ/USDT',   coinbase: null,         kraken: null,        coingecko: 'chiliz',                 name: 'Chiliz' },
  { internal: 'ENJ/USDT',   coinbase: null,         kraken: null,        coingecko: 'enjincoin',              name: 'Enjin' },
  { internal: 'PEPE/USDT',  coinbase: 'PEPE-USD',   kraken: null,        coingecko: 'pepe',                   name: 'Pepe' },
  { internal: 'WIF/USDT',   coinbase: 'WIF-USD',    kraken: null,        coingecko: 'dogwifhat',              name: 'dogwifhat' },
  { internal: 'BONK/USDT',  coinbase: null,         kraken: null,        coingecko: 'bonk',                   name: 'Bonk' },
  { internal: 'FLOKI/USDT', coinbase: null,         kraken: null,        coingecko: 'floki',                  name: 'Floki' },
  { internal: 'SHIB/USDT',  coinbase: 'SHIB-USD',   kraken: null,        coingecko: 'shiba-inu',              name: 'Shiba Inu' },
  { internal: 'PYTH/USDT',  coinbase: 'PYTH-USD',   kraken: null,        coingecko: 'pyth-network',           name: 'Pyth Network' },
  { internal: 'JTO/USDT',   coinbase: 'JTO-USD',    kraken: null,        coingecko: 'jito-governance-token',  name: 'Jito' },
  { internal: 'ORDI/USDT',  coinbase: null,         kraken: null,        coingecko: 'ordi',                   name: 'Ordinals' },
  { internal: 'RNDR/USDT',  coinbase: 'RNDR-USD',   kraken: null,        coingecko: 'render-token',           name: 'Render' },
  { internal: 'FET/USDT',   coinbase: null,         kraken: null,        coingecko: 'fetch-ai',               name: 'Fetch.ai' },
  { internal: 'AGIX/USDT',  coinbase: null,         kraken: null,        coingecko: 'singularitynet',         name: 'SingularityNET' },
  { internal: 'OCEAN/USDT', coinbase: null,         kraken: null,        coingecko: 'ocean-protocol',         name: 'Ocean Protocol' },
  { internal: 'THETA/USDT', coinbase: null,         kraken: null,        coingecko: 'theta-token',            name: 'Theta' },
  { internal: 'ICP/USDT',   coinbase: 'ICP-USD',    kraken: 'ICPUSD',    coingecko: 'internet-computer',      name: 'Internet Computer' },
  // ─── Additional liquid alts ───
  { internal: 'ETC/USDT',   coinbase: 'ETC-USD',    kraken: 'XETCZUSD',  coingecko: 'ethereum-classic',       name: 'Ethereum Classic' },
  { internal: 'ALGO/USDT',  coinbase: 'ALGO-USD',   kraken: 'ALGOUSD',   coingecko: 'algorand',               name: 'Algorand' },
  { internal: 'FLOW/USDT',  coinbase: 'FLOW-USD',   kraken: 'FLOWUSD',   coingecko: 'flow',                   name: 'Flow' },
  { internal: 'EGLD/USDT',  coinbase: null,         kraken: null,        coingecko: 'elrond-erd-2',           name: 'MultiversX' },
  { internal: 'HBAR/USDT',  coinbase: 'HBAR-USD',   kraken: 'HBARUSD',   coingecko: 'hedera-hashgraph',       name: 'Hedera' },
  { internal: 'ICX/USDT',   coinbase: null,         kraken: null,        coingecko: 'icon',                   name: 'ICON' },
  { internal: 'KSM/USDT',   coinbase: null,         kraken: 'KSMUSD',    coingecko: 'kusama',                 name: 'Kusama' },
  { internal: 'MINA/USDT',  coinbase: 'MINA-USD',   kraken: 'MINAUSD',   coingecko: 'mina-protocol',          name: 'Mina' },
  { internal: 'QTUM/USDT',  coinbase: null,         kraken: 'QTUMUSD',   coingecko: 'qtum',                   name: 'Qtum' },
  { internal: 'XMR/USDT',   coinbase: null,         kraken: 'XXMRZUSD',  coingecko: 'monero',                 name: 'Monero' },
  { internal: 'ZEC/USDT',   coinbase: 'ZEC-USD',    kraken: 'XZECZUSD',  coingecko: 'zcash',                  name: 'Zcash' },
  { internal: 'DASH/USDT',  coinbase: null,         kraken: 'DASHUSD',   coingecko: 'dash',                   name: 'Dash' },
  { internal: 'CRV/USDT',   coinbase: 'CRV-USD',    kraken: 'CRVUSD',    coingecko: 'curve-dao-token',        name: 'Curve DAO' },
  { internal: 'SNX/USDT',   coinbase: 'SNX-USD',    kraken: 'SNXUSD',    coingecko: 'havven',                 name: 'Synthetix' },
  { internal: 'COMP/USDT',  coinbase: 'COMP-USD',   kraken: 'COMPUSD',   coingecko: 'compound-governance-token', name: 'Compound' },
  { internal: 'UNI/USDT',   coinbase: 'UNI-USD',    kraken: 'UNIUSD',    coingecko: 'uniswap',                name: 'Uniswap' },
  { internal: 'DYDX/USDT',  coinbase: 'DYDX-USD',   kraken: 'DYDXUSD',   coingecko: 'dydx-chain',             name: 'dYdX' },
  { internal: 'GMX/USDT',   coinbase: null,         kraken: null,        coingecko: 'gmx',                    name: 'GMX' },
  { internal: 'PENDLE/USDT',coinbase: null,         kraken: null,        coingecko: 'pendle',                 name: 'Pendle' },
  { internal: 'JUP/USDT',   coinbase: 'JUP-USD',    kraken: null,        coingecko: 'jupiter-exchange-solana', name: 'Jupiter' },
  { internal: 'PYUSD/USDT', coinbase: 'PYUSD-USD',  kraken: null,        coingecko: 'paypal-usd',             name: 'PayPal USD' },
  { internal: 'WLD/USDT',   coinbase: 'WLD-USD',    kraken: null,        coingecko: 'worldcoin-wld',          name: 'Worldcoin' },
  { internal: 'TON/USDT',   coinbase: null,         kraken: null,        coingecko: 'the-open-network',       name: 'Toncoin' },
  { internal: 'KAVA/USDT',  coinbase: null,         kraken: 'KAVAUSD',   coingecko: 'kava',                   name: 'Kava' },
  { internal: 'ZIL/USDT',   coinbase: null,         kraken: null,        coingecko: 'zilliqa',                name: 'Zilliqa' },
  { internal: '1INCH/USDT', coinbase: '1INCH-USD',  kraken: '1INCHUSD',  coingecko: '1inch',                  name: '1inch' },
  { internal: 'BAL/USDT',   coinbase: null,         kraken: 'BALUSD',    coingecko: 'balancer',               name: 'Balancer' },
  { internal: 'SUSHI/USDT', coinbase: null,         kraken: 'SUSHIUSD',  coingecko: 'sushi',                  name: 'Sushi' },
  { internal: 'WAVES/USDT', coinbase: null,         kraken: null,        coingecko: 'waves',                  name: 'Waves' },
  { internal: 'XTZ/USDT',   coinbase: 'XTZ-USD',    kraken: 'XTZUSD',    coingecko: 'tezos',                  name: 'Tezos' },
  { internal: 'KCS/USDT',   coinbase: null,         kraken: null,        coingecko: 'kucoin-shares',          name: 'KuCoin' },
  { internal: 'GT/USDT',    coinbase: null,         kraken: null,        coingecko: 'gate',                   name: 'Gate' },
  { internal: 'CRO/USDT',   coinbase: null,         kraken: null,        coingecko: 'crypto-com-chain',       name: 'Cronos' },
  { internal: 'LEO/USDT',   coinbase: null,         kraken: null,        coingecko: 'leo-token',              name: 'LEO' },
  { internal: 'BGB/USDT',   coinbase: null,         kraken: null,        coingecko: 'bitget-token',           name: 'Bitget' },
  { internal: 'OKB/USDT',   coinbase: null,         kraken: null,        coingecko: 'okb',                    name: 'OKB' },
]

// Build lookup tables once
const META_BY_INTERNAL: Map<string, TokenMeta> = new Map(TOKEN_META.map(m => [m.internal, m]))
const META_BY_COINBASE: Map<string, TokenMeta> = new Map(
  TOKEN_META.filter(m => m.coinbase).map(m => [m.coinbase!, m])
)
const META_BY_COINGECKO: Map<string, TokenMeta> = new Map(
  TOKEN_META.filter(m => m.coingecko).map(m => [m.coingecko!, m])
)
const ALL_COINGECKO_IDS = TOKEN_META.filter(m => m.coingecko).map(m => m.coingecko!)

// Coinbase WS subscription list (USD pairs only)
const COINBASE_PAIRS = TOKEN_META.filter(m => m.coinbase).map(m => m.coinbase!)

// ─── Public exports for engine ───
export const SUPPORTED_TOKENS_LIST = TOKEN_META.map(m => m.internal)

export function getTokenName(symbol: string): string {
  return META_BY_INTERNAL.get(symbol)?.name || symbol
}

import { logClient } from './client-logger'

const COINBASE_WS_URL = 'wss://ws-feed.exchange.coinbase.com'
// Use our own Next.js API route as a proxy. The browser cannot call
// api.coingecko.com directly because CoinGecko does not send
// Access-Control-Allow-Origin (CORS). The proxy route runs server-side
// where CORS does not apply, and caches the response for 30s.
const COINGECKO_MARKETS_URL = '/api/coingecko/markets'
// Use our own Next.js API route as a proxy (same reason as CoinGecko).
const KRAKEN_TICKER_URL = '/api/kraken/ticker'

export class LivePriceFeed {
  private ws: WebSocket | null = null
  private prices: Map<string, TickerData> = new Map()
  private subscribers: Set<(symbol: string, data: TickerData) => void> = new Set()
  private symbols: string[]
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connected = false
  private intentionallyClosed = false
  private restPollInterval: ReturnType<typeof setInterval> | null = null
  private lastWsErrorTime = 0
  private wsErrorCount = 0

  constructor(symbols: string[]) {
    this.symbols = [...new Set(symbols)]
    this.connect()
    // Always start REST polling — it supplements WS with 24h change % which
    // Coinbase ticker channel doesn't provide directly. WS gives us price,
    // REST gives us 24h stats. Together we have everything.
    this.startRestPolling()
  }

  private connect() {
    if (this.intentionallyClosed) return
    if (COINBASE_PAIRS.length === 0) {
      console.warn('[PriceFeed] No Coinbase pairs to subscribe to')
      return
    }

    console.log(`[PriceFeed] Connecting to Coinbase WS (${COINBASE_PAIRS.length} pairs)...`)
    try {
      this.ws = new WebSocket(COINBASE_WS_URL)

      this.ws.onopen = () => {
        console.log('[PriceFeed] Connected to Coinbase WebSocket')
        logClient.wsConnect('Coinbase WebSocket connected')
        // Subscribe to ticker channel — gives us real-time price updates
        this.ws!.send(JSON.stringify({
          type: 'subscribe',
          product_ids: COINBASE_PAIRS,
          channels: ['ticker'],
        }))
        this.connected = true
        this.reconnectAttempts = 0
      }

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data)
          // Coinbase ticker message shape:
          //   { type: 'ticker', product_id: 'BTC-USD', price: '...', ... }
          if (msg.type === 'ticker' && msg.product_id && msg.price) {
            const meta = META_BY_COINBASE.get(msg.product_id)
            if (!meta) return
            const internal = meta.internal
            const price = parseFloat(msg.price)
            const existing = this.prices.get(internal)
            // Preserve 24h change/volume from REST poll
            const changePct = existing?.changePct ?? 0
            const volume = msg.volume_24h ? parseFloat(msg.volume_24h) : existing?.volume ?? 0
            const quoteVolume = msg.volume_24h
              ? parseFloat(msg.volume_24h) * price
              : existing?.quoteVolume ?? 0
            const data: TickerData = {
              symbol: internal,
              rawSymbol: msg.product_id,
              price,
              changePct,
              volume,
              quoteVolume,
              high: existing?.high ?? price,
              low: existing?.low ?? price,
              timestamp: Date.now(),
            }
            this.prices.set(internal, data)
            this.subscribers.forEach(cb => cb(internal, data))
          }
        } catch {
          // Ignore malformed messages
        }
      }

      this.ws.onerror = (e: Event) => {
        this.wsErrorCount++
        const now = Date.now()
        if (now - this.lastWsErrorTime > 5000) {
          this.lastWsErrorTime = now
          console.warn(
            `[PriceFeed] Coinbase WS error #${this.wsErrorCount} (type=${e.type}). ` +
            `REST polling continues. Will retry WS.`
          )
        }
      }

      this.ws.onclose = () => {
        this.connected = false
        if (this.intentionallyClosed) return
        console.log('[PriceFeed] WS closed, scheduling reconnect...')
        logClient.wsDisconnect('WebSocket closed, reconnecting')
        this.scheduleReconnect()
      }
    } catch (e) {
      console.error('[PriceFeed] Failed to construct WebSocket:', e)
      logClient.error('WebSocket construction failed', { error: String(e) })
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectAttempts++
    // Exponential backoff: 2s, 3s, 4.5s, 7s... capped at 30s
    const delay = Math.min(30000, 2000 * Math.pow(1.5, this.reconnectAttempts - 1))
    console.log(`[PriceFeed] Reconnecting WS in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  /**
   * REST polling — supplements WS with 24h change % and volume.
   * Uses CoinGecko (one call for ALL tokens, gives changePct + volume),
   * then Kraken for any tokens CoinGecko missed.
   * Runs every 10s.
   */
  private startRestPolling() {
    if (this.intentionallyClosed) return
    const poll = async () => {
      if (this.intentionallyClosed) return
      await Promise.allSettled([
        this.pollCoinGecko(),
        this.pollKraken(),
      ])
    }
    poll()
    this.restPollInterval = setInterval(poll, 10000)
  }

  private async pollCoinGecko() {
    if (ALL_COINGECKO_IDS.length === 0) return
    try {
      // Request all tokens in one batch. CoinGecko allows up to 250 ids per call.
      const idsParam = ALL_COINGECKO_IDS.join(',')
      const url = `${COINGECKO_MARKETS_URL}?ids=${encodeURIComponent(idsParam)}`
      // Same-origin request to our /api/coingecko proxy — no CORS issue.
      const resp = await fetch(url, {
        headers: { 'Accept': 'application/json' },
      })
      if (!resp.ok) {
        if (resp.status === 429) {
          console.warn('[PriceFeed] CoinGecko rate-limited (429) — will retry next cycle')
          logClient.error('CoinGecko rate-limited (429)', { will_retry: True })
        } else {
          console.warn(`[PriceFeed] CoinGecko HTTP ${resp.status}`)
          logClient.error(`CoinGecko HTTP ${resp.status}`, { status: resp.status })
        }
        return
      }
      const arr = await resp.json() as any[]
      let updated = 0
      for (const c of arr) {
        const meta = META_BY_COINGECKO.get(c.id)
        if (!meta) continue
        const internal = meta.internal
        const price = c.current_price
        // CoinGecko returns null current_price for illiquid / delisted coins.
        // Storing null here would crash PaperTradingEngine.snapshot() when
        // it calls t.price.toFixed(...). Skip these entries entirely —
        // if no other source provides a price for this token, the engine's
        // snapshot loop will simply skip the token (defensive guard added).
        if (typeof price !== 'number' || !isFinite(price) || price <= 0) continue
        const totalVol = typeof c.total_volume === 'number' && isFinite(c.total_volume)
          ? c.total_volume : 0
        const changePct = typeof c.price_change_percentage_24h === 'number'
          && isFinite(c.price_change_percentage_24h)
          ? c.price_change_percentage_24h : 0
        const data: TickerData = {
          symbol: internal,
          rawSymbol: c.id,
          price,
          changePct,
          volume: totalVol > 0 ? totalVol / price : 0, // base vol approx quote/price
          quoteVolume: totalVol,
          high: (typeof c.high_24h === 'number' && isFinite(c.high_24h)) ? c.high_24h : price,
          low: (typeof c.low_24h === 'number' && isFinite(c.low_24h)) ? c.low_24h : price,
          timestamp: Date.now(),
        }
        // Always update 24h stats from CoinGecko (authoritative source).
        // If WS hasn't sent us a price yet, use CoinGecko's price too.
        this.prices.set(internal, data)
        this.subscribers.forEach(cb => cb(internal, data))
        updated++
      }
      if (!this.connected && updated > 0) {
        this.connected = true
      }
    } catch (e: any) {
      // Network errors are expected occasionally — silent
      if (e?.name !== 'TypeError') {
        console.warn('[PriceFeed] CoinGecko fetch failed:', e?.message || e)
        logClient.error('CoinGecko fetch failed', { error: e?.message || String(e) })
      }
    }
  }

  private async pollKraken() {
    // For tokens without CoinGecko coverage (rare), try Kraken
    const krakenTokens = TOKEN_META.filter(m => m.kraken && !m.coingecko)
    if (krakenTokens.length === 0) return
    const pairList = krakenTokens.map(m => m.kraken!).join(',')
    try {
      const resp = await fetch(`${KRAKEN_TICKER_URL}?pair=${encodeURIComponent(pairList)}`)
      if (!resp.ok) {
        return
      }
      const json = await resp.json() as any
      if (json.error && json.error.length > 0) return
      for (const [kpair, t] of Object.entries(json.result || {})) {
        const meta = krakenTokens.find(m => m.kraken === kpair)
        if (!meta) continue
        const ticker = t as any
        const price = parseFloat(ticker.c?.[0] || '0')
        if (price <= 0) continue
        const internal = meta.internal
        const existing = this.prices.get(internal)
        const quoteVol = parseFloat(ticker.q?.[1] || ticker.v?.[1] || '0') * price
        const data: TickerData = {
          symbol: internal,
          rawSymbol: kpair,
          price,
          changePct: existing?.changePct ?? 0,
          volume: parseFloat(ticker.v?.[1] || '0'),
          quoteVolume: quoteVol,
          high: parseFloat(ticker.h?.[1] || '0') || price,
          low: parseFloat(ticker.l?.[1] || '0') || price,
          timestamp: Date.now(),
        }
        this.prices.set(internal, data)
        this.subscribers.forEach(cb => cb(internal, data))
      }
    } catch {
      // Network errors are silent
    }
  }

  isConnected(): boolean {
    return this.connected
  }

  getPrice(symbol: string): number | null {
    return this.prices.get(symbol)?.price ?? null
  }

  getData(symbol: string): TickerData | null {
    return this.prices.get(symbol) ?? null
  }

  getAllPrices(): Map<string, TickerData> {
    return new Map(this.prices)
  }

  getAvailableSymbols(): string[] {
    return Array.from(this.prices.keys())
  }

  getActiveSymbolCount(): number {
    return this.prices.size
  }

  subscribe(cb: (symbol: string, data: TickerData) => void): () => void {
    this.subscribers.add(cb)
    return () => { this.subscribers.delete(cb) }
  }

  /**
   * Update the set of subscribed symbols. For Coinbase WS we always
   * subscribe to ALL known USD pairs regardless of this list — it's a
   * single connection so no extra cost. The list is kept for engine
   * accounting (activeTokens) and the REST poll covers everything.
   */
  setSymbols(symbols: string[]) {
    const newSet = [...new Set(symbols)]
    const changed =
      newSet.length !== this.symbols.length ||
      !newSet.every(s => this.symbols.includes(s))
    if (!changed) return
    this.symbols = newSet
    // No WS reconnect needed — we always subscribe to all pairs
  }

  disconnect() {
    this.intentionallyClosed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.restPollInterval) {
      clearInterval(this.restPollInterval)
      this.restPollInterval = null
    }
    if (this.ws) {
      try { this.ws.close() } catch {}
      this.ws = null
    }
    this.subscribers.clear()
    this.connected = false
  }
}
