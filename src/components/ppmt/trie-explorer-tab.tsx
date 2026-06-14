'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import { GitBranch, Activity, Layers, Trash2, Scissors } from 'lucide-react';

interface TrieStat {
  symbol: string;
  level: string;
  patternCount: number;
  nodeCount: number;
  totalObservations: number;
  tradingObservations: number;
  lastUpdated: string | null;
}

export function TrieExplorerTab() {
  const [stats, setStats] = useState<TrieStat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch('/api/ppmt/trie-stats');
        const json = await res.json();
        if (json.success && json.data?.tries) {
          const trieStats: TrieStat[] = Object.entries(json.data.tries as Record<string, Record<string, unknown>>).map(
            ([symbol, data]) => ({
              symbol,
              level: String(data.level || 'n3'),
              patternCount: Number(data.pattern_count || 0),
              nodeCount: Number(data.node_count || 0),
              totalObservations: Number(data.total_observations || 0),
              tradingObservations: Number(data.trading_observations || 0),
              lastUpdated: String(data.updated_at || null),
            })
          );
          setStats(trieStats);
        }
      } catch {
        // PPMT DB may not be available
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const totalPatterns = stats.reduce((sum, s) => sum + s.patternCount, 0);
  const totalObs = stats.reduce((sum, s) => sum + s.totalObservations, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <GitBranch className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-bold text-zinc-50">Trie Explorer</h2>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <div className="flex items-center gap-2 mb-1">
            <Layers className="h-4 w-4 text-cyan-400" />
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Total Patterns</span>
          </div>
          <div className="text-2xl font-bold text-zinc-50 tabular-nums">{totalPatterns.toLocaleString()}</div>
        </div>
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <div className="flex items-center gap-2 mb-1">
            <Activity className="h-4 w-4 text-emerald-400" />
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Total Observations</span>
          </div>
          <div className="text-2xl font-bold text-zinc-50 tabular-nums">{totalObs.toLocaleString()}</div>
        </div>
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <div className="flex items-center gap-2 mb-1">
            <Scissors className="h-4 w-4 text-amber-400" />
            <span className="text-xs text-zinc-500 uppercase tracking-wider">Symbols Tracked</span>
          </div>
          <div className="text-2xl font-bold text-zinc-50 tabular-nums">{stats.length}</div>
        </div>
      </div>

      {/* Trie table */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Per-Symbol Trie Statistics</h3>
        {loading ? (
          <div className="py-8 text-center text-zinc-500">Loading trie data...</div>
        ) : stats.length === 0 ? (
          <div className="py-8 text-center text-zinc-600">
            <p className="mb-1">No trie data found</p>
            <p className="text-xs">Build a trie first by creating and running a strategy</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                  <th className="text-left py-2 px-3">Symbol</th>
                  <th className="text-left py-2 px-3">Level</th>
                  <th className="text-right py-2 px-3">Patterns</th>
                  <th className="text-right py-2 px-3">Nodes</th>
                  <th className="text-right py-2 px-3">Observations</th>
                  <th className="text-right py-2 px-3">Trading Obs</th>
                  <th className="text-right py-2 px-3">Updated</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((s) => (
                  <tr key={`${s.symbol}-${s.level}`} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                    <td className="py-2 px-3 font-medium text-zinc-200">{s.symbol}</td>
                    <td className="py-2 px-3">
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400">
                        {s.level.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right text-zinc-300 tabular-nums">{s.patternCount.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right text-zinc-300 tabular-nums">{s.nodeCount.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right text-zinc-300 tabular-nums">{s.totalObservations.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right tabular-nums">
                      <span className={s.tradingObservations > 0 ? 'text-emerald-400' : 'text-zinc-600'}>
                        {s.tradingObservations.toLocaleString()}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right text-zinc-500 text-xs">
                      {s.lastUpdated ? new Date(s.lastUpdated).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pruning info */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Pruning Configuration</h3>
        <p className="text-xs text-zinc-500 mb-3">
          The Living Trie automatically prunes stale branches during trading.
          Nodes with fewer than 2 observations or near-zero confidence are removed,
          while established patterns (10+ observations) and traded nodes are always preserved.
        </p>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="flex justify-between"><span className="text-zinc-500">Min Observations</span><span className="text-zinc-300 font-mono">2</span></div>
          <div className="flex justify-between"><span className="text-zinc-500">Preserve Traded</span><span className="text-emerald-400 font-mono">Yes</span></div>
          <div className="flex justify-between"><span className="text-zinc-500">Safety Threshold</span><span className="text-zinc-300 font-mono">10 obs</span></div>
          <div className="flex justify-between"><span className="text-zinc-500">Min Confidence</span><span className="text-zinc-300 font-mono">0.01</span></div>
        </div>
      </div>
    </div>
  );
}
