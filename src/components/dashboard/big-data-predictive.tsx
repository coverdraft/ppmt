'use client';

import { useState, useCallback, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from '@/components/ui/tooltip';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Bot,
  Brain,
  CircleDot,
  Clock,
  Droplets,
  Gauge,
  Layers,
  Minus,
  RefreshCw,
  Signal,
  TrendingDown,
  TrendingUp,
  Zap,
  Database,
  Eye,
  Target,
  Radio,
  Waves,
  LineChart,
  ArrowRightLeft,
  Info,
  Loader2,
  Play,
  ChevronDown,
  ChevronRight,
  Fingerprint,
  Timer,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

type MarketRegime = 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION';
type PredictiveSignalType =
  | 'REGIME_CHANGE'
  | 'BOT_SWARM'
  | 'WHALE_MOVEMENT'
  | 'LIQUIDITY_DRAIN'
  | 'CORRELATION_BREAK'
  | 'ANOMALY'
  | 'CYCLE_POSITION'
  | 'SECTOR_ROTATION'
  | 'MEAN_REVERSION_ZONE'
  | 'SMART_MONEY_POSITIONING'
  | 'VOLATILITY_REGIME';
type BotSwarmLevel = 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
type VolatilityRegime = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME';
type LiquidityTrend = 'ACCUMULATING' | 'STABLE' | 'DRAINING' | 'CRITICAL_DRAIN';
type WhaleDirection = 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL' | 'ROTATING';
type SmartMoneyFlowDirection = 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';

interface PredictiveSignal {
  id: string;
  signalType: PredictiveSignalType;
  chain: string;
  tokenAddress: string | null;
  sector: string | null;
  prediction: string;
  confidence: number;
  timeframe: string;
  validUntil: string;
  evidence: string;
  historicalHitRate: number;
  dataPointsUsed: number;
  createdAt: string;
}

interface MarketContextData {
  regime: MarketRegime;
  volatilityRegime: VolatilityRegime;
  botSwarmLevel: BotSwarmLevel;
  whaleDirection: WhaleDirection;
  smartMoneyFlow: SmartMoneyFlowDirection;
  liquidityTrend: LiquidityTrend;
  correlationStability: number;
  tokenCount?: number;
  signalCount?: number;
  chains?: string[];
  computedAt?: string;
  source?: 'live' | 'computed' | 'fallback';
  liveTokenCount?: number;
  signalBreakdown?: Record<string, number>;
}

interface GenerateSignalResponse {
  generated: number;
  signals: Array<{
    id: string;
    signalType: string;
    chain: string;
    confidence: number;
  }>;
  chains: string[];
  signalTypes: string[];
}

// ============================================================
// CONSTANTS
// ============================================================

const SIGNAL_TYPES_CONFIG: Array<{
  type: PredictiveSignalType;
  label: string;
  icon: React.ElementType;
  description: string;
  color: { bg: string; text: string; border: string; accent: string };
}> = [
  {
    type: 'REGIME_CHANGE',
    label: 'Regime Change',
    icon: Layers,
    description: 'Market regime transition detection via SMA crossover & momentum analysis',
    color: { bg: 'bg-violet-500/15', text: 'text-violet-400', border: 'border-violet-500/30', accent: '#8b5cf6' },
  },
  {
    type: 'BOT_SWARM',
    label: 'Bot Swarm',
    icon: Bot,
    description: 'Coordinated bot activity detection using HHI concentration & trade velocity',
    color: { bg: 'bg-rose-500/15', text: 'text-rose-400', border: 'border-rose-500/30', accent: '#f43f5e' },
  },
  {
    type: 'WHALE_MOVEMENT',
    label: 'Whale Movement',
    icon: Waves,
    description: 'Whale accumulation/distribution forecasts via net flow & synchronicity analysis',
    color: { bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30', accent: '#06b6d4' },
  },
  {
    type: 'LIQUIDITY_DRAIN',
    label: 'Liquidity Drain',
    icon: Droplets,
    description: 'Liquidity withdrawal detection using linear regression slope & acceleration',
    color: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30', accent: '#f97316' },
  },
  {
    type: 'CORRELATION_BREAK',
    label: 'Correlation Break',
    icon: ArrowRightLeft,
    description: 'Correlation stability breaks detected via Pearson coefficient deviation',
    color: { bg: 'bg-indigo-500/15', text: 'text-indigo-400', border: 'border-indigo-500/30', accent: '#6366f1' },
  },
  {
    type: 'ANOMALY',
    label: 'Anomaly',
    icon: AlertTriangle,
    description: 'Z-score anomaly detection against historical statistical baselines',
    color: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30', accent: '#eab308' },
  },
  {
    type: 'CYCLE_POSITION',
    label: 'Cycle Position',
    icon: CircleDot,
    description: 'Market cycle positioning using trend strength & R² regression analysis',
    color: { bg: 'bg-teal-500/15', text: 'text-teal-400', border: 'border-teal-500/30', accent: '#14b8a6' },
  },
  {
    type: 'SECTOR_ROTATION',
    label: 'Sector Rotation',
    icon: ArrowRightLeft,
    description: 'Sector rotation signals via relative momentum & capital flow analysis',
    color: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30', accent: '#10b981' },
  },
  {
    type: 'MEAN_REVERSION_ZONE',
    label: 'Mean Reversion',
    icon: LineChart,
    description: 'Bollinger Band mean reversion zones with probability scoring',
    color: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30', accent: '#0ea5e9' },
  },
  {
    type: 'SMART_MONEY_POSITIONING',
    label: 'Smart Money',
    icon: Brain,
    description: 'Smart money flow analysis with sector breakdown & wallet clustering',
    color: { bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/30', accent: '#d4af37' },
  },
  {
    type: 'VOLATILITY_REGIME',
    label: 'Volatility Regime',
    icon: Gauge,
    description: 'Volatility regime changes via ATR normalization & Wilder smoothing',
    color: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30', accent: '#ef4444' },
  },
];

const CHAIN_OPTIONS = ['ALL', 'SOL', 'ETH', 'BASE', 'ARB'];

const REGIME_COLORS: Record<MarketRegime, { bg: string; text: string; border: string }> = {
  BULL: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  BEAR: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  SIDEWAYS: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  TRANSITION: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' },
};

const VOLATILITY_COLORS: Record<VolatilityRegime, { bg: string; text: string; border: string }> = {
  LOW: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  NORMAL: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30' },
  HIGH: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  EXTREME: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
};

const SWARM_COLORS: Record<BotSwarmLevel, { bg: string; text: string; border: string }> = {
  NONE: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  LOW: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30' },
  MEDIUM: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  HIGH: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' },
  CRITICAL: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
};

const WHALE_COLORS: Record<WhaleDirection, { bg: string; text: string; border: string; icon: React.ElementType }> = {
  ACCUMULATING: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30', icon: TrendingUp },
  DISTRIBUTING: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30', icon: TrendingDown },
  NEUTRAL: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30', icon: Minus },
  ROTATING: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30', icon: ArrowRightLeft },
};

const SM_FLOW_COLORS: Record<SmartMoneyFlowDirection, { bg: string; text: string; border: string }> = {
  INFLOW: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  OUTFLOW: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  NEUTRAL: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30' },
};

const LIQUIDITY_COLORS: Record<LiquidityTrend, { bg: string; text: string; border: string }> = {
  ACCUMULATING: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  STABLE: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30' },
  DRAINING: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  CRITICAL_DRAIN: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
};

// ============================================================
// SIGNAL-DERIVED FALLBACK CONTEXT
// When the API is unavailable, we derive context from existing
// signal data rather than using hardcoded values.
// ============================================================

function deriveContextFromSignals(signalsByType: Record<PredictiveSignalType, PredictiveSignal[]>): MarketContextData {
  let regime: MarketRegime = 'SIDEWAYS';
  let volatilityRegime: VolatilityRegime = 'NORMAL';
  let botSwarmLevel: BotSwarmLevel = 'LOW';
  let whaleDirection: WhaleDirection = 'NEUTRAL';
  let smartMoneyFlow: SmartMoneyFlowDirection = 'NEUTRAL';
  let liquidityTrend: LiquidityTrend = 'STABLE';
  let correlationStability = 0.5;

  // Derive from REGIME_CHANGE signals
  const regimeSignals = signalsByType['REGIME_CHANGE'] || [];
  if (regimeSignals.length > 0) {
    const p = parsePrediction(regimeSignals[0].prediction);
    if (p.toRegime && ['BULL', 'BEAR', 'SIDEWAYS', 'TRANSITION'].includes(p.toRegime as string)) {
      regime = p.toRegime as MarketRegime;
    }
  }

  // Derive from VOLATILITY_REGIME signals
  const volSignals = signalsByType['VOLATILITY_REGIME'] || [];
  if (volSignals.length > 0) {
    const p = parsePrediction(volSignals[0].prediction);
    if (p.current && ['LOW', 'NORMAL', 'HIGH', 'EXTREME'].includes(p.current as string)) {
      volatilityRegime = p.current as VolatilityRegime;
    }
  }

  // Derive from BOT_SWARM signals
  const botSignals = signalsByType['BOT_SWARM'] || [];
  if (botSignals.length > 0) {
    const p = parsePrediction(botSignals[0].prediction);
    if (p.level && ['NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(p.level as string)) {
      botSwarmLevel = p.level as BotSwarmLevel;
    }
  }

  // Derive from WHALE_MOVEMENT signals
  const whaleSignals = signalsByType['WHALE_MOVEMENT'] || [];
  if (whaleSignals.length > 0) {
    const p = parsePrediction(whaleSignals[0].prediction);
    if (p.direction && ['ACCUMULATING', 'DISTRIBUTING', 'NEUTRAL', 'ROTATING'].includes(p.direction as string)) {
      whaleDirection = p.direction as WhaleDirection;
    }
  }

  // Derive from SMART_MONEY_POSITIONING signals
  const smSignals = signalsByType['SMART_MONEY_POSITIONING'] || [];
  if (smSignals.length > 0) {
    const p = parsePrediction(smSignals[0].prediction);
    if (p.netDirection && ['INFLOW', 'OUTFLOW', 'NEUTRAL'].includes(p.netDirection as string)) {
      smartMoneyFlow = p.netDirection as SmartMoneyFlowDirection;
    }
  }

  // Derive from LIQUIDITY_DRAIN signals
  const liqSignals = signalsByType['LIQUIDITY_DRAIN'] || [];
  if (liqSignals.length > 0) {
    const p = parsePrediction(liqSignals[0].prediction);
    if (p.trend && ['ACCUMULATING', 'STABLE', 'DRAINING', 'CRITICAL_DRAIN'].includes(p.trend as string)) {
      liquidityTrend = p.trend as LiquidityTrend;
    }
  }

  // Derive from CORRELATION_BREAK signals
  const corrSignals = signalsByType['CORRELATION_BREAK'] || [];
  if (corrSignals.length > 0) {
    const p = parsePrediction(corrSignals[0].prediction);
    if (typeof p.stability === 'number') {
      correlationStability = p.stability;
    } else if (typeof p.correlation === 'number') {
      correlationStability = Math.abs(p.correlation);
    }
  }

  // Count total signals for metadata
  const totalSignals = Object.values(signalsByType).flat().length;

  return {
    regime,
    volatilityRegime,
    botSwarmLevel,
    whaleDirection,
    smartMoneyFlow,
    liquidityTrend,
    correlationStability,
    signalCount: totalSignals,
    source: 'computed',
    computedAt: new Date().toISOString(),
  };
}

// ============================================================
// HELPERS
// ============================================================

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center justify-center ml-1 text-[#64748b] hover:text-[#94a3b8] transition-colors cursor-pointer">
          <Info className="h-3 w-3" />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="bg-[#1a1f2e] border border-[#2d3748] text-[#94a3b8] text-[11px] font-mono max-w-xs z-[100]">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

function ConfidenceBar({ confidence, color }: { confidence: number; color?: string }) {
  const pct = Math.round(confidence * 100);
  const barColor =
    color ||
    (pct >= 80 ? 'bg-emerald-500' : pct >= 60 ? 'bg-yellow-500' : pct >= 40 ? 'bg-orange-500' : 'bg-red-500');
  const textColor =
    pct >= 80 ? 'text-emerald-400' : pct >= 60 ? 'text-yellow-400' : pct >= 40 ? 'text-orange-400' : 'text-red-400';

  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
        <motion.div
          className={`h-full ${barColor} rounded-full`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
      </div>
      <span className={`mono-data text-[10px] ${textColor} w-8 text-right font-bold`}>{pct}%</span>
    </div>
  );
}

function HitRateBadge({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100);
  const color =
    pct >= 70 ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    : pct >= 50 ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'
    : 'bg-red-500/15 text-red-400 border-red-500/30';

  return (
    <Badge className={`text-[9px] h-4 px-1.5 font-mono border ${color}`}>
      <Target className="h-2.5 w-2.5 mr-0.5" />
      {pct}% hit
    </Badge>
  );
}

function parsePrediction(predictionStr: string | null | undefined): Record<string, unknown> {
  if (!predictionStr) return {};
  try {
    const parsed = JSON.parse(predictionStr);
    return typeof parsed === 'object' && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function parseEvidence(evidenceStr: string | null | undefined): string[] {
  if (!evidenceStr) return [];
  try {
    const parsed = JSON.parse(evidenceStr);
    if (Array.isArray(parsed)) return parsed;
    // If it's an object, convert to readable strings
    if (typeof parsed === 'object' && parsed !== null) {
      return Object.entries(parsed).map(([k, v]) => `${k}: ${v}`);
    }
    // If it's a single string, wrap in array
    if (typeof parsed === 'string') return [parsed];
    return [];
  } catch {
    // If JSON parse fails, try splitting by newlines or commas
    if (typeof evidenceStr === 'string' && evidenceStr.length > 0) {
      return evidenceStr.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
    }
    return [];
  }
}

function formatValidity(validUntil: string): string {
  const now = Date.now();
  const then = new Date(validUntil).getTime();
  const diff = then - now;
  if (diff <= 0) return 'Expired';
  const mins = Math.floor(diff / 60000);
  const hrs = Math.floor(mins / 60);
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function PredictionDataDisplay({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);

  return (
    <div className="space-y-1">
      {entries.map(([key, value]) => {
        const displayKey = key.replace(/([A-Z])/g, ' $1').replace(/^./, (s) => s.toUpperCase());
        let displayValue: string;
        let valueColor = 'text-[#e2e8f0]';

        if (typeof value === 'number') {
          if (Math.abs(value) >= 1000000) {
            displayValue = `$${(value / 1000000).toFixed(2)}M`;
          } else if (Math.abs(value) >= 1000) {
            displayValue = `$${(value / 1000).toFixed(1)}K`;
          } else if (key.toLowerCase().includes('rate') || key.toLowerCase().includes('probability') || key.toLowerCase().includes('score')) {
            displayValue = `${(value * 100).toFixed(1)}%`;
          } else {
            displayValue = value.toFixed(2);
          }

          if (value > 0 && (key.toLowerCase().includes('flow') || key.toLowerCase().includes('net'))) {
            valueColor = 'text-emerald-400';
          } else if (value < 0 && (key.toLowerCase().includes('flow') || key.toLowerCase().includes('net'))) {
            valueColor = 'text-red-400';
          }
        } else if (typeof value === 'string') {
          displayValue = value;
          const upper = value.toUpperCase();
          if (['BULL', 'ACCUMULATING', 'INFLOW', 'LOW', 'ABOVE'].includes(upper)) {
            valueColor = 'text-emerald-400';
          } else if (['BEAR', 'DISTRIBUTING', 'OUTFLOW', 'CRITICAL', 'EXTREME', 'BELOW'].includes(upper)) {
            valueColor = 'text-red-400';
          } else if (['SIDEWAYS', 'NEUTRAL', 'NORMAL', 'MEDIUM', 'ROTATING'].includes(upper)) {
            valueColor = 'text-yellow-400';
          } else if (['HIGH', 'DRAINING', 'DRITICAL_DRAIN'].includes(upper)) {
            valueColor = 'text-orange-400';
          }
        } else if (Array.isArray(value)) {
          displayValue = value.join(', ');
        } else {
          displayValue = String(value);
        }

        return (
          <div key={key} className="flex items-center justify-between py-0.5">
            <span className="text-[10px] font-mono text-[#64748b]">{displayKey}</span>
            <span className={`mono-data text-[11px] font-bold ${valueColor}`}>{displayValue}</span>
          </div>
        );
      })}
    </div>
  );
}

function CorrelationStabilityBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? 'bg-emerald-500' : pct >= 60 ? 'bg-sky-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-red-500';
  const textColor = pct >= 80 ? 'text-emerald-400' : pct >= 60 ? 'text-sky-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400';

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`mono-data text-[10px] ${textColor} font-bold`}>{value.toFixed(2)}</span>
    </div>
  );
}

// ============================================================
// MARKET CONTEXT PANEL
// ============================================================

function MarketContextPanel({ context, dataSource }: { context: MarketContextData; dataSource?: 'live' | 'computed' | 'fallback' }) {
  const regimeColor = REGIME_COLORS[context.regime];
  const volColor = VOLATILITY_COLORS[context.volatilityRegime];
  const swarmColor = SWARM_COLORS[context.botSwarmLevel];
  const whaleColor = WHALE_COLORS[context.whaleDirection];
  const smColor = SM_FLOW_COLORS[context.smartMoneyFlow];
  const liqColor = LIQUIDITY_COLORS[context.liquidityTrend];
  const WhaleIcon = whaleColor.icon;

  const effectiveSource = dataSource || context.source || 'fallback';

  // Format computed time
  const computedTime = context.computedAt
    ? new Date(context.computedAt).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '--:--:--';

  return (
    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Panel Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1e293b] bg-[#0a0e17]">
        <Database className="h-4 w-4 text-[#d4af37]" />
        <span className="font-mono text-xs font-bold text-[#e2e8f0] uppercase tracking-wider">Big Data Engine</span>
        <span className="text-[10px] font-mono text-[#64748b]">— Aggregate Market Context</span>
        <div className="ml-auto flex items-center gap-3">
          {/* Metadata badges */}
          {context.chains && context.chains.length > 0 && (
            <span className="text-[9px] font-mono text-[#64748b]">
              {context.chains.length} chain{context.chains.length > 1 ? 's' : ''}
            </span>
          )}
          {context.tokenCount !== undefined && context.tokenCount > 0 && (
            <span className="text-[9px] font-mono text-[#64748b]">
              {context.tokenCount} token{context.tokenCount !== 1 ? 's' : ''}
              {context.liveTokenCount !== undefined && context.liveTokenCount > 0 && (
                <span className="text-emerald-400"> ({context.liveTokenCount} live)</span>
              )}
            </span>
          )}
          {context.signalCount !== undefined && context.signalCount > 0 && (
            <span className="text-[9px] font-mono text-[#64748b]">
              {context.signalCount} signal{context.signalCount !== 1 ? 's' : ''}
            </span>
          )}
          <span className="text-[9px] font-mono text-[#475569]">
            {computedTime}
          </span>
          {/* Source indicator */}
          <div className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${effectiveSource === 'live' ? 'bg-emerald-500' : effectiveSource === 'computed' ? 'bg-yellow-500' : 'bg-gray-500'} animate-pulse`} />
            <span className={`text-[9px] font-mono ${effectiveSource === 'live' ? 'text-emerald-400' : effectiveSource === 'computed' ? 'text-yellow-400' : 'text-gray-400'}`}>
              {effectiveSource === 'live' ? 'LIVE' : effectiveSource === 'computed' ? 'COMPUTED' : 'FALLBACK'}
            </span>
          </div>
        </div>
      </div>

      {/* Context Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-px bg-[#1e293b]">
        {/* Regime */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Layers className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Regime</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${regimeColor.bg} ${regimeColor.text} ${regimeColor.border}`}>
            {context.regime}
          </Badge>
        </div>

        {/* Volatility */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Gauge className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Volatility</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${volColor.bg} ${volColor.text} ${volColor.border}`}>
            {context.volatilityRegime}
          </Badge>
        </div>

        {/* Bot Swarm */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Bot className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Bot Swarm</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${swarmColor.bg} ${swarmColor.text} ${swarmColor.border}`}>
            {context.botSwarmLevel}
          </Badge>
        </div>

        {/* Whale Direction */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Waves className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Whales</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${whaleColor.bg} ${whaleColor.text} ${whaleColor.border}`}>
            <WhaleIcon className="h-2.5 w-2.5 mr-0.5" />
            {context.whaleDirection}
          </Badge>
        </div>

        {/* Smart Money Flow */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Brain className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">SM Flow</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${smColor.bg} ${smColor.text} ${smColor.border}`}>
            {context.smartMoneyFlow === 'INFLOW' ? <ArrowDown className="h-2.5 w-2.5 mr-0.5" /> : context.smartMoneyFlow === 'OUTFLOW' ? <ArrowUp className="h-2.5 w-2.5 mr-0.5" /> : <Minus className="h-2.5 w-2.5 mr-0.5" />}
            {context.smartMoneyFlow}
          </Badge>
        </div>

        {/* Liquidity Trend */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <Droplets className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Liquidity</span>
          </div>
          <Badge className={`text-[10px] h-5 px-2 font-mono font-bold border ${liqColor.bg} ${liqColor.text} ${liqColor.border}`}>
            {context.liquidityTrend}
          </Badge>
        </div>

        {/* Correlation Stability */}
        <div className="bg-[#0d1117] p-3">
          <div className="flex items-center gap-1 mb-1.5">
            <ArrowRightLeft className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Corr. Stability</span>
          </div>
          <CorrelationStabilityBar value={context.correlationStability} />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SIGNAL GENERATION CONTROLS
// ============================================================

function SignalControls({
  selectedChain,
  setSelectedChain,
  selectedSignalType,
  setSelectedSignalType,
  onRunFullAnalysis,
  autoRefresh,
  setAutoRefresh,
  isGenerating,
}: {
  selectedChain: string;
  setSelectedChain: (v: string) => void;
  selectedSignalType: string;
  setSelectedSignalType: (v: string) => void;
  onRunFullAnalysis: () => void;
  autoRefresh: boolean;
  setAutoRefresh: (v: boolean) => void;
  isGenerating: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 bg-[#0a0e17] border border-[#1e293b] rounded-lg">
      <div className="flex items-center gap-2">
        <Radio className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Signal Controls</span>
      </div>

      {/* Chain Selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] font-mono text-[#64748b]">Chain:</span>
        <Select value={selectedChain} onValueChange={setSelectedChain}>
          <SelectTrigger className="h-6 w-20 text-[10px] font-mono bg-[#111827] border-[#2d3748] text-[#e2e8f0]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
            {CHAIN_OPTIONS.map((c) => (
              <SelectItem key={c} value={c} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Signal Type Filter */}
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] font-mono text-[#64748b]">Type:</span>
        <Select value={selectedSignalType} onValueChange={setSelectedSignalType}>
          <SelectTrigger className="h-6 w-36 text-[10px] font-mono bg-[#111827] border-[#2d3748] text-[#e2e8f0]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
            <SelectItem value="ALL" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
              All Signal Types
            </SelectItem>
            {SIGNAL_TYPES_CONFIG.map((s) => (
              <SelectItem key={s.type} value={s.type} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Run Full Analysis */}
      <Button
        onClick={onRunFullAnalysis}
        disabled={isGenerating}
        className="h-6 px-3 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30"
      >
        {isGenerating ? (
          <Loader2 className="h-3 w-3 mr-1 animate-spin" />
        ) : (
          <Play className="h-3 w-3 mr-1" />
        )}
        {isGenerating ? 'Analyzing...' : 'Run Full Analysis'}
      </Button>

      {/* Auto Refresh */}
      <div className="flex items-center gap-1.5 ml-auto">
        <span className="text-[9px] font-mono text-[#64748b]">Auto-refresh</span>
        <Switch
          checked={autoRefresh}
          onCheckedChange={setAutoRefresh}
          className="data-[state=checked]:bg-[#d4af37]/40 data-[state=unchecked]:bg-[#1a1f2e]"
        />
        {autoRefresh && (
          <span className="text-[9px] font-mono text-emerald-400 flex items-center gap-0.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
            30s
          </span>
        )}
      </div>
    </div>
  );
}

// ============================================================
// SIGNAL CARD
// ============================================================

function SignalTypeCard({
  config,
  signals,
  onGenerate,
  isGenerating,
}: {
  config: (typeof SIGNAL_TYPES_CONFIG)[number];
  signals: PredictiveSignal[];
  onGenerate: (type: PredictiveSignalType) => void;
  isGenerating: boolean;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const Icon = config.icon;
  const latestSignal = signals[0];
  const signalCount = signals.length;

  // Compute average confidence from signals
  const avgConfidence = signalCount > 0
    ? signals.reduce((sum, s) => sum + s.confidence, 0) / signalCount
    : 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="bg-[#111827] border border-[#1e293b] rounded-lg overflow-hidden hover:border-[#2d3748] transition-all group"
    >
      {/* Card Header */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <div className={`p-1.5 rounded-md ${config.color.bg}`}>
              <Icon className={`h-4 w-4 ${config.color.text}`} />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-sm font-bold text-[#e2e8f0] group-hover:text-[#d4af37] transition-colors">
                  {config.label}
                </span>
                {signalCount > 0 && (
                  <Badge className={`text-[8px] h-4 px-1.5 font-mono border ${config.color.bg} ${config.color.text} ${config.color.border}`}>
                    {signalCount}
                  </Badge>
                )}
              </div>
              <span className="text-[9px] font-mono text-[#64748b]">{config.type}</span>
            </div>
          </div>
          <InfoTip text={config.description} />
        </div>

        {/* Confidence Bar */}
        <div className="mb-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Confidence</span>
            {latestSignal && (
              <span className="text-[9px] font-mono text-[#64748b]">
                Latest: {formatTimestamp(latestSignal.createdAt)}
              </span>
            )}
          </div>
          <ConfidenceBar confidence={avgConfidence || 0} color={avgConfidence >= 0.7 ? 'bg-emerald-500' : avgConfidence >= 0.5 ? 'bg-yellow-500' : avgConfidence > 0 ? 'bg-orange-500' : 'bg-[#2d3748]'} />
        </div>

        {/* Latest Signal Data */}
        {latestSignal ? (
          <div className="space-y-2">
            {/* Prediction Data */}
            <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
              <div className="flex items-center gap-1 mb-1.5">
                <Signal className="h-3 w-3 text-[#d4af37]" />
                <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Prediction</span>
              </div>
              <PredictionDataDisplay
                data={parsePrediction(latestSignal.prediction)}
              />
            </div>

            {/* Evidence List */}
            <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
              <div className="flex items-center gap-1 mb-1.5">
                <Eye className="h-3 w-3 text-[#d4af37]" />
                <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Evidence</span>
              </div>
              <ul className="space-y-0.5">
                {parseEvidence(latestSignal.evidence).slice(0, isExpanded ? undefined : 3).map((ev, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[10px] font-mono text-[#94a3b8]">
                    <ChevronRight className="h-2.5 w-2.5 text-[#d4af37] mt-0.5 shrink-0" />
                    <span>{ev}</span>
                  </li>
                ))}
              </ul>
              {parseEvidence(latestSignal.evidence).length > 3 && (
                <button
                  onClick={() => setIsExpanded(!isExpanded)}
                  className="flex items-center gap-1 text-[9px] font-mono text-[#64748b] hover:text-[#94a3b8] mt-1 transition-colors"
                >
                  <ChevronDown className={`h-2.5 w-2.5 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                  {isExpanded ? 'Show less' : `+${parseEvidence(latestSignal.evidence).length - 3} more`}
                </button>
              )}
            </div>

            {/* Meta Row */}
            <div className="flex items-center justify-between gap-2 pt-1">
              <div className="flex items-center gap-2">
                <HitRateBadge rate={latestSignal.historicalHitRate} />
                <Badge variant="outline" className="text-[9px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
                  <Clock className="h-2.5 w-2.5 mr-0.5" />
                  {latestSignal.timeframe}
                </Badge>
                <Badge variant="outline" className="text-[9px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
                  <Timer className="h-2.5 w-2.5 mr-0.5" />
                  {formatValidity(latestSignal.validUntil)}
                </Badge>
              </div>
              <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                {latestSignal.dataPointsUsed} pts
              </Badge>
            </div>
          </div>
        ) : (
          <div className="bg-[#0a0e17] rounded-md p-4 border border-[#1e293b] text-center">
            <Icon className="h-6 w-6 text-[#2d3748] mx-auto mb-2" />
            <p className="text-[10px] font-mono text-[#64748b]">No active signals</p>
            <p className="text-[9px] font-mono text-[#475569]">Run analysis to generate</p>
          </div>
        )}

        {/* Generate Button */}
        <Button
          onClick={() => onGenerate(config.type)}
          disabled={isGenerating}
          className={`w-full mt-3 h-7 text-[10px] font-mono ${config.color.bg} ${config.color.text} hover:brightness-125 border ${config.color.border} transition-all`}
        >
          {isGenerating ? (
            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
          ) : (
            <Zap className="h-3 w-3 mr-1" />
          )}
          Generate Signal
        </Button>
      </div>
    </motion.div>
  );
}

// ============================================================
// SIGNAL DETAIL LIST (for expanded view)
// ============================================================

function SignalDetailList({ signals }: { signals: PredictiveSignal[] }) {
  if (signals.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-[#64748b] font-mono text-xs">
        <Signal className="h-4 w-4 mr-2" />
        No signals generated yet. Run analysis to begin.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <AnimatePresence>
        {signals.map((signal, index) => {
          const config = SIGNAL_TYPES_CONFIG.find((c) => c.type === signal.signalType);
          if (!config) return null;
          const Icon = config.icon;
          const prediction = parsePrediction(signal.prediction);
          const evidence = parseEvidence(signal.evidence);

          return (
            <motion.div
              key={signal.id}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ delay: index * 0.03 }}
              className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 hover:border-[#2d3748] transition-all"
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="flex items-center gap-2">
                  <Icon className={`h-4 w-4 ${config.color.text}`} />
                  <Badge className={`text-[9px] h-4 px-1.5 font-mono border ${config.color.bg} ${config.color.text} ${config.color.border}`}>
                    {config.label}
                  </Badge>
                  {signal.chain && (
                    <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                      {signal.chain}
                    </Badge>
                  )}
                  {signal.sector && (
                    <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                      {signal.sector}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <HitRateBadge rate={signal.historicalHitRate} />
                  <span className="mono-data text-[9px] text-[#64748b]">{formatTimestamp(signal.createdAt)}</span>
                </div>
              </div>

              <div className="mb-2">
                <ConfidenceBar confidence={signal.confidence} />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
                  <div className="flex items-center gap-1 mb-1">
                    <Signal className="h-3 w-3 text-[#d4af37]" />
                    <span className="text-[9px] font-mono text-[#94a3b8] uppercase">Prediction</span>
                  </div>
                  <PredictionDataDisplay data={prediction} />
                </div>

                <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
                  <div className="flex items-center gap-1 mb-1">
                    <Eye className="h-3 w-3 text-[#d4af37]" />
                    <span className="text-[9px] font-mono text-[#94a3b8] uppercase">Evidence</span>
                  </div>
                  <ul className="space-y-0.5 max-h-24 overflow-y-auto">
                    {evidence.map((ev, i) => (
                      <li key={i} className="flex items-start gap-1 text-[10px] font-mono text-[#94a3b8]">
                        <ChevronRight className="h-2.5 w-2.5 text-[#d4af37] mt-0.5 shrink-0" />
                        <span>{ev}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="flex items-center gap-2 mt-2">
                <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                  <Clock className="h-2.5 w-2.5 mr-0.5" /> {signal.timeframe}
                </Badge>
                <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                  <Timer className="h-2.5 w-2.5 mr-0.5" /> Valid {formatValidity(signal.validUntil)}
                </Badge>
                <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                  {signal.dataPointsUsed} data pts
                </Badge>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function BigDataPredictive() {
  const queryClient = useQueryClient();
  const [selectedChain, setSelectedChain] = useState('ALL');
  const [selectedSignalType, setSelectedSignalType] = useState('ALL');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [generatingType, setGeneratingType] = useState<PredictiveSignalType | null>(null);

  // Fetch market context from real-time API (derived from DB + DexScreener + signals)
  const { data: apiContextData } = useQuery({
    queryKey: ['market-context'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/market/context');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return {
          data: json.data as MarketContextData | null,
          source: json.source as 'live' | 'computed' | 'fallback' | undefined,
        };
      } catch {
        return null;
      }
    },
    refetchInterval: 30000, // Refresh every 30s
    staleTime: 15000,
  });

  // Fetch signals
  const { data: signalsData, isLoading: signalsLoading } = useQuery({
    queryKey: ['predictive-signals', selectedChain, selectedSignalType],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (selectedChain !== 'ALL') params.set('chain', selectedChain);
      if (selectedSignalType !== 'ALL') params.set('signalType', selectedSignalType);
      params.set('limit', '100');

      try {
        const res = await fetch(`/api/predictive?${params.toString()}`);
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return (json.data || []) as PredictiveSignal[];
      } catch {
        return [] as PredictiveSignal[];
      }
    },
    refetchInterval: autoRefresh ? 30000 : false,
    staleTime: 10000,
  });

  const signals: PredictiveSignal[] = signalsData || [];

  // Group signals by type
  const signalsByType = signals.reduce<Record<PredictiveSignalType, PredictiveSignal[]>>(
    (acc, signal) => {
      if (!acc[signal.signalType as PredictiveSignalType]) {
        acc[signal.signalType as PredictiveSignalType] = [];
      }
      acc[signal.signalType as PredictiveSignalType].push(signal);
      return acc;
    },
    {} as Record<PredictiveSignalType, PredictiveSignal[]>
  );

  // Generate signal mutation
  const generateMutation = useMutation({
    mutationFn: async ({ chains, signalTypes }: { chains: string[]; signalTypes: string[] }) => {
      const res = await fetch('/api/predictive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chains, signalTypes }),
      });
      if (!res.ok) throw new Error('Failed to generate');
      return res.json() as Promise<{ data: GenerateSignalResponse }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['predictive-signals'] });
    },
  });

  const handleGenerateSignal = useCallback(
    (type: PredictiveSignalType) => {
      setGeneratingType(type);
      const chains = selectedChain === 'ALL' ? ['SOL', 'ETH', 'BASE', 'ARB'] : [selectedChain];
      generateMutation.mutate(
        { chains, signalTypes: [type] },
        {
          onSettled: () => setGeneratingType(null),
        }
      );
    },
    [selectedChain, generateMutation]
  );

  const handleRunFullAnalysis = useCallback(() => {
    setGeneratingType('REGIME_CHANGE' as PredictiveSignalType);
    const chains = selectedChain === 'ALL' ? ['SOL', 'ETH', 'BASE', 'ARB'] : [selectedChain];
    const types = selectedSignalType === 'ALL'
      ? SIGNAL_TYPES_CONFIG.map((c) => c.type)
      : [selectedSignalType as PredictiveSignalType];

    generateMutation.mutate(
      { chains, signalTypes: types },
      {
        onSettled: () => setGeneratingType(null),
      }
    );
  }, [selectedChain, selectedSignalType, generateMutation]);

  // Compute market context from signals, enriched by API context
  // Priority: 1) API context (live DexScreener + DB), 2) Signal-derived fallback
  const marketContext: MarketContextData = useMemo(() => {
    // Derive fallback from signals (always computed — even when API is available,
    // this provides signal-enrichment for individual signal type overrides)
    const signalDerivedBase = deriveContextFromSignals(signalsByType);

    // Use API context as base when available (includes real DexScreener + DB computations)
    // Falls back to signal-derived context (no more hardcoded defaults!)
    const apiBase = apiContextData?.data || signalDerivedBase;

    // Start with API-computed context, then override with individual signal data
    // This allows real-time signal updates to refine the API-computed base
    let regime: MarketRegime = apiBase.regime;
    let volatilityRegime: VolatilityRegime = apiBase.volatilityRegime;
    let botSwarmLevel: BotSwarmLevel = apiBase.botSwarmLevel;
    let whaleDirection: WhaleDirection = apiBase.whaleDirection;
    let smartMoneyFlow: SmartMoneyFlowDirection = apiBase.smartMoneyFlow;
    let liquidityTrend: LiquidityTrend = apiBase.liquidityTrend;
    let correlationStability = apiBase.correlationStability;

    // Override with latest signal data when available (signals are more recent than API cache)
    const regimeSignals = signalsByType['REGIME_CHANGE'] || [];
    const volSignals = signalsByType['VOLATILITY_REGIME'] || [];
    const botSignals = signalsByType['BOT_SWARM'] || [];
    const whaleSignals = signalsByType['WHALE_MOVEMENT'] || [];
    const smSignals = signalsByType['SMART_MONEY_POSITIONING'] || [];
    const liqSignals = signalsByType['LIQUIDITY_DRAIN'] || [];
    const corrSignals = signalsByType['CORRELATION_BREAK'] || [];

    if (regimeSignals.length > 0) {
      const p = parsePrediction(regimeSignals[0].prediction);
      if (p.toRegime && ['BULL', 'BEAR', 'SIDEWAYS', 'TRANSITION'].includes(p.toRegime as string)) {
        regime = p.toRegime as MarketRegime;
      }
    }

    if (volSignals.length > 0) {
      const p = parsePrediction(volSignals[0].prediction);
      if (p.current && ['LOW', 'NORMAL', 'HIGH', 'EXTREME'].includes(p.current as string)) {
        volatilityRegime = p.current as VolatilityRegime;
      }
    }

    if (botSignals.length > 0) {
      const p = parsePrediction(botSignals[0].prediction);
      if (p.level && ['NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(p.level as string)) {
        botSwarmLevel = p.level as BotSwarmLevel;
      }
    }

    if (whaleSignals.length > 0) {
      const p = parsePrediction(whaleSignals[0].prediction);
      if (p.direction && ['ACCUMULATING', 'DISTRIBUTING', 'NEUTRAL', 'ROTATING'].includes(p.direction as string)) {
        whaleDirection = p.direction as WhaleDirection;
      }
    }

    if (smSignals.length > 0) {
      const p = parsePrediction(smSignals[0].prediction);
      if (p.netDirection && ['INFLOW', 'OUTFLOW', 'NEUTRAL'].includes(p.netDirection as string)) {
        smartMoneyFlow = p.netDirection as SmartMoneyFlowDirection;
      }
    }

    if (liqSignals.length > 0) {
      const p = parsePrediction(liqSignals[0].prediction);
      if (p.trend && ['ACCUMULATING', 'STABLE', 'DRAINING', 'CRITICAL_DRAIN'].includes(p.trend as string)) {
        liquidityTrend = p.trend as LiquidityTrend;
      }
    }

    if (corrSignals.length > 0) {
      const p = parsePrediction(corrSignals[0].prediction);
      if (typeof p.stability === 'number') {
        correlationStability = p.stability;
      } else if (typeof p.correlation === 'number') {
        correlationStability = Math.abs(p.correlation);
      }
    }

    // Determine effective source
    const effectiveSource: 'live' | 'computed' | 'fallback' =
      apiContextData?.data ? (apiContextData.source || 'computed') : 'computed';

    return {
      regime,
      volatilityRegime,
      botSwarmLevel,
      whaleDirection,
      smartMoneyFlow,
      liquidityTrend,
      correlationStability,
      // Pass through API metadata when available
      tokenCount: apiBase.tokenCount,
      signalCount: apiBase.signalCount || Object.values(signalsByType).flat().length,
      chains: apiBase.chains,
      computedAt: apiBase.computedAt || new Date().toISOString(),
      source: effectiveSource,
      liveTokenCount: apiBase.liveTokenCount,
      signalBreakdown: apiBase.signalBreakdown,
    };
  }, [signalsByType, apiContextData]);

  // Determine data source for the indicator
  const dataSource: 'live' | 'computed' | 'fallback' = marketContext.source || (apiContextData?.data ? 'computed' : 'fallback');

  // Stats
  const totalSignals = signals.length;
  const avgConfidence = totalSignals > 0
    ? signals.reduce((sum, s) => sum + s.confidence, 0) / totalSignals
    : 0;
  const highConfidenceCount = signals.filter((s) => s.confidence >= 0.7).length;

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* Main Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-md bg-[#d4af37]/15">
            <Database className="h-5 w-5 text-[#d4af37]" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-sm font-bold text-[#e2e8f0] uppercase tracking-wider">
                Big Data Predictive Engine
              </h2>
              <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#d4af37]/15 text-[#d4af37] border border-[#d4af37]/30">
                <Activity className="h-2.5 w-2.5 mr-0.5" />
                ACTIVE
              </Badge>
            </div>
            <p className="text-[10px] font-mono text-[#64748b]">
              Multi-signal predictive analysis &middot; Z-score &middot; Bollinger &middot; Pearson &middot; HHI &middot; ATR
            </p>
          </div>
        </div>

        {/* Aggregate Stats */}
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Total Signals</div>
            <div className="mono-data text-lg font-bold text-[#e2e8f0]">{totalSignals}</div>
          </div>
          <div className="w-px h-8 bg-[#1e293b]" />
          <div className="text-right">
            <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Avg Confidence</div>
            <div className={`mono-data text-lg font-bold ${avgConfidence >= 0.7 ? 'text-emerald-400' : avgConfidence >= 0.5 ? 'text-yellow-400' : 'text-red-400'}`}>
              {Math.round(avgConfidence * 100)}%
            </div>
          </div>
          <div className="w-px h-8 bg-[#1e293b]" />
          <div className="text-right">
            <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">High Conf</div>
            <div className="mono-data text-lg font-bold text-emerald-400">{highConfidenceCount}</div>
          </div>
        </div>
      </div>

      {/* Market Context Panel */}
      <div className="px-4 pt-3 shrink-0">
        <MarketContextPanel context={marketContext} dataSource={dataSource} />
      </div>

      {/* Signal Generation Controls */}
      <div className="px-4 pt-3 shrink-0">
        <SignalControls
          selectedChain={selectedChain}
          setSelectedChain={setSelectedChain}
          selectedSignalType={selectedSignalType}
          setSelectedSignalType={setSelectedSignalType}
          onRunFullAnalysis={handleRunFullAnalysis}
          autoRefresh={autoRefresh}
          setAutoRefresh={setAutoRefresh}
          isGenerating={generateMutation.isPending}
        />
      </div>

      {/* Signal Type Cards Grid */}
      <div className="flex-1 overflow-hidden mt-3">
        <ScrollArea className="h-full px-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 pb-4">
            {SIGNAL_TYPES_CONFIG.map((config) => (
              <SignalTypeCard
                key={config.type}
                config={config}
                signals={signalsByType[config.type] || []}
                onGenerate={handleGenerateSignal}
                isGenerating={generateMutation.isPending && generatingType === config.type}
              />
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* Signal List Footer */}
      {signals.length > 0 && (
        <div className="border-t border-[#1e293b] bg-[#0d1117] shrink-0">
          <div className="px-4 py-2">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Fingerprint className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">
                  Recent Signals Log
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
                  {signals.length} entries
                </Badge>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => queryClient.invalidateQueries({ queryKey: ['predictive-signals'] })}
                className="h-5 px-2 text-[9px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
              >
                <RefreshCw className="h-3 w-3 mr-1" />
                Refresh
              </Button>
            </div>
            <ScrollArea className="max-h-48">
              <SignalDetailList signals={signals.slice(0, 20)} />
            </ScrollArea>
          </div>
        </div>
      )}

      {/* Loading Overlay */}
      <AnimatePresence>
        {signalsLoading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-[#0a0e17]/80 flex items-center justify-center z-50"
          >
            <div className="flex items-center gap-3 bg-[#111827] border border-[#2d3748] rounded-lg px-6 py-4">
              <Loader2 className="h-5 w-5 text-[#d4af37] animate-spin" />
              <div>
                <div className="font-mono text-sm text-[#e2e8f0]">Loading Predictive Data</div>
                <div className="font-mono text-[10px] text-[#64748b]">Analyzing multi-signal patterns...</div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
