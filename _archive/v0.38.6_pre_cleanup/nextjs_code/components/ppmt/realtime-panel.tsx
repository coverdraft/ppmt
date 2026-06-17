'use client';

import { useState, useEffect, useCallback } from 'react';
import { ppmtRealtimeService, type PPMTSignal } from '@/lib/services/execution/ppmt-realtime-service';
import { cn } from '@/lib/utils';
import {
  Activity,
  Play,
  Square,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Minus,
  Wifi,
  WifiOff,
  Zap,
  BarChart3,
  Shield,
  Clock,
  Bot,
  DollarSign,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

// Active profiles from the user's setup
const DEFAULT_PROFILES = [
  { symbol: 'BTC/USDT', timeframe: '1h', assetClass: 'blue_chip' },
  { symbol: 'ETH/USDT', timeframe: '1h', assetClass: 'blue_chip' },
  { symbol: 'SOL/USDT', timeframe: '5m', assetClass: 'large_cap' },
  { symbol: 'DOGE/USDT', timeframe: '5m', assetClass: 'meme' },
  { symbol: 'LINK/USDT', timeframe: '1m', assetClass: 'defi' },
];

const DIRECTION_CONFIG = {
  LONG: { icon: TrendingUp, color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' },
  SHORT: { icon: TrendingDown, color: 'text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/20' },
  FLAT: { icon: Minus, color: 'text-zinc-400', bg: 'bg-zinc-800', border: 'border-zinc-700' },
};

const SIGNAL_TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  ENTRY_LONG: { label: 'LONG', color: 'text-emerald-400', bg: 'bg-emerald-500/20' },
  ENTRY_SHORT: { label: 'SHORT', color: 'text-rose-400', bg: 'bg-rose-500/20' },
  EXIT: { label: 'EXIT', color: 'text-amber-400', bg: 'bg-amber-500/20' },
  HOLD: { label: 'HOLD', color: 'text-cyan-400', bg: 'bg-cyan-500/20' },
  TRAILING: { label: 'TRAIL', color: 'text-violet-400', bg: 'bg-violet-500/20' },
  NO_SIGNAL: { label: '—', color: 'text-zinc-500', bg: 'bg-zinc-800' },
};

// Position type from the paper trading API
interface PPMTPosition {
  id: string;
  symbol: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  entryTime: string;
  entryPrice: number;
  currentPrice: number;
  positionSizeUsd: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  systemName: string;
  exitConditions: string[];
}

export function PPMTRealtimePanel() {
  const [signals, setSignals] = useState<Map<string, PPMTSignal>>(new Map());
  const [isLive, setIsLive] = useState(false);
  const [activeSubs, setActiveSubs] = useState<Array<{ symbol: string; timeframe: string }>>([]);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [signalLog, setSignalLog] = useState<Array<{ time: Date; signal: PPMTSignal }>>([]);
  const [loadingSymbol, setLoadingSymbol] = useState<string | null>(null);

  // Auto-trade state
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [positions, setPositions] = useState<PPMTPosition[]>([]);
  const [positionsLoading, setPositionsLoading] = useState(false);

  // Fetch PPMT positions from paper trading
  const fetchPositions = useCallback(async () => {
    setPositionsLoading(true);
    try {
      const res = await fetch('/api/paper-trading/positions');
      if (res.ok) {
        const json = await res.json();
        const allPositions: PPMTPosition[] = json.data || [];
        // Filter for PPMT_Auto positions only
        const ppmtPositions = allPositions.filter(
          (p) => p.systemName === 'PPMT_Auto'
        );
        setPositions(ppmtPositions);
      }
    } catch {
      // Silently fail
    } finally {
      setPositionsLoading(false);
    }
  }, []);

  // Poll positions periodically when live
  useEffect(() => {
    if (!isLive) return;
    fetchPositions();
    const interval = setInterval(fetchPositions, 10000); // every 10s
    return () => clearInterval(interval);
  }, [isLive, fetchPositions]);

  // Toggle auto-trade on server
  const toggleAutoTrade = useCallback(async () => {
    try {
      const res = await fetch('/api/ppmt/realtime', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'toggle_paper_trading' }),
      });
      const json = await res.json();
      if (json.success) {
        setAutoTradeEnabled(json.paperTradingEnabled);
        toast.success(json.paperTradingEnabled
          ? 'Auto-trade enabled — signals will execute paper trades'
          : 'Auto-trade disabled'
        );
      }
    } catch {
      toast.error('Failed to toggle auto-trade');
    }
  }, []);

  // Execute auto-trade for a signal
  const executeAutoTrade = useCallback(async (signal: PPMTSignal) => {
    if (!signal.signal) return;
    const signalType = signal.signal.signal_type;
    if (signalType !== 'ENTRY_LONG' && signalType !== 'ENTRY_SHORT') return;

    try {
      const res = await fetch('/api/ppmt/realtime/trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ signal }),
      });
      const json = await res.json();

      if (json.success) {
        const direction = signalType === 'ENTRY_LONG' ? 'LONG' : 'SHORT';
        const price = signal.current_price;
        toast.success(
          `Auto-trade executed: ${signal.symbol} ${direction} @ $${price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
          { icon: '🤖' }
        );
        // Refresh positions
        fetchPositions();
      } else {
        toast.warning(`Auto-trade skipped: ${json.error || json.message || 'Unknown reason'}`);
      }
    } catch {
      toast.error('Auto-trade execution failed');
    }
  }, [fetchPositions]);

  // Listen for signals
  useEffect(() => {
    const onSignal = (signal: PPMTSignal) => {
      setSignals(prev => {
        const next = new Map(prev);
        next.set(`${signal.symbol}@${signal.timeframe}`, signal);
        return next;
      });
      setLastUpdate(new Date());

      if (signal.signal) {
        setSignalLog(prev => [{ time: new Date(), signal }, ...prev].slice(0, 20));

        // Auto-trade if enabled
        if (autoTradeEnabled) {
          const signalType = signal.signal.signal_type;
          if (signalType === 'ENTRY_LONG' || signalType === 'ENTRY_SHORT') {
            executeAutoTrade(signal);
          }
        }
      }
    };

    ppmtRealtimeService.on('signal', onSignal);
    return () => {
      ppmtRealtimeService.off('signal', onSignal);
    };
  }, [autoTradeEnabled, executeAutoTrade]);

  // Sync auto-trade state on mount
  useEffect(() => {
    fetch('/api/ppmt/realtime')
      .then(res => res.json())
      .then(data => {
        if (data.paperTradingEnabled !== undefined) {
          setAutoTradeEnabled(data.paperTradingEnabled);
        }
      })
      .catch(() => {});
  }, []);

  const startLive = useCallback(() => {
    for (const profile of DEFAULT_PROFILES) {
      ppmtRealtimeService.subscribe(profile.symbol, profile.timeframe);
    }
    setIsLive(true);
    setActiveSubs(DEFAULT_PROFILES.map(p => ({ symbol: p.symbol, timeframe: p.timeframe })));

    // Notify server that we're live
    fetch('/api/ppmt/realtime', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'start' }),
    }).catch(() => {});

    toast.success('PPMT Real-Time started — monitoring 5 profiles');
  }, []);

  const stopLive = useCallback(() => {
    for (const profile of DEFAULT_PROFILES) {
      ppmtRealtimeService.unsubscribe(profile.symbol, profile.timeframe);
    }
    ppmtRealtimeService.stop();
    setIsLive(false);
    setActiveSubs([]);
    setPositions([]);

    // Notify server
    fetch('/api/ppmt/realtime', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop' }),
    }).catch(() => {});

    toast.info('PPMT Real-Time stopped');
  }, []);

  const runPrediction = useCallback(async (symbol: string, timeframe: string) => {
    setLoadingSymbol(`${symbol}@${timeframe}`);
    try {
      const result = await ppmtRealtimeService.predict(symbol, timeframe);
      if (result) {
        setSignals(prev => {
          const next = new Map(prev);
          next.set(`${symbol}@${timeframe}`, result);
          return next;
        });
        setLastUpdate(new Date());
        if (result.signal) {
          toast.success(`${symbol}: ${result.signal.signal_type} ${(result.signal.confidence * 100).toFixed(0)}%`);
        } else {
          toast.info(`${symbol}: No signal (${result.prediction.direction})`);
        }
      }
    } catch {
      toast.error(`Prediction failed for ${symbol}`);
    } finally {
      setLoadingSymbol(null);
    }
  }, []);

  const runAllPredictions = useCallback(async () => {
    for (const profile of DEFAULT_PROFILES) {
      await runPrediction(profile.symbol, profile.timeframe);
    }
  }, [runPrediction]);

  // Close a PPMT position
  const closePosition = useCallback(async (positionId: string) => {
    try {
      const res = await fetch('/api/paper-trading/positions', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ positionId, reason: 'manual_close' }),
      });
      const json = await res.json();
      if (json.data) {
        toast.success(`Position closed — PnL: ${json.data.pnl >= 0 ? '+' : ''}$${json.data.pnl.toFixed(2)}`);
        fetchPositions();
      }
    } catch {
      toast.error('Failed to close position');
    }
  }, [fetchPositions]);

  // Total unrealized PnL
  const totalPnl = positions.reduce((sum, p) => sum + p.unrealizedPnl, 0);
  const totalPnlPct = positions.length > 0
    ? positions.reduce((sum, p) => sum + p.unrealizedPnlPct, 0) / positions.length
    : 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="h-5 w-5 text-cyan-400" />
          <h2 className="text-lg font-bold text-zinc-50">PPMT Real-Time</h2>
          <span className={cn(
            'text-xs px-2 py-0.5 rounded-full flex items-center gap-1',
            isLive ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-800 text-zinc-500'
          )}>
            {isLive ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
            {isLive ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Auto Trade Toggle */}
          <button
            onClick={toggleAutoTrade}
            className={cn(
              'flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg transition-colors',
              autoTradeEnabled
                ? 'bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border border-amber-500/30'
                : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700 border border-zinc-700'
            )}
          >
            <Bot className="h-3 w-3" />
            Auto Trade
            {autoTradeEnabled && <span className="ml-1 w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />}
          </button>
          <button
            onClick={runAllPredictions}
            disabled={loadingSymbol !== null}
            className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3 w-3', loadingSymbol && 'animate-spin')} />
            Scan All
          </button>
          {!isLive ? (
            <button
              onClick={startLive}
              className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 transition-colors"
            >
              <Play className="h-3 w-3" />
              Go Live
            </button>
          ) : (
            <button
              onClick={stopLive}
              className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-rose-600 text-white hover:bg-rose-500 transition-colors"
            >
              <Square className="h-3 w-3" />
              Stop
            </button>
          )}
        </div>
      </div>

      {/* Signal Grid */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
        {DEFAULT_PROFILES.map((profile) => {
          const key = `${profile.symbol}@${profile.timeframe}`;
          const signal = signals.get(key);
          const dir = signal?.prediction?.direction || 'FLAT';
          const dirCfg = DIRECTION_CONFIG[dir as keyof typeof DIRECTION_CONFIG] || DIRECTION_CONFIG.FLAT;
          const DirIcon = dirCfg.icon;
          const isLoading = loadingSymbol === key;

          return (
            <div
              key={key}
              className={cn(
                'bg-zinc-900 rounded-xl border p-3 transition-all duration-200',
                signal?.signal ? dirCfg.border : 'border-zinc-800',
                isLoading && 'animate-pulse'
              )}
            >
              {/* Symbol header */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5">
                  <span className="font-bold text-sm text-zinc-100">{profile.symbol.split('/')[0]}</span>
                  <span className="text-[10px] text-zinc-500 font-mono">@{profile.timeframe}</span>
                </div>
                <DirIcon className={cn('h-4 w-4', dirCfg.color)} />
              </div>

              {/* Price */}
              <div className="text-lg font-bold text-zinc-50 tabular-nums mb-1">
                {signal?.current_price ? `$${signal.current_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—'}
              </div>

              {/* Prediction */}
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-zinc-500">Move</span>
                  <span className={cn('font-mono', signal?.prediction?.expected_move_pct && signal.prediction.expected_move_pct >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
                    {signal?.prediction ? `${signal.prediction.expected_move_pct >= 0 ? '+' : ''}${signal.prediction.expected_move_pct.toFixed(2)}%` : '—'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-zinc-500">Conf</span>
                  <span className="text-zinc-300 font-mono">
                    {signal?.prediction ? `${(signal.prediction.confidence * 100).toFixed(0)}%` : '—'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-zinc-500">Regime</span>
                  <span className="text-zinc-300 capitalize">
                    {signal?.regime?.name || '—'}
                  </span>
                </div>
              </div>

              {/* Signal badge */}
              <div className="mt-2 pt-2 border-t border-zinc-800">
                {signal?.signal ? (
                  <div className="flex items-center justify-between">
                    <span className={cn('text-xs font-bold px-2 py-0.5 rounded', SIGNAL_TYPE_CONFIG[signal.signal.signal_type]?.bg, SIGNAL_TYPE_CONFIG[signal.signal.signal_type]?.color)}>
                      {SIGNAL_TYPE_CONFIG[signal.signal.signal_type]?.label || signal.signal.signal_type}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      R:R {signal.signal.risk_reward_ratio.toFixed(1)} | WR {(signal.signal.win_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-zinc-600">No signal</span>
                    <button
                      onClick={() => runPrediction(profile.symbol, profile.timeframe)}
                      disabled={isLoading}
                      className="text-[10px] text-cyan-400 hover:text-cyan-300 disabled:opacity-50"
                    >
                      {isLoading ? '...' : 'Predict'}
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Signal Log */}
      {signalLog.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <h3 className="text-sm font-medium text-zinc-300 mb-3 flex items-center gap-1.5">
            <Zap className="h-4 w-4 text-amber-400" />
            Signal Log
          </h3>
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {signalLog.map((entry, i) => {
              const sigCfg = SIGNAL_TYPE_CONFIG[entry.signal.signal?.signal_type || 'NO_SIGNAL'];
              return (
                <div key={i} className="flex items-center justify-between py-1 text-xs">
                  <div className="flex items-center gap-2">
                    <Clock className="h-3 w-3 text-zinc-600" />
                    <span className="text-zinc-500 font-mono">
                      {entry.time.toLocaleTimeString()}
                    </span>
                    <span className="text-zinc-300">{entry.signal.symbol}</span>
                    <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-bold', sigCfg.bg, sigCfg.color)}>
                      {sigCfg.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-zinc-500">
                    <span>Move: <span className={entry.signal.prediction.expected_move_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {entry.signal.prediction.expected_move_pct >= 0 ? '+' : ''}{entry.signal.prediction.expected_move_pct.toFixed(2)}%
                    </span></span>
                    <span>Conf: <span className="text-zinc-300">{(entry.signal.signal?.confidence || 0) * 100}%</span></span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Open PPMT Positions */}
      {isLive && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-1.5">
              <Shield className="h-4 w-4 text-cyan-400" />
              PPMT Auto Positions
              {positions.length > 0 && (
                <span className="text-xs bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded-full ml-1">
                  {positions.length}
                </span>
              )}
            </h3>
            {positions.length > 0 && (
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1 text-xs">
                  <DollarSign className="h-3 w-3 text-zinc-500" />
                  <span className="text-zinc-500">Total PnL:</span>
                  <span className={cn(
                    'font-mono font-bold',
                    totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  )}>
                    {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
                  </span>
                  <span className={cn(
                    'text-[10px]',
                    totalPnlPct >= 0 ? 'text-emerald-500' : 'text-rose-500'
                  )}>
                    ({totalPnlPct >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%)
                  </span>
                </div>
              </div>
            )}
          </div>

          {positions.length === 0 ? (
            <div className="text-xs text-zinc-600 text-center py-4">
              {autoTradeEnabled
                ? 'Waiting for trade signals to auto-execute...'
                : 'Enable Auto Trade to automatically open positions from signals'
              }
            </div>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {positions.map((pos) => (
                <div
                  key={pos.id}
                  className="flex items-center justify-between bg-zinc-800/50 rounded-lg p-3 border border-zinc-700/50"
                >
                  <div className="flex items-center gap-3">
                    <div className={cn(
                      'flex items-center justify-center w-8 h-8 rounded-lg',
                      pos.direction === 'LONG' ? 'bg-emerald-500/20' : 'bg-rose-500/20'
                    )}>
                      {pos.direction === 'LONG' ? (
                        <TrendingUp className="h-4 w-4 text-emerald-400" />
                      ) : (
                        <TrendingDown className="h-4 w-4 text-rose-400" />
                      )}
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-bold text-zinc-100">{pos.symbol}</span>
                        <span className={cn(
                          'text-[10px] font-bold px-1.5 py-0.5 rounded',
                          pos.direction === 'LONG' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'
                        )}>
                          {pos.direction}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                        <span>Entry: ${pos.entryPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                        <span>→</span>
                        <span>Now: ${pos.currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    {/* PnL */}
                    <div className="text-right">
                      <div className={cn(
                        'text-sm font-bold font-mono',
                        pos.unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      )}>
                        {pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl.toFixed(2)}
                      </div>
                      <div className={cn(
                        'text-[10px] font-mono',
                        pos.unrealizedPnlPct >= 0 ? 'text-emerald-500' : 'text-rose-500'
                      )}>
                        {pos.unrealizedPnlPct >= 0 ? '+' : ''}{pos.unrealizedPnlPct.toFixed(2)}%
                      </div>
                    </div>

                    {/* Close button */}
                    <button
                      onClick={() => closePosition(pos.id)}
                      className="p-1 rounded hover:bg-zinc-700 text-zinc-500 hover:text-rose-400 transition-colors"
                      title="Close position"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Last update */}
      {lastUpdate && (
        <div className="text-[10px] text-zinc-600 text-right">
          Last update: {lastUpdate.toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
