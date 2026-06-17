'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useCryptoStore, type MarketRegimeData } from '@/store/crypto-store';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { motion, AnimatePresence } from 'framer-motion';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  AlertTriangle,
  RefreshCw,
  Loader2,
  Activity,
  BarChart3,
  Shield,
  Zap,
  ArrowRightLeft,
  Target,
  Pause,
  Play,
  Clock,
  Waves,
  Gauge,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';

// ============================================================
// TYPES
// ============================================================

type RegimeType =
  | 'TRENDING_BULL'
  | 'TRENDING_BEAR'
  | 'RANGING'
  | 'ACCUMULATION'
  | 'DISTRIBUTION'
  | 'PANIC'
  | 'EUPHORIA';

interface RegimeConfig {
  label: string;
  shortLabel: string;
  color: string;
  bgColor: string;
  borderColor: string;
  icon: typeof TrendingUp;
  pulse?: boolean;
}

interface StrategyImplications {
  positionSizingMultiplier: number;
  stopLossAdjustment: number;
  pauseStrategies: string[];
  activateStrategies: string[];
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME';
}

interface TimelineSegment {
  regime: string;
  label: string;
  color: string;
  duration: number; // proportion of the timeline
}

// ============================================================
// REGIME CONFIG MAP
// ============================================================

const REGIME_CONFIG: Record<string, RegimeConfig> = {
  TRENDING_BULL: {
    label: 'BULL',
    shortLabel: 'BULL',
    color: '#10b981',
    bgColor: 'bg-emerald-500/10',
    borderColor: 'border-emerald-500/30',
    icon: TrendingUp,
  },
  TRENDING_BEAR: {
    label: 'BEAR',
    shortLabel: 'BEAR',
    color: '#ef4444',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    icon: TrendingDown,
  },
  RANGING: {
    label: 'SIDEWAYS',
    shortLabel: 'SIDE',
    color: '#eab308',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    icon: Minus,
  },
  ACCUMULATION: {
    label: 'RECOVERY',
    shortLabel: 'RECOV',
    color: '#3b82f6',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
    icon: RefreshCw,
  },
  DISTRIBUTION: {
    label: 'DISTRIBUTION',
    shortLabel: 'DIST',
    color: '#f97316',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
    icon: ArrowDownRight,
  },
  PANIC: {
    label: 'CRISIS',
    shortLabel: 'CRISIS',
    color: '#ef4444',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    icon: AlertTriangle,
    pulse: true,
  },
  EUPHORIA: {
    label: 'EUPHORIA',
    shortLabel: 'EUPH',
    color: '#a855f7',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/30',
    icon: Zap,
  },
};

// ============================================================
// STRATEGY IMPLICATIONS BY REGIME
// ============================================================

function deriveStrategyImplications(regime: string, confidence: number): StrategyImplications {
  const configs: Record<string, StrategyImplications> = {
    TRENDING_BULL: {
      positionSizingMultiplier: 1.2 + confidence * 0.3,
      stopLossAdjustment: -0.15,
      pauseStrategies: ['mean-reversion', 'short-biased'],
      activateStrategies: ['momentum-long', 'breakout', 'trend-following'],
      riskLevel: 'LOW',
    },
    TRENDING_BEAR: {
      positionSizingMultiplier: 0.5 + (1 - confidence) * 0.3,
      stopLossAdjustment: 0.25,
      pauseStrategies: ['momentum-long', 'breakout', 'trend-following'],
      activateStrategies: ['short-biased', 'hedging', 'defensive'],
      riskLevel: 'HIGH',
    },
    RANGING: {
      positionSizingMultiplier: 0.8,
      stopLossAdjustment: -0.05,
      pauseStrategies: ['trend-following', 'breakout'],
      activateStrategies: ['mean-reversion', 'grid-trading', 'range-bound'],
      riskLevel: 'MEDIUM',
    },
    ACCUMULATION: {
      positionSizingMultiplier: 0.9 + confidence * 0.2,
      stopLossAdjustment: 0.1,
      pauseStrategies: ['short-biased', 'aggressive-short'],
      activateStrategies: ['dca-long', 'accumulation', 'value-buying'],
      riskLevel: 'LOW',
    },
    DISTRIBUTION: {
      positionSizingMultiplier: 0.4 + (1 - confidence) * 0.3,
      stopLossAdjustment: 0.3,
      pauseStrategies: ['momentum-long', 'dca-long'],
      activateStrategies: ['take-profit', 'hedging', 'defensive'],
      riskLevel: 'HIGH',
    },
    PANIC: {
      positionSizingMultiplier: 0.15,
      stopLossAdjustment: 0.5,
      pauseStrategies: ['momentum-long', 'breakout', 'trend-following', 'mean-reversion'],
      activateStrategies: ['emergency-hedge', 'capital-preserve', 'stop-all'],
      riskLevel: 'EXTREME',
    },
    EUPHORIA: {
      positionSizingMultiplier: 0.6,
      stopLossAdjustment: 0.2,
      pauseStrategies: ['dca-long', 'accumulation'],
      activateStrategies: ['take-profit', 'trailing-stop', 'de-risk'],
      riskLevel: 'HIGH',
    },
  };
  return configs[regime] ?? configs.RANGING;
}

// ============================================================
// MOCK TIMELINE GENERATOR (30-day regime history)
// ============================================================

function generateTimeline(regime: string): TimelineSegment[] {
  const colorMap: Record<string, string> = {};
  for (const [key, cfg] of Object.entries(REGIME_CONFIG)) {
    colorMap[key] = cfg.color;
  }

  const labelMap: Record<string, string> = {};
  for (const [key, cfg] of Object.entries(REGIME_CONFIG)) {
    labelMap[key] = cfg.shortLabel;
  }

  // Generate a plausible 30-day history ending in current regime
  const segments: TimelineSegment[] = [];
  const regimePool: string[] = ['RANGING', 'RANGING', 'ACCUMULATION', 'TRENDING_BULL', 'RANGING'];

  let remaining = 30;
  let i = 0;
  while (remaining > 0) {
    const isLast = remaining <= 5 + Math.floor(Math.random() * 4);
    const currentRegime = isLast ? regime : regimePool[i % regimePool.length];
    const duration = isLast ? remaining : Math.min(remaining, 3 + Math.floor(Math.random() * 7));
    segments.push({
      regime: currentRegime,
      label: labelMap[currentRegime] ?? currentRegime,
      color: colorMap[currentRegime] ?? '#64748b',
      duration,
    });
    remaining -= duration;
    i++;
  }

  return segments;
}

// ============================================================
// CUSTOM RECHARTS TOOLTIP
// ============================================================

function CustomBarTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: { name: string; value: number; color: string } }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-md px-2.5 py-1.5 shadow-xl">
      <p className="text-[10px] font-mono text-[#e2e8f0] font-semibold">{d.name}</p>
      <p className="text-[10px] font-mono" style={{ color: d.color }}>
        {(d.value * 100).toFixed(1)}%
      </p>
    </div>
  );
}

// ============================================================
// INDICATOR CARD COMPONENT
// ============================================================

function IndicatorCard({
  icon: Icon,
  label,
  value,
  subValue,
  signal,
  color,
  delay,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  subValue?: string;
  signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  color: string;
  delay: number;
}) {
  const signalColor =
    signal === 'BULLISH'
      ? 'text-emerald-400'
      : signal === 'BEARISH'
        ? 'text-red-400'
        : 'text-[#94a3b8]';

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.3 }}
    >
      <Card className="bg-[#0d1117] border-[#1e293b] p-3">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Icon className="h-3 w-3" style={{ color }} />
          <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
            {label}
          </span>
          <Badge
            className="text-[7px] h-3.5 px-1 ml-auto font-mono"
            style={{
              backgroundColor:
                signal === 'BULLISH'
                  ? '#10b98120'
                  : signal === 'BEARISH'
                    ? '#ef444420'
                    : '#94a3b820',
              color: signalColor,
              borderColor:
                signal === 'BULLISH'
                  ? '#10b98140'
                  : signal === 'BEARISH'
                    ? '#ef444440'
                    : '#94a3b840',
            }}
          >
            {signal}
          </Badge>
        </div>
        <div className="text-[15px] font-mono font-bold text-[#f1f5f9]">{value}</div>
        {subValue && (
          <div className="text-[9px] font-mono text-[#475569] mt-0.5">{subValue}</div>
        )}
      </Card>
    </motion.div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function MarketRegimePanel() {
  const {
    marketRegime,
    setMarketRegime,
    setMarketRegimeLoading,
  } = useCryptoStore();

  const [timeline, setTimeline] = useState<TimelineSegment[]>([]);
  const [countdown, setCountdown] = useState(60);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- FETCH REGIME DATA via useQuery ----
  const { refetch: refetchRegime, error: queryError, isFetching } = useQuery({
    queryKey: ['regime-assess'],
    queryFn: async () => {
      const res = await fetch('/api/regime/assess');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      return json.data as MarketRegimeData;
    },
    refetchInterval: 60000,
    staleTime: 30000,
    select: (data) => {
      // Sync to store whenever data changes
      setMarketRegime(data);
      setTimeline(generateTimeline(data.regime));
      return data;
    },
  });

  // Sync loading state from query
  useEffect(() => {
    setMarketRegimeLoading(isFetching);
  }, [isFetching, setMarketRegimeLoading]);

  // Countdown timer
  useEffect(() => {
    countdownRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          refetchRegime();
          return 60;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [refetchRegime]);

  const error = queryError ? (queryError instanceof Error ? queryError.message : 'Failed to fetch regime') : null;

  // ---- DERIVED DATA ----
  const regime = marketRegime?.regime ?? 'RANGING';
  const confidence = marketRegime?.confidence ?? 0;
  const keyIndicators = marketRegime?.keyIndicators ?? [];
  const transitionProbs = marketRegime?.transitionProbabilities ?? {};
  const assessedAt = marketRegime?.assessedAt;
  const lastChangedAt = marketRegime?.lastChangedAt;

  const config = REGIME_CONFIG[regime] ?? REGIME_CONFIG.RANGING;
  const RegimeIcon = config.icon;
  const strategy = deriveStrategyImplications(regime, confidence);

  // Extract indicator values from keyIndicators
  const getIndicator = (name: string) =>
    keyIndicators.find((k) => k.name.toLowerCase().includes(name.toLowerCase()));

  const volatilityIndicator = getIndicator('vol') ?? getIndicator('volatility');
  const trendIndicator = getIndicator('trend') ?? getIndicator('ma');
  const momentumIndicator = getIndicator('momentum') ?? getIndicator('rsi');
  const meanReversionIndicator = getIndicator('mean') ?? getIndicator('reversion');
  const volumeIndicator = getIndicator('volume') ?? getIndicator('vol_profile');
  const correlationIndicator = getIndicator('correlation') ?? getIndicator('cross');

  // Build transition chart data
  const transitionData = Object.entries(transitionProbs)
    .filter(([key]) => key !== regime) // exclude "stay" for the chart, show separately
    .map(([key, value]) => ({
      name: REGIME_CONFIG[key]?.shortLabel ?? key,
      fullName: REGIME_CONFIG[key]?.label ?? key,
      value,
      color: REGIME_CONFIG[key]?.color ?? '#64748b',
    }))
    .sort((a, b) => b.value - a.value);

  const stayProb = transitionProbs[regime] ?? 0;

  // Next shift probability = 1 - stay probability
  const nextShiftProb = 1 - stayProb;

  // Risk level color
  const riskColorMap: Record<string, string> = {
    LOW: '#10b981',
    MEDIUM: '#eab308',
    HIGH: '#f97316',
    EXTREME: '#ef4444',
  };

  // ---- FORMATTERS ----
  const formatTimestamp = (iso?: string) => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return '—';
    }
  };

  const formatTimeAgo = (iso?: string) => {
    if (!iso) return '';
    try {
      const diff = Date.now() - new Date(iso).getTime();
      const mins = Math.floor(diff / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return `${mins}m ago`;
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return `${hrs}h ago`;
      return `${Math.floor(hrs / 24)}d ago`;
    } catch {
      return '';
    }
  };

  // ============================================================
  // RENDER: LOADING STATE
  // ============================================================

  if (isFetching && !marketRegime) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2.5 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
            <Activity className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
            Market Regime Engine
          </span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-cyan-400 mb-3" />
          <span className="text-sm font-mono text-[#64748b]">Assessing market regime...</span>
          <span className="text-[10px] font-mono text-[#475569] mt-1">
            Analyzing multi-factor signals
          </span>
        </div>
      </div>
    );
  }

  // ============================================================
  // RENDER: ERROR STATE
  // ============================================================

  if (error && !marketRegime) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2.5 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
            <Activity className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
            Market Regime Engine
          </span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-red-500/10 border border-red-500/20 mb-4">
            <AlertTriangle className="h-6 w-6 text-red-400" />
          </div>
          <h3 className="text-sm font-mono font-bold text-red-400 mb-2">
            Assessment Failed
          </h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md">
            {error}
          </p>
          <Button
            onClick={() => refetchRegime()}
            className="mt-4 h-7 text-[10px] font-mono font-bold bg-cyan-500 hover:bg-cyan-600 text-black px-4"
          >
            <RefreshCw className="h-3 w-3 mr-1.5" />
            Retry Assessment
          </Button>
        </div>
      </div>
    );
  }

  // ============================================================
  // RENDER: MAIN PANEL
  // ============================================================

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
        {/* ===== HEADER ===== */}
        <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
              <Activity className="h-3.5 w-3.5 text-cyan-400" />
            </div>
            <div>
              <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
                Market Regime Engine
              </span>
              <span className="text-[9px] font-mono text-[#475569] ml-2">
                HMM-inspired detection
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-mono text-[#475569]">
              <Clock className="h-2.5 w-2.5 inline mr-1" />
              {countdown}s
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetchRegime()}
              disabled={isFetching}
              className="h-6 text-[9px] font-mono px-2 text-[#94a3b8] hover:text-cyan-400 hover:bg-cyan-500/10"
            >
              {isFetching ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3 mr-1" />
              )}
              Refresh
            </Button>
          </div>
        </div>

        {/* ===== SCROLLABLE CONTENT ===== */}
        <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
          {/* ---- SECTION 1: CURRENT REGIME (Hero) ---- */}
          <div className="px-3 py-4 border-b border-[#1e293b]">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.4 }}
              className="relative"
            >
              <div
                className={`relative rounded-xl border-2 ${config.borderColor} ${config.bgColor} p-5 overflow-hidden`}
              >
                {/* Background glow */}
                <div
                  className="absolute inset-0 opacity-5"
                  style={{
                    background: `radial-gradient(circle at 50% 50%, ${config.color}, transparent 70%)`,
                  }}
                />

                <div className="relative flex items-center gap-5">
                  {/* Regime Icon */}
                  <div className="relative">
                    <div
                      className={`flex items-center justify-center w-16 h-16 rounded-2xl border ${config.borderColor}`}
                      style={{ backgroundColor: `${config.color}15` }}
                    >
                      <RegimeIcon
                        className="h-8 w-8"
                        style={{ color: config.color }}
                      />
                    </div>
                    {config.pulse && (
                      <span className="absolute -top-1 -right-1 flex h-3 w-3">
                        <span
                          className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
                          style={{ backgroundColor: config.color }}
                        />
                        <span
                          className="relative inline-flex rounded-full h-3 w-3"
                          style={{ backgroundColor: config.color }}
                        />
                      </span>
                    )}
                  </div>

                  {/* Regime Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2.5 mb-1">
                      <h2
                        className="text-[22px] font-mono font-black tracking-tight"
                        style={{ color: config.color }}
                      >
                        {config.label}
                      </h2>
                      <Badge
                        className="text-[8px] h-4 px-1.5 font-mono"
                        style={{
                          backgroundColor: `${config.color}20`,
                          color: config.color,
                          borderColor: `${config.color}40`,
                        }}
                      >
                        {regime}
                      </Badge>
                    </div>

                    {/* Confidence bar */}
                    <div className="mt-2 mb-1.5">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                          Confidence
                        </span>
                        <span
                          className="text-[11px] font-mono font-bold"
                          style={{ color: config.color }}
                        >
                          {(confidence * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                        <motion.div
                          className="h-full rounded-full"
                          initial={{ width: 0 }}
                          animate={{ width: `${confidence * 100}%` }}
                          transition={{ duration: 0.8, ease: 'easeOut' }}
                          style={{ backgroundColor: config.color }}
                        />
                      </div>
                    </div>

                    {/* Timestamps */}
                    <div className="flex items-center gap-3 mt-1.5">
                      <span className="text-[9px] font-mono text-[#475569]">
                        Assessed: {formatTimestamp(assessedAt)}
                      </span>
                      {lastChangedAt && (
                        <span className="text-[9px] font-mono text-[#475569]">
                          Changed: {formatTimeAgo(lastChangedAt)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </motion.div>
          </div>

          {/* ---- SECTION 2: REGIME INDICATORS (Grid) ---- */}
          <div className="px-3 py-3 border-b border-[#1e293b]">
            <div className="flex items-center gap-2 mb-2.5">
              <Gauge className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Regime Indicators
              </span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
              <IndicatorCard
                icon={Waves}
                label="Volatility"
                value={
                  volatilityIndicator
                    ? `${(volatilityIndicator.value * 100).toFixed(1)}%`
                    : '—'
                }
                subValue={
                  volatilityIndicator
                    ? 'Current vs 30d avg'
                    : undefined
                }
                signal={volatilityIndicator?.signal ?? 'NEUTRAL'}
                color="#f97316"
                delay={0.05}
              />
              <IndicatorCard
                icon={TrendingUp}
                label="Trend Strength"
                value={
                  trendIndicator
                    ? `${trendIndicator.value > 0 ? '+' : ''}${trendIndicator.value.toFixed(3)}`
                    : '—'
                }
                subValue={
                  trendIndicator
                    ? trendIndicator.value > 0.3
                      ? 'Positive trend'
                      : trendIndicator.value < -0.3
                        ? 'Negative trend'
                        : 'Neutral'
                    : undefined
                }
                signal={trendIndicator?.signal ?? 'NEUTRAL'}
                color="#3b82f6"
                delay={0.1}
              />
              <IndicatorCard
                icon={Zap}
                label="Momentum"
                value={
                  momentumIndicator
                    ? `${(momentumIndicator.value * 100).toFixed(1)}`
                    : '—'
                }
                subValue={
                  momentumIndicator
                    ? momentumIndicator.value > 0.5
                      ? 'Strong momentum'
                      : momentumIndicator.value < -0.5
                        ? 'Weak momentum'
                        : 'Moderate'
                    : undefined
                }
                signal={momentumIndicator?.signal ?? 'NEUTRAL'}
                color="#a855f7"
                delay={0.15}
              />
              <IndicatorCard
                icon={ArrowRightLeft}
                label="Mean Reversion"
                value={
                  meanReversionIndicator
                    ? `${(meanReversionIndicator.value * 100).toFixed(1)}%`
                    : '—'
                }
                subValue={
                  meanReversionIndicator
                    ? 'Signal strength'
                    : undefined
                }
                signal={meanReversionIndicator?.signal ?? 'NEUTRAL'}
                color="#eab308"
                delay={0.2}
              />
              <IndicatorCard
                icon={BarChart3}
                label="Volume Profile"
                value={
                  volumeIndicator
                    ? volumeIndicator.value > 0.6
                      ? 'Above Avg'
                      : volumeIndicator.value < 0.4
                        ? 'Below Avg'
                        : 'Average'
                    : '—'
                }
                subValue={
                  volumeIndicator
                    ? `${(volumeIndicator.value * 100).toFixed(0)}th pctile`
                    : undefined
                }
                signal={volumeIndicator?.signal ?? 'NEUTRAL'}
                color="#10b981"
                delay={0.25}
              />
              <IndicatorCard
                icon={Shield}
                label="Cross-Asset Corr"
                value={
                  correlationIndicator
                    ? `${(correlationIndicator.value * 100).toFixed(1)}%`
                    : '—'
                }
                subValue={
                  correlationIndicator
                    ? correlationIndicator.value > 0.7
                      ? 'High correlation'
                      : 'Low correlation'
                    : undefined
                }
                signal={correlationIndicator?.signal ?? 'NEUTRAL'}
                color="#06b6d4"
                delay={0.3}
              />
            </div>
          </div>

          {/* ---- SECTION 3: TRANSITION PROBABILITIES ---- */}
          <div className="px-3 py-3 border-b border-[#1e293b]">
            <div className="flex items-center gap-2 mb-2.5">
              <Target className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Transition Probabilities
              </span>
            </div>

            {/* Stay + Next Shift */}
            <div className="grid grid-cols-2 gap-2 mb-3">
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    P(Stay {config.label})
                  </span>
                </div>
                <div className="text-[20px] font-mono font-bold" style={{ color: config.color }}>
                  {(stayProb * 100).toFixed(1)}%
                </div>
                <div className="mt-1">
                  <Progress
                    value={stayProb * 100}
                    className="h-1.5 bg-[#1e293b]"
                  />
                </div>
              </Card>

              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Next Shift Probability
                  </span>
                </div>
                <div
                  className="text-[20px] font-mono font-bold"
                  style={{
                    color:
                      nextShiftProb > 0.5
                        ? '#ef4444'
                        : nextShiftProb > 0.3
                          ? '#eab308'
                          : '#10b981',
                  }}
                >
                  {(nextShiftProb * 100).toFixed(1)}%
                </div>
                <div className="mt-1">
                  <Badge
                    className="text-[8px] h-4 px-1.5 font-mono"
                    style={{
                      backgroundColor:
                        nextShiftProb > 0.5
                          ? '#ef444420'
                          : nextShiftProb > 0.3
                            ? '#eab30820'
                            : '#10b98120',
                      color:
                        nextShiftProb > 0.5
                          ? '#ef4444'
                          : nextShiftProb > 0.3
                            ? '#eab308'
                            : '#10b981',
                      borderColor:
                        nextShiftProb > 0.5
                          ? '#ef444440'
                          : nextShiftProb > 0.3
                            ? '#eab30840'
                            : '#10b98140',
                    }}
                  >
                    {nextShiftProb > 0.5
                      ? 'HIGH SHIFT RISK'
                      : nextShiftProb > 0.3
                        ? 'MODERATE'
                        : 'STABLE'}
                  </Badge>
                </div>
              </Card>
            </div>

            {/* Transition Bar Chart */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              <div className="h-[160px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={transitionData}
                    layout="vertical"
                    margin={{ top: 0, right: 30, bottom: 0, left: 0 }}
                    barCategoryGap={4}
                  >
                    <XAxis
                      type="number"
                      domain={[0, 1]}
                      tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                      tick={{ fontSize: 8, fontFamily: 'monospace', fill: '#475569' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 9, fontFamily: 'monospace', fill: '#94a3b8' }}
                      axisLine={false}
                      tickLine={false}
                      width={45}
                    />
                    <RechartsTooltip
                      content={<CustomBarTooltip />}
                      cursor={{ fill: '#1e293b40' }}
                    />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={14}>
                      {transitionData.map((entry, index) => (
                        <Cell key={index} fill={entry.color} fillOpacity={0.8} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          {/* ---- SECTION 4: STRATEGY IMPLICATIONS ---- */}
          <div className="px-3 py-3 border-b border-[#1e293b]">
            <div className="flex items-center gap-2 mb-2.5">
              <Shield className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Strategy Implications
              </span>
              <Badge
                className="text-[8px] h-4 px-1.5 font-mono ml-auto"
                style={{
                  backgroundColor: `${riskColorMap[strategy.riskLevel]}20`,
                  color: riskColorMap[strategy.riskLevel],
                  borderColor: `${riskColorMap[strategy.riskLevel]}40`,
                }}
              >
                {strategy.riskLevel} RISK
              </Badge>
            </div>

            {/* Sizing & Stop Loss */}
            <div className="grid grid-cols-2 gap-2 mb-2">
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1">
                  <Target className="h-3 w-3 text-amber-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Position Sizing
                  </span>
                </div>
                <div
                  className={`text-[18px] font-mono font-bold ${
                    strategy.positionSizingMultiplier >= 1
                      ? 'text-emerald-400'
                      : strategy.positionSizingMultiplier >= 0.7
                        ? 'text-amber-400'
                        : 'text-red-400'
                  }`}
                >
                  {strategy.positionSizingMultiplier.toFixed(2)}x
                </div>
                <div className="text-[9px] font-mono text-[#475569] mt-0.5">
                  Multiplier applied to base size
                </div>
              </Card>

              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-1">
                  <Shield className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Stop Loss Adj.
                  </span>
                </div>
                <div
                  className={`text-[18px] font-mono font-bold ${
                    strategy.stopLossAdjustment > 0
                      ? 'text-red-400'
                      : strategy.stopLossAdjustment > -0.1
                        ? 'text-amber-400'
                        : 'text-emerald-400'
                  }`}
                >
                  {strategy.stopLossAdjustment > 0 ? '+' : ''}
                  {(strategy.stopLossAdjustment * 100).toFixed(0)}%
                </div>
                <div className="text-[9px] font-mono text-[#475569] mt-0.5">
                  {strategy.stopLossAdjustment > 0
                    ? 'Widen stops for volatility'
                    : 'Tighten stops in trend'}
                </div>
              </Card>
            </div>

            {/* Pause / Activate Strategies */}
            <div className="grid grid-cols-2 gap-2">
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <Pause className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Pause Strategies
                  </span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {strategy.pauseStrategies.length > 0 ? (
                    strategy.pauseStrategies.map((s) => (
                      <Badge
                        key={s}
                        className="text-[8px] h-4 px-1.5 font-mono bg-red-500/10 text-red-400 border border-red-500/20"
                      >
                        {s}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-[9px] font-mono text-[#475569]">None</span>
                  )}
                </div>
              </Card>

              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <Play className="h-3 w-3 text-emerald-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Activate Strategies
                  </span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {strategy.activateStrategies.length > 0 ? (
                    strategy.activateStrategies.map((s) => (
                      <Badge
                        key={s}
                        className="text-[8px] h-4 px-1.5 font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      >
                        {s}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-[9px] font-mono text-[#475569]">None</span>
                  )}
                </div>
              </Card>
            </div>
          </div>

          {/* ---- SECTION 5: HISTORICAL REGIME TIMELINE ---- */}
          <div className="px-3 py-3">
            <div className="flex items-center gap-2 mb-2.5">
              <Clock className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                30-Day Regime Timeline
              </span>
            </div>
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              {/* Timeline bar */}
              <div className="flex h-6 rounded-md overflow-hidden border border-[#1e293b]">
                <AnimatePresence>
                  {timeline.map((seg, i) => (
                    <Tooltip key={i}>
                      <TooltipTrigger asChild>
                        <motion.div
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 0.85 }}
                          transition={{ delay: i * 0.04 }}
                          className="h-full flex items-center justify-center cursor-default"
                          style={{
                            width: `${(seg.duration / 30) * 100}%`,
                            backgroundColor: seg.color,
                            minWidth: seg.duration > 1 ? '2px' : '1px',
                          }}
                        >
                          {seg.duration >= 5 && (
                            <span className="text-[7px] font-mono font-bold text-black/60 truncate px-0.5">
                              {seg.label}
                            </span>
                          )}
                        </motion.div>
                      </TooltipTrigger>
                      <TooltipContent
                        side="top"
                        className="bg-[#111827] border-[#1e293b] text-[10px] font-mono"
                      >
                        <p className="text-[#e2e8f0] font-semibold">{seg.regime}</p>
                        <p className="text-[#94a3b8]">{seg.duration} days</p>
                      </TooltipContent>
                    </Tooltip>
                  ))}
                </AnimatePresence>
              </div>

              {/* Timeline legend */}
              <div className="flex flex-wrap items-center gap-2.5 mt-2.5">
                {Object.entries(REGIME_CONFIG).map(([key, cfg]) => (
                  <div key={key} className="flex items-center gap-1">
                    <span
                      className="inline-block w-2 h-2 rounded-sm"
                      style={{ backgroundColor: cfg.color }}
                    />
                    <span className="text-[8px] font-mono text-[#64748b]">
                      {cfg.shortLabel}
                    </span>
                  </div>
                ))}
              </div>

              {/* Day markers */}
              <div className="flex justify-between mt-1.5">
                <span className="text-[8px] font-mono text-[#475569]">30d ago</span>
                <span className="text-[8px] font-mono text-[#475569]">Today</span>
              </div>
            </Card>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}

export { MarketRegimePanel };
