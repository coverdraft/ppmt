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
        console.error('[PriceFeed] WS error:', e)
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
    if (this.ws) {
      try { this.ws.close() } catch {}
      this.ws = null
    }
    this.subscribers.clear()
    this.connected = false
  }
}
