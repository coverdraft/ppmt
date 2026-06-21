/**
 * TokenMatrix — Pantalla de selección de token/timeframe.
 *
 * Se muestra cuando el usuario entra a PAPER LIVE sin haber seleccionado
 * un par todavía. Grid de 10 tarjetas (5 tokens × 2 timeframes).
 * Al hacer click en CONECTAR, se establece el activeTrade en App.tsx
 * y el layout 60/40 aparece con datos en vivo.
 */

export interface ActiveTrade {
  symbol: string;
  timeframe: string;
}

interface TokenMatrixProps {
  onSelect: (trade: ActiveTrade) => void;
}

// ─── 5 tokens cubriendo __UNIVERSAL__ + 3 __CLASS_* pools ───
//   BTC/ETH → blue_chip,  SOL/XRP → large_cap,  DOGE → meme
const TOKENS = [
  { symbol: 'BTC/USDT',  label: 'BTC',  classLabel: 'blue_chip', classColor: 'text-yellow-400' },
  { symbol: 'ETH/USDT',  label: 'ETH',  classLabel: 'blue_chip', classColor: 'text-yellow-400' },
  { symbol: 'SOL/USDT',  label: 'SOL',  classLabel: 'large_cap', classColor: 'text-blue-400' },
  { symbol: 'XRP/USDT',  label: 'XRP',  classLabel: 'large_cap', classColor: 'text-blue-400' },
  { symbol: 'DOGE/USDT', label: 'DOGE', classLabel: 'meme',      classColor: 'text-purple-400' },
];

const TIMEFRAMES = ['1m', '5m'];

export default function TokenMatrix({ onSelect }: TokenMatrixProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6">
      {/* Header */}
      <div className="mb-8 text-center">
        <h2 className="text-xl font-mono font-bold text-white mb-1">
          Selecciona un Token
        </h2>
        <p className="text-xs font-mono text-gray-500">
          5 tokens · 2 timeframes · Pools __UNIVERSAL__ + __CLASS_*
        </p>
      </div>

      {/* Grid: 3 columns on md+, 2 on sm */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 max-w-2xl w-full">
        {TOKENS.map((token) =>
          TIMEFRAMES.map((tf) => (
            <div
              key={`${token.symbol}-${tf}`}
              className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-3 hover:border-gray-600 transition-colors duration-200"
            >
              {/* Token name + class */}
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold text-white text-sm">
                  {token.label}<span className="text-gray-500">/USDT</span>
                </span>
                <span className={`text-[9px] font-mono ${token.classColor} bg-gray-800 px-1.5 py-0.5 rounded`}>
                  {token.classLabel}
                </span>
              </div>

              {/* Timeframe */}
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-gray-400">Timeframe:</span>
                <span className="text-xs font-mono text-white font-semibold">{tf}</span>
              </div>

              {/* Status */}
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-gray-400">Estado:</span>
                <span className="flex items-center gap-1 text-xs font-mono text-emerald-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  Disponible
                </span>
              </div>

              {/* Connect button */}
              <button
                onClick={() => onSelect({ symbol: token.symbol, timeframe: tf })}
                className="mt-auto w-full py-2 rounded-md bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white text-xs font-mono font-semibold transition-colors duration-150"
              >
                CONECTAR
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
