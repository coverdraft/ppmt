import { useState, useCallback } from 'react';
import type { OperationMode, PositionState } from './types/position';
import { getMockPosition } from './mock/candles';
import TopBar from './components/TopBar';
import TradingChart from './components/TradingChart';
import RightPanel from './components/RightPanel';

export default function App() {
  const [mode, setMode] = useState<OperationMode>('PAPER_LIVE');
  const [position, setPosition] = useState<PositionState>(getMockPosition);

  const handlePositionUpdate = useCallback(
    (updater: (prev: PositionState) => PositionState) => {
      setPosition((prev) => updater(prev));
    },
    []
  );

  return (
    <div className="h-screen flex flex-col bg-terminal-bg overflow-hidden">
      <TopBar mode={mode} onModeChange={setMode} />

      {/* Main content area */}
      <main className="flex-1 flex overflow-hidden">
        {mode === 'ANALYSIS' ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-6xl mb-4">{'\uD83D\uDCCA'}</div>
              <h2 className="text-xl font-mono text-gray-400 mb-2">
                Modo An&aacute;lisis (Offline)
              </h2>
              <p className="text-sm text-gray-600 font-mono max-w-md">
                Usa data hist&oacute;rica para Monte Carlo / Backtesting.
                No hay operativa en tiempo real.
              </p>
            </div>
          </div>
        ) : (
          /* Two-panel layout: 60% chart / 40% right panel */
          <div className="flex-1 flex gap-3 p-3 overflow-hidden">
            {/* Left panel — Chart (60%) */}
            <div className="w-[60%] flex flex-col min-w-0">
              <div className="flex items-center justify-between mb-2 flex-shrink-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold text-white text-sm">
                    DOGE/USDT
                  </span>
                  <span className="text-[10px] text-gray-500 font-mono">1m</span>
                </div>
                <div className="flex items-center gap-2 text-[10px] font-mono text-gray-600">
                  <span>
                    {mode === 'LIVE_TRADING' ? 'LIVE MEXC' : 'PAPER'}
                  </span>
                  {mode === 'LIVE_TRADING' && (
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  )}
                </div>
              </div>
              <TradingChart
                position={position}
                onPositionUpdate={handlePositionUpdate}
              />
            </div>

            {/* Right panel — 40% */}
            <div className="w-[40%] min-w-0">
              <RightPanel position={position} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
