'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { formatUptime, ALL_CHAINS, getChainBadge } from '@/lib/format';

// ============================================================
// TYPES
// ============================================================

interface TableCount {
  name: string;
  count: number;
  status: 'healthy' | 'low' | 'empty';
  threshold: number;
  description: string;
}

interface ApiHealth {
  name: string;
  url: string;
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  latencyMs: number;
  lastChecked: string;
  error?: string;
}

interface DataMonitorReport {
  timestamp: string;
  database: {
    tables: TableCount[];
    totalRecords: number;
    fillRate: number;
  };
  apis: ApiHealth[];
  brain: {
    status: string;
    uptime: number;
    totalCyclesCompleted: number;
    tasks: Array<{
      name: string;
      runCount: number;
      errorCount: number;
      lastRunAt: string | null;
      isRunning: boolean;
    }>;
    lastError: string | null;
  };
  tokens: {
    total: number;
    withDna: number;
    withoutDna: number;
    chainDistribution: Record<string, number>;
    newestToken: string | null;
    lastPriceUpdate: string | null;
  };
  dataGaps: string[];
  recommendations: string[];
}

// ============================================================
// HELPERS
// ============================================================

function getStatusColor(status: string) {
  switch (status) {
    case 'healthy': case 'ok': case 'RUNNING': return 'text-emerald-400';
    case 'low': case 'degraded': case 'STARTING': case 'PAUSED': return 'text-yellow-400';
    case 'empty': case 'down': case 'STOPPED': case 'ERROR': return 'text-red-400';
    default: return 'text-[#64748b]';
  }
}

function getStatusDot(status: string) {
  const color = status === 'healthy' || status === 'ok' || status === 'RUNNING'
    ? 'bg-emerald-500'
    : status === 'low' || status === 'degraded' || status === 'PAUSED'
      ? 'bg-yellow-500'
      : 'bg-red-500';
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function DataMonitor() {
  const queryClient = useQueryClient();
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);

  // Fetch data with React Query — auto-refresh every 30s
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['data-monitor'],
    queryFn: async (): Promise<DataMonitorReport | null> => {
      try {
        const res = await fetch('/api/data-monitor?detail=summary');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return json.data ?? null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  // Action mutation
  const executeAction = useCallback(async (action: string, params?: Record<string, unknown>) => {
    setActionLoading(action);
    setActionResult(null);
    try {
      const res = await fetch('/api/data-monitor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, params }),
      });
      const json = await res.json();
      if (json.success) {
        setActionResult(`✓ ${action}: ${JSON.stringify(json.data)}`);
        setTimeout(() => refetch(), 2000);
      } else {
        setActionResult(`✗ ${action}: ${json.error}`);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setActionResult(`✗ ${action}: ${msg}`);
    } finally {
      setActionLoading(null);
    }
  }, [refetch]);

  // -------------------------------------------------------------------
  // LOADING
  // -------------------------------------------------------------------
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg">
        <div className="text-[#64748b] font-mono text-sm animate-pulse">Loading data monitor...</div>
      </div>
    );
  }

  if (!data || isError) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg gap-3">
        <div className="text-red-400 font-mono text-sm">Failed to load monitor data</div>
        <button
          onClick={() => refetch()}
          className="px-3 py-1.5 bg-[#1a1f2e] border border-[#2a3040] rounded text-xs font-mono text-[#94a3b8] hover:text-[#e2e8f0]"
        >
          Retry
        </button>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // MAIN RENDER
  // -------------------------------------------------------------------
  const dnaPct = data.tokens.total > 0 ? Math.round(data.tokens.withDna / data.tokens.total * 100) : 0;

  return (
    <div className="h-full overflow-y-auto bg-[#0a0e17] p-4 space-y-4">
      {/* Top Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* DB Fill Rate */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <div className="text-[#64748b] font-mono text-[10px] uppercase tracking-wider mb-1">DB Fill Rate</div>
          <div className="flex items-end gap-2">
            <span className="text-2xl font-bold font-mono text-[#e2e8f0]">{data.database.fillRate}%</span>
            <span className="text-xs font-mono text-[#64748b]">{data.database.totalRecords.toLocaleString()} records</span>
          </div>
          <div className="mt-2 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                data.database.fillRate >= 70 ? 'bg-emerald-500' :
                data.database.fillRate >= 40 ? 'bg-yellow-500' : 'bg-red-500'
              }`}
              style={{ width: `${data.database.fillRate}%` }}
            />
          </div>
        </div>

        {/* Brain Status */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <div className="text-[#64748b] font-mono text-[10px] uppercase tracking-wider mb-1">Brain Scheduler</div>
          <div className="flex items-center gap-2">
            {getStatusDot(data.brain.status)}
            <span className={`text-lg font-bold font-mono ${getStatusColor(data.brain.status)}`}>
              {data.brain.status}
            </span>
          </div>
          <div className="text-xs font-mono text-[#64748b] mt-1">
            {data.brain.totalCyclesCompleted} cycles · {formatUptime(data.brain.uptime)}
          </div>
        </div>

        {/* Token Coverage */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <div className="text-[#64748b] font-mono text-[10px] uppercase tracking-wider mb-1">Token Coverage</div>
          <div className="text-2xl font-bold font-mono text-[#e2e8f0]">{data.tokens.total}</div>
          <div className="text-xs font-mono text-[#64748b] mt-1">
            {data.tokens.withDna} with DNA · {data.tokens.withoutDna} missing
          </div>
          <div className="mt-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div className="h-full bg-[#d4af37] rounded-full" style={{ width: `${dnaPct}%` }} />
          </div>
        </div>

        {/* Chain Distribution */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <div className="text-[#64748b] font-mono text-[10px] uppercase tracking-wider mb-1">Chains</div>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {ALL_CHAINS.map(chain => {
              const count = data.tokens.chainDistribution[chain] || 0;
              const badge = getChainBadge(chain);
              return (
                <span key={chain} className={`px-1.5 py-0.5 ${badge.bg} border ${badge.border} rounded text-[10px] font-mono ${badge.color}`}>
                  {chain}: <span className="text-[#e2e8f0]">{count}</span>
                </span>
              );
            })}
          </div>
          {data.tokens.lastPriceUpdate && (
            <div className="text-[10px] font-mono text-[#64748b] mt-2">
              Last update: {new Date(data.tokens.lastPriceUpdate).toLocaleTimeString()}
            </div>
          )}
        </div>
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-2 gap-4">
        {/* Database Tables */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
            <span className="text-[#d4af37] font-mono text-xs font-bold uppercase tracking-wider">Database Tables</span>
            <span className="text-[#64748b] font-mono text-[10px]">
              {data.database.tables.filter(t => t.count > 0).length}/{data.database.tables.length} with data
            </span>
          </div>
          <div className="max-h-[300px] overflow-y-auto">
            <table className="w-full text-xs font-mono">
              <thead className="sticky top-0 bg-[#0d1117]">
                <tr className="text-[#64748b] text-[10px]">
                  <th className="text-left px-3 py-1.5">Table</th>
                  <th className="text-left px-3 py-1.5">Description</th>
                  <th className="text-right px-3 py-1.5">Records</th>
                  <th className="text-center px-3 py-1.5">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.database.tables.map((table) => (
                  <tr key={table.name} className="border-t border-[#1e293b]/50 hover:bg-[#1a1f2e]/50">
                    <td className="px-3 py-1.5 text-[#94a3b8]">{table.name}</td>
                    <td className="px-3 py-1.5 text-[#64748b]">{table.description}</td>
                    <td className="px-3 py-1.5 text-right text-[#e2e8f0]">{table.count.toLocaleString()}</td>
                    <td className="px-3 py-1.5 text-center">
                      <span className={`inline-flex items-center gap-1 ${getStatusColor(table.status)}`}>
                        {getStatusDot(table.status)}
                        {table.status === 'healthy' ? 'OK' : table.status === 'low' ? 'LOW' : 'EMPTY'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Brain Scheduler Tasks */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
            <span className="text-[#d4af37] font-mono text-xs font-bold uppercase tracking-wider">Brain Scheduler</span>
            <div className="flex gap-1.5">
              {data.brain.status === 'STOPPED' ? (
                <button
                  onClick={() => executeAction('start_scheduler')}
                  disabled={actionLoading !== null}
                  className="px-2 py-0.5 bg-emerald-600/20 text-emerald-400 border border-emerald-600/40 rounded text-[10px] font-mono hover:bg-emerald-600/30 disabled:opacity-50"
                >
                  {actionLoading === 'start_scheduler' ? '...' : 'START'}
                </button>
              ) : (
                <>
                  <button
                    onClick={() => executeAction(data.brain.status === 'PAUSED' ? 'resume_scheduler' : 'pause_scheduler')}
                    disabled={actionLoading !== null}
                    className="px-2 py-0.5 bg-yellow-600/20 text-yellow-400 border border-yellow-600/40 rounded text-[10px] font-mono hover:bg-yellow-600/30 disabled:opacity-50"
                  >
                    {actionLoading === 'pause_scheduler' || actionLoading === 'resume_scheduler' ? '...' : data.brain.status === 'PAUSED' ? 'RESUME' : 'PAUSE'}
                  </button>
                  <button
                    onClick={() => executeAction('stop_scheduler')}
                    disabled={actionLoading !== null}
                    className="px-2 py-0.5 bg-red-600/20 text-red-400 border border-red-600/40 rounded text-[10px] font-mono hover:bg-red-600/30 disabled:opacity-50"
                  >
                    {actionLoading === 'stop_scheduler' ? '...' : 'STOP'}
                  </button>
                </>
              )}
            </div>
          </div>
          <div className="max-h-[260px] overflow-y-auto">
            <table className="w-full text-xs font-mono">
              <thead className="sticky top-0 bg-[#0d1117]">
                <tr className="text-[#64748b] text-[10px]">
                  <th className="text-left px-3 py-1.5">Task</th>
                  <th className="text-right px-3 py-1.5">Runs</th>
                  <th className="text-right px-3 py-1.5">Errors</th>
                  <th className="text-center px-3 py-1.5">State</th>
                  <th className="text-right px-3 py-1.5">Last Run</th>
                </tr>
              </thead>
              <tbody>
                {data.brain.tasks.map((task) => (
                  <tr key={task.name} className="border-t border-[#1e293b]/50 hover:bg-[#1a1f2e]/50">
                    <td className="px-3 py-1.5 text-[#94a3b8]">{task.name}</td>
                    <td className="px-3 py-1.5 text-right text-[#e2e8f0]">{task.runCount}</td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={task.errorCount > 0 ? 'text-red-400' : 'text-[#64748b]'}>{task.errorCount}</span>
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      {task.isRunning ? (
                        <span className="text-emerald-400 animate-pulse">ACTIVE</span>
                      ) : task.runCount === 0 ? (
                        <span className="text-[#64748b]">IDLE</span>
                      ) : (
                        <span className="text-[#94a3b8]">WAITING</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right text-[#64748b]">
                      {task.lastRunAt ? new Date(task.lastRunAt).toLocaleTimeString() : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.brain.lastError && (
            <div className="px-3 py-1.5 border-t border-[#1e293b] bg-red-900/10 text-red-400 text-[10px] font-mono truncate">
              Error: {data.brain.lastError}
            </div>
          )}
        </div>
      </div>

      {/* API Health & Actions Row */}
      <div className="grid grid-cols-2 gap-4">
        {/* API Health */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
            <span className="text-[#d4af37] font-mono text-xs font-bold uppercase tracking-wider">External APIs</span>
            <button
              onClick={() => refetch()}
              className="px-2 py-0.5 bg-[#1a1f2e] text-[#94a3b8] border border-[#2a3040] rounded text-[10px] font-mono hover:text-[#e2e8f0]"
            >
              REFRESH
            </button>
          </div>
          <div className="max-h-[200px] overflow-y-auto">
            {data.apis.length === 0 ? (
              <div className="px-3 py-4 text-center text-[#64748b] font-mono text-[10px]">
                Click REFRESH to check API health status
              </div>
            ) : (
              <table className="w-full text-xs font-mono">
                <thead className="sticky top-0 bg-[#0d1117]">
                  <tr className="text-[#64748b] text-[10px]">
                    <th className="text-left px-3 py-1.5">API</th>
                    <th className="text-center px-3 py-1.5">Status</th>
                    <th className="text-right px-3 py-1.5">Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {data.apis.map((api) => (
                    <tr key={api.name} className="border-t border-[#1e293b]/50 hover:bg-[#1a1f2e]/50">
                      <td className="px-3 py-1.5">
                        <div className="text-[#94a3b8]">{api.name}</div>
                        <div className="text-[#64748b] text-[9px]">{api.url}</div>
                        {api.error && <div className="text-red-400 text-[9px] truncate">{api.error}</div>}
                      </td>
                      <td className="px-3 py-1.5 text-center">
                        <span className={`inline-flex items-center gap-1 ${getStatusColor(api.status)}`}>
                          {getStatusDot(api.status)}
                          {api.status.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-right text-[#e2e8f0]">
                        {api.latencyMs > 0 ? `${api.latencyMs}ms` : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Actions & Recommendations */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e293b]">
            <span className="text-[#d4af37] font-mono text-xs font-bold uppercase tracking-wider">Quick Actions</span>
          </div>
          <div className="p-3 space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => executeAction('trigger_sync', { chains: ['solana', 'ethereum', 'base', 'bsc', 'polygon', 'arbitrum', 'optimism', 'avalanche'] })}
                disabled={actionLoading !== null}
                className="px-3 py-2 bg-[#1a1f2e] border border-[#2a3040] rounded text-[10px] font-mono text-[#94a3b8] hover:text-[#e2e8f0] hover:border-[#d4af37]/40 disabled:opacity-50 text-left"
              >
                <div className="text-[#e2e8f0] font-bold mb-0.5">Sync Tokens</div>
                <div className="text-[9px]">8 chains · DexScreener + CoinGecko</div>
              </button>
              <button
                onClick={() => executeAction('trigger_backfill', { batchSize: 5 })}
                disabled={actionLoading !== null}
                className="px-3 py-2 bg-[#1a1f2e] border border-[#2a3040] rounded text-[10px] font-mono text-[#94a3b8] hover:text-[#e2e8f0] hover:border-[#d4af37]/40 disabled:opacity-50 text-left"
              >
                <div className="text-[#e2e8f0] font-bold mb-0.5">OHLCV Backfill</div>
                <div className="text-[9px]">5 tokens · Multi-timeframe</div>
              </button>
              <button
                onClick={() => executeAction('generate_dna', { limit: 20 })}
                disabled={actionLoading !== null}
                className="px-3 py-2 bg-[#1a1f2e] border border-[#2a3040] rounded text-[10px] font-mono text-[#94a3b8] hover:text-[#e2e8f0] hover:border-[#d4af37]/40 disabled:opacity-50 text-left"
              >
                <div className="text-[#e2e8f0] font-bold mb-0.5">Generate DNA</div>
                <div className="text-[9px]">{data.tokens.withoutDna} tokens without DNA</div>
              </button>
              <button
                onClick={() => refetch()}
                disabled={actionLoading !== null}
                className="px-3 py-2 bg-[#1a1f2e] border border-[#2a3040] rounded text-[10px] font-mono text-[#94a3b8] hover:text-[#e2e8f0] hover:border-[#d4af37]/40 disabled:opacity-50 text-left"
              >
                <div className="text-[#e2e8f0] font-bold mb-0.5">Refresh</div>
                <div className="text-[9px]">Update monitor data</div>
              </button>
            </div>

            {actionResult && (
              <div className="px-2 py-1.5 bg-[#1a1f2e] border border-[#2a3040] rounded text-[10px] font-mono text-[#94a3b8] max-h-16 overflow-y-auto">
                {actionResult}
              </div>
            )}

            {data.recommendations.length > 0 && (
              <div className="space-y-1">
                <div className="text-[#d4af37] font-mono text-[10px] font-bold uppercase">Recommendations</div>
                {data.recommendations.map((rec, i) => (
                  <div key={i} className="px-2 py-1 bg-yellow-900/10 border border-yellow-900/20 rounded text-[10px] font-mono text-yellow-300/80">
                    {rec}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Data Gaps */}
      {data.dataGaps.length > 0 && (
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <div className="text-[#d4af37] font-mono text-xs font-bold uppercase tracking-wider mb-2">Data Gaps</div>
          <div className="grid grid-cols-3 gap-2">
            {data.dataGaps.map((gap, i) => (
              <div key={i} className="px-2 py-1 bg-red-900/10 border border-red-900/20 rounded text-[10px] font-mono text-red-300/80">
                {gap}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-center text-[#64748b] font-mono text-[10px]">
        Last updated: {new Date(data.timestamp).toLocaleTimeString()} · Auto-refresh every 30s
      </div>
    </div>
  );
}
