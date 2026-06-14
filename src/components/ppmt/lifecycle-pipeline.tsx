'use client';

import { usePPMTStore, type StrategyStatus } from '@/store/ppmt-strategy-store';
import { cn } from '@/lib/utils';
import { ArrowRight } from 'lucide-react';

const STAGES: { id: StrategyStatus; label: string; color: string; bgColor: string }[] = [
  { id: 'draft', label: 'Draft', color: 'text-zinc-400', bgColor: 'bg-zinc-800' },
  { id: 'backtesting', label: 'Backtest', color: 'text-amber-400', bgColor: 'bg-amber-500/10' },
  { id: 'paper_trading', label: 'Paper', color: 'text-cyan-400', bgColor: 'bg-cyan-500/10' },
  { id: 'forward_testing', label: 'Forward', color: 'text-orange-400', bgColor: 'bg-orange-500/10' },
  { id: 'live', label: 'Live', color: 'text-emerald-400', bgColor: 'bg-emerald-500/10' },
];

export function LifecyclePipeline() {
  const { strategies, setActiveTab } = usePPMTStore();

  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-300">Strategy Pipeline</h3>
        <button
          onClick={() => setActiveTab('strategies')}
          className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
        >
          View all →
        </button>
      </div>
      <div className="flex items-center gap-1">
        {STAGES.map((stage, i) => {
          const count = strategies.filter((s) => s.status === stage.id).length;
          return (
            <div key={stage.id} className="flex items-center gap-1 flex-1">
              <div
                className={cn(
                  'flex flex-col items-center justify-center rounded-lg py-2 px-2 flex-1 transition-colors',
                  count > 0 ? stage.bgColor : 'bg-zinc-800/30'
                )}
              >
                <span className={cn(
                  'text-lg font-bold tabular-nums',
                  count > 0 ? stage.color : 'text-zinc-600'
                )}>
                  {count}
                </span>
                <span className={cn(
                  'text-[10px] uppercase tracking-wider',
                  count > 0 ? stage.color : 'text-zinc-600'
                )}>
                  {stage.label}
                </span>
              </div>
              {i < STAGES.length - 1 && (
                <ArrowRight className="h-3 w-3 text-zinc-700 flex-shrink-0" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
