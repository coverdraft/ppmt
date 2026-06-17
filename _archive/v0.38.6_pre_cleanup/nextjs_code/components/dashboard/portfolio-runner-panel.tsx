'use client';

import { useState, useCallback } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Play,
  Square,
  Loader2,
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  Shield,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  BarChart3,
  Zap,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

interface TokenResult {
  asset_class: string;
  alpha: number;
  window: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  pnl: number;
  pnl_pct: number;
  max_dd: number;
  signals_generated: number;
  signals_approved: number;
  signals_rejected: number;
  rejection_rate: number;
}

interface RunnerResult {
  total_capital: number;
  final_value: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  total_trades: number;
  total_wins: number;
  total_losses: number;
  win_rate: number;
  total_signals: number;
  signals_approved: number;
  signals_rejected: number;
  rejection_rate: number;
  rebalance_count: number;
  regime_transitions: number;
  duration_candles: number;
  tokens: Record<string, TokenResult>;
}

// ============================================================
// PORTFOLIO RUNNER PANEL
// ============================================================

export function PortfolioRunnerPanel() {
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<RunnerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tokens, setTokens] = useState<string>('BTC/USDT,ETH/USDT,SOL/USDT');
  const [timeframe, setTimeframe] = useState('1h');
  const [allocationMethod, setAllocationMethod] = useState('REGIME_AWARE');
  const [initialCapital, setInitialCapital] = useState('50000');

  const startRunner = useCallback(async () => {
    setIsRunning(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch('/api/ppmt/runner', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tokens: tokens.split(',').map(t => t.trim()).filter(Boolean),
          timeframe,
          allocationMethod,
          initialCapital: parseFloat(initialCapital),
        }),
      });

      const data = await res.json();

      if (data.success && data.result) {
        setResult(data.result);
        toast.success('Portfolio Runner completed', {
          description: `Return: ${data.result.total_return_pct >= 0 ? '+' : ''}${data.result.total_return_pct.toFixed(1)}%`,
        });
      } else {
        setError(data.error || 'Unknown error');
        toast.error('Portfolio Runner failed', { description: data.error });
      }
    } catch (err: any) {
      setError(err.message);
      toast.error('Portfolio Runner failed', { description: err.message });
    } finally {
      setIsRunning(false);
    }
  }, [tokens, timeframe, allocationMethod, initialCapital]);

  const stopRunner = useCallback(async () => {
    try {
      await fetch('/api/ppmt/runner', { method: 'DELETE' });
      setIsRunning(false);
      toast.info('Portfolio Runner stopped');
    } catch {
      // Ignore
    }
  }, []);

  const fetchResult = useCallback(async () => {
    try {
      const res = await fetch('/api/ppmt/runner');
      const data = await res.json();
      if (data.success && data.result) {
        setResult(data.result);
      }
    } catch {
      // Ignore
    }
  }, []);

  // ============================================================
  // RENDER HELPERS
  // ============================================================

  const renderMetricCard = (
    label: string,
    value: string,
    icon: React.ReactNode,
    color: string = 'text-foreground',
  ) => (
    <div className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/30 p-3">
      <div className={`rounded-md p-1.5 ${color === 'text-green-500' ? 'bg-green-500/10' : color === 'text-red-500' ? 'bg-red-500/10' : 'bg-primary/10'}`}>
        {icon}
      </div>
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={`text-sm font-bold ${color}`}>{value}</p>
      </div>
    </div>
  );

  const returnColor = result && result.total_return_pct >= 0 ? 'text-green-500' : 'text-red-500';
  const returnIcon = result && result.total_return_pct >= 0 ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />;

  return (
    <div className="space-y-6">
      {/* ============================================================ */}
      {/* Configuration Panel */}
      {/* ============================================================ */}
      <Card className="p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Zap className="h-5 w-5 text-cyan-500" />
            <h3 className="text-lg font-bold">Portfolio Runner</h3>
            <Badge variant="outline" className="text-xs">v0.17.0</Badge>
          </div>
          <div className="flex items-center gap-2">
            {isRunning && (
              <Badge variant="default" className="bg-cyan-500 text-white animate-pulse">
                <Loader2 className="h-3 w-3 mr-1 animate-spin" /> Running
              </Badge>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Tokens */}
          <div className="col-span-2">
            <Label className="text-xs text-muted-foreground">Tokens (comma-separated)</Label>
            <input
              type="text"
              value={tokens}
              onChange={(e) => setTokens(e.target.value)}
              className="w-full mt-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
              placeholder="BTC/USDT,ETH/USDT,SOL/USDT"
              disabled={isRunning}
            />
          </div>

          {/* Timeframe */}
          <div>
            <Label className="text-xs text-muted-foreground">Timeframe</Label>
            <Select value={timeframe} onValueChange={setTimeframe} disabled={isRunning}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1m">1m</SelectItem>
                <SelectItem value="5m">5m</SelectItem>
                <SelectItem value="1h">1h</SelectItem>
                <SelectItem value="4h">4h</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Allocation Method */}
          <div>
            <Label className="text-xs text-muted-foreground">Allocation</Label>
            <Select value={allocationMethod} onValueChange={setAllocationMethod} disabled={isRunning}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="EQUAL_WEIGHT">Equal Weight</SelectItem>
                <SelectItem value="RISK_PARITY">Risk Parity</SelectItem>
                <SelectItem value="REGIME_AWARE">Regime Aware</SelectItem>
                <SelectItem value="QUALITY_WEIGHTED">Quality Weighted</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Initial Capital */}
          <div>
            <Label className="text-xs text-muted-foreground">Initial Capital ($)</Label>
            <input
              type="number"
              value={initialCapital}
              onChange={(e) => setInitialCapital(e.target.value)}
              className="w-full mt-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
              disabled={isRunning}
            />
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 mt-4">
          <Button
            onClick={startRunner}
            disabled={isRunning}
            className="bg-cyan-600 hover:bg-cyan-700 text-white"
          >
            {isRunning ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Running...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Start Runner
              </>
            )}
          </Button>

          <Button
            onClick={stopRunner}
            disabled={!isRunning}
            variant="destructive"
          >
            <Square className="h-4 w-4 mr-2" />
            Stop
          </Button>

          <Button
            onClick={fetchResult}
            variant="outline"
          >
            <Activity className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </Card>

      {/* ============================================================ */}
      {/* Error Display */}
      {/* ============================================================ */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            <Card className="p-4 border-red-500/50 bg-red-500/5">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-red-500" />
                <p className="text-sm text-red-500 font-medium">Error: {error}</p>
              </div>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ============================================================ */}
      {/* Results */}
      {/* ============================================================ */}
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            className="space-y-4"
          >
            {/* Portfolio Overview */}
            <Card className="p-4">
              <h4 className="text-sm font-semibold text-muted-foreground mb-3 flex items-center gap-2">
                <BarChart3 className="h-4 w-4" /> Portfolio Overview
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {renderMetricCard('Return', `${result.total_return_pct >= 0 ? '+' : ''}${result.total_return_pct.toFixed(1)}%`, returnIcon, returnColor)}
                {renderMetricCard('Final Value', `$${result.final_value.toLocaleString()}`, <Target className="h-4 w-4" />)}
                {renderMetricCard('Sharpe', result.sharpe_ratio.toFixed(2), <Activity className="h-4 w-4" />)}
                {renderMetricCard('Max DD', `${result.max_drawdown_pct.toFixed(1)}%`, <TrendingDown className="h-4 w-4 text-red-500" />, 'text-red-500')}
                {renderMetricCard('Calmar', result.calmar_ratio.toFixed(2), <Shield className="h-4 w-4" />)}
                {renderMetricCard('Sortino', result.sortino_ratio.toFixed(2), <Shield className="h-4 w-4" />)}
                {renderMetricCard('Win Rate', `${(result.win_rate * 100).toFixed(0)}%`, <CheckCircle2 className="h-4 w-4 text-green-500" />, 'text-green-500')}
                {renderMetricCard('Rebalances', `${result.rebalance_count}`, <Zap className="h-4 w-4" />)}
              </div>
            </Card>

            {/* Signal Statistics */}
            <Card className="p-4">
              <h4 className="text-sm font-semibold text-muted-foreground mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4" /> Signal Statistics
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {renderMetricCard('Total Signals', `${result.total_signals}`, <Zap className="h-4 w-4" />)}
                {renderMetricCard('Approved', `${result.signals_approved}`, <CheckCircle2 className="h-4 w-4 text-green-500" />, 'text-green-500')}
                {renderMetricCard('Rejected', `${result.signals_rejected}`, <XCircle className="h-4 w-4 text-red-500" />, 'text-red-500')}
                {renderMetricCard('Rejection Rate', `${result.rejection_rate.toFixed(1)}%`, <AlertTriangle className="h-4 w-4 text-yellow-500" />, 'text-yellow-500')}
              </div>
            </Card>

            {/* Per-Token Results */}
            <Card className="p-4">
              <h4 className="text-sm font-semibold text-muted-foreground mb-3 flex items-center gap-2">
                <BarChart3 className="h-4 w-4" /> Per-Token Performance
              </h4>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border/50">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Token</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Class</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Trades</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Win Rate</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">PnL %</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Max DD</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Signals</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Approved</th>
                      <th className="text-right py-2 px-3 font-medium text-muted-foreground">Rejected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(result.tokens).map(([symbol, r]) => (
                      <tr key={symbol} className="border-b border-border/30 hover:bg-muted/20">
                        <td className="py-2 px-3 font-medium">{symbol}</td>
                        <td className="py-2 px-3">
                          <Badge variant="outline" className="text-xs">{r.asset_class}</Badge>
                        </td>
                        <td className="py-2 px-3 text-right">{r.trades}</td>
                        <td className="py-2 px-3 text-right">
                          <span className={r.win_rate >= 0.5 ? 'text-green-500' : 'text-red-500'}>
                            {(r.win_rate * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right">
                          <span className={r.pnl_pct >= 0 ? 'text-green-500' : 'text-red-500'}>
                            {r.pnl_pct >= 0 ? '+' : ''}{r.pnl_pct.toFixed(1)}%
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right text-red-400">{r.max_dd.toFixed(1)}%</td>
                        <td className="py-2 px-3 text-right">{r.signals_generated}</td>
                        <td className="py-2 px-3 text-right text-green-500">{r.signals_approved}</td>
                        <td className="py-2 px-3 text-right text-red-400">{r.signals_rejected}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* Session Info */}
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Duration: {result.duration_candles} candles ({(result.duration_candles / 24).toFixed(0)} days at 1h)</span>
              <span>Regime changes: {result.regime_transitions}</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
