import type { PositionState } from '../types/position';
import TrieLab from './TrieLab';

interface RightPanelProps {
  position: PositionState;
  /** Brain update data from WebSocket */
  brainUpdate?: {
    current_sax_symbol: string[];
    active_path_ids: string[];
    n1_confidence: number;
    n2_confidence: number;
    weighted_confidence: number;
    signal_type: string;
  } | null;
  /** Whether we're in live mode */
  isLive?: boolean;
}

/**
 * RightPanel — 40% of the main layout.
 *
 * Three vertically stacked blocks:
 *   Block A: BTC Context (10% height)
 *   Block B: PositionCard + Walk-Forward strip (30% height)
 *   Block C: Trie Lab D3.js Radial Tree (60% height)
 */
export default function RightPanel({ position, brainUpdate, isLive = false }: RightPanelProps) {
  return (
    <div className="w-full h-full flex flex-col gap-2 min-w-0">
      {/* ─── Block A: BTC Context (10%) ──────────────────────── */}
      <div className="bg-[#0d0d14] border border-terminal-border rounded-lg px-4 py-2.5 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-yellow-500 flex-shrink-0" />
            <span className="font-mono text-xs text-gray-300">
              BTC: <span className="text-yellow-400 font-semibold">
                {isLive ? (brainUpdate ? 'ANALYZING' : 'CONNECTING...') : 'RANGING'}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs font-mono">
            {isLive && brainUpdate ? (
              <>
                <span className="text-gray-500">N1:</span>
                <span className="text-gray-300">{brainUpdate.n1_confidence.toFixed(2)}</span>
                <span className="text-terminal-border">|</span>
                <span className="text-gray-500">N2:</span>
                <span className="text-gray-300">{brainUpdate.n2_confidence.toFixed(2)}</span>
                <span className="text-terminal-border">|</span>
                <span className="text-gray-500">Conf:</span>
                <span className="text-emerald-400 font-semibold">
                  {brainUpdate.weighted_confidence.toFixed(3)}
                </span>
              </>
            ) : (
              <>
                <span className="text-gray-500">Move:</span>
                <span className="text-gray-300">0.8%</span>
                <span className="text-terminal-border">|</span>
                <span className="text-gray-500">Filtros:</span>
                <span className="text-red-400 font-semibold">LONG Memes SUPRIMIDO</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ─── Block B: Position Card (30%) ────────────────────── */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 flex-shrink-0">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-mono text-gray-500 uppercase tracking-wider">
            Estado de Operación
          </h3>
          <span
            className={`px-2 py-0.5 rounded text-xs font-mono font-semibold ${
              position.status === 'ACTIVE'
                ? 'bg-blue-500/20 text-blue-400'
                : position.status === 'BREAK_EVEN_SECURED'
                ? 'bg-yellow-500/20 text-yellow-400'
                : position.status === 'TP_EXTENDED'
                ? 'bg-emerald-500/20 text-emerald-400'
                : position.status === 'CLOSED_BY_DIVERGENCE'
                ? 'bg-orange-500/20 text-orange-400'
                : position.status.startsWith('CLOSED')
                ? 'bg-gray-500/20 text-gray-400'
                : 'bg-gray-800/20 text-gray-600'
            }`}
          >
            {position.status === 'CLOSED_BY_DIVERGENCE'
              ? 'CERRADA POR DIVERGENCIA'
              : position.status}
          </span>
        </div>

        {/* Position details */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-xs mb-3">
          <div className="flex justify-between">
            <span className="text-gray-500">Dirección</span>
            <span
              className={
                position.direction === 'LONG'
                  ? 'text-emerald-400 font-semibold'
                  : 'text-red-400 font-semibold'
              }
            >
              {position.direction}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">P&L</span>
            <span className={position.pnl_pct && position.pnl_pct >= 0 ? 'text-emerald-400 font-semibold' : 'text-red-400 font-semibold'}>
              {position.pnl_pct !== undefined ? `${position.pnl_pct >= 0 ? '+' : ''}${position.pnl_pct.toFixed(2)}%` : '—'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Entry</span>
            <span className="text-white">{position.entry_price.toFixed(6)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Size</span>
            <span className="text-white">${position.size_usdt.toFixed(0)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">SL</span>
            <span className="text-red-400">{position.current_sl.toFixed(6)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">TP</span>
            <span className="text-emerald-400">{position.current_tp.toFixed(6)}</span>
          </div>
        </div>

        {/* Walk-Forward Sequence Strip */}
        <div className="border-t border-terminal-border pt-2">
          <span className="text-gray-600 text-[10px] font-mono block mb-1.5">
            WALK-FORWARD SEQUENCE
            {isLive && brainUpdate && brainUpdate.current_sax_symbol.length > 0 && (
              <span className="text-emerald-400 ml-2">
                Current: [{brainUpdate.current_sax_symbol.join(', ')}]
              </span>
            )}
          </span>
          <div className="flex gap-1 overflow-x-auto">
            {position.expected_sequence.map((sym, idx) => (
              <div
                key={idx}
                className={`flex-shrink-0 px-2 py-1 rounded text-[10px] font-mono border transition-all duration-300 ${
                  idx < position.sequence_index
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
                    : idx === position.sequence_index
                    ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400 animate-pulse'
                    : 'bg-gray-800/50 border-gray-700/50 text-gray-600'
                }`}
              >
                {idx < position.sequence_index
                  ? '\u2705'
                  : idx === position.sequence_index
                  ? '\u23F3'
                  : '\u2B1A'}{' '}
                {sym[0]},{sym[1] ?? sym[0]}
              </div>
            ))}
            {position.expected_sequence.length === 0 && (
              <span className="text-gray-700 text-[10px] font-mono">
                {isLive ? 'Esperando señal...' : 'Sin secuencia esperada'}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ─── Block C: Trie Lab (60%) ─────────────────────────── */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg flex-1 min-h-0 overflow-hidden flex flex-col">
        <div className="px-4 pt-3 pb-1 flex-shrink-0">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono text-gray-500 uppercase tracking-wider">
              Trie Lab
              {isLive && brainUpdate && (
                <span className="text-emerald-400 ml-2 normal-case">
                  Signal: {brainUpdate.signal_type}
                </span>
              )}
            </h3>
            <div className="flex items-center gap-3 text-[10px] font-mono text-gray-600">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-red-500" />
                Low
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-yellow-500" />
                Mid
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                High
              </span>
            </div>
          </div>
        </div>
        <div className="flex-1 min-h-0">
          <TrieLab activePathIds={brainUpdate?.active_path_ids} />
        </div>
      </div>
    </div>
  );
}
