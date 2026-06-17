'use client';

import { usePPMTStore } from '@/store/ppmt-strategy-store';
import { useEffect } from 'react';
import { LifecyclePipeline } from './lifecycle-pipeline';
import { StrategyCard } from './strategy-card';
import { CreateStrategyDialog } from './create-strategy-dialog';
import { EquityCurveChart } from './equity-curve-chart';
import { cn } from '@/lib/utils';
import {
  TrendingUp,
  Target,
  Shield,
  Wallet,
  Activity,
} from 'lucide-react';

function StatCard({ icon, label, value, subValue, colorClass }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  subValue?: string;
  colorClass: string;
}) {
  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className={cn('p-1.5 rounded-lg', colorClass)}>{icon}</span>
        <span className="text-xs text-zinc-500 uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-xl font-bold text-zinc-50 tabular-nums">{value}</div>
      {subValue && <div className="text-xs text-zinc-500 mt-0.5">{subValue}</div>}
    </div>
  );
}

export function OverviewTab() {
  const { strategies, fetchStrategies, isLoading } = usePPMTStore();

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  const totalPnl = strategies.reduce((sum, s) => sum + s.totalPnl, 0);
  const totalCapital = strategies.reduce((sum, s) => sum + s.capitalAllocated, 0);
  const liveStrategies = strategies.filter((s) => s.status === 'live');
  const avgWinRate = strategies.length > 0
    ? strategies.filter((s) => s.totalTrades > 0).reduce((sum, s) => sum + s.winRate, 0) /
      Math.max(strategies.filter((s) => s.totalTrades > 0).length, 1)
    : 0;
  const bestStrategy = [...strategies].sort((a, b) => b.totalPnlPct - a.totalPnlPct)[0];

  // Generate aggregate equity curve from best strategy
  const bestEquity = bestStrategy?.runs?.[0]?.equityCurve
    ? JSON.parse(bestStrategy.runs[0].equityCurve)
    : [];

  return (
    <div className="space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          icon={<TrendingUp className="h-4 w-4" />}
          label="Total PnL"
          value={`$${totalPnl.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
          subValue={`${strategies.length} strategies`}
          colorClass="bg-emerald-500/10 text-emerald-400"
        />
        <StatCard
          icon={<Target className="h-4 w-4" />}
          label="Avg Win Rate"
          value={`${(avgWinRate * 100).toFixed(1)}%`}
          subValue={`${strategies.filter(s => s.totalTrades > 0).length} with data`}
          colorClass="bg-cyan-500/10 text-cyan-400"
        />
        <StatCard
          icon={<Shield className="h-4 w-4" />}
          label="Live Strategies"
          value={String(liveStrategies.length)}
          subValue={`of ${strategies.length} total`}
          colorClass="bg-amber-500/10 text-amber-400"
        />
        <StatCard
          icon={<Wallet className="h-4 w-4" />}
          label="Capital Deployed"
          value={`$${totalCapital.toLocaleString()}`}
          colorClass="bg-violet-500/10 text-violet-400"
        />
      </div>

      {/* Lifecycle pipeline */}
      <LifecyclePipeline />

      {/* Best performer equity curve */}
      {bestStrategy && bestEquity.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-medium text-zinc-300">
                Best Performer: {bestStrategy.symbol} @ {bestStrategy.timeframe}
              </h3>
              <span className={cn(
                'text-xs',
                bestStrategy.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-rose-400'
              )}>
                {bestStrategy.totalPnlPct >= 0 ? '+' : ''}{bestStrategy.totalPnlPct.toFixed(1)}% PnL
              </span>
            </div>
            <Activity className="h-4 w-4 text-zinc-600" />
          </div>
          <EquityCurveChart equityCurve={bestEquity} capital={bestStrategy.initialCapital} />
        </div>
      )}

      {/* Strategy cards grid */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-zinc-300">All Strategies</h3>
          <CreateStrategyDialog />
        </div>
        {isLoading ? (
          <div className="flex items-center justify-center py-12 text-zinc-500">
            <Activity className="h-5 w-5 animate-spin mr-2" />
            Loading strategies...
          </div>
        ) : strategies.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-zinc-500">
            <p className="mb-3">No strategies yet</p>
            <CreateStrategyDialog />
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {strategies.map((s) => (
              <StrategyCard key={s.id} strategy={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
