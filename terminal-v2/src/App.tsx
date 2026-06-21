import { useState, useCallback, useMemo, useEffect } from 'react';
import type { OperationMode, PositionState } from './types/position';
import { getMockPosition } from './mock/candles';
import TopBar from './components/TopBar';
import TradingChart from './components/TradingChart';
import RightPanel from './components/RightPanel';
import AuthModal from './components/AuthModal';
import TokenMatrix from './components/TokenMatrix';
import type { ActiveTrade } from './components/TokenMatrix';
import { usePaperLive } from './hooks/usePaperLive';
import { useLiveTrading } from './hooks/useLiveTrading';
import type { LiveConnectionStatus } from './hooks/useLiveTrading';

export default function App() {
  const [mode, setMode] = useState<OperationMode>('PAPER_LIVE');
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [activeTrade, setActiveTrade] = useState<ActiveTrade | null>(null);

  // Mock position state (used in ANALYSIS mode and as fallback)
  const [mockPosition, setMockPosition] = useState<PositionState>(getMockPosition);

  // ─── PAPER LIVE hook ────────────────────────────────────────
  // Only connect when activeTrade is set AND we're in PAPER_LIVE mode
  const paperLive = usePaperLive(
    mode === 'PAPER_LIVE' && activeTrade ? activeTrade.symbol : null,
    activeTrade?.timeframe ?? '1m'
  );

  // ─── LIVE TRADING hook (manual connect) ─────────────────────
  const { state: liveState, connect: liveConnect, disconnect: liveDisconnect, _wsRef: liveWsRef } = useLiveTrading(
    mode === 'LIVE_TRADING' && activeTrade ? activeTrade.symbol : null,
    activeTrade?.timeframe ?? '1m'
  );

  // Cleanup live WS on unmount
  useEffect(() => {
    return () => {
      if (liveWsRef.current) {
        liveWsRef.current.close();
        liveWsRef.current = null;
      }
    };
  }, [liveWsRef]);

  // ─── Mode change handler ────────────────────────────────────
  const handleModeChange = useCallback((newMode: OperationMode) => {
    if (newMode === 'LIVE_TRADING') {
      // Switch to LIVE mode → show auth modal (don't connect yet)
      setMode('LIVE_TRADING');
      setShowAuthModal(true);
      liveDisconnect(); // Reset any previous live state
    } else {
      // Leaving LIVE mode → disconnect
      if (mode === 'LIVE_TRADING') {
        liveDisconnect();
      }
      setMode(newMode);
      setShowAuthModal(false);
    }
  }, [mode, liveDisconnect]);

  // ─── Token selection handler ────────────────────────────────
  const handleTokenSelect = useCallback((trade: ActiveTrade) => {
    setActiveTrade(trade);
  }, []);

  // ─── Back to matrix handler ─────────────────────────────────
  const handleBackToMatrix = useCallback(() => {
    setActiveTrade(null);
  }, []);

  // ─── Auth modal handlers ────────────────────────────────────
  const handleAuthConnect = useCallback(
    (sessionPassword: string, apiKey: string, apiSecret: string, _allocatedUsdt: number, customBaseUrl?: string) => {
      liveConnect(sessionPassword, apiKey, apiSecret, _allocatedUsdt, customBaseUrl);
    },
    [liveConnect]
  );

  const handleAuthCancel = useCallback(() => {
    setShowAuthModal(false);
    // If not connected, revert to PAPER mode
    if (liveState.status !== 'connected') {
      setMode('PAPER_LIVE');
    }
  }, [liveState.status]);

  // Close modal when auth succeeds
  useEffect(() => {
    if (liveState.status === 'connected' && showAuthModal) {
      setShowAuthModal(false);
    }
  }, [liveState.status, showAuthModal]);

  // ─── Determine which data source to use ─────────────────────
  const isLive = mode === 'PAPER_LIVE' || mode === 'LIVE_TRADING';
  const isLiveTrading = mode === 'LIVE_TRADING';
  const isLiveConnected = isLiveTrading && liveState.status === 'connected';

  const candles = useMemo(() => {
    if (isLiveTrading) return liveState.candles;
    return paperLive.candles;
  }, [isLiveTrading, liveState.candles, paperLive.candles]);

  const brainUpdate = useMemo(() => {
    if (isLiveTrading) return liveState.brainUpdate;
    return paperLive.brainUpdate;
  }, [isLiveTrading, liveState.brainUpdate, paperLive.brainUpdate]);

  const position = useMemo(() => {
    if (isLiveTrading && liveState.position) return liveState.position;
    if (mode === 'PAPER_LIVE' && paperLive.position) return paperLive.position;
    return mockPosition;
  }, [isLiveTrading, liveState.position, mode, paperLive.position, mockPosition]);

  const connected = isLiveTrading ? liveState.status === 'connected' : paperLive.connected;
  const error = isLiveTrading ? liveState.error : paperLive.error;

  const handlePositionUpdate = useCallback(
    (updater: (prev: PositionState) => PositionState) => {
      setMockPosition((prev) => updater(prev));
    },
    []
  );

  // ─── Live status badge text ─────────────────────────────────
  const liveStatusText = useMemo((): { label: string; color: string } => {
    if (!isLiveTrading) return { label: '', color: '' };
    switch (liveState.status) {
      case 'idle': return { label: 'OFFLINE', color: 'text-gray-500' };
      case 'connecting': return { label: 'CONNECTING...', color: 'text-yellow-400' };
      case 'authenticating': return { label: 'AUTHENTICATING...', color: 'text-yellow-400' };
      case 'connected': return { label: 'LIVE', color: 'text-red-400' };
      case 'error': return { label: 'ERROR', color: 'text-red-500' };
    }
  }, [isLiveTrading, liveState.status]);

  // ─── Resolved display symbol/timeframe ─────────────────────
  const displaySymbol = activeTrade?.symbol ?? '—';
  const displayTimeframe = activeTrade?.timeframe ?? '—';

  return (
    <div className="h-screen flex flex-col bg-terminal-bg overflow-hidden">
      <TopBar mode={mode} onModeChange={handleModeChange} />

      {/* Main content area */}
      <main className="flex-1 flex overflow-hidden">
        {mode === 'ANALYSIS' ? (
          /* ─── ANALYSIS: Offline placeholder ─── */
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-6xl mb-4">{'\uD83D\uDCCA'}</div>
              <h2 className="text-xl font-mono text-gray-400 mb-2">
                Modo Análisis (Offline)
              </h2>
              <p className="text-sm text-gray-600 font-mono max-w-md">
                Usa data histórica para Monte Carlo / Backtesting.
                No hay operativa en tiempo real.
              </p>
            </div>
          </div>
        ) : !activeTrade ? (
          /* ─── TOKEN MATRIX: No token selected yet ─── */
          <TokenMatrix onSelect={handleTokenSelect} />
        ) : (
          /* ─── TRADING LAYOUT: 60% chart / 40% right panel ─── */
          <div className="flex-1 flex gap-3 p-3 overflow-hidden">
            {/* Left panel — Chart (60%) */}
            <div className="w-[60%] flex flex-col min-w-0">
              <div className="flex items-center justify-between mb-2 flex-shrink-0">
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleBackToMatrix}
                    className="text-gray-500 hover:text-white transition-colors mr-1"
                    title="Volver a selección de token"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                    </svg>
                  </button>
                  <span className="font-mono font-semibold text-white text-sm">
                    {displaySymbol}
                  </span>
                  <span className="text-[10px] text-gray-500 font-mono">{displayTimeframe}</span>
                  {/* Connection status indicator */}
                  <span className={`flex items-center gap-1 text-[10px] font-mono ${
                    connected ? 'text-emerald-400' : 'text-red-400'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      connected
                        ? isLiveTrading ? 'bg-red-500 animate-pulse' : 'bg-emerald-500 animate-pulse'
                        : 'bg-red-500'
                    }`} />
                    {isLiveTrading ? liveStatusText.label : (connected ? 'LIVE' : 'DISCONNECTED')}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[10px] font-mono text-gray-600">
                  <span>
                    {isLiveTrading ? 'LIVE MEXC' : 'PAPER'}
                  </span>
                  {isLiveTrading && liveState.status === 'connected' && (
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  )}
                </div>
              </div>
              <TradingChart
                position={position}
                onPositionUpdate={handlePositionUpdate}
                liveCandles={candles}
                isLive={isLive}
              />
              {/* Error display */}
              {error && (
                <div className="mt-1 px-2 py-1 bg-red-900/20 border border-red-800/30 rounded text-red-400 text-[10px] font-mono">
                  {error}
                </div>
              )}
              {/* Reconnect button for LIVE mode errors */}
              {isLiveTrading && liveState.status === 'error' && (
                <button
                  onClick={() => setShowAuthModal(true)}
                  className="mt-2 px-3 py-1.5 bg-red-600/20 border border-red-500/30 rounded text-red-400 text-xs font-mono hover:bg-red-600/30 transition-colors"
                >
                  REINTENTAR CONEXIÓN
                </button>
              )}
            </div>

            {/* Right panel — 40% */}
            <div className="w-[40%] min-w-0">
              <RightPanel
                position={position}
                brainUpdate={brainUpdate}
                isLive={isLive}
              />
            </div>
          </div>
        )}
      </main>

      {/* ─── Auth Modal (LIVE TRADING) ─────────────────────────── */}
      {showAuthModal && isLiveTrading && (
        <AuthModal
          onConnect={handleAuthConnect}
          onCancel={handleAuthCancel}
          status={liveState.status}
          error={liveState.error}
        />
      )}
    </div>
  );
}
