import { useState } from 'react';
import type { OperationMode } from './types/position';
import TopBar from './components/TopBar';
import TradingChart from './components/TradingChart';

export default function App() {
  const [mode, setMode] = useState<OperationMode>('PAPER_LIVE');

  return (
    <div className="h-screen flex flex-col bg-terminal-bg overflow-hidden">
      <TopBar mode={mode} onModeChange={setMode} />

      {/* Main content area */}
      <main className="flex-1 flex overflow-hidden">
        {mode === 'ANALYSIS' ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-6xl mb-4">📊</div>
              <h2 className="text-xl font-mono text-gray-400 mb-2">Modo Análisis (Offline)</h2>
              <p className="text-sm text-gray-600 font-mono max-w-md">
                Usa data histórica para Monte Carlo / Backtesting.
                No hay operativa en tiempo real.
              </p>
            </div>
          </div>
        ) : (
          /* Two-panel layout: 60% chart / 40% data */
          <div className="flex-1 flex gap-0 p-4 overflow-hidden">
            {/* Left panel — Chart (60%) */}
            <div className="w-[60%] flex flex-col min-w-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold text-white">DOGE/USDT</span>
                  <span className="text-xs text-gray-500 font-mono">1m</span>
                </div>
                <div className="flex items-center gap-2 text-xs font-mono text-gray-500">
                  <span>BTC: Lateralizado</span>
                  <span className="text-terminal-accent">|</span>
                  <span>Filtros: LONG Memes OK</span>
                </div>
              </div>
              <TradingChart />
            </div>

            {/* Right panel — Data & Trie (40%) */}
            <div className="w-[40%] flex flex-col gap-4 pl-4">
              {/* BTC Context */}
              <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
                <h3 className="text-xs font-mono text-gray-500 mb-2">CONTEXTO BTC</h3>
                <div className="flex items-center gap-3">
                  <span className="w-2 h-2 rounded-full bg-yellow-500" />
                  <span className="font-mono text-sm text-gray-300">Lateralizado</span>
                  <span className="text-xs text-gray-600 font-mono">Vol: 1.02%</span>
                </div>
                <p className="text-xs text-gray-600 font-mono mt-2">
                  Filtros: LONG en Memes — APROBADO
                </p>
              </div>

              {/* Operation Status */}
              <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 flex-1">
                <h3 className="text-xs font-mono text-gray-500 mb-3">ESTADO DE OPERACIÓN</h3>
                <div className="space-y-3 font-mono text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Dirección</span>
                    <span className="text-emerald-400 font-semibold">LONG</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">P&L Actual</span>
                    <span className="text-emerald-400">+2.18%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Entrada</span>
                    <span className="text-white">0.165000</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Tamaño</span>
                    <span className="text-white">$100.00</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">SL Actual</span>
                    <span className="text-red-400">0.162600</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">TP Actual</span>
                    <span className="text-emerald-400">0.170000</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Pool Dominante</span>
                    <span className="text-yellow-400">N2 CLASS_meme</span>
                  </div>
                </div>
              </div>

              {/* Trie Lab placeholder */}
              <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 h-48 flex items-center justify-center">
                <div className="text-center">
                  <div className="text-2xl mb-2">🌳</div>
                  <p className="text-xs font-mono text-gray-600">Trie Lab (D3.js)</p>
                  <p className="text-xs font-mono text-gray-700">Entregable futuro</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
