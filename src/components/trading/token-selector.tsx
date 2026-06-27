/**
 * TokenSelector — Premium multi-token selector with quick stats.
 * Shows all available tokens with prices, toggles, and selection.
 */
'use client'

import { useTradingStore, TokenState } from '@/stores/trading-store'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { motion, AnimatePresence } from 'framer-motion'
import { useTradingSocket } from '@/lib/use-trading-socket'
import { Coins, Star } from 'lucide-react'

const TOKEN_ICONS: Record<string, string> = {
  'SOL/USDT': '◎',
  'BTC/USDT': '₿',
  'ETH/USDT': 'Ξ',
  'DOGE/USDT': 'Ð',
  'AVAX/USDT': '▲',
  'ADA/USDT': '₳',
  'LINK/USDT': '⬡',
  'DOT/USDT': '●',
  'MATIC/USDT': '◇',
  'UNI/USDT': '🦄',
}

const ALL_TOKENS = [
  'SOL/USDT', 'BTC/USDT', 'ETH/USDT',
  'DOGE/USDT', 'AVAX/USDT', 'ADA/USDT',
  'LINK/USDT', 'DOT/USDT', 'MATIC/USDT', 'UNI/USDT',
]

export function TokenSelector() {
  const { tokenStates, activeTokens, selectedToken } = useTradingStore()
  const { emit } = useTradingSocket()

  const tokens = Object.values(tokenStates)

  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1 scrollbar-none">
      {ALL_TOKENS.map((sym) => {
        const token = tokenStates[sym]
        const isActive = activeTokens.includes(sym)
        const isSelected = selectedToken === sym
        const icon = TOKEN_ICONS[sym] || '●'
        const color = token?.color || '#6b7280'
        const price = token?.price
        const change = token?.change24h || 0

        return (
          <motion.button
            key={sym}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-[10px] font-mono transition-all whitespace-nowrap ${
              isSelected
                ? 'bg-[#1a2334] border-blue-500/40 shadow-lg shadow-blue-500/10'
                : isActive
                ? 'bg-[#121a26] border-[#2a3a5d] hover:border-[#3a4a7d]'
                : 'bg-[#0a0e17] border-[#1e2a3d] opacity-50 hover:opacity-75'
            }`}
            onClick={() => {
              useTradingStore.getState().selectToken(sym)
              emit('switch-symbol', { symbol: sym })
            }}
          >
            {/* Token icon dot */}
            <div
              className="w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold shrink-0"
              style={{
                backgroundColor: `${color}20`,
                color: color,
              }}
            >
              {icon}
            </div>

            <span className={`${isSelected ? 'text-white font-bold' : 'text-gray-300'}`}>
              {sym.replace('/USDT', '')}
            </span>

            {price !== undefined && isActive && (
              <span className="text-gray-500 text-[9px]">
                ${price < 1 ? price.toFixed(4) : price < 100 ? price.toFixed(2) : price.toFixed(0)}
              </span>
            )}

            {isActive && change !== 0 && (
              <span className={`text-[8px] ${change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {change >= 0 ? '+' : ''}{change.toFixed(1)}%
              </span>
            )}

            {/* Active indicator */}
            {isActive && (
              <div className="w-1 h-1 rounded-full bg-emerald-400 shrink-0" />
            )}
          </motion.button>
        )
      })}
    </div>
  )
}
