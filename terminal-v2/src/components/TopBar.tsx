import type { OperationMode } from '../types/position';

interface TopBarProps {
  mode: OperationMode;
  onModeChange: (mode: OperationMode) => void;
}

const modes: { key: OperationMode; label: string; activeColor: string }[] = [
  { key: 'ANALYSIS', label: 'ANÁLISIS', activeColor: 'bg-gray-600 text-white' },
  { key: 'PAPER_LIVE', label: 'PAPER LIVE', activeColor: 'bg-gray-700 text-gray-200' },
  { key: 'LIVE_TRADING', label: 'LIVE TRADING (MEXC)', activeColor: 'bg-red-900/60 text-red-300 border border-red-500/30' },
];

export default function TopBar({ mode, onModeChange }: TopBarProps) {
  return (
    <header
      className={`h-14 flex items-center justify-between px-4 border-b transition-colors duration-300 ${
        mode === 'LIVE_TRADING'
          ? 'bg-[#0f0a0a] border-red-900/40'
          : mode === 'PAPER_LIVE'
          ? 'bg-[#0a0a12] border-terminal-border'
          : 'bg-terminal-bg border-terminal-border'
      }`}
    >
      {/* Logo */}
      <div className="flex items-center gap-3">
        <span className="font-mono font-bold text-lg tracking-tight text-white">
          PPMT<span className="text-terminal-accent">.</span>
        </span>
        <span className="text-xs text-gray-600 font-mono">v2.0</span>
      </div>

      {/* Mode Toggle */}
      <div className="flex items-center gap-1 bg-terminal-surface rounded-lg p-1 border border-terminal-border">
        {modes.map((m) => (
          <button
            key={m.key}
            onClick={() => onModeChange(m.key)}
            className={`px-4 py-1.5 rounded-md text-xs font-mono font-semibold transition-all duration-200 ${
              mode === m.key ? m.activeColor : 'text-gray-400 hover:bg-gray-800/50'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Status Badge */}
      <div className="flex items-center gap-2">
        {mode === 'LIVE_TRADING' && (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-xs font-mono text-red-400">LIVE</span>
          </span>
        )}
        {mode === 'PAPER_LIVE' && (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-yellow-500" />
            <span className="text-xs font-mono text-yellow-400">PAPER</span>
          </span>
        )}
        {mode === 'ANALYSIS' && (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-gray-500" />
            <span className="text-xs font-mono text-gray-400">OFFLINE</span>
          </span>
        )}
      </div>
    </header>
  );
}
