/**
 * LivePriceFeed — Real-time crypto prices from Binance public WebSocket.
 *
 * Connects to wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/...
 * No API key required, no auth, fully public. Receives 24h ticker updates
 * every ~1s for each subscribed symbol.
 *
 * Used by PaperTradingEngine to get real market prices for paper trading.
 * If the WebSocket fails (e.g. browser blocks it, network issue), the
 * feed returns the last known price or null, and the engine will refuse
 * to execute trades until prices are available again.
 */

export interface TickerData {
  symbol: string        // normalized: "BTC/USDT"
  rawSymbol: string     // exchange format: "BTCUSDT"
  price: number
  changePct: number     // 24h price change %
  volume: number        // 24h base volume
  quoteVolume: number   // 24h quote volume (USDT)
  high: number          // 24h high
  low: number           // 24h low
  timestamp: number
}

const BINANCE_WS_URL = 'wss://stream.binance.com:9443/stream?streams='
const BINANCE_REST_URL = 'https://api.binance.com/api/v3/ticker/24hr'

// Binance combined stream supports up to 1024 streams per connection.
// We'll use one connection for all symbols.
export class LivePriceFeed {
  private ws: WebSocket | null = null
  private prices: Map<string, TickerData> = new Map()
  private subscribers: Set<(symbol: string, data: TickerData) => void> = new Set()
  private symbols: string[]
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connected = false
  private intentionallyClosed = false
  private restFallbackActive = false
  private restInterval: ReturnType<typeof setInterval> | null = null
  private lastWsErrorTime = 0
  private wsErrorCount = 0

  constructor(symbols: string[]) {
    this.symbols = [...new Set(symbols)] // dedupe
    this.connect()
  }

  private connect() {
    if (this.intentionallyClosed) return
    if (this.symbols.length === 0) {
      console.warn('[PriceFeed] No symbols to subscribe to')
      return
    }

    const streams = this.symbols
      .map(s => s.toLowerCase().replace('/', '') + '@ticker')
      .join('/')

    const url = BINANCE_WS_URL + streams
    console.log(`[PriceFeed] Connecting to Binance WS with ${this.symbols.length} symbols...`)

    try {
      this.ws = new WebSocket(url)

      this.ws.onopen = () => {
        console.log('[PriceFeed] Connected to Binance WebSocket')
        this.connected = true
        this.reconnectAttempts = 0
      }

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.data && msg.data.s) {
            const rawSymbol: string = msg.data.s
            const symWithSlash = this.normalizeSymbol(rawSymbol)
            const data: TickerData = {
              symbol: symWithSlash,
              rawSymbol,
              price: parseFloat(msg.data.c),
              changePct: parseFloat(msg.data.P),
              volume: parseFloat(msg.data.v),
              quoteVolume: parseFloat(msg.data.q),
              high: parseFloat(msg.data.h),
              low: parseFloat(msg.data.l),
              timestamp: msg.data.E,
            }
            this.prices.set(symWithSlash, data)
            this.subscribers.forEach(cb => cb(symWithSlash, data))
          }
        } catch (e) {
          // Ignore malformed messages
        }
      }

      this.ws.onerror = (e: Event) => {
        // Binance WS error events don't carry a message — log a meaningful
        // summary instead of dumping the empty object. Common causes:
        //   - Network blocking WSS
        //   - Browser mixed-content policy
        //   - Geo-restriction on Binance
        //   - Too many streams in URL (limit: 1024)
        // We count errors and switch to REST fallback after 3 failures.
        this.wsErrorCount++
        const now = Date.now()
        // Throttle logs to one per 5s to avoid spam
        if (now - this.lastWsErrorTime > 5000) {
          this.lastWsErrorTime = now
          console.warn(
            `[PriceFeed] WS error #${this.wsErrorCount} (type=${e.type}). ` +
            `Likely cause: network/firewall/geo-block. ` +
            `Will ${this.reconnectAttempts >= 3 ? 'switch to REST fallback' : 'retry WS'}.`
          )
        }
        // After 3 failed attempts, start REST fallback in parallel
        if (this.reconnectAttempts >= 3 && !this.restFallbackActive) {
          this.startRestFallback()
        }
      }

      this.ws.onclose = () => {
        this.connected = false
        if (this.intentionallyClosed) return
        console.log('[PriceFeed] Disconnected, scheduling reconnect...')
        this.scheduleReconnect()
      }
    } catch (e) {
      console.error('[PriceFeed] Failed to construct WebSocket:', e)
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectAttempts++
    // Exponential backoff: 2s, 3s, 4.5s, 7s... capped at 30s
    const delay = Math.min(30000, 2000 * Math.pow(1.5, this.reconnectAttempts - 1))
    console.log(`[PriceFeed] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  private normalizeSymbol(raw: string): string {
    // BTCUSDT -> BTC/USDT, 1000SATSUSDT -> 1000SATS/USDT
    if (raw.endsWith('USDT')) return raw.slice(0, -4) + '/USDT'
    if (raw.endsWith('BUSD')) return raw.slice(0, -4) + '/BUSD'
    if (raw.endsWith('BTC')) return raw.slice(0, -3) + '/BTC'
    if (raw.endsWith('ETH')) return raw.slice(0, -3) + '/ETH'
    return raw
  }

  /**
   * REST API fallback: polls Binance /api/v3/ticker/24hr every 5s.
   * Returns ALL tickers in one request (~1MB), so we filter to our
   * subscribed symbols. Less efficient than WS but works when the
   * WebSocket is blocked.
   */
  private startRestFallback() {
    if (this.restFallbackActive) return
    this.restFallbackActive = true
    console.log('[PriceFeed] Starting REST API fallback (polling every 5s)')

    const poll = async () => {
      if (this.intentionallyClosed) return
      try {
        const resp = await fetch(BINANCE_REST_URL)
        if (!resp.ok) {
          console.warn(`[PriceFeed] REST fallback HTTP ${resp.status}`)
          return
        }
        const arr = await resp.json() as any[]
        const subscribedSet = new Set(this.symbols.map(s => s.replace('/', '').toUpperCase()))
        for (const t of arr) {
          if (!subscribedSet.has(t.symbol)) continue
          const symWithSlash = this.normalizeSymbol(t.symbol)
          const data: TickerData = {
            symbol: symWithSlash,
            rawSymbol: t.symbol,
            price: parseFloat(t.lastPrice),
            changePct: parseFloat(t.priceChangePercent),
            volume: parseFloat(t.volume),
            quoteVolume: parseFloat(t.quoteVolume),
            high: parseFloat(t.highPrice),
            low: parseFloat(t.lowPrice),
            timestamp: t.closeTime,
          }
          this.prices.set(symWithSlash, data)
          this.subscribers.forEach(cb => cb(symWithSlash, data))
        }
        if (!this.connected) {
          this.connected = true
          console.log(`[PriceFeed] REST fallback active — ${this.prices.size} symbols streaming`)
        }
      } catch (e: any) {
        console.warn('[PriceFeed] REST fallback fetch failed:', e?.message || e)
      }
    }

    poll() // immediate first poll
    this.restInterval = setInterval(poll, 5000)
  }

  private stopRestFallback() {
    if (this.restInterval) {
      clearInterval(this.restInterval)
      this.restInterval = null
    }
    this.restFallbackActive = false
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

  /** Returns the number of symbols currently receiving price updates. */
  getActiveSymbolCount(): number {
    return this.prices.size
  }

  subscribe(cb: (symbol: string, data: TickerData) => void): () => void {
    this.subscribers.add(cb)
    return () => { this.subscribers.delete(cb) }
  }

  /**
   * Update the set of subscribed symbols. Reconnects the WebSocket
   * with the new stream list. Old symbols not in the new list will
   * stop receiving updates.
   */
  setSymbols(symbols: string[]) {
    const newSet = [...new Set(symbols)]
    const changed =
      newSet.length !== this.symbols.length ||
      !newSet.every(s => this.symbols.includes(s))
    if (!changed) return

    this.symbols = newSet
    // Clean up prices for symbols no longer subscribed
    for (const sym of Array.from(this.prices.keys())) {
      if (!this.symbols.includes(sym)) {
        this.prices.delete(sym)
      }
    }

    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.connect()
  }

  disconnect() {
    this.intentionallyClosed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopRestFallback()
    if (this.ws) {
      try { this.ws.close() } catch {}
      this.ws = null
    }
    this.subscribers.clear()
    this.connected = false
  }
}
