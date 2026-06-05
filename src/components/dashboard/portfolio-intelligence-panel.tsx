'use client';

import { useState, useCallback, useMemo } from 'react';
import { useCryptoStore } from '@/store/crypto-store';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  PieChart,
  TrendingDown,
  Zap,
  BarChart3,
  Loader2,
  Shield,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ArrowRight,
  Activity,
  Target,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Cell,
  PieChart as RechartsPie,
  Pie,
  Legend,
} from 'recharts';
import { motion, AnimatePresence } from 'framer-motion';
import { toast } from 'sonner';

// ============================================================
// CONSTANTS & HELPERS
// ============================================================

const STRESS_SCENARIOS = [
  { id: 'market_crash_20', label: 'Market Crash', description: '-20% broad market sell-off', icon: TrendingDown, color: '#ef4444' },
  { id: 'crypto_winter_50', label: 'Flash Crash', description: '-50% crypto winter', icon: AlertTriangle, color: '#dc2626' },
  { id: 'correlation_break', label: 'Sector Rotation', description: 'Assets decouple, rotation', icon: Activity, color: '#f59e0b' },
  { id: 'liquidity_crisis', label: 'Liquidity Freeze', description: 'Severe liquidity drain', icon: Shield, color: '#8b5cf6' },
  { id: 'flash_crash_10', label: 'Black Swan', description: 'Sudden flash crash -10%', icon: XCircle, color: '#991b1b' },
] as const;

const OPTIMIZATION_METHODS = [
  { id: 'MEAN_VARIANCE', label: 'MVO', description: 'Mean-Variance Optimization' },
  { id: 'RISK_PARITY', label: 'Risk Parity', description: 'Equal risk contribution' },
  { id: 'MAX_DIVERSIFICATION', label: 'Max Sharpe', description: 'Maximum diversification ratio' },
  { id: 'MIN_VARIANCE', label: 'Min Variance', description: 'Minimum portfolio variance' },
] as const;

function formatCurrency(v: number): string {
  if (v == null || isNaN(v)) return '$0.00';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

function formatPct(v: number): string {
  if (v == null || isNaN(v)) return '0.00%';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function impactScoreColor(score: number): string {
  // score is -1 to +1, mapped to 0-100 for display
  const normalized = (score + 1) / 2; // 0 to 1
  if (normalized >= 0.7) return '#10b981';
  if (normalized >= 0.5) return '#f59e0b';
  if (normalized >= 0.3) return '#f97316';
  return '#ef4444';
}

function impactScoreToDisplay(score: number): number {
  // Map -1..+1 to 0..100
  return Math.round(((score + 1) / 2) * 100);
}

function recommendationBadge(approved: boolean, impactScore: number) {
  if (!approved || impactScore < -0.3) return { label: 'REJECT', color: '#ef4444', bg: '#ef444420', border: '#ef444440' };
  if (impactScore < 0.1) return { label: 'REDUCE', color: '#f59e0b', bg: '#f59e0b20', border: '#f59e0b40' };
  return { label: 'APPROVE', color: '#10b981', bg: '#10b98120', border: '#10b98140' };
}

const CHART_COLORS = ['#10b981', '#f59e0b', '#3b82f6', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899', '#84cc16'];

// ============================================================
// IMPACT ANALYSIS TAB
// ============================================================

function ImpactAnalysisTab() {
  const { portfolioIntelligence, setPortfolioIntelligence } = useCryptoStore();
  const [symbol, setSymbol] = useState('');
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY');
  const [positionSizeUsd, setPositionSizeUsd] = useState('');
  const [detailsOpen, setDetailsOpen] = useState(false);

  const handleAnalyze = useCallback(async () => {
    if (!symbol.trim()) {
      toast.error('Enter a token symbol');
      return;
    }
    const size = parseFloat(positionSizeUsd);
    if (!size || size <= 0) {
      toast.error('Enter a valid position size');
      return;
    }

    setPortfolioIntelligence({ impactLoading: true, impactResult: null });

    try {
      const res = await fetch('/api/portfolio/intelligence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tokenAddress: symbol.trim().toLowerCase(),
          chain: 'ethereum',
          direction: action,
          sizeUsd: size,
          expectedReturn: 0.15,
          expectedVol: 0.6,
        }),
      });

      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Impact analysis failed');
      }

      setPortfolioIntelligence({ impactResult: json.data, impactLoading: false });
      toast.success('Impact analysis complete');
    } catch (err) {
      setPortfolioIntelligence({ impactLoading: false });
      toast.error(err instanceof Error ? err.message : 'Impact analysis failed');
    }
  }, [symbol, action, positionSizeUsd, setPortfolioIntelligence]);

  const result = portfolioIntelligence.impactResult;
  const loading = portfolioIntelligence.impactLoading;

  // Correlation heatmap data
  const correlationData = useMemo(() => {
    if (!result) return [];
    const corr = result.correlationWithExisting;
    return [
      { name: symbol.toUpperCase(), correlation: corr },
    ];
  }, [result, symbol]);

  const rec = result ? recommendationBadge(result.approved, result.impactScore) : null;

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Input Form */}
      <Card className="bg-[#0d1117] border-[#1e293b] p-4">
        <div className="flex items-center gap-2 mb-3">
          <Zap className="h-3.5 w-3.5 text-amber-400" />
          <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
            Proposed Position
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
              Token Symbol
            </Label>
            <Input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="e.g. BTC, ETH, SOL"
              className="h-8 text-[11px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder:text-[#475569]"
            />
          </div>
          <div>
            <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
              Action
            </Label>
            <Select value={action} onValueChange={(v: 'BUY' | 'SELL') => setAction(v)}>
              <SelectTrigger className="h-8 text-[11px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#111827] border-[#1e293b]">
                <SelectItem value="BUY" className="text-[11px] font-mono text-emerald-400">BUY</SelectItem>
                <SelectItem value="SELL" className="text-[11px] font-mono text-red-400">SELL</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
              Position Size (USD)
            </Label>
            <Input
              type="number"
              value={positionSizeUsd}
              onChange={(e) => setPositionSizeUsd(e.target.value)}
              placeholder="1000"
              className="h-8 text-[11px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder:text-[#475569]"
              min={1}
            />
          </div>
        </div>
        <div className="flex justify-end mt-3">
          <Button
            onClick={handleAnalyze}
            disabled={loading || !symbol.trim() || !positionSizeUsd}
            className="h-8 text-[10px] font-mono font-bold bg-amber-500 hover:bg-amber-600 text-black px-4 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                Analyzing...
              </>
            ) : (
              <>
                <Zap className="h-3 w-3 mr-1.5" />
                Analyze Impact
              </>
            )}
          </Button>
        </div>
      </Card>

      {/* Results */}
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.3 }}
            className="flex flex-col gap-3"
          >
            {/* Key Metrics Row */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              {/* Impact Score */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-0.5" style={{ backgroundColor: impactScoreColor(result.impactScore) }} />
                <div className="flex items-center gap-1.5 mb-1.5">
                  <Target className="h-3 w-3 text-amber-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Impact Score</span>
                </div>
                <div className="text-[20px] font-mono font-bold" style={{ color: impactScoreColor(result.impactScore) }}>
                  {impactScoreToDisplay(result.impactScore)}
                </div>
                <div className="mt-1.5 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                  <motion.div
                    className="h-full rounded-full"
                    initial={{ width: 0 }}
                    animate={{ width: `${impactScoreToDisplay(result.impactScore)}%` }}
                    transition={{ duration: 0.8, ease: 'easeOut' }}
                    style={{ backgroundColor: impactScoreColor(result.impactScore) }}
                  />
                </div>
              </Card>

              {/* Risk Contribution */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <Activity className="h-3 w-3 text-cyan-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Risk Contribution</span>
                </div>
                <div className={`text-[20px] font-mono font-bold ${result.riskContribution > 0.05 ? 'text-red-400' : 'text-emerald-400'}`}>
                  {(result.riskContribution * 100).toFixed(2)}%
                </div>
                <span className="text-[8px] font-mono text-[#475569]">marginal risk</span>
              </Card>

              {/* Diversification Delta */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <PieChart className="h-3 w-3 text-emerald-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Diversification Δ</span>
                </div>
                <div className={`text-[20px] font-mono font-bold ${result.diversificationDelta >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {result.diversificationDelta >= 0 ? '+' : ''}{(result.diversificationDelta * 100).toFixed(2)}%
                </div>
                <span className="text-[8px] font-mono text-[#475569]">
                  {result.diversificationDelta >= 0 ? 'improves' : 'reduces'} diversification
                </span>
              </Card>

              {/* Recommendation */}
              {rec && (
                <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Shield className="h-3 w-3" style={{ color: rec.color }} />
                    <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Recommendation</span>
                  </div>
                  <Badge
                    className="text-[11px] h-7 px-3 font-mono font-bold border"
                    style={{ backgroundColor: rec.bg, color: rec.color, borderColor: rec.border }}
                  >
                    {rec.label === 'APPROVE' && <CheckCircle2 className="h-3.5 w-3.5 mr-1.5" />}
                    {rec.label === 'REDUCE' && <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />}
                    {rec.label === 'REJECT' && <XCircle className="h-3.5 w-3.5 mr-1.5" />}
                    {rec.label}
                  </Badge>
                </Card>
              )}
            </div>

            {/* Correlation & VaR Impact */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Correlation with existing */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                <div className="flex items-center gap-2 mb-3">
                  <BarChart3 className="h-3.5 w-3.5 text-cyan-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Correlation with Existing
                  </span>
                </div>
                <div className="flex items-center gap-4">
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[9px] font-mono text-[#64748b]">Avg Correlation</span>
                      <span className={`text-[11px] font-mono font-bold ${result.correlationWithExisting > 0.6 ? 'text-red-400' : result.correlationWithExisting < 0.3 ? 'text-emerald-400' : 'text-amber-400'}`}>
                        {(result.correlationWithExisting * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                      <motion.div
                        className="h-full rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${result.correlationWithExisting * 100}%` }}
                        transition={{ duration: 0.6 }}
                        style={{
                          backgroundColor: result.correlationWithExisting > 0.6
                            ? '#ef4444'
                            : result.correlationWithExisting < 0.3
                              ? '#10b981'
                              : '#f59e0b',
                        }}
                      />
                    </div>
                    <div className="flex justify-between mt-1">
                      <span className="text-[7px] font-mono text-emerald-500">Uncorrelated</span>
                      <span className="text-[7px] font-mono text-red-500">Highly Correlated</span>
                    </div>
                  </div>
                </div>
                {/* Mini heatmap cells */}
                {correlationData.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {correlationData.map((item, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-1.5 px-2 py-1 rounded border border-[#1e293b] bg-[#0a0e17]"
                      >
                        <div
                          className="w-3 h-3 rounded-sm"
                          style={{
                            backgroundColor:
                              item.correlation > 0.6 ? '#ef4444'
                                : item.correlation > 0.3 ? '#f59e0b'
                                  : '#10b981',
                          }}
                        />
                        <span className="text-[8px] font-mono text-[#e2e8f0]">{item.name}</span>
                        <span className="text-[8px] font-mono text-[#64748b]">{(item.correlation * 100).toFixed(0)}%</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              {/* VaR Impact */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                <div className="flex items-center gap-2 mb-3">
                  <TrendingDown className="h-3.5 w-3.5 text-red-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    VaR Impact
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-center">
                    <span className="text-[8px] font-mono text-[#64748b] block mb-1">VaR Δ</span>
                    <span className={`text-[18px] font-mono font-bold ${result.varDelta > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                      {result.varDelta >= 0 ? '+' : ''}{formatCurrency(result.varDelta)}
                    </span>
                  </div>
                  <Separator orientation="vertical" className="h-12 bg-[#1e293b]" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-[8px] font-mono text-[#64748b]">Diversification Δ</span>
                      <ArrowRight className="h-3 w-3 text-[#475569]" />
                      <span className={`text-[10px] font-mono font-bold ${result.diversificationDelta >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {formatPct(result.diversificationDelta * 100)}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[8px] font-mono text-[#64748b]">Risk Contribution</span>
                      <ArrowRight className="h-3 w-3 text-[#475569]" />
                      <span className="text-[10px] font-mono font-bold text-amber-400">
                        {(result.riskContribution * 100).toFixed(2)}%
                      </span>
                    </div>
                  </div>
                </div>
              </Card>
            </div>

            {/* Detailed Breakdown */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <button
                onClick={() => setDetailsOpen(!detailsOpen)}
                className="flex items-center gap-2 w-full text-left"
              >
                <Activity className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Detailed Breakdown
                </span>
                <span className="ml-auto">
                  {detailsOpen ? (
                    <ChevronUp className="h-3.5 w-3.5 text-[#64748b]" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-[#64748b]" />
                  )}
                </span>
              </button>
              <AnimatePresence>
                {detailsOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="mt-3 space-y-1.5 max-h-64 overflow-y-auto custom-scrollbar">
                      {result.recommendations.map((rec_text, i) => {
                        const isWarning = rec_text.includes('WARNING') || rec_text.includes('Reduces') || rec_text.includes('concentrated') || rec_text.includes('too large');
                        const isBlocked = rec_text.includes('BLOCKED');
                        const isPositive = rec_text.includes('Improves') || rec_text.includes('Low correlation') || rec_text.includes('good diversification');
                        return (
                          <motion.div
                            key={i}
                            initial={{ opacity: 0, x: -8 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: i * 0.05 }}
                            className={`flex items-start gap-2 px-3 py-2 rounded border ${
                              isBlocked
                                ? 'bg-red-500/5 border-red-500/20'
                                : isWarning
                                  ? 'bg-amber-500/5 border-amber-500/20'
                                  : isPositive
                                    ? 'bg-emerald-500/5 border-emerald-500/20'
                                    : 'bg-[#0a0e17] border-[#1e293b]'
                            }`}
                          >
                            {isBlocked ? (
                              <XCircle className="h-3 w-3 text-red-400 shrink-0 mt-0.5" />
                            ) : isWarning ? (
                              <AlertTriangle className="h-3 w-3 text-amber-400 shrink-0 mt-0.5" />
                            ) : isPositive ? (
                              <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0 mt-0.5" />
                            ) : (
                              <Activity className="h-3 w-3 text-[#64748b] shrink-0 mt-0.5" />
                            )}
                            <span className={`text-[9px] font-mono leading-relaxed ${
                              isBlocked ? 'text-red-400'
                                : isWarning ? 'text-amber-400'
                                  : isPositive ? 'text-emerald-400'
                                    : 'text-[#94a3b8]'
                            }`}>
                              {rec_text}
                            </span>
                          </motion.div>
                        );
                      })}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// STRESS TEST TAB
// ============================================================

function StressTestTab() {
  const { portfolioIntelligence, setPortfolioIntelligence } = useCryptoStore();
  const [selectedScenario, setSelectedScenario] = useState<string>('');
  const [runAll, setRunAll] = useState(false);

  const handleRunStressTest = useCallback(async (scenario?: string) => {
    setPortfolioIntelligence({ stressLoading: true, stressResult: null });

    try {
      const body = scenario ? { scenario } : {};
      const res = await fetch('/api/portfolio/stress-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Stress test failed');
      }

      setPortfolioIntelligence({ stressResult: json.data, stressLoading: false });
      toast.success('Stress test complete');
    } catch (err) {
      setPortfolioIntelligence({ stressLoading: false });
      toast.error(err instanceof Error ? err.message : 'Stress test failed');
    }
  }, [setPortfolioIntelligence]);

  const result = portfolioIntelligence.stressResult;
  const loading = portfolioIntelligence.stressLoading;

  // Chart data for scenario impacts
  const chartData = useMemo(() => {
    if (!result?.scenarioResults) return [];
    return result.scenarioResults.map((sr) => ({
      name: sr.scenarioName.length > 15 ? sr.scenarioName.slice(0, 15) + '…' : sr.scenarioName,
      fullName: sr.scenarioName,
      impact: Math.abs(sr.portfolioImpactPct * 100),
      impactUsd: sr.portfolioImpactUsd,
      recovery: sr.recoveryDaysEstimate,
    }));
  }, [result]);

  // Kill switch triggers: positions with >20% impact
  const killSwitchPositions = useMemo(() => {
    if (!result?.scenarioResults) return [];
    const positions: Array<{ scenario: string; tokens: string[] }> = [];
    for (const sr of result.scenarioResults) {
      const triggered = Object.entries(sr.positionImpacts)
        .filter(([, impact]) => impact < -0.15)
        .map(([token]) => token);
      if (triggered.length > 0) {
        positions.push({ scenario: sr.scenarioName, tokens: triggered });
      }
    }
    return positions;
  }, [result]);

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Scenario Selector */}
      <Card className="bg-[#0d1117] border-[#1e293b] p-4">
        <div className="flex items-center gap-2 mb-3">
          <TrendingDown className="h-3.5 w-3.5 text-red-400" />
          <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
            Stress Scenarios
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-3">
          {STRESS_SCENARIOS.map((scenario) => {
            const Icon = scenario.icon;
            return (
              <button
                key={scenario.id}
                onClick={() => {
                  setSelectedScenario(scenario.id);
                  setRunAll(false);
                }}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-all ${
                  selectedScenario === scenario.id && !runAll
                    ? 'bg-amber-500/10 border-amber-500/30'
                    : 'bg-[#0a0e17] border-[#1e293b] hover:border-[#334155]'
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" style={{ color: scenario.color }} />
                <div className="min-w-0">
                  <div className="text-[10px] font-mono font-semibold text-[#e2e8f0] truncate">
                    {scenario.label}
                  </div>
                  <div className="text-[8px] font-mono text-[#64748b] truncate">
                    {scenario.description}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2 justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setRunAll(true);
              handleRunStressTest();
            }}
            disabled={loading}
            className="h-7 text-[9px] font-mono text-[#94a3b8] hover:text-amber-400 hover:bg-amber-500/10 px-2"
          >
            Run All Scenarios
          </Button>
          <Button
            onClick={() => handleRunStressTest(runAll ? undefined : selectedScenario)}
            disabled={loading || (!runAll && !selectedScenario)}
            className="h-8 text-[10px] font-mono font-bold bg-red-500/80 hover:bg-red-600 text-white px-4 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                Testing...
              </>
            ) : (
              <>
                <TrendingDown className="h-3 w-3 mr-1.5" />
                Run Stress Test
              </>
            )}
          </Button>
        </div>
      </Card>

      {/* Results */}
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.3 }}
            className="flex flex-col gap-3"
          >
            {/* Key Stats Row */}
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
              {/* Worst Case */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-0.5 bg-red-500" />
                <div className="flex items-center gap-1.5 mb-1.5">
                  <XCircle className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Worst Case Drawdown</span>
                </div>
                <div className="text-[20px] font-mono font-bold text-red-400">
                  {result.worstCase ? formatPct(result.worstCase.portfolioImpactPct * 100) : 'N/A'}
                </div>
                {result.worstCase && (
                  <span className="text-[8px] font-mono text-[#475569]">
                    {result.worstCase.scenarioName} · {formatCurrency(result.worstCase.portfolioImpactUsd)}
                  </span>
                )}
              </Card>

              {/* Recovery Time */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-0.5 bg-amber-500" />
                <div className="flex items-center gap-1.5 mb-1.5">
                  <Activity className="h-3 w-3 text-amber-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Recovery Estimate</span>
                </div>
                <div className="text-[20px] font-mono font-bold text-amber-400">
                  {result.worstCase ? `${result.worstCase.recoveryDaysEstimate}d` : 'N/A'}
                </div>
                <span className="text-[8px] font-mono text-[#475569]">worst case scenario</span>
              </Card>

              {/* Average Impact */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-0.5 bg-cyan-500" />
                <div className="flex items-center gap-1.5 mb-1.5">
                  <BarChart3 className="h-3 w-3 text-cyan-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Avg Impact</span>
                </div>
                <div className="text-[20px] font-mono font-bold text-cyan-400">
                  {formatPct(result.averageImpactPct * 100)}
                </div>
                <span className="text-[8px] font-mono text-[#475569]">across all scenarios</span>
              </Card>
            </div>

            {/* Scenario Impact Chart */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Portfolio Impact per Scenario
                </span>
              </div>
              {chartData.length > 0 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      dataKey="name"
                      tick={{ fill: '#64748b', fontSize: 8, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis
                      tick={{ fill: '#64748b', fontSize: 8, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '6px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                        color: '#e2e8f0',
                      }}
                      formatter={(value: number, name: string) => {
                        if (name === 'impact') return [`${value.toFixed(1)}%`, 'Impact'];
                        return [value, name];
                      }}
                    />
                    <Bar dataKey="impact" name="impact" radius={[4, 4, 0, 0]}>
                      {chartData.map((entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={entry.impact > 30 ? '#ef4444' : entry.impact > 15 ? '#f59e0b' : '#06b6d4'}
                          fillOpacity={0.8}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-40 text-[#475569]">
                  <span className="text-[10px] font-mono">No scenario results to display</span>
                </div>
              )}
            </Card>

            {/* Scenario Table */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Target className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Scenario Details
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[9px] font-mono">
                  <thead>
                    <tr className="border-b border-[#1e293b]">
                      <th className="text-left py-2 px-2 text-[#64748b] uppercase tracking-wider">Scenario</th>
                      <th className="text-right py-2 px-2 text-[#64748b] uppercase tracking-wider">Impact %</th>
                      <th className="text-right py-2 px-2 text-[#64748b] uppercase tracking-wider">Impact $</th>
                      <th className="text-right py-2 px-2 text-[#64748b] uppercase tracking-wider">Recovery</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.scenarioResults.map((sr, i) => (
                      <motion.tr
                        key={sr.scenarioId}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className="border-b border-[#1e293b]/50"
                      >
                        <td className="py-2 px-2 text-[#e2e8f0]">{sr.scenarioName}</td>
                        <td className={`text-right py-2 px-2 font-bold ${sr.portfolioImpactPct < -0.2 ? 'text-red-400' : sr.portfolioImpactPct < -0.1 ? 'text-amber-400' : 'text-cyan-400'}`}>
                          {formatPct(sr.portfolioImpactPct * 100)}
                        </td>
                        <td className="text-right py-2 px-2 text-[#94a3b8]">
                          {formatCurrency(sr.portfolioImpactUsd)}
                        </td>
                        <td className="text-right py-2 px-2 text-[#94a3b8]">
                          {sr.recoveryDaysEstimate}d
                        </td>
                      </motion.tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* Kill Switch Triggers */}
            {killSwitchPositions.length > 0 && (
              <Card className="bg-[#0d1117] border-red-500/20 p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Shield className="h-3.5 w-3.5 text-red-400" />
                  <span className="text-[10px] font-mono font-semibold text-red-400 uppercase tracking-wider">
                    Kill Switch Triggers
                  </span>
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto custom-scrollbar">
                  {killSwitchPositions.map((trigger, i) => (
                    <div key={i} className="flex items-center gap-2 px-3 py-1.5 bg-red-500/5 border border-red-500/15 rounded">
                      <XCircle className="h-3 w-3 text-red-400 shrink-0" />
                      <span className="text-[9px] font-mono text-red-400">
                        <strong>{trigger.scenario}</strong>: {trigger.tokens.join(', ')}
                      </span>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// OPTIMIZATION TAB
// ============================================================

function OptimizationTab() {
  const { portfolioIntelligence, setPortfolioIntelligence } = useCryptoStore();
  const [method, setMethod] = useState<string>('MEAN_VARIANCE');

  const handleOptimize = useCallback(async () => {
    setPortfolioIntelligence({ optimizationLoading: true, optimizationResult: null });

    try {
      const res = await fetch('/api/portfolio/optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method }),
      });

      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Optimization failed');
      }

      // Also fetch current portfolio weights
      const statsRes = await fetch('/api/portfolio/stats');
      let currentWeights: Record<string, number> = {};
      if (statsRes.ok) {
        const statsJson = await statsRes.json();
        const openPositions: Array<{ symbol: string; sizeUsd: number }> = statsJson.data?.openPositions ?? [];
        const totalSize = openPositions.reduce((s: number, p: { sizeUsd: number }) => s + p.sizeUsd, 0);
        if (totalSize > 0) {
          currentWeights = Object.fromEntries(
            openPositions.map((p: { symbol: string; sizeUsd: number }) => [p.symbol, p.sizeUsd / totalSize])
          );
        }
      }

      setPortfolioIntelligence({
        optimizationResult: json.data,
        optimizationLoading: false,
        currentWeights,
      });
      toast.success('Portfolio optimization complete');
    } catch (err) {
      setPortfolioIntelligence({ optimizationLoading: false });
      toast.error(err instanceof Error ? err.message : 'Optimization failed');
    }
  }, [method, setPortfolioIntelligence]);

  const result = portfolioIntelligence.optimizationResult;
  const loading = portfolioIntelligence.optimizationLoading;
  const currentWeights = portfolioIntelligence.currentWeights;

  // Pie chart data for current weights
  const currentPieData = useMemo(() => {
    const entries = Object.entries(currentWeights);
    if (entries.length === 0) {
      return [{ name: 'No Positions', value: 1, fill: '#1e293b' }];
    }
    return entries.map(([name, value], i) => ({
      name,
      value: Math.round(value * 10000) / 100,
      fill: CHART_COLORS[i % CHART_COLORS.length],
    }));
  }, [currentWeights]);

  // Comparison chart data (current vs optimized)
  const comparisonData = useMemo(() => {
    if (!result) return [];
    const allTokens = new Set([
      ...Object.keys(currentWeights),
      ...Object.keys(result.weights),
    ]);
    return Array.from(allTokens).map((token) => ({
      name: token.length > 6 ? token.slice(0, 6) + '…' : token,
      fullName: token,
      current: Math.round((currentWeights[token] ?? 0) * 10000) / 100,
      optimized: Math.round((result.weights[token] ?? 0) * 10000) / 100,
    }));
  }, [result, currentWeights]);

  // Suggested trades
  const suggestedTrades = useMemo(() => {
    if (!result) return [];
    const allTokens = new Set([
      ...Object.keys(currentWeights),
      ...Object.keys(result.weights),
    ]);
    return Array.from(allTokens)
      .map((token) => {
        const current = currentWeights[token] ?? 0;
        const optimized = result.weights[token] ?? 0;
        const delta = optimized - current;
        return {
          token,
          current: current * 100,
          optimized: optimized * 100,
          delta: delta * 100,
          action: delta > 0.5 ? 'BUY' as const : delta < -0.5 ? 'SELL' as const : 'HOLD' as const,
        };
      })
      .filter((t) => Math.abs(t.delta) > 0.5)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  }, [result, currentWeights]);

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Method Selector */}
      <Card className="bg-[#0d1117] border-[#1e293b] p-4">
        <div className="flex items-center gap-2 mb-3">
          <BarChart3 className="h-3.5 w-3.5 text-cyan-400" />
          <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
            Optimization Method
          </span>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-3">
          {OPTIMIZATION_METHODS.map((m) => (
            <button
              key={m.id}
              onClick={() => setMethod(m.id)}
              className={`flex flex-col items-start px-3 py-2.5 rounded-lg border transition-all ${
                method === m.id
                  ? 'bg-cyan-500/10 border-cyan-500/30'
                  : 'bg-[#0a0e17] border-[#1e293b] hover:border-[#334155]'
              }`}
            >
              <span className={`text-[10px] font-mono font-bold ${method === m.id ? 'text-cyan-400' : 'text-[#e2e8f0]'}`}>
                {m.label}
              </span>
              <span className="text-[8px] font-mono text-[#64748b]">{m.description}</span>
            </button>
          ))}
        </div>
        <div className="flex justify-end">
          <Button
            onClick={handleOptimize}
            disabled={loading}
            className="h-8 text-[10px] font-mono font-bold bg-cyan-600 hover:bg-cyan-700 text-white px-4 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                Optimizing...
              </>
            ) : (
              <>
                <BarChart3 className="h-3 w-3 mr-1.5" />
                Optimize
              </>
            )}
          </Button>
        </div>
      </Card>

      {/* Results */}
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.3 }}
            className="flex flex-col gap-3"
          >
            {/* Current Weights + Optimized Stats */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Current Portfolio Weights Pie */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                <div className="flex items-center gap-2 mb-3">
                  <PieChart className="h-3.5 w-3.5 text-emerald-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Current Weights
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={180}>
                  <RechartsPie>
                    <Pie
                      data={currentPieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={40}
                      outerRadius={70}
                      dataKey="value"
                      stroke="#0d1117"
                      strokeWidth={2}
                    >
                      {currentPieData.map((entry, index) => (
                        <Cell key={`pie-${index}`} fill={entry.fill} />
                      ))}
                    </Pie>
                    <Legend
                      wrapperStyle={{ fontSize: '8px', fontFamily: 'monospace', color: '#94a3b8' }}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '6px',
                        fontSize: '9px',
                        fontFamily: 'monospace',
                        color: '#e2e8f0',
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}%`, 'Weight']}
                    />
                  </RechartsPie>
                </ResponsiveContainer>
              </Card>

              {/* Optimized Portfolio Stats */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Zap className="h-3.5 w-3.5 text-amber-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Optimization Result
                  </span>
                </div>
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="px-3 py-2 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                      <span className="text-[8px] font-mono text-[#64748b] uppercase block">Expected Return</span>
                      <span className={`text-[16px] font-mono font-bold ${result.expectedReturn >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {formatPct(result.expectedReturn * 100)}
                      </span>
                    </div>
                    <div className="px-3 py-2 bg-[#0a0e17] rounded-lg border border-[#1e293b]">
                      <span className="text-[8px] font-mono text-[#64748b] uppercase block">Expected Vol</span>
                      <span className="text-[16px] font-mono font-bold text-amber-400">
                        {(result.expectedVol * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                  <div className="px-3 py-2 bg-[#0a0e17] rounded-lg border border-amber-500/20">
                    <div className="flex items-center justify-between">
                      <span className="text-[8px] font-mono text-[#64748b] uppercase">Sharpe Ratio</span>
                      <span className="text-[9px] font-mono text-[#475569]">{result.method}</span>
                    </div>
                    <span className={`text-[24px] font-mono font-bold ${result.sharpeRatio >= 1 ? 'text-emerald-400' : result.sharpeRatio >= 0 ? 'text-amber-400' : 'text-red-400'}`}>
                      {result.sharpeRatio.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Badge className="text-[7px] h-4 px-1.5 font-mono bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                      {OPTIMIZATION_METHODS.find(m => m.id === result.method)?.label ?? result.method}
                    </Badge>
                  </div>
                </div>
              </Card>
            </div>

            {/* Current vs Optimized Weights Comparison */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="h-3.5 w-3.5 text-cyan-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Current vs Optimized Weights
                </span>
              </div>
              {comparisonData.length > 0 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={comparisonData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      dataKey="name"
                      tick={{ fill: '#64748b', fontSize: 8, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis
                      tick={{ fill: '#64748b', fontSize: 8, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '6px',
                        fontSize: '9px',
                        fontFamily: 'monospace',
                        color: '#e2e8f0',
                      }}
                      formatter={(value: number, name: string) => [
                        `${value.toFixed(1)}%`,
                        name === 'current' ? 'Current' : 'Optimized',
                      ]}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace', color: '#94a3b8' }}
                    />
                    <Bar dataKey="current" name="current" fill="#64748b" radius={[2, 2, 0, 0]} fillOpacity={0.7} />
                    <Bar dataKey="optimized" name="optimized" fill="#06b6d4" radius={[2, 2, 0, 0]} fillOpacity={0.9} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-40 text-[#475569]">
                  <span className="text-[10px] font-mono">No weight data to display</span>
                </div>
              )}
            </Card>

            {/* Suggested Trades */}
            {suggestedTrades.length > 0 && (
              <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Zap className="h-3.5 w-3.5 text-amber-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Suggested Rebalance Trades
                  </span>
                  <Badge className="text-[7px] h-4 px-1.5 font-mono bg-amber-500/10 text-amber-400 border border-amber-500/20 ml-auto">
                    {suggestedTrades.length} trades
                  </Badge>
                </div>
                <div className="space-y-1.5 max-h-64 overflow-y-auto custom-scrollbar">
                  {suggestedTrades.map((trade, i) => (
                    <motion.div
                      key={trade.token}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.05 }}
                      className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${
                        trade.action === 'BUY'
                          ? 'bg-emerald-500/5 border-emerald-500/20'
                          : trade.action === 'SELL'
                            ? 'bg-red-500/5 border-red-500/20'
                            : 'bg-[#0a0e17] border-[#1e293b]'
                      }`}
                    >
                      <Badge
                        className={`text-[8px] h-4 px-1.5 font-mono font-bold border ${
                          trade.action === 'BUY'
                            ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                            : trade.action === 'SELL'
                              ? 'bg-red-500/15 text-red-400 border-red-500/30'
                              : 'bg-[#1e293b] text-[#94a3b8] border-[#334155]'
                        }`}
                      >
                        {trade.action}
                      </Badge>
                      <span className="text-[10px] font-mono font-bold text-[#e2e8f0] min-w-[60px]">
                        {trade.token}
                      </span>
                      <div className="flex-1 flex items-center gap-2">
                        <span className="text-[9px] font-mono text-[#64748b]">
                          {trade.current.toFixed(1)}%
                        </span>
                        <ArrowRight className="h-3 w-3 text-[#475569]" />
                        <span className="text-[9px] font-mono text-cyan-400 font-bold">
                          {trade.optimized.toFixed(1)}%
                        </span>
                      </div>
                      <span className={`text-[9px] font-mono font-bold ${
                        trade.delta > 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}>
                        {trade.delta > 0 ? '+' : ''}{trade.delta.toFixed(1)}%
                      </span>
                    </motion.div>
                  ))}
                </div>
              </Card>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function PortfolioIntelligencePanel() {
  const [activeTab, setActiveTab] = useState('impact');

  return (
    <div className="flex flex-col h-full bg-[#0a0e17]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <PieChart className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
            Portfolio Intelligence
          </span>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col flex-1 min-h-0">
        <div className="px-3 pt-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <TabsList className="bg-[#0a0e17] h-8 p-0.5 w-full">
            <TabsTrigger
              value="impact"
              className="h-7 text-[9px] font-mono data-[state=active]:bg-[#1e293b] data-[state=active]:text-amber-400 flex-1 gap-1"
            >
              <Zap className="h-3 w-3" />
              Impact Analysis
            </TabsTrigger>
            <TabsTrigger
              value="stress"
              className="h-7 text-[9px] font-mono data-[state=active]:bg-[#1e293b] data-[state=active]:text-red-400 flex-1 gap-1"
            >
              <TrendingDown className="h-3 w-3" />
              Stress Test
            </TabsTrigger>
            <TabsTrigger
              value="optimization"
              className="h-7 text-[9px] font-mono data-[state=active]:bg-[#1e293b] data-[state=active]:text-cyan-400 flex-1 gap-1"
            >
              <BarChart3 className="h-3 w-3" />
              Optimization
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="impact" className="flex-1 overflow-y-auto min-h-0 mt-0">
          <ImpactAnalysisTab />
        </TabsContent>
        <TabsContent value="stress" className="flex-1 overflow-y-auto min-h-0 mt-0">
          <StressTestTab />
        </TabsContent>
        <TabsContent value="optimization" className="flex-1 overflow-y-auto min-h-0 mt-0">
          <OptimizationTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default PortfolioIntelligencePanel;
