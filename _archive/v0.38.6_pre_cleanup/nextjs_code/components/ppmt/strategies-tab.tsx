'use client';

import { usePPMTStore, type StrategyStatus } from '@/store/ppmt-strategy-store';
import { useEffect, useState } from 'react';
import { LifecyclePipeline } from './lifecycle-pipeline';
import { StrategyCard } from './strategy-card';
import { CreateStrategyDialog } from './create-strategy-dialog';
import { StrategyDetail } from './strategy-detail';
import { Search, Filter } from 'lucide-react';

const STATUS_FILTERS: { value: StrategyStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'backtesting', label: 'Backtest' },
  { value: 'paper_trading', label: 'Paper' },
  { value: 'forward_testing', label: 'Forward' },
  { value: 'live', label: 'Live' },
];

export function StrategiesTab() {
  const { strategies, fetchStrategies, isLoading, selectedStrategyId, selectStrategy } = usePPMTStore();
  const [filter, setFilter] = useState<StrategyStatus | 'all'>('all');
  const [search, setSearch] = useState('');

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  const selectedStrategy = strategies.find((s) => s.id === selectedStrategyId);

  const filtered = strategies.filter((s) => {
    if (filter !== 'all' && s.status !== filter) return false;
    if (search && !s.symbol.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  // If a strategy is selected, show detail view
  if (selectedStrategy) {
    return <StrategyDetail strategy={selectedStrategy} onBack={() => selectStrategy(null)} />;
  }

  return (
    <div className="space-y-6">
      <LifecyclePipeline />

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
          <input
            type="text"
            placeholder="Search symbol..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-9 pr-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <Filter className="h-4 w-4 text-zinc-500" />
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`text-xs px-2.5 py-1.5 rounded-lg transition-colors ${
                filter === f.value
                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                  : 'text-zinc-500 hover:text-zinc-300 border border-transparent'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <CreateStrategyDialog />
        </div>
      </div>

      {/* Strategy cards */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-zinc-500">
          Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
          <p className="mb-2">No strategies found</p>
          <p className="text-xs text-zinc-600">
            {strategies.length === 0 ? 'Create your first strategy to get started' : 'Try a different filter'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filtered.map((s) => (
            <StrategyCard key={s.id} strategy={s} />
          ))}
        </div>
      )}
    </div>
  );
}
