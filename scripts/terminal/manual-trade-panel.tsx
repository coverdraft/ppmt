/**
 * ManualTradePanel — Manual BUY / SELL panel for paper trading.
 *
 * Lets the user:
 *  - Select any of the 25 supported tokens (or use the one already selected)
 *  - Enter a USDT amount (with quick presets: 50 / 100 / 500 / 25% / 50%)
 *  - See the live price, 24h change, and resulting position info
 *  - Click BUY (opens/adds to LONG) or SELL (closes LONG or opens SHORT)
 *
 * Uses the `emit` function from useTradingSocket to send manual-buy /
 * manual-sell events to the PaperTradingEngine.
 */
'use client'

import { useState, useEffect } from 'react'
import { useTradingStore } from '@/stores/trading-store'
import { useTradingSocket } from '@/lib/use-trading-socket'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { SUPPORTED_TOKENS, TOKEN_NAMES } from '@/lib/paper-trading-engine'
import { TrendingUp, TrendingDown, Zap } from 'lucide-react'

const QUICK_AMOUNTS = [50, 100, 500]
const PERCENT_OPTIONS = [25, 50, 100]

export function ManualTradePanel() {
  const {
    selectedToken,
    currentPrice,
    cash,
    tokenStates,
    isRunning,
    killSwitchActive,
  } = useTradingStore()
  const { emit } = useTradingSocket()

  const [symbol, setSymbol] = useState(selectedToken || 'BTC/USDT')
  const [amount, setAmount] = useState('100')
  const [lastResult, setLastResult] = useState<{ ok: boolean; msg: string } | null>(null)

  // Sync local symbol with store selectedToken (e.g. when user clicks
  // a token in TokenSelector or PortfolioManager)
  useEffect(() => {
    if (selectedToken) setSymbol(selectedToken)
  }, [selectedToken])

  // Live ticker for the selected symbol
  const ticker = tokenStates[symbol]
  const livePrice = ticker?.price ?? currentPrice ?? 0
  const change24h = ticker?.change24h ?? 0

  const handleBuy = () => {
    const amt = parseFloat(amount)
    if (!amt || amt <= 0) {
      setLastResult({ ok: false, msg: 'Invalid amount' })
      return
    }
    emit('manual-buy', { symbol, amount: amt })
    setLastResult({
      ok: true,
      msg: `BUY ${amt} USDT of ${symbol} @ ~${livePrice.toFixed(livePrice < 1 ? 6 : 2)}`,
    })
    setTimeout(() => setLastResult(null), 4000)
  }

  const handleSell = () => {
    const amt = parseFloat(amount)
    if (!amt || amt <= 0) {
      setLastResult({ ok: false, msg: 'Invalid amount' })
      return
    }
    emit('manual-sell', { symbol, amount: amt })
    setLastResult({
      ok: true,
      msg: `SELL ${amt} USDT of ${symbol} @ ~${livePrice.toFixed(livePrice < 1 ? 6 : 2)}`,
    })
    setTimeout(() => setLastResult(null), 4000)
  }

  const handleSymbolChange = (val: string) => {
    setSymbol(val)
    emit('switch-symbol', { symbol: val })
  }

  const applyPercent = (pct: number) => {
    const usdt = (cash * pct) / 100
    setAmount(usdt.toFixed(2))
  }

  const tradingBlocked = !isRunning || killSwitchActive
  const changeColor = change24h >= 0 ? 'text-emerald-400' : 'text-red-400'

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d]">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Zap className="w-4 h-4 text-amber-400" />
          <span className="text-gray-200 font-mono">MANUAL TRADE</span>
          <Badge
            variant="outline"
            className="text-[9px] font-mono bg-blue-500/20 text-blue-400 border-blue-500/30 px-1.5 py-0 ml-auto"
          >
            PAPER
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3 space-y-3">
        {/* Symbol selector + live price */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-gray-500 font-mono">SYMBOL</label>
            <Select value={symbol} onValueChange={handleSymbolChange}>
              <SelectTrigger className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#121a26] border-[#1e2a3d] max-h-72">
                {SUPPORTED_TOKENS.map((sym) => (
                  <SelectItem key={sym} value={sym} className="text-xs font-mono">
                    {sym} — {TOKEN_NAMES[sym] || sym}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[10px] text-gray-500 font-mono">PRICE</label>
            <div className="h-8 flex items-center bg-[#121a26] border border-[#1e2a3d] rounded px-2">
              <span className="text-xs text-gray-200 font-mono flex-1">
                {livePrice > 0 ? livePrice.toFixed(livePrice < 1 ? 6 : livePrice < 100 ? 4 : 2) : '...'}
              </span>
              <span className={`text-[10px] font-mono ${changeColor}`}>
                {change24h >= 0 ? '+' : ''}{change24h.toFixed(2)}%
              </span>
            </div>
          </div>
        </div>

        {/* Amount input */}
        <div>
          <label className="text-[10px] text-gray-500 font-mono">AMOUNT (USDT)</label>
          <Input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono"
            placeholder="100"
            min={0}
            step={10}
          />
        </div>

        {/* Quick amounts */}
        <div className="flex flex-wrap gap-1">
          {QUICK_AMOUNTS.map((amt) => (
            <Button
              key={amt}
              size="sm"
              variant="outline"
              className="h-6 text-[10px] font-mono px-2"
              onClick={() => setAmount(String(amt))}
            >
              {amt}
            </Button>
          ))}
          <div className="w-px bg-[#1e2a3d] mx-1" />
          {PERCENT_OPTIONS.map((pct) => (
            <Button
              key={pct}
              size="sm"
              variant="outline"
              className="h-6 text-[10px] font-mono px-2"
              onClick={() => applyPercent(pct)}
              title={`${pct}% of available cash (${((cash * pct) / 100).toFixed(2)} USDT)`}
            >
              {pct}%
            </Button>
          ))}
        </div>

        {/* Buy / Sell buttons */}
        <div className="grid grid-cols-2 gap-2">
          <Button
            size="sm"
            className="h-9 bg-emerald-600 hover:bg-emerald-500 text-white font-mono text-xs"
            disabled={tradingBlocked || livePrice === 0}
            onClick={handleBuy}
          >
            <TrendingUp className="w-4 h-4 mr-1" />
            BUY / LONG
          </Button>
          <Button
            size="sm"
            className="h-9 bg-red-600 hover:bg-red-500 text-white font-mono text-xs"
            disabled={tradingBlocked || livePrice === 0}
            onClick={handleSell}
          >
            <TrendingDown className="w-4 h-4 mr-1" />
            SELL / SHORT
          </Button>
        </div>

        {/* Status / result */}
        {tradingBlocked && (
          <div className="text-[10px] text-amber-400 font-mono text-center bg-amber-500/10 rounded py-1">
            ⚠ Trading disabled — click Start Trading first
          </div>
        )}
        {lastResult && (
          <div className={`text-[10px] font-mono text-center rounded py-1 ${
            lastResult.ok ? 'text-emerald-400 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'
          }`}>
            {lastResult.msg}
          </div>
        )}

        {/* Cash + estimated qty */}
        <div className="grid grid-cols-2 gap-2 text-[10px] font-mono pt-1">
          <div className="bg-[#121a26] rounded px-2 py-1 border border-[#1e2a3d]">
            <div className="text-gray-500">CASH</div>
            <div className="text-gray-200">{cash.toFixed(2)} USDT</div>
          </div>
          <div className="bg-[#121a26] rounded px-2 py-1 border border-[#1e2a3d]">
            <div className="text-gray-500">EST. QTY</div>
            <div className="text-gray-200">
              {livePrice > 0 && parseFloat(amount) > 0
                ? (parseFloat(amount) / livePrice).toFixed(livePrice < 1 ? 4 : 6)
                : '--'}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
