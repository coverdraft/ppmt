'use client';

import { useDeepAnalysisStore } from '@/store/deep-analysis-store';
import type { DeepAnalysis, ThinkingDepth } from '@/lib/services/strategy/deep-analysis-engine';
import type { DeepAnalysisResult } from '@/lib/services/strategy/deep-analysis-engine';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search,
  Loader2,
  Brain,
  Shield,
  ShieldAlert,
  ShieldX,
  TrendingUp,
  TrendingDown,
  Minus,
  ChevronDown,
  ChevronRight,
  Zap,
  Target,
  AlertTriangle,
  Eye,
  Bot,
  Fish,
  Activity,
  BarChart3,
  Scale,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
  Layers,
  CheckCircle2,
  XCircle,
  Circle,
  Sparkles,
  Gauge,
  Crosshair,
  Route,
} from 'lucide-react';

export type AnalysisData = DeepAnalysis & Partial<DeepAnalysisResult> & { timestamp?: Date };

/**
 * Normalize analysis data to ensure all nested objects exist.
 * The API may return DeepAnalysisResult (flat) which lacks verdict/phaseAssessment/etc.
 * This ensures the frontend always has safe access to nested properties.
 */
function normalizeAnalysis(analysis: AnalysisData): AnalysisData {
  const a = analysis as any;
  const safeAnalysis = { ...analysis };
  (safeAnalysis as any).verdict = analysis.verdict || {
    action: a.recommendation || 'HOLD',
    confidence: a.recommendationConfidence || 0.5,
    reasoning: a.summary || a.justification?.join('. ') || '',
    summary: a.summary || '',
  };
  (safeAnalysis as any).phaseAssessment = analysis.phaseAssessment || {
    phase: 'GROWTH',
    confidence: a.recommendationConfidence || 0.5,
    timeInPhase: a.suggestedTimeHorizon || 'Unknown',
    narrative: a.scenarios?.base?.description || '',
  };
  (safeAnalysis as any).patternAssessment = analysis.patternAssessment || {
    dominantPattern: null,
    patternSentiment: 'NEUTRAL',
    multiTfConfirmed: false,
    narrative: '',
  };
  (safeAnalysis as any).traderAssessment = analysis.traderAssessment || {
    dominantArchetype: 'UNKNOWN',
    behaviorFlow: 'NEUTRAL',
    riskFromBots: 'LOW',
    riskFromWhales: 'MODERATE',
    narrative: '',
  };
  (safeAnalysis as any).riskAssessment = analysis.riskAssessment && typeof analysis.riskAssessment === 'object'
    ? analysis.riskAssessment
    : {
        overallRisk: a.riskLevel || 'MEDIUM',
        keyRisks: a.bearishFactors || [],
        mitigatingFactors: a.bullishFactors || [],
        blackSwanRisk: 'LOW',
      };
  (safeAnalysis as any).strategyRecommendation = analysis.strategyRecommendation || {
    strategy: 'WAIT_AND_MONITOR',
    direction: 'NEUTRAL',
    confidenceLevel: 0.5,
    positionSizeRecommendation: '5%',
    stopLossRecommendation: '-10%',
    takeProfitRecommendation: '15%',
    entryConditions: [],
    exitConditions: [],
  };
  (safeAnalysis as any).pros = analysis.pros || (a.bullishFactors || []).map((f: string) => ({ factor: f, weight: 0.7, explanation: f }));
  (safeAnalysis as any).cons = analysis.cons || (a.bearishFactors || []).map((f: string) => ({ factor: f, weight: 0.6, explanation: f }));
  (safeAnalysis as any).neutrals = analysis.neutrals || (a.neutralFactors || []).map((f: string) => ({ factor: f, weight: 0.5, explanation: f }));
  (safeAnalysis as any).reasoningChain = analysis.reasoningChain || a.justification || [];
  return safeAnalysis;
}

// Add missing properties to DeepAnalysis verdict type
// ============================================================

const VERDICT_CONFIG: Record<string, { color: string; bg: string; border: string; icon: React.ElementType }> = {
  STRONG_BUY: { color: 'text-emerald-300', bg: 'bg-emerald-500/15', border: 'border-emerald-500/40', icon: TrendingUp },
  BUY: { color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', icon: ArrowUpRight },
  HOLD: { color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', icon: Minus },
  SELL: { color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', icon: ArrowDownRight },
  STRONG_SELL: { color: 'text-red-300', bg: 'bg-red-500/15', border: 'border-red-500/40', icon: TrendingDown },
  WAIT: { color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', icon: Clock },
};

const DIRECTION_CONFIG: Record<string, { color: string; bg: string; icon: React.ElementType }> = {
  LONG: { color: 'text-emerald-400', bg: 'bg-emerald-500/15', icon: TrendingUp },
  SHORT: { color: 'text-red-400', bg: 'bg-red-500/15', icon: TrendingDown },
  HOLD: { color: 'text-yellow-400', bg: 'bg-yellow-500/15', icon: Minus },
  WAIT: { color: 'text-slate-400', bg: 'bg-slate-500/15', icon: Clock },
};

const RISK_CONFIG: Record<string, { color: string; bg: string; border: string; icon: React.ElementType }> = {
  VERY_LOW: { color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', icon: Shield },
  LOW: { color: 'text-emerald-300', bg: 'bg-emerald-500/8', border: 'border-emerald-500/20', icon: Shield },
  MEDIUM: { color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', icon: ShieldAlert },
  HIGH: { color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', icon: ShieldAlert },
  EXTREME: { color: 'text-red-300', bg: 'bg-red-500/15', border: 'border-red-500/40', icon: ShieldX },
};

const PHASE_COLORS: Record<string, { color: string; bg: string; border: string }> = {
  GENESIS: { color: 'text-violet-400', bg: 'bg-violet-500/15', border: 'border-violet-500/30' },
  INCIPIENT: { color: 'text-cyan-400', bg: 'bg-cyan-500/15', border: 'border-cyan-500/30' },
  GROWTH: { color: 'text-emerald-400', bg: 'bg-emerald-500/15', border: 'border-emerald-500/30' },
  FOMO: { color: 'text-amber-400', bg: 'bg-amber-500/15', border: 'border-amber-500/30' },
  DECLINE: { color: 'text-red-400', bg: 'bg-red-500/15', border: 'border-red-500/30' },
  LEGACY: { color: 'text-slate-400', bg: 'bg-slate-500/15', border: 'border-slate-500/30' },
};

const DEPTH_CONFIG: Record<ThinkingDepth, { label: string; desc: string; color: string; badgeText: string; badgeBg: string; badgeBorder: string; glowClass: string; infoText: string }> = {
  QUICK: {
    label: 'Quick',
    desc: 'Fast scan, basic analysis',
    color: 'text-yellow-400',
    badgeText: '\u26A1 Quick Scan',
    badgeBg: 'bg-amber-500/15',
    badgeBorder: 'border-amber-500/40',
    glowClass: '',
    infoText: 'Quick scan analyzed 3 core factors. For deeper insights, run Standard or Deep analysis.',
  },
  STANDARD: {
    label: 'Standard',
    desc: 'Balanced depth and speed',
    color: 'text-cyan-400',
    badgeText: '\uD83D\uDD0D Standard Analysis',
    badgeBg: 'bg-cyan-500/15',
    badgeBorder: 'border-cyan-500/40',
    glowClass: '',
    infoText: 'Standard analysis evaluated 6 weighted factors with 3 scenarios. For stress tests and detailed narratives, run Deep analysis.',
  },
  DEEP: {
    label: 'Deep',
    desc: 'Extended analysis with scenarios',
    color: 'text-violet-400',
    badgeText: '\uD83E\uDDEC Deep Analysis',
    badgeBg: 'bg-violet-500/15',
    badgeBorder: 'border-violet-500/40',
    glowClass: 'drop-shadow-[0_0_8px_rgba(139,92,246,0.5)]',
    infoText: 'Deep analysis evaluated 12 factors with 5 scenarios, stress tests, phase transition probabilities, and whale narratives.',
  },
};

const CHAINS = [
  { value: 'AUTO', label: '🔍 Auto-detect' },
  { value: 'SOL', label: 'Solana' },
  { value: 'ETH', label: 'Ethereum' },
  { value: 'BASE', label: 'Base' },
  { value: 'BSC', label: 'BNB Chain' },
  { value: 'ARB', label: 'Arbitrum' },
];

function formatConfidence(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function getWeightBarColor(weight: number): string {
  if (weight >= 0.7) return 'bg-emerald-500';
  if (weight >= 0.5) return 'bg-emerald-400/70';
  if (weight >= 0.3) return 'bg-yellow-500/70';
  return 'bg-slate-500/50';
}

// ============================================================
// ANIMATION VARIANTS
// ============================================================

const cardVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.06, duration: 0.35, ease: 'easeOut' as const },
  }),
  exit: { opacity: 0, y: -8, transition: { duration: 0.2 } },
};

const staggerContainer = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.05 } },
};

const itemVariants = {
  hidden: { opacity: 0, x: -8 },
  visible: { opacity: 1, x: 0, transition: { duration: 0.25 } },
};

// ============================================================
// SUB-COMPONENTS
// ============================================================

/** Mini confidence bar used inline */
function MiniConfidenceBar({ value, className = '' }: { value: number; className?: string }) {
  const pct = Math.round(value * 100);
  const barColor =
    pct >= 70 ? 'bg-emerald-500' : pct >= 50 ? 'bg-yellow-500' : pct >= 30 ? 'bg-orange-500' : 'bg-red-500';
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
      </div>
      <span className="mono-data text-[10px] text-[#94a3b8] w-8 text-right">{pct}%</span>
    </div>
  );
}

/** Risk level badge */
function RiskBadge({ level }: { level: string }) {
  const cfg = RISK_CONFIG[level] || RISK_CONFIG.MEDIUM;
  const Icon = cfg.icon;
  return (
    <Badge className={`${cfg.bg} ${cfg.color} ${cfg.border} border text-[10px] font-mono font-bold gap-1`}>
      <Icon className="h-3 w-3" />
      {level.replace('_', ' ')}
    </Badge>
  );
}

/** Verdict badge with icon and pulse */
function VerdictBadge({ action }: { action: string }) {
  const cfg = VERDICT_CONFIG[action] || VERDICT_CONFIG.HOLD;
  const Icon = cfg.icon;
  return (
    <motion.div
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 300, damping: 20 }}
    >
      <Badge className={`${cfg.bg} ${cfg.color} ${cfg.border} border text-xs font-mono font-bold gap-1.5 px-3 py-1`}>
        <Icon className="h-3.5 w-3.5" />
        {action.replace('_', ' ')}
      </Badge>
    </motion.div>
  );
}

/** Direction badge */
function DirectionBadge({ direction }: { direction: string }) {
  const cfg = DIRECTION_CONFIG[direction] || DIRECTION_CONFIG.HOLD;
  const Icon = cfg.icon;
  return (
    <Badge className={`${cfg.bg} ${cfg.color} border border-transparent text-[10px] font-mono font-bold gap-1`}>
      <Icon className="h-3 w-3" />
      {direction}
    </Badge>
  );
}

/** Evidence item row */
function EvidenceItem({
  factor,
  weight,
  explanation,
  type,
}: {
  factor: string;
  weight: number;
  explanation: string;
  type: 'pro' | 'con' | 'neutral';
}) {
  const icon =
    type === 'pro' ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0 mt-0.5" /> :
    type === 'con' ? <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0 mt-0.5" /> :
    <Circle className="h-3.5 w-3.5 text-yellow-400/60 shrink-0 mt-0.5" />;

  const weightColor =
    type === 'pro' ? 'text-emerald-400' :
    type === 'con' ? 'text-red-400' :
    'text-yellow-400';

  return (
    <motion.div variants={itemVariants} className="flex gap-2 py-1.5">
      {icon}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-[#e2e8f0] truncate">{factor}</span>
          <span className={`mono-data text-[9px] font-bold ${weightColor} shrink-0`}>
            w:{weight.toFixed(2)}
          </span>
        </div>
        <div className="mt-0.5">
          <div className="h-0.5 w-16 bg-[#1a1f2e] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${getWeightBarColor(weight)}`}
              style={{ width: `${Math.min(weight * 100, 100)}%` }}
            />
          </div>
        </div>
        <p className="text-[10px] text-[#94a3b8] mt-0.5 leading-relaxed">{explanation}</p>
      </div>
    </motion.div>
  );
}

// ============================================================
// INPUT FORM
// ============================================================

function AnalysisInputForm() {
  const {
    tokenAddress,
    chain,
    depth,
    status,
    setTokenAddress,
    setChain,
    setDepth,
    runAnalysis,
  } = useDeepAnalysisStore();

  const isLoading = status === 'loading';

  return (
    <Card className="bg-[#0d1117] border-[#1e293b]">
      <CardHeader className="pb-2 px-4 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Brain className="h-4 w-4 text-[#d4af37]" />
          <span className="font-mono text-[11px] uppercase tracking-wider text-[#94a3b8]">
            Deep Analysis Engine
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-3">
        <div className="flex flex-col sm:flex-row gap-2">
          {/* Token Address Input */}
          <div className="flex-1 relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#64748b]" />
            <Input
              placeholder="Token or wallet address (auto-detects chain)..."
              value={tokenAddress}
              onChange={(e) => setTokenAddress(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isLoading) runAnalysis();
              }}
              className="pl-8 h-8 bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] font-mono text-xs placeholder:text-[#475569] focus:border-[#d4af37]/50 focus:ring-[#d4af37]/20"
              disabled={isLoading}
            />
          </div>

          {/* Chain Selector */}
          <Select value={chain} onValueChange={setChain} disabled={isLoading}>
            <SelectTrigger className="w-full sm:w-[130px] h-8 bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-[#0d1117] border-[#1e293b]">
              {CHAINS.map((c) => (
                <SelectItem key={c.value} value={c.value} className="text-xs font-mono text-[#e2e8f0] focus:bg-[#1e293b] focus:text-[#e2e8f0]">
                  {c.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Depth Selector */}
          <Select value={depth} onValueChange={(v) => setDepth(v as ThinkingDepth)} disabled={isLoading}>
            <SelectTrigger className="w-full sm:w-[140px] h-8 bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-[#0d1117] border-[#1e293b]">
              {(Object.entries(DEPTH_CONFIG) as [ThinkingDepth, typeof DEPTH_CONFIG[ThinkingDepth]][]).map(([key, cfg]) => (
                <SelectItem key={key} value={key} className="text-xs font-mono focus:bg-[#1e293b] focus:text-[#e2e8f0]">
                  <div className="flex items-center gap-2">
                    <span className={cfg.color}>{cfg.label}</span>
                    <span className="text-[9px] text-[#64748b]">{cfg.desc}</span>
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Analyze Button */}
          <Button
            onClick={runAnalysis}
            disabled={isLoading || !tokenAddress.trim()}
            className="h-8 px-4 bg-[#d4af37] hover:bg-[#c4a030] text-[#0a0e17] font-mono text-xs font-bold gap-1.5 shrink-0 disabled:opacity-50"
          >
            {isLoading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Analyzing...
              </>
            ) : (
              <>
                <Zap className="h-3.5 w-3.5" />
                Analyze
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================
// ANALYSIS HEADER
// ============================================================

function AnalysisHeader({ analysis }: { analysis: AnalysisData }) {
  const verdictAction = analysis.verdict?.action || (analysis as any).recommendation || 'HOLD';
  const vCfg = VERDICT_CONFIG[verdictAction] || VERDICT_CONFIG.HOLD;
  const phasePhase = analysis.phaseAssessment?.phase || 'GROWTH';
  const pCfg = PHASE_COLORS[phasePhase] || PHASE_COLORS.LEGACY;
  const depthCfg = DEPTH_CONFIG[analysis.depth] || DEPTH_CONFIG.STANDARD;

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col gap-3 px-4 py-3 bg-[#0d1117] border border-[#1e293b] rounded-lg"
    >
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        {/* Symbol & Price */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center h-9 w-9 rounded-lg bg-[#1a1f2e] border border-[#2d3748]">
            <Sparkles className="h-4 w-4 text-[#d4af37]" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-base font-bold text-[#e2e8f0]">{analysis.symbol}</span>
              <Badge className={`${pCfg.bg} ${pCfg.color} ${pCfg.border} border text-[9px] font-mono font-bold`}>
                {phasePhase}
              </Badge>
            </div>
            <span className="font-mono text-[11px] text-[#64748b]">
              {analysis.tokenAddress?.slice(0, 6)}...{analysis.tokenAddress?.slice(-4)}
            </span>
          </div>
        </div>

        <div className="sm:ml-auto flex items-center gap-3">
          {/* Confidence */}
          <div className="text-right">
            <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Confidence</div>
            <div className="font-mono text-sm font-bold text-[#e2e8f0]">
              {formatConfidence(analysis.verdict?.confidence || (analysis as any).recommendationConfidence || 0.5)}
            </div>
          </div>

          <Separator orientation="vertical" className="h-8 bg-[#1e293b]" />

          {/* Verdict */}
          <VerdictBadge action={verdictAction} />
        </div>
      </div>

      {/* Prominent Depth Badge */}
      <div className="flex items-center">
        <Badge className={`${depthCfg.badgeBg} ${depthCfg.color} ${depthCfg.badgeBorder} border text-[10px] font-mono font-bold px-3 py-1 gap-1.5 ${depthCfg.glowClass}`}>
          {analysis.depth === 'DEEP' && <span className="inline-block animate-pulse">\u2726</span>}
          {depthCfg.badgeText}
        </Badge>
      </div>
    </motion.div>
  );
}

// ============================================================
// PHASE ASSESSMENT CARD
// ============================================================

function PhaseAssessmentCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const phase = analysis.phaseAssessment?.phase || 'GROWTH';
  const pCfg = PHASE_COLORS[phase] || PHASE_COLORS.LEGACY;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b] h-full">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Layers className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
              Phase Assessment
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-2.5">
          <div className="flex items-center gap-2">
            <Badge className={`${pCfg.bg} ${pCfg.color} ${pCfg.border} border text-[11px] font-mono font-bold`}>
              {phase}
            </Badge>
            <span className="text-[10px] font-mono text-[#64748b]">
              {analysis.phaseAssessment?.timeInPhase || 'Unknown'}
            </span>
          </div>
          <MiniConfidenceBar value={analysis.phaseAssessment?.confidence || 0.5} />
          <p className="text-[10px] text-[#94a3b8] leading-relaxed">
            {analysis.phaseAssessment?.narrative || ''}
          </p>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// PATTERN ASSESSMENT CARD
// ============================================================

function PatternAssessmentCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const sentiment = analysis.patternAssessment?.patternSentiment || 'NEUTRAL';
  const sentimentColor =
    sentiment === 'BULLISH' ? 'text-emerald-400' :
    sentiment === 'BEARISH' ? 'text-red-400' :
    'text-yellow-400';

  const SentimentIcon =
    sentiment === 'BULLISH' ? TrendingUp :
    sentiment === 'BEARISH' ? TrendingDown :
    Minus;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b] h-full">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <BarChart3 className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
              Pattern Assessment
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-2.5">
          {analysis.patternAssessment?.dominantPattern ? (
            <>
              <div className="flex items-center gap-2">
                <SentimentIcon className={`h-4 w-4 ${sentimentColor}`} />
                <span className="font-mono text-[11px] font-bold text-[#e2e8f0]">
                  {analysis.patternAssessment.dominantPattern}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Badge className={`text-[9px] font-mono font-bold ${
                  sentiment === 'BULLISH'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : sentiment === 'BEARISH'
                    ? 'bg-red-500/15 text-red-400 border-red-500/30'
                    : 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'
                } border`}>
                  {sentiment}
                </Badge>
                {analysis.patternAssessment.multiTfConfirmed && (
                  <Badge className="bg-cyan-500/15 text-cyan-400 border-cyan-500/30 border text-[9px] font-mono font-bold gap-1">
                    <CheckCircle2 className="h-2.5 w-2.5" />
                    Multi-TF Confirmed
                  </Badge>
                )}
              </div>
            </>
          ) : (
            <div className="flex items-center gap-2 text-[#64748b]">
              <Minus className="h-4 w-4" />
              <span className="font-mono text-[11px]">No dominant pattern detected</span>
            </div>
          )}
          <p className="text-[10px] text-[#94a3b8] leading-relaxed">
            {analysis.patternAssessment?.narrative || ''}
          </p>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// TRADER ASSESSMENT CARD
// ============================================================

function TraderAssessmentCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const behaviorFlow = analysis.traderAssessment?.behaviorFlow || 'NEUTRAL';
  const flowColor =
    behaviorFlow === 'BULLISH' ? 'text-emerald-400' :
    behaviorFlow === 'BEARISH' ? 'text-red-400' :
    'text-yellow-400';

  const FlowIcon =
    behaviorFlow === 'BULLISH' ? TrendingUp :
    behaviorFlow === 'BEARISH' ? TrendingDown :
    Minus;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b] h-full">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Eye className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
              Trader Assessment
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-2.5">
          {/* Dominant Archetype */}
          <div className="flex items-center gap-2">
            <Activity className="h-3.5 w-3.5 text-[#64748b]" />
            <span className="text-[10px] font-mono text-[#64748b]">Archetype:</span>
            <span className="font-mono text-[11px] font-bold text-[#e2e8f0]">
              {analysis.traderAssessment?.dominantArchetype || 'UNKNOWN'}
            </span>
          </div>

          {/* Behavior Flow */}
          <div className="flex items-center gap-2">
            <FlowIcon className={`h-3.5 w-3.5 ${flowColor}`} />
            <span className="text-[10px] font-mono text-[#64748b]">Flow:</span>
            <Badge className={`text-[9px] font-mono font-bold ${
              behaviorFlow === 'BULLISH'
                ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                : behaviorFlow === 'BEARISH'
                ? 'bg-red-500/15 text-red-400 border-red-500/30'
                : 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'
            } border`}>
              {behaviorFlow}
            </Badge>
          </div>

          {/* Risk indicators */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <Bot className="h-3 w-3 text-[#64748b]" />
              <span className="text-[9px] font-mono text-[#64748b]">Bot</span>
              <RiskBadge level={analysis.traderAssessment?.riskFromBots || 'LOW'} />
            </div>
            <div className="flex items-center gap-1.5">
              <Fish className="h-3 w-3 text-[#64748b]" />
              <span className="text-[9px] font-mono text-[#64748b]">Whale</span>
              <RiskBadge level={analysis.traderAssessment?.riskFromWhales || 'MODERATE'} />
            </div>
          </div>

          <p className="text-[10px] text-[#94a3b8] leading-relaxed">
            {analysis.traderAssessment?.narrative || ''}
          </p>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// EVIDENCE MATRIX
// ============================================================

function EvidenceMatrixCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const totalWeight = (items: Array<{ weight: number }>) =>
    items.reduce((s, i) => s + i.weight, 0);

  const proWeight = totalWeight(analysis.pros);
  const conWeight = totalWeight(analysis.cons);
  const netWeight = proWeight - conWeight;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs">
              <Scale className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
                Evidence Matrix
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="mono-data text-[9px] text-emerald-400">+{proWeight.toFixed(1)}</span>
              <span className="text-[9px] text-[#475569]">vs</span>
              <span className="mono-data text-[9px] text-red-400">-{conWeight.toFixed(1)}</span>
              <Badge className={`text-[9px] font-mono font-bold border ${
                netWeight > 0
                  ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                  : netWeight < 0
                  ? 'bg-red-500/15 text-red-400 border-red-500/30'
                  : 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'
              }`}>
                Net: {netWeight > 0 ? '+' : ''}{netWeight.toFixed(2)}
              </Badge>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Pros Column */}
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                <span className="text-[10px] font-mono font-bold text-emerald-400 uppercase tracking-wider">
                  Pros ({analysis.pros.length})
                </span>
              </div>
              <ScrollArea className="max-h-64">
                <motion.div variants={staggerContainer} initial="hidden" animate="visible">
                  {analysis.pros.map((pro, i) => (
                    <EvidenceItem key={i} factor={pro.factor} weight={pro.weight} explanation={pro.explanation} type="pro" />
                  ))}
                  {analysis.pros.length === 0 && (
                    <span className="text-[10px] text-[#475569] font-mono italic">No bullish factors identified</span>
                  )}
                </motion.div>
              </ScrollArea>
            </div>

            {/* Cons Column */}
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <XCircle className="h-3 w-3 text-red-400" />
                <span className="text-[10px] font-mono font-bold text-red-400 uppercase tracking-wider">
                  Cons ({analysis.cons.length})
                </span>
              </div>
              <ScrollArea className="max-h-64">
                <motion.div variants={staggerContainer} initial="hidden" animate="visible">
                  {analysis.cons.map((con, i) => (
                    <EvidenceItem key={i} factor={con.factor} weight={con.weight} explanation={con.explanation} type="con" />
                  ))}
                  {analysis.cons.length === 0 && (
                    <span className="text-[10px] text-[#475569] font-mono italic">No bearish factors identified</span>
                  )}
                </motion.div>
              </ScrollArea>
            </div>
          </div>

          {/* Neutral items */}
          {analysis.neutrals.length > 0 && (
            <>
              <Separator className="my-3 bg-[#1e293b]" />
              <div className="flex items-center gap-1.5 mb-2">
                <Circle className="h-3 w-3 text-yellow-400/60" />
                <span className="text-[10px] font-mono font-bold text-yellow-400/80 uppercase tracking-wider">
                  Neutral ({analysis.neutrals.length})
                </span>
              </div>
              <ScrollArea className="max-h-32">
                <motion.div variants={staggerContainer} initial="hidden" animate="visible">
                  {analysis.neutrals.map((n, i) => (
                    <EvidenceItem key={i} factor={n.factor} weight={n.weight} explanation={n.explanation} type="neutral" />
                  ))}
                </motion.div>
              </ScrollArea>
            </>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// STRATEGY RECOMMENDATION CARD
// ============================================================

function StrategyRecommendationCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const strat = analysis.strategyRecommendation || {
    strategy: 'WAIT_AND_MONITOR', direction: 'NEUTRAL', confidenceLevel: 0.5,
    positionSizeRecommendation: '5%', stopLossRecommendation: '-10%', takeProfitRecommendation: '15%',
    entryConditions: [], exitConditions: [],
  };
  const dirCfg = DIRECTION_CONFIG[strat.direction] || DIRECTION_CONFIG.HOLD;
  const DirIcon = dirCfg.icon;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Crosshair className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
              Strategy Recommendation
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-3">
          {/* Strategy Name & Direction */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Target className="h-4 w-4 text-[#d4af37]" />
              <span className="font-mono text-xs font-bold text-[#e2e8f0]">{strat.strategy}</span>
            </div>
            <DirectionBadge direction={strat.direction} />
          </div>

          {/* Confidence */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Strategy Confidence</span>
              <span className="mono-data text-[10px] text-[#e2e8f0]">{formatConfidence(strat.confidenceLevel)}</span>
            </div>
            <Progress
              value={strat.confidenceLevel * 100}
              className="h-1.5 bg-[#1a1f2e] [&>[data-slot=progress-indicator]]:bg-[#d4af37]"
            />
          </div>

          {/* Position / SL / TP Grid */}
          <div className="grid grid-cols-3 gap-2">
            <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
              <div className="flex items-center gap-1 mb-0.5">
                <Gauge className="h-3 w-3 text-[#64748b]" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase">Position</span>
              </div>
              <span className="mono-data text-[11px] font-bold text-[#e2e8f0]">{strat.positionSizeRecommendation}</span>
            </div>
            <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
              <div className="flex items-center gap-1 mb-0.5">
                <ShieldAlert className="h-3 w-3 text-red-400/60" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase">Stop Loss</span>
              </div>
              <span className="mono-data text-[11px] font-bold text-red-400">{strat.stopLossRecommendation}</span>
            </div>
            <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
              <div className="flex items-center gap-1 mb-0.5">
                <Target className="h-3 w-3 text-emerald-400/60" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase">Take Profit</span>
              </div>
              <span className="mono-data text-[11px] font-bold text-emerald-400">{strat.takeProfitRecommendation}</span>
            </div>
          </div>

          {/* Entry & Exit Conditions */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <ArrowUpRight className="h-3 w-3 text-emerald-400/60" />
                <span className="text-[9px] font-mono font-bold text-emerald-400/80 uppercase tracking-wider">
                  Entry Conditions
                </span>
              </div>
              <ul className="space-y-1">
                {strat.entryConditions.map((cond, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[10px] text-[#94a3b8]">
                    <span className="text-emerald-400/60 mt-0.5 shrink-0">&rsaquo;</span>
                    <span>{cond}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <ArrowDownRight className="h-3 w-3 text-red-400/60" />
                <span className="text-[9px] font-mono font-bold text-red-400/80 uppercase tracking-wider">
                  Exit Conditions
                </span>
              </div>
              <ul className="space-y-1">
                {strat.exitConditions.map((cond, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[10px] text-[#94a3b8]">
                    <span className="text-red-400/60 mt-0.5 shrink-0">&rsaquo;</span>
                    <span>{cond}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// RISK ASSESSMENT CARD
// ============================================================

function RiskAssessmentCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const risk = analysis.riskAssessment || {
    overallRisk: (analysis as any).riskLevel || 'MEDIUM',
    keyRisks: (analysis as any).bearishFactors || [],
    mitigatingFactors: (analysis as any).bullishFactors || [],
    blackSwanRisk: 'LOW',
  };
  const rCfg = RISK_CONFIG[risk.overallRisk] || RISK_CONFIG.MEDIUM;
  const RiskIcon = rCfg.icon;

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs">
              <ShieldAlert className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
                Risk Assessment
              </span>
            </div>
            <RiskBadge level={risk.overallRisk} />
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-3">
          {/* Key Risks */}
          <div>
            <div className="flex items-center gap-1.5 mb-1.5">
              <AlertTriangle className="h-3 w-3 text-red-400/60" />
              <span className="text-[9px] font-mono font-bold text-red-400/80 uppercase tracking-wider">
                Key Risks
              </span>
            </div>
            <ul className="space-y-1">
              {risk.keyRisks.map((r, i) => (
                <li key={i} className="flex items-start gap-1.5 text-[10px] text-[#94a3b8]">
                  <XCircle className="h-3 w-3 text-red-400/50 shrink-0 mt-0.5" />
                  <span>{r}</span>
                </li>
              ))}
              {risk.keyRisks.length === 0 && (
                <span className="text-[10px] text-[#475569] font-mono italic">No significant risks identified</span>
              )}
            </ul>
          </div>

          {/* Mitigating Factors */}
          <div>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Shield className="h-3 w-3 text-emerald-400/60" />
              <span className="text-[9px] font-mono font-bold text-emerald-400/80 uppercase tracking-wider">
                Mitigating Factors
              </span>
            </div>
            <ul className="space-y-1">
              {risk.mitigatingFactors.map((f, i) => (
                <li key={i} className="flex items-start gap-1.5 text-[10px] text-[#94a3b8]">
                  <CheckCircle2 className="h-3 w-3 text-emerald-400/50 shrink-0 mt-0.5" />
                  <span>{f}</span>
                </li>
              ))}
              {risk.mitigatingFactors.length === 0 && (
                <span className="text-[10px] text-[#475569] font-mono italic">No mitigating factors</span>
              )}
            </ul>
          </div>

          {/* Black Swan Risk */}
          <div className={`rounded-md p-2.5 border ${rCfg.bg} ${rCfg.border}`}>
            <div className="flex items-center gap-1.5 mb-1">
              <ShieldX className={`h-3.5 w-3.5 ${rCfg.color}`} />
              <span className={`text-[9px] font-mono font-bold uppercase tracking-wider ${rCfg.color}`}>
                Black Swan Risk
              </span>
            </div>
            <p className="text-[10px] text-[#94a3b8] leading-relaxed">{risk.blackSwanRisk}</p>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// REASONING CHAIN (Collapsible)
// ============================================================

function ReasoningChainCard({ analysis }: { analysis: AnalysisData }) {
  const { showReasoningChain, toggleReasoningChain } = useDeepAnalysisStore();

  return (
    <Collapsible open={showReasoningChain} onOpenChange={toggleReasoningChain}>
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CollapsibleTrigger asChild>
          <CardHeader className="pb-2 px-4 pt-3 cursor-pointer hover:bg-[#0a0e17]/50 transition-colors">
            <CardTitle className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs">
                <Route className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
                  Reasoning Chain
                </span>
                <Badge variant="outline" className="text-[8px] font-mono border-[#2d3748] text-[#64748b] h-4 px-1.5">
                  {analysis.reasoningChain.length} steps
                </Badge>
              </div>
              <motion.div
                animate={{ rotate: showReasoningChain ? 180 : 0 }}
                transition={{ duration: 0.2 }}
              >
                <ChevronDown className="h-4 w-4 text-[#64748b]" />
              </motion.div>
            </CardTitle>
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="px-4 pb-3">
            <div className="bg-[#0a0e17] rounded-md border border-[#1e293b] p-3 max-h-80 overflow-y-auto">
              <div className="space-y-1.5">
                {analysis.reasoningChain.map((step, i) => {
                  const isHeader = step.startsWith('[');
                  const tagMatch = step.match(/^\[(\w+)\]/);
                  const tag = tagMatch ? tagMatch[1] : null;
                  const text = tag ? step.replace(/^\[\w+\]\s*/, '') : step;

                  const tagColor: Record<string, string> = {
                    PHASE: 'text-violet-400',
                    PATTERN: 'text-cyan-400',
                    TRADERS: 'text-amber-400',
                    WEIGHING: 'text-emerald-400',
                    STRATEGY: 'text-[#d4af37]',
                    RISK: 'text-red-400',
                    VERDICT: 'text-[#d4af37]',
                    DEEP: 'text-violet-400',
                    SCENARIO: 'text-cyan-400',
                    'DATA GAPS': 'text-orange-400',
                    'TRANSITION WATCH': 'text-teal-400',
                  };

                  return (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: -6 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.03, duration: 0.2 }}
                      className={`flex gap-2 ${isHeader ? 'mt-2' : ''}`}
                    >
                      <span className="mono-data text-[9px] text-[#475569] w-5 shrink-0 text-right">
                        {i + 1}.
                      </span>
                      {tag && (
                        <Badge className={`text-[8px] font-mono font-bold border border-transparent ${
                          tagColor[tag] || 'text-[#64748b]'
                        } bg-transparent h-3.5 px-1 shrink-0`}>
                          {tag}
                        </Badge>
                      )}
                      <span className={`text-[10px] font-mono ${
                        isHeader ? 'text-[#e2e8f0] font-medium' : 'text-[#94a3b8]'
                      }`}>
                        {text.trim()}
                      </span>
                    </motion.div>
                  );
                })}
              </div>
            </div>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}

// ============================================================
// VERDICT SUMMARY
// ============================================================

function VerdictSummaryCard({ analysis }: { analysis: AnalysisData }) {
  const verdictAction = analysis.verdict?.action || (analysis as any).recommendation || 'HOLD';
  const vCfg = VERDICT_CONFIG[verdictAction] || VERDICT_CONFIG.HOLD;
  const VerdictIcon = vCfg.icon;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
    >
      <Card className={`bg-[#0d1117] border ${vCfg.border}`}>
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className={`flex items-center justify-center h-10 w-10 rounded-lg ${vCfg.bg}`}>
              <VerdictIcon className={`h-5 w-5 ${vCfg.color}`} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className={`font-mono text-lg font-bold ${vCfg.color}`}>
                  {verdictAction.replace('_', ' ')}
                </span>
                <span className="mono-data text-[11px] text-[#64748b]">
                  ({formatConfidence(analysis.verdict?.confidence || (analysis as any).recommendationConfidence || 0.5)} confidence)
                </span>
              </div>
              <p className="text-[11px] text-[#94a3b8] leading-relaxed">{analysis.verdict?.summary || analysis.verdict?.reasoning || ''}</p>
              {analysis.verdict?.criticalNote && (
                <div className="mt-2 flex items-start gap-1.5 bg-red-500/10 border border-red-500/20 rounded-md p-2">
                  <AlertTriangle className="h-3 w-3 text-red-400 shrink-0 mt-0.5" />
                  <span className="text-[10px] text-red-300 leading-relaxed">{analysis.verdict.criticalNote}</span>
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// LOADING STATE
// ============================================================

function AnalysisLoadingState() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center py-12 gap-4"
    >
      <div className="relative">
        <Brain className="h-12 w-12 text-[#d4af37]/30" />
        <Loader2 className="h-12 w-12 text-[#d4af37] animate-spin absolute inset-0" />
      </div>
      <div className="text-center">
        <p className="font-mono text-sm text-[#e2e8f0] font-bold">Running Deep Analysis...</p>
        <p className="font-mono text-[10px] text-[#64748b] mt-1">
          Evaluating phase, patterns, trader behavior & risk
        </p>
      </div>
      <div className="flex gap-1 mt-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <motion.div
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-[#d4af37]"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
          />
        ))}
      </div>
    </motion.div>
  );
}

// ============================================================
// ERROR STATE
// ============================================================

function AnalysisErrorState({ error }: { error: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 flex items-start gap-3"
    >
      <AlertTriangle className="h-5 w-5 text-red-400 shrink-0 mt-0.5" />
      <div>
        <p className="font-mono text-xs font-bold text-red-400">Analysis Failed</p>
        <p className="font-mono text-[10px] text-red-300/80 mt-0.5">{error}</p>
      </div>
    </motion.div>
  );
}

// ============================================================
// EMPTY STATE
// ============================================================

function AnalysisEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="flex items-center justify-center h-16 w-16 rounded-xl bg-[#1a1f2e] border border-[#2d3748]">
        <Brain className="h-8 w-8 text-[#d4af37]/40" />
      </div>
      <div className="text-center">
        <p className="font-mono text-sm text-[#64748b]">No analysis yet</p>
        <p className="font-mono text-[10px] text-[#475569] mt-1">
          Enter a token address above to run a deep analysis
        </p>
      </div>
    </div>
  );
}

// ============================================================
// SCENARIOS CARD (STANDARD / DEEP)
// ============================================================

function ScenariosCard({ analysis, index }: { analysis: AnalysisData; index: number }) {
  const scenarios = (analysis as any).scenarios;
  if (!scenarios) return null;

  const bullScenario = scenarios.bull || { probability: 0.35, targetPct: 15, description: 'Bullish scenario' };
  const baseScenario = scenarios.base || { probability: 0.35, targetPct: 0, description: 'Base scenario' };
  const bearScenario = scenarios.bear || { probability: 0.30, targetPct: -15, description: 'Bearish scenario' };

  return (
    <motion.div custom={index} variants={cardVariants} initial="hidden" animate="visible" exit="exit">
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Target className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[#94a3b8]">
              Scenario Analysis
            </span>
            <Badge variant="outline" className="text-[8px] font-mono border-[#2d3748] text-[#64748b] h-4 px-1.5 ml-1">
              {(bullScenario.probability * 100).toFixed(0)}/{(baseScenario.probability * 100).toFixed(0)}/{(bearScenario.probability * 100).toFixed(0)}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3">
          <div className="grid grid-cols-3 gap-3">
            {/* Bull Scenario */}
            <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-md p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <TrendingUp className="h-4 w-4 text-emerald-400" />
                <span className="font-mono text-[10px] font-bold text-emerald-400 uppercase">Bull</span>
                <span className="mono-data text-[10px] text-emerald-300 ml-auto">{(bullScenario.probability * 100).toFixed(0)}%</span>
              </div>
              <div className="mono-data text-lg font-bold text-emerald-400 mb-1.5">
                +{bullScenario.targetPct}%
              </div>
              <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden mb-2">
                <motion.div
                  className="h-full rounded-full bg-emerald-500"
                  initial={{ width: 0 }}
                  animate={{ width: `${bullScenario.probability * 100}%` }}
                  transition={{ duration: 0.8, ease: 'easeOut' }}
                />
              </div>
              <p className="text-[9px] text-[#94a3b8] leading-relaxed">{bullScenario.description}</p>
            </div>

            {/* Base Scenario */}
            <div className="bg-yellow-500/5 border border-yellow-500/20 rounded-md p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Minus className="h-4 w-4 text-yellow-400" />
                <span className="font-mono text-[10px] font-bold text-yellow-400 uppercase">Base</span>
                <span className="mono-data text-[10px] text-yellow-300 ml-auto">{(baseScenario.probability * 100).toFixed(0)}%</span>
              </div>
              <div className="mono-data text-lg font-bold text-yellow-400 mb-1.5">
                {baseScenario.targetPct > 0 ? '+' : ''}{baseScenario.targetPct}%
              </div>
              <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden mb-2">
                <motion.div
                  className="h-full rounded-full bg-yellow-500"
                  initial={{ width: 0 }}
                  animate={{ width: `${baseScenario.probability * 100}%` }}
                  transition={{ duration: 0.8, ease: 'easeOut', delay: 0.1 }}
                />
              </div>
              <p className="text-[9px] text-[#94a3b8] leading-relaxed">{baseScenario.description}</p>
            </div>

            {/* Bear Scenario */}
            <div className="bg-red-500/5 border border-red-500/20 rounded-md p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <TrendingDown className="h-4 w-4 text-red-400" />
                <span className="font-mono text-[10px] font-bold text-red-400 uppercase">Bear</span>
                <span className="mono-data text-[10px] text-red-300 ml-auto">{(bearScenario.probability * 100).toFixed(0)}%</span>
              </div>
              <div className="mono-data text-lg font-bold text-red-400 mb-1.5">
                {bearScenario.targetPct}%
              </div>
              <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden mb-2">
                <motion.div
                  className="h-full rounded-full bg-red-500"
                  initial={{ width: 0 }}
                  animate={{ width: `${bearScenario.probability * 100}%` }}
                  transition={{ duration: 0.8, ease: 'easeOut', delay: 0.2 }}
                />
              </div>
              <p className="text-[9px] text-[#94a3b8] leading-relaxed">{bearScenario.description}</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============================================================
// QUICK STRATEGY CARD (Quick mode compact view)
// ============================================================

function QuickStrategyCard({ analysis }: { analysis: AnalysisData }) {
  const strat = analysis.strategyRecommendation || {
    strategy: 'WAIT_AND_MONITOR', direction: 'NEUTRAL', confidenceLevel: 0.5,
    positionSizeRecommendation: '5%', stopLossRecommendation: '-10%', takeProfitRecommendation: '15%',
  };
  const risk = analysis.riskAssessment && typeof analysis.riskAssessment === 'object'
    ? analysis.riskAssessment as { overallRisk: string; keyRisks: string[]; mitigatingFactors: string[]; blackSwanRisk: string }
    : { overallRisk: (analysis as any).riskLevel || 'MEDIUM', keyRisks: [], mitigatingFactors: [], blackSwanRisk: 'LOW' };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      {/* Compact Strategy */}
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardContent className="p-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Crosshair className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-xs font-bold text-[#e2e8f0]">{strat.strategy}</span>
            <DirectionBadge direction={strat.direction} />
          </div>
          <div className="flex items-center gap-3">
            <div className="text-center">
              <span className="text-[8px] font-mono text-[#64748b] uppercase">Pos</span>
              <div className="mono-data text-[10px] font-bold text-[#e2e8f0]">{strat.positionSizeRecommendation}</div>
            </div>
            <div className="text-center">
              <span className="text-[8px] font-mono text-[#64748b] uppercase">SL</span>
              <div className="mono-data text-[10px] font-bold text-red-400">{strat.stopLossRecommendation}</div>
            </div>
            <div className="text-center">
              <span className="text-[8px] font-mono text-[#64748b] uppercase">TP</span>
              <div className="mono-data text-[10px] font-bold text-emerald-400">{strat.takeProfitRecommendation}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Compact Risk */}
      <Card className="bg-[#0d1117] border-[#1e293b]">
        <CardContent className="p-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="font-mono text-xs text-[#94a3b8]">Risk</span>
          </div>
          <div className="flex items-center gap-2">
            <RiskBadge level={risk.overallRisk} />
            {(analysis as any).riskScore != null && (
              <span className="mono-data text-[10px] text-[#94a3b8]">{(analysis as any).riskScore}/100</span>
            )}
            {(analysis as any).suggestedTimeHorizon && (
              <Badge variant="outline" className="text-[8px] font-mono border-[#2d3748] text-[#64748b] h-4 px-1.5">
                {(analysis as any).suggestedTimeHorizon}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function DeepAnalysisPanel() {
  const { analysis: rawAnalysis, status, error } = useDeepAnalysisStore();
  const analysis = rawAnalysis ? normalizeAnalysis(rawAnalysis) : null;

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1e293b] bg-[#0a0e17] shrink-0">
        <Brain className="h-4 w-4 text-[#d4af37]" />
        <span className="font-mono text-xs font-bold text-[#e2e8f0] uppercase tracking-wider">
          Deep Analysis Engine
        </span>
        <Badge variant="outline" className="text-[8px] font-mono border-[#2d3748] text-[#64748b] h-4 px-1.5 ml-1">
          v1.0
        </Badge>
        <div className="ml-auto flex items-center gap-2">
          {status === 'loading' && (
            <div className="flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 text-[#d4af37] animate-spin" />
              <span className="font-mono text-[9px] text-[#d4af37]">Processing...</span>
            </div>
          )}
          {status === 'success' && analysis && (
            <div className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3 w-3 text-emerald-400" />
              <span className="font-mono text-[9px] text-emerald-400">
                {new Date(analysis.timestamp || analysis.analyzedAt || Date.now()).toLocaleTimeString()}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Input Form */}
        <AnalysisInputForm />

        {/* Analysis Content */}
        <AnimatePresence mode="wait">
          {status === 'loading' && <AnalysisLoadingState key="loading" />}

          {status === 'error' && error && <AnalysisErrorState error={error} key="error" />}

          {status === 'idle' && !analysis && <AnalysisEmptyState key="empty" />}

          {analysis && status === 'success' && (
            <motion.div
              key="results"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="space-y-3"
            >
              {/* Header Bar with Verdict */}
              <AnalysisHeader analysis={analysis} />

              {/* Verdict Summary */}
              <VerdictSummaryCard analysis={analysis} />

              {/* Assessment Cards Row - STANDARD/DEEP only; hidden for QUICK */}
              {analysis.depth !== 'QUICK' && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <PhaseAssessmentCard analysis={analysis} index={0} />
                  <PatternAssessmentCard analysis={analysis} index={1} />
                  <TraderAssessmentCard analysis={analysis} index={2} />
                </div>
              )}

              {/* Evidence Matrix - STANDARD/DEEP only; hidden for QUICK */}
              {analysis.depth !== 'QUICK' && (
                <EvidenceMatrixCard analysis={analysis} index={3} />
              )}

              {/* Scenarios Card - STANDARD and DEEP */}
              {analysis.depth !== 'QUICK' && (analysis as any).scenarios && (
                <ScenariosCard analysis={analysis} index={6} />
              )}

              {/* Strategy & Risk Row - STANDARD/DEEP show full; QUICK shows compact */}
              {analysis.depth !== 'QUICK' && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  <StrategyRecommendationCard analysis={analysis} index={4} />
                  <RiskAssessmentCard analysis={analysis} index={5} />
                </div>
              )}

              {/* Quick mode: show compact strategy + risk */}
              {analysis.depth === 'QUICK' && (
                <QuickStrategyCard analysis={analysis} />
              )}

              {/* Reasoning Chain - DEEP only */}
              {analysis.depth === 'DEEP' && (
                <ReasoningChainCard analysis={analysis} />
              )}

              {/* Data Source info - DEEP only */}
              {analysis.depth === 'DEEP' && (analysis as any).source && (
                <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] border border-[#1e293b] rounded-lg">
                  <Activity className="h-3 w-3 text-[#64748b]" />
                  <span className="text-[9px] font-mono text-[#64748b]">
                    Analysis Source: <span className="text-[#94a3b8] font-bold">{(analysis as any).source || 'RULE_BASED'}</span>
                    {(analysis as any).riskScore != null && (
                      <> &middot; Risk Score: <span className="text-[#94a3b8] font-bold">{(analysis as any).riskScore}/100</span></>
                    )}
                    {(analysis as any).urgencyLevel && (
                      <> &middot; Urgency: <span className="text-[#94a3b8] font-bold">{(analysis as any).urgencyLevel}</span></>
                    )}
                    {(analysis as any).suggestedTimeHorizon && (
                      <> &middot; Horizon: <span className="text-[#94a3b8] font-bold">{(analysis as any).suggestedTimeHorizon}</span></>
                    )}
                  </span>
                </div>
              )}

              {/* Analysis Depth Info Card */}
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.3, duration: 0.3 }}
              >
                <div className={`flex items-start gap-2.5 px-4 py-3 rounded-lg border ${DEPTH_CONFIG[analysis.depth]?.badgeBg || 'bg-cyan-500/10'} ${DEPTH_CONFIG[analysis.depth]?.badgeBorder || 'border-cyan-500/30'}`}>
                  <Layers className={`h-4 w-4 shrink-0 mt-0.5 ${DEPTH_CONFIG[analysis.depth]?.color || 'text-cyan-400'}`} />
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`font-mono text-[10px] font-bold uppercase tracking-wider ${DEPTH_CONFIG[analysis.depth]?.color || 'text-cyan-400'}`}>
                        Analysis Depth: {DEPTH_CONFIG[analysis.depth]?.label || 'Standard'}
                      </span>
                    </div>
                    <p className="text-[10px] text-[#94a3b8] leading-relaxed">
                      {DEPTH_CONFIG[analysis.depth]?.infoText || 'Standard analysis evaluated 6 weighted factors with 3 scenarios.'}
                    </p>
                  </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export default DeepAnalysisPanel;
