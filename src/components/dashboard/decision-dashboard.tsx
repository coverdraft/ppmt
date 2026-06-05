'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Brain,
  Loader2,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Shield,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Minus,
  FileSearch,
  Activity,
  Zap,
  BarChart3,
  Target,
  Filter,
  ArrowUpDown,
  Download,
} from 'lucide-react';
import { toast } from 'sonner';
import type {
  StrategyDecision,
  StrategyState,
  CapitalAction,
  SignalQuality,
  DecisionAuditRecord,
  VetoResult,
} from '@/lib/services/strategy/strategy-decision-engine';

// ============================================================
// TYPES
// ============================================================

interface PortfolioReviewSummary {
  total: number;
  active: number;
  conditional: number;
  paused: number;
  rejected: number;
}

interface PortfolioReviewResponse {
  decisions: StrategyDecision[];
  summary: PortfolioReviewSummary;
}

// Filter & Sort types
type StateFilter = 'ALL' | StrategyState;
type SignalFilter = 'ALL' | SignalQuality;
type MethodFilter = 'ALL' | string;
type SortField = 'name' | 'robustness' | 'overfitting' | 'stability' | 'capital' | 'date';
type SortDir = 'asc' | 'desc';

// ============================================================
// HELPERS
// ============================================================

/** Relative time — e.g. "in 3 days", "in 24 hours", "2 hours ago" */
function formatRelativeTime(date: Date | string): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  const isFuture = diffMs > 0;
  const absDiff = Math.abs(diffMs);

  const minutes = Math.floor(absDiff / (1000 * 60));
  const hours = Math.floor(absDiff / (1000 * 60 * 60));
  const days = Math.floor(absDiff / (1000 * 60 * 60 * 24));

  if (days > 0) return isFuture ? `in ${days} day${days !== 1 ? 's' : ''}` : `${days} day${days !== 1 ? 's' : ''} ago`;
  if (hours > 0) return isFuture ? `in ${hours} hour${hours !== 1 ? 's' : ''}` : `${hours} hour${hours !== 1 ? 's' : ''} ago`;
  if (minutes > 0) return isFuture ? `in ${minutes} min` : `${minutes} min ago`;
  return isFuture ? 'now' : 'just now';
}

/** State badge color mapping */
function stateColor(state: StrategyState): { bg: string; text: string; border: string } {
  switch (state) {
    case 'ACTIVE':
      return { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' };
    case 'CONDITIONAL':
      return { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' };
    case 'PAUSED':
      return { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' };
    case 'REJECTED':
      return { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' };
  }
}

/** Capital action badge color mapping */
function capitalActionColor(action: CapitalAction): { bg: string; text: string; border: string } {
  switch (action) {
    case 'INCREASE':
      return { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' };
    case 'MAINTAIN':
      return { bg: 'bg-blue-500/15', text: 'text-blue-400', border: 'border-blue-500/30' };
    case 'REDUCE':
      return { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' };
    case 'EXIT':
      return { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' };
  }
}

/** Signal quality badge color mapping */
function signalQualityColor(quality: SignalQuality): { bg: string; text: string; border: string } {
  switch (quality) {
    case 'STRONG':
      return { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' };
    case 'ADEQUATE':
      return { bg: 'bg-blue-500/15', text: 'text-blue-400', border: 'border-blue-500/30' };
    case 'WEAK':
      return { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30' };
  }
}

/** Progress bar color based on value and context */
function scoreBarColor(value: number, type: 'robustness' | 'overfitting' | 'stability'): string {
  if (type === 'overfitting') {
    // Lower is better for overfitting
    if (value <= 25) return 'bg-emerald-500';
    if (value <= 50) return 'bg-yellow-500';
    return 'bg-red-500';
  }
  // Higher is better for robustness and stability
  if (value >= 70) return 'bg-emerald-500';
  if (value >= 45) return 'bg-yellow-500';
  return 'bg-red-500';
}

/** Capital action icon */
function CapitalActionIcon({ action }: { action: CapitalAction }) {
  switch (action) {
    case 'INCREASE':
      return <TrendingUp className="h-3 w-3" />;
    case 'REDUCE':
      return <TrendingDown className="h-3 w-3" />;
    case 'EXIT':
      return <XCircle className="h-3 w-3" />;
    case 'MAINTAIN':
    default:
      return <Minus className="h-3 w-3" />;
  }
}

/** Translate state to display label */
function stateLabel(state: StrategyState): string {
  switch (state) {
    case 'ACTIVE': return 'ACTIVE';
    case 'CONDITIONAL': return 'CONDITIONAL';
    case 'PAUSED': return 'PAUSED';
    case 'REJECTED': return 'REJECTED';
  }
}

/** Translate capital action to display label */
function capitalActionLabel(action: CapitalAction): string {
  switch (action) {
    case 'INCREASE': return 'INCREASE';
    case 'MAINTAIN': return 'MAINTAIN';
    case 'REDUCE': return 'REDUCE';
    case 'EXIT': return 'EXIT';
  }
}

/** Translate signal quality to display label */
function signalQualityLabel(quality: SignalQuality): string {
  switch (quality) {
    case 'STRONG': return 'STRONG';
    case 'ADEQUATE': return 'ADEQUATE';
    case 'WEAK': return 'WEAK';
  }
}

/** Allocation method display name */
function methodLabel(method: string): string {
  const labels: Record<string, string> = {
    KELLY_MODIFIED: 'Kelly Mod.',
    RISK_PARITY: 'Risk Parity',
    VOLATILITY_TARGETING: 'Vol Target',
    MAX_DRAWDOWN_CONTROL: 'Max DD Ctrl',
    EQUAL_WEIGHT: 'Equal Weight',
  };
  return labels[method] || method;
}

// ============================================================
// MINI SPARKLINE COMPONENT
// ============================================================

function MiniSparkline({ values, color, width = 50, height = 14 }: { values: number[]; color: string; width?: number; height?: number }) {
  if (values.length < 2) {
    return <span className="text-[7px] font-mono text-[#475569]">--</span>;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 1;
  const chartW = width - pad * 2;
  const chartH = height - pad * 2;

  const points = values.map((v, i) => {
    const x = pad + (i / (values.length - 1)) * chartW;
    const y = pad + chartH - ((v - min) / range) * chartH;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="inline-block">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1" strokeLinejoin="round" />
    </svg>
  );
}

// ============================================================
// SUMMARY CARD COMPONENT
// ============================================================

function SummaryCard({
  label,
  count,
  icon: Icon,
  colorClass,
  borderColor,
}: {
  label: string;
  count: number;
  icon: React.ElementType;
  colorClass: string;
  borderColor: string;
}) {
  return (
    <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
      <div className={`absolute top-0 left-0 w-full h-0.5 ${borderColor}`} />
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon className={`h-3 w-3 ${colorClass}`} />
        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">{label}</span>
      </div>
      <div className={`text-[20px] font-mono font-bold ${colorClass}`}>{count}</div>
    </Card>
  );
}

// ============================================================
// SCORE PROGRESS BAR
// ============================================================

function ScoreProgressBar({
  value,
  type,
  label,
}: {
  value: number;
  type: 'robustness' | 'overfitting' | 'stability';
  label: string;
}) {
  const clamped = Math.min(100, Math.max(0, value));
  const barColor = scoreBarColor(clamped, type);

  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <span className="text-[8px] font-mono text-[#64748b] w-[52px] shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="text-[9px] font-mono text-[#94a3b8] w-7 text-right shrink-0">
        {clamped.toFixed(0)}
      </span>
    </div>
  );
}

// ============================================================
// VETO BADGE
// ============================================================

function VetoBadge({ veto }: { veto: VetoResult }) {
  const passed = veto.passed;
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#0d1117] border border-[#1e293b]">
      {passed ? (
        <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0" />
      ) : (
        <XCircle className="h-3 w-3 text-red-400 shrink-0" />
      )}
      <span className="text-[8px] font-mono text-[#94a3b8]">{veto.veto}</span>
      <span className={`text-[8px] font-mono ${passed ? 'text-emerald-400' : 'text-red-400'}`}>
        {passed ? 'OK' : 'FAIL'}
      </span>
    </div>
  );
}

// ============================================================
// AUDIT SECTION
// ============================================================

function AuditSection({
  strategyId,
  strategyName,
}: {
  strategyId: string;
  strategyName: string;
}) {
  const [auditRecords, setAuditRecords] = useState<DecisionAuditRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAudit = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/strategy-decision/audit?strategyId=${encodeURIComponent(strategyId)}&limit=20`);
      if (!res.ok) throw new Error('Failed to load audit');
      const json = await res.json();
      setAuditRecords(json.data ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [strategyId]);

  useEffect(() => {
    fetchAudit();
  }, [fetchAudit]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="h-4 w-4 animate-spin text-[#d4af37] mr-2" />
        <span className="text-[9px] font-mono text-[#64748b]">Loading audit...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-1.5 px-3 py-2 bg-red-500/5 border border-red-500/20 rounded">
        <AlertTriangle className="h-3 w-3 text-red-400 shrink-0" />
        <span className="text-[9px] font-mono text-red-400">{error}</span>
      </div>
    );
  }

  if (auditRecords.length === 0) {
    return (
      <div className="text-center py-4">
        <FileSearch className="h-5 w-5 text-[#475569] mx-auto mb-1" />
        <span className="text-[9px] font-mono text-[#475569]">
          No audit records for &quot;{strategyName}&quot;
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2 max-h-64 overflow-y-auto pr-1 custom-scrollbar">
      {auditRecords.map((record) => {
        const decision = record.decision;
        const sc = stateColor(decision.state);
        const ca = capitalActionColor(decision.capitalAction);
        const sq = signalQualityColor(decision.signalQuality);

        return (
          <div
            key={record.id}
            className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3 space-y-2"
          >
            {/* Audit header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-[8px] font-mono text-[#475569]">ID:</span>
                <span className="text-[9px] font-mono text-[#94a3b8] truncate max-w-[140px]">
                  {record.id}
                </span>
              </div>
              <span className="text-[9px] font-mono text-[#64748b]">
                {formatRelativeTime(record.timestamp)}
              </span>
            </div>

            {/* Decision badges */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge className={`text-[8px] h-4 px-1.5 font-mono ${sc.bg} ${sc.text} border ${sc.border}`}>
                {stateLabel(decision.state)}
              </Badge>
              <Badge className={`text-[8px] h-4 px-1.5 font-mono ${ca.bg} ${ca.text} border ${ca.border}`}>
                {capitalActionLabel(decision.capitalAction)}
              </Badge>
              <Badge className={`text-[8px] h-4 px-1.5 font-mono ${sq.bg} ${sq.text} border ${sq.border}`}>
                {signalQualityLabel(decision.signalQuality)}
              </Badge>
            </div>

            {/* Scores */}
            <div className="space-y-1">
              <ScoreProgressBar value={decision.scores.robustness} type="robustness" label="Robustness" />
              <ScoreProgressBar value={decision.scores.overfitting} type="overfitting" label="Overfitting" />
              <ScoreProgressBar value={decision.scores.stability} type="stability" label="Stability" />
            </div>

            {/* Vetos summary */}
            <div className="flex items-center gap-1 flex-wrap">
              {record.processing.vetoResults.map((v) => (
                <VetoBadge key={v.veto} veto={v} />
              ))}
            </div>

            {/* Capital recommendation */}
            <div className="flex items-center justify-between text-[9px] font-mono">
              <span className="text-[#64748b]">Target capital:</span>
              <span className="text-[#e2e8f0] font-bold">
                {decision.capitalRecommendation.targetPct.toFixed(1)}% ({methodLabel(decision.capitalRecommendation.method)})
              </span>
            </div>

            {/* Feedback if available */}
            {record.feedback && (
              <div className="flex items-center gap-2 px-2 py-1 bg-[#0d1117] rounded border border-[#1e293b]">
                {record.feedback.wasCorrect ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0" />
                ) : (
                  <XCircle className="h-3 w-3 text-red-400 shrink-0" />
                )}
                <span className="text-[8px] font-mono text-[#94a3b8]">
                  Feedback: {record.feedback.wasCorrect ? 'Correct' : 'Incorrect'} • PnL: {record.feedback.realizedPnlPct >= 0 ? '+' : ''}{record.feedback.realizedPnlPct.toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// STRATEGY DECISION ROW
// ============================================================

function StrategyDecisionRow({ decision, scoreHistory }: { decision: StrategyDecision; scoreHistory?: Array<{ robustness: number; overfitting: number; stability: number }> }) {
  const [expanded, setExpanded] = useState(false);
  const [showAudit, setShowAudit] = useState(false);

  const sc = stateColor(decision.state);
  const ca = capitalActionColor(decision.capitalAction);
  const sq = signalQualityColor(decision.signalQuality);

  return (
    <div className="border-b border-[#1e293b] last:border-b-0">
      {/* Main row */}
      <div
        className="grid grid-cols-[1fr_90px_90px_80px_100px_100px_100px_110px_80px_28px] items-center gap-1 px-3 py-2 cursor-pointer hover:bg-white/[0.02] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Strategy Name */}
        <div className="flex items-center gap-1.5 min-w-0">
          {expanded ? (
            <ChevronDown className="h-3 w-3 text-[#64748b] shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 text-[#64748b] shrink-0" />
          )}
          <span className="text-[10px] font-mono text-[#e2e8f0] truncate">
            {decision.strategyName}
          </span>
        </div>

        {/* State */}
        <Badge className={`text-[8px] h-4 px-1.5 font-mono justify-center ${sc.bg} ${sc.text} border ${sc.border}`}>
          {stateLabel(decision.state)}
        </Badge>

        {/* Capital Action */}
        <Badge className={`text-[8px] h-4 px-1.5 font-mono justify-center gap-0.5 ${ca.bg} ${ca.text} border ${ca.border}`}>
          <CapitalActionIcon action={decision.capitalAction} />
          {capitalActionLabel(decision.capitalAction)}
        </Badge>

        {/* Signal Quality */}
        <Badge className={`text-[8px] h-4 px-1.5 font-mono justify-center ${sq.bg} ${sq.text} border ${sq.border}`}>
          {signalQualityLabel(decision.signalQuality)}
        </Badge>

        {/* Robustness */}
        <div className="flex items-center gap-1.5">
          <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${scoreBarColor(decision.scores.robustness, 'robustness')}`}
              style={{ width: `${Math.min(100, Math.max(0, decision.scores.robustness))}%` }}
            />
          </div>
          <span className="text-[8px] font-mono text-[#94a3b8] w-5 text-right shrink-0">
            {decision.scores.robustness.toFixed(0)}
          </span>
        </div>

        {/* Overfitting */}
        <div className="flex items-center gap-1.5">
          <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${scoreBarColor(decision.scores.overfitting, 'overfitting')}`}
              style={{ width: `${Math.min(100, Math.max(0, decision.scores.overfitting))}%` }}
            />
          </div>
          <span className="text-[8px] font-mono text-[#94a3b8] w-5 text-right shrink-0">
            {decision.scores.overfitting.toFixed(0)}
          </span>
        </div>

        {/* Stability */}
        <div className="flex items-center gap-1.5">
          <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${scoreBarColor(decision.scores.stability, 'stability')}`}
              style={{ width: `${Math.min(100, Math.max(0, decision.scores.stability))}%` }}
            />
          </div>
          <span className="text-[8px] font-mono text-[#94a3b8] w-5 text-right shrink-0">
            {decision.scores.stability.toFixed(0)}
          </span>
        </div>

        {/* Capital Recommendation */}
        <div className="flex items-center gap-1">
          <span className="text-[9px] font-mono font-bold text-[#e2e8f0]">
            {decision.capitalRecommendation.targetPct.toFixed(1)}%
          </span>
          <span className="text-[7px] font-mono text-[#64748b] truncate">
            {methodLabel(decision.capitalRecommendation.method)}
          </span>
        </div>

        {/* Next Review */}
        <span className="text-[9px] font-mono text-[#94a3b8]">
          {formatRelativeTime(decision.nextReviewDate)}
        </span>

        {/* Expand indicator */}
        <div className="flex justify-center">
          <ChevronDown className={`h-3 w-3 text-[#475569] transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </div>

      {/* Expanded detail */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-4 py-3 bg-[#0a0e17] border-t border-[#1e293b]/50 space-y-3">
              {/* Veto results */}
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <Shield className="h-3 w-3 text-[#d4af37]" />
                  <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Veto Results
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {decision.vetoResults.map((v) => (
                    <VetoBadge key={v.veto} veto={v} />
                  ))}
                </div>
              </div>

              {/* Detailed scores */}
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <BarChart3 className="h-3 w-3 text-cyan-400" />
                  <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Detailed Scores
                  </span>
                  {scoreHistory && scoreHistory.length > 1 && (
                    <span className="text-[7px] font-mono text-[#475569] ml-1">Historical trend:</span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <ScoreProgressBar value={decision.scores.robustness} type="robustness" label="Robustness" />
                    {scoreHistory && scoreHistory.length > 1 && (
                      <MiniSparkline values={scoreHistory.map(h => h.robustness)} color="#10b981" />
                    )}
                  </div>
                  <div>
                    <ScoreProgressBar value={decision.scores.overfitting} type="overfitting" label="Overfitting" />
                    {scoreHistory && scoreHistory.length > 1 && (
                      <MiniSparkline values={scoreHistory.map(h => h.overfitting)} color="#ef4444" />
                    )}
                  </div>
                  <div>
                    <ScoreProgressBar value={decision.scores.stability} type="stability" label="Stability" />
                    {scoreHistory && scoreHistory.length > 1 && (
                      <MiniSparkline values={scoreHistory.map(h => h.stability)} color="#06b6d4" />
                    )}
                  </div>
                </div>
              </div>

              {/* Recommendations */}
              {decision.recommendations.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <Target className="h-3 w-3 text-amber-400" />
                    <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                      Recommendations
                    </span>
                  </div>
                  <div className="space-y-1">
                    {decision.recommendations.map((rec, i) => (
                      <div key={i} className="flex items-start gap-1.5 px-2 py-1 bg-[#0d1117] rounded border border-[#1e293b]">
                        <span className="text-[8px] font-mono text-[#64748b] shrink-0 mt-px">•</span>
                        <span className="text-[9px] font-mono text-[#94a3b8]">{rec}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Capital recommendation detail */}
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <Zap className="h-3 w-3 text-emerald-400" />
                  <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Capital Recommendation
                  </span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
                    <span className="text-[8px] font-mono text-[#475569] block">Target</span>
                    <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                      {decision.capitalRecommendation.targetPct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
                    <span className="text-[8px] font-mono text-[#475569] block">Size USD</span>
                    <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                      ${decision.capitalRecommendation.sizeUsd.toFixed(2)}
                    </span>
                  </div>
                  <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
                    <span className="text-[8px] font-mono text-[#475569] block">Method</span>
                    <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                      {methodLabel(decision.capitalRecommendation.method)}
                    </span>
                  </div>
                  <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
                    <span className="text-[8px] font-mono text-[#475569] block">Reason</span>
                    <span className="text-[9px] font-mono text-[#94a3b8] line-clamp-2">
                      {decision.capitalRecommendation.reason}
                    </span>
                  </div>
                </div>
              </div>

              {/* Audit ID */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <FileSearch className="h-3 w-3 text-[#475569]" />
                  <span className="text-[8px] font-mono text-[#475569]">Audit ID:</span>
                  <span className="text-[9px] font-mono text-[#94a3b8]">{decision.auditId}</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className={`h-5 text-[8px] font-mono px-2 ${showAudit ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#64748b]'}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowAudit(!showAudit);
                  }}
                >
                  <Activity className="h-2.5 w-2.5 mr-1" />
                  {showAudit ? 'Hide Audit' : 'View Audit'}
                </Button>
              </div>

              {/* Audit section */}
              <AnimatePresence>
                {showAudit && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                  >
                    <Separator className="my-2 bg-[#1e293b]" />
                    <AuditSection
                      strategyId={decision.strategyId}
                      strategyName={decision.strategyName}
                    />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function DecisionDashboard() {
  const [decisions, setDecisions] = useState<StrategyDecision[]>([]);
  const [summary, setSummary] = useState<PortfolioReviewSummary>({
    total: 0,
    active: 0,
    conditional: 0,
    paused: 0,
    rejected: 0,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [stateFilter, setStateFilter] = useState<StateFilter>('ALL');
  const [signalFilter, setSignalFilter] = useState<SignalFilter>('ALL');
  const [methodFilter, setMethodFilter] = useState<MethodFilter>('ALL');
  const [showFilters, setShowFilters] = useState(false);

  // Sorting
  const [sortField, setSortField] = useState<SortField>('date');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Historical score data for sparklines
  const [scoreHistory, setScoreHistory] = useState<Record<string, Array<{ robustness: number; overfitting: number; stability: number }>>>({});

  /** Fetch portfolio review data */
  const fetchPortfolioReview = useCallback(async (showRefreshLoader = false) => {
    if (showRefreshLoader) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    setError(null);

    try {
      const res = await fetch('/api/strategy-decision/portfolio-review?skipAudit=true');
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || 'Failed to load portfolio review');
      }
      const json = await res.json();
      const data = json.data as PortfolioReviewResponse | null;

      if (data) {
        setDecisions(data.decisions);
        setSummary(data.summary);
      } else {
        setDecisions([]);
        setSummary({ total: 0, active: 0, conditional: 0, paused: 0, rejected: 0 });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      toast.error('Failed to load SDE decisions');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  /** Fetch historical score data for sparklines */
  const fetchScoreHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/strategy-decision/audit?limit=200');
      if (!res.ok) return;
      const json = await res.json();
      const records = json.data as DecisionAuditRecord[];

      const history: Record<string, Array<{ robustness: number; overfitting: number; stability: number }>> = {};
      for (const record of records) {
        const sid = record.strategyId;
        if (!history[sid]) history[sid] = [];
        history[sid].unshift({
          robustness: record.processing.scores.robustness,
          overfitting: record.processing.scores.overfitting,
          stability: record.processing.scores.stability,
        });
      }
      setScoreHistory(history);
    } catch {
      // Non-critical — sparklines just won't show
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchPortfolioReview();
    fetchScoreHistory();
  }, [fetchPortfolioReview, fetchScoreHistory]);

  // Auto-refresh every 60 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      fetchPortfolioReview(true);
    }, 60000);
    return () => clearInterval(interval);
  }, [fetchPortfolioReview]);

  /** Get unique methods from decisions */
  const uniqueMethods = useMemo(() => {
    const methods = new Set<string>();
    for (const d of decisions) {
      methods.add(d.capitalRecommendation.method);
    }
    return Array.from(methods);
  }, [decisions]);

  /** Filter and sort decisions */
  const filteredAndSorted = useMemo(() => {
    let result = [...decisions];

    // Apply filters
    if (stateFilter !== 'ALL') {
      result = result.filter(d => d.state === stateFilter);
    }
    if (signalFilter !== 'ALL') {
      result = result.filter(d => d.signalQuality === signalFilter);
    }
    if (methodFilter !== 'ALL') {
      result = result.filter(d => d.capitalRecommendation.method === methodFilter);
    }

    // Apply sorting
    const stateOrder: Record<StrategyState, number> = { REJECTED: 0, PAUSED: 1, CONDITIONAL: 2, ACTIVE: 3 };
    const actionOrder: Record<CapitalAction, number> = { EXIT: 0, REDUCE: 1, MAINTAIN: 2, INCREASE: 3 };

    result.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'name':
          cmp = a.strategyName.localeCompare(b.strategyName);
          break;
        case 'robustness':
          cmp = a.scores.robustness - b.scores.robustness;
          break;
        case 'overfitting':
          cmp = a.scores.overfitting - b.scores.overfitting;
          break;
        case 'stability':
          cmp = a.scores.stability - b.scores.stability;
          break;
        case 'capital':
          cmp = a.capitalRecommendation.targetPct - b.capitalRecommendation.targetPct;
          if (cmp === 0) cmp = (actionOrder[a.capitalAction] ?? 0) - (actionOrder[b.capitalAction] ?? 0);
          break;
        case 'date':
          cmp = new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
          if (cmp === 0) cmp = (stateOrder[a.state] ?? 0) - (stateOrder[b.state] ?? 0);
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return result;
  }, [decisions, stateFilter, signalFilter, methodFilter, sortField, sortDir]);

  /** Export decisions as JSON */
  const handleExport = useCallback(() => {
    const exportData = {
      exportedAt: new Date().toISOString(),
      summary,
      decisions: filteredAndSorted.map(d => ({
        strategyId: d.strategyId,
        strategyName: d.strategyName,
        state: d.state,
        capitalAction: d.capitalAction,
        signalQuality: d.signalQuality,
        scores: d.scores,
        capitalRecommendation: d.capitalRecommendation,
        vetoResults: d.vetoResults,
        recommendations: d.recommendations,
        nextReviewDate: d.nextReviewDate,
        auditId: d.auditId,
        timestamp: d.timestamp,
      })),
      filters: { state: stateFilter, signal: signalFilter, method: methodFilter },
      sort: { field: sortField, direction: sortDir },
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sde-decisions-${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success('Decision data exported');
  }, [summary, filteredAndSorted, stateFilter, signalFilter, methodFilter, sortField, sortDir]);

  /** Toggle sort direction or change sort field */
  const handleSort = useCallback((field: SortField) => {
    if (sortField === field) {
      setSortDir(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  }, [sortField]);

  // ---- LOADING STATE ----
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0a0e17]">
        <Loader2 className="h-8 w-8 animate-spin text-[#d4af37] mb-3" />
        <span className="text-sm font-mono text-[#64748b]">Loading Decision Engine...</span>
      </div>
    );
  }

  // ---- ERROR STATE ----
  if (error && decisions.length === 0) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <Brain className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Decision Engine</span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-red-500/10 border border-red-500/20 mb-4">
            <AlertTriangle className="h-6 w-6 text-red-400" />
          </div>
          <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">Load Error</h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md mb-4">{error}</p>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-[10px] font-mono text-[#d4af37] hover:bg-[#d4af37]/10"
            onClick={() => fetchPortfolioReview()}
          >
            <RefreshCw className="h-3 w-3 mr-1.5" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  // ---- EMPTY STATE ----
  if (decisions.length === 0) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <Brain className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Decision Engine</span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-[#d4af37]/10 border border-[#d4af37]/20 mb-4">
            <Brain className="h-6 w-6 text-[#d4af37]" />
          </div>
          <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">Strategic Decision Engine</h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md">
            Create trading systems and run backtests to generate SDE decisions.
            The engine evaluates robustness, overfitting, and stability to determine
            the state and capital action for each strategy.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <Brain className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
              Strategic Decision Engine
            </span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              {summary.total} strateg{summary.total !== 1 ? 'ies' : 'y'} evaluated
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {isRefreshing && <Loader2 className="h-3 w-3 animate-spin text-[#d4af37]" />}
          <Button
            variant="ghost"
            size="sm"
            className={`h-6 text-[9px] font-mono px-2 ${showFilters ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'} hover:text-[#d4af37] hover:bg-[#d4af37]/10`}
            onClick={() => setShowFilters(!showFilters)}
          >
            <Filter className="h-3 w-3 mr-1" />
            Filters
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-[9px] font-mono px-2 text-[#94a3b8] hover:text-[#d4af37] hover:bg-[#d4af37]/10"
            onClick={handleExport}
          >
            <Download className="h-3 w-3 mr-1" />
            Export
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-[9px] font-mono px-2 text-[#94a3b8] hover:text-[#d4af37] hover:bg-[#d4af37]/10"
            onClick={() => fetchPortfolioReview(true)}
            disabled={isRefreshing}
          >
            <RefreshCw className={`h-3 w-3 mr-1 ${isRefreshing ? 'animate-spin' : ''}`} />
            Review
          </Button>
        </div>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- SUMMARY CARDS ROW ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <SummaryCard
              label="Activas"
              count={summary.active}
              icon={CheckCircle2}
              colorClass="text-emerald-400"
              borderColor="bg-emerald-500"
            />
            <SummaryCard
              label="Condicionales"
              count={summary.conditional}
              icon={AlertTriangle}
              colorClass="text-yellow-400"
              borderColor="bg-yellow-500"
            />
            <SummaryCard
              label="Pausadas"
              count={summary.paused}
              icon={Clock}
              colorClass="text-orange-400"
              borderColor="bg-orange-500"
            />
            <SummaryCard
              label="Rechazadas"
              count={summary.rejected}
              icon={XCircle}
              colorClass="text-red-400"
              borderColor="bg-red-500"
            />
          </div>
        </div>

        {/* ---- FILTER & SORT BAR ---- */}
        <AnimatePresence>
          {showFilters && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="overflow-hidden"
            >
              <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0d1117] flex flex-wrap items-center gap-2">
                <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">State:</span>
                {(['ALL', 'ACTIVE', 'CONDITIONAL', 'PAUSED', 'REJECTED'] as const).map(s => (
                  <Button
                    key={s}
                    variant="ghost"
                    size="sm"
                    className={`h-5 text-[8px] font-mono px-2 ${stateFilter === s ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
                    onClick={() => setStateFilter(s)}
                  >
                    {s === 'ALL' ? 'All' : stateLabel(s as StrategyState)}
                  </Button>
                ))}
                <Separator orientation="vertical" className="h-4 bg-[#1e293b]" />
                <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">Signal:</span>
                {(['ALL', 'STRONG', 'ADEQUATE', 'WEAK'] as const).map(q => (
                  <Button
                    key={q}
                    variant="ghost"
                    size="sm"
                    className={`h-5 text-[8px] font-mono px-2 ${signalFilter === q ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
                    onClick={() => setSignalFilter(q)}
                  >
                    {q === 'ALL' ? 'All' : signalQualityLabel(q as SignalQuality)}
                  </Button>
                ))}
                {uniqueMethods.length > 1 && (
                  <>
                    <Separator orientation="vertical" className="h-4 bg-[#1e293b]" />
                    <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">Method:</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className={`h-5 text-[8px] font-mono px-2 ${methodFilter === 'ALL' ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
                      onClick={() => setMethodFilter('ALL')}
                    >
                      All
                    </Button>
                    {uniqueMethods.map(m => (
                      <Button
                        key={m}
                        variant="ghost"
                        size="sm"
                        className={`h-5 text-[8px] font-mono px-2 ${methodFilter === m ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
                        onClick={() => setMethodFilter(m)}
                      >
                        {methodLabel(m)}
                      </Button>
                    ))}
                  </>
                )}
                <Separator orientation="vertical" className="h-4 bg-[#1e293b]" />
                <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">Sort:</span>
                {([
                  { field: 'date' as SortField, label: 'Date' },
                  { field: 'name' as SortField, label: 'Name' },
                  { field: 'robustness' as SortField, label: 'Robustness' },
                  { field: 'overfitting' as SortField, label: 'Overfitting' },
                  { field: 'stability' as SortField, label: 'Stability' },
                  { field: 'capital' as SortField, label: 'Capital' },
                ]).map(s => (
                  <Button
                    key={s.field}
                    variant="ghost"
                    size="sm"
                    className={`h-5 text-[8px] font-mono px-2 ${sortField === s.field ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
                    onClick={() => handleSort(s.field)}
                  >
                    <ArrowUpDown className="h-2.5 w-2.5 mr-0.5" />
                    {s.label}
                    {sortField === s.field && <span className="ml-0.5">{sortDir === 'asc' ? '↑' : '↓'}</span>}
                  </Button>
                ))}
                <span className="text-[8px] font-mono text-[#475569] ml-auto">
                  {filteredAndSorted.length}/{decisions.length} shown
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---- DECISION TABLE ---- */}
        <div className="px-3 py-3">
          <div className="flex items-center gap-2 mb-2">
            <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Strategy Decisions
            </span>
            {!showFilters && (
              <span className="text-[8px] font-mono text-[#475569] ml-auto">
                {filteredAndSorted.length}/{decisions.length} shown
              </span>
            )}
          </div>

          {/* Desktop table header — visible on lg+ */}
          <div className="hidden lg:grid grid-cols-[1fr_90px_90px_80px_100px_100px_100px_110px_80px_28px] items-center gap-1 px-3 py-1.5 bg-[#0d1117] border border-[#1e293b] rounded-t-lg">
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('name')}>Strategy {sortField === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">State</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">Cap. Action</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">Signal</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('robustness')}>Robustness {sortField === 'robustness' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('overfitting')}>Overfitting {sortField === 'overfitting' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('stability')}>Stability {sortField === 'stability' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('capital')}>Capital {sortField === 'capital' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider cursor-pointer hover:text-[#d4af37]" onClick={() => handleSort('date')}>Review {sortField === 'date' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</span>
            <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider" />
          </div>

          {/* Table body */}
          <div className="bg-[#0d1117]/50 border border-t-0 border-[#1e293b] rounded-b-lg overflow-hidden">
            {filteredAndSorted.length === 0 && decisions.length > 0 && (
              <div className="text-center py-6">
                <span className="text-[9px] font-mono text-[#475569]">No strategies match the current filters</span>
              </div>
            )}
            {filteredAndSorted.map((decision) => (
              <StrategyDecisionRow key={decision.strategyId} decision={decision} scoreHistory={scoreHistory[decision.strategyId]} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
