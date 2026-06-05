'use client';

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Shield,
  ShieldCheck,
  ShieldX,
  AlertTriangle,
  Loader2,
  Play,
  RotateCcw,
  Zap,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useCryptoStore } from '@/store/crypto-store';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

interface PreFilterFormState {
  symbol: string;
  signalType: 'BUY' | 'SELL';
  positionSizeUsd: number;
  confidence: number;
}

// ============================================================
// HELPERS
// ============================================================

function riskColor(score: number): string {
  if (score <= 30) return '#10b981';
  if (score <= 60) return '#f59e0b';
  return '#ef4444';
}

function riskLabel(score: number): string {
  if (score <= 30) return 'LOW';
  if (score <= 60) return 'MEDIUM';
  return 'HIGH';
}

/** Map the backend API response into the store's PreFilterResult shape */
function mapApiResponse(raw: Record<string, unknown>) {
  const data = (raw.data ?? raw) as Record<string, unknown>;
  const passed = Boolean(data.passed);
  const riskScore = typeof data.riskScore === 'number' ? Math.round(data.riskScore * 100) : 0;

  // Build checks list — the backend returns filterDetails with more granular names,
  // plus rejectionReasons / warnings. We map them into the 5 user-facing categories.
  const filterDetails = Array.isArray(data.filterDetails)
    ? (data.filterDetails as Array<{ filterName: string; passed: boolean; reason?: string }>)
    : [];

  const rejectionReasons: string[] = Array.isArray(data.rejectionReasons)
    ? (data.rejectionReasons as string[])
    : [];

  const warnings: string[] = Array.isArray(data.warnings)
    ? (data.warnings as string[])
    : [];

  // Map filter names to user-facing check categories
  const checkNameMap: Record<string, string> = {
    HARD_VETO_GLOBAL_KILL_SWITCH: 'Drawdown Budget',
    HARD_VETO_STRATEGY_PAUSE: 'Drawdown Budget',
    HARD_VETO_TOKEN_BLACKLIST: 'Concentration Limit',
    HARD_VETO_LIQUIDITY: 'Liquidity',
    HARD_VETO_SPREAD: 'Liquidity',
    PORTFOLIO_CONSTRAINTS: 'Concentration Limit',
    MARKET_REGIME_FILTER: 'Volatility Threshold',
    CORRELATION_CHECK: 'Correlation Limit',
    DATA_QUALITY_GATE: 'Volatility Threshold',
    VAR_BUDGET_CHECK: 'Drawdown Budget',
  };

  // Deduplicate by mapped check name, prefer failed result
  const checksMap = new Map<string, { passed: boolean; reason?: string }>();

  for (const fd of filterDetails) {
    const mapped = checkNameMap[fd.filterName] ?? fd.filterName;
    const existing = checksMap.get(mapped);
    if (!existing || !existing.passed) {
      // keep first non-passing or set if none yet
      if (!existing) {
        checksMap.set(mapped, { passed: fd.passed, reason: fd.reason });
      }
    } else if (!fd.passed) {
      checksMap.set(mapped, { passed: false, reason: fd.reason });
    }
  }

  // Ensure all 5 categories present
  const requiredChecks = [
    'Drawdown Budget',
    'Concentration Limit',
    'Correlation Limit',
    'Volatility Threshold',
    'Liquidity',
  ];
  for (const name of requiredChecks) {
    if (!checksMap.has(name)) {
      checksMap.set(name, { passed: true });
    }
  }

  const checks = requiredChecks.map((name) => ({
    name,
    ...(checksMap.get(name)!),
  }));

  // Build top-level reason if failed
  const reason = !passed
    ? rejectionReasons.length > 0
      ? rejectionReasons.join('; ')
      : warnings.length > 0
        ? warnings.join('; ')
        : undefined
    : undefined;

  return { passed, checks, riskScore, reason, warnings };
}

// ============================================================
// RISK SCORE GAUGE (half-circle SVG)
// ============================================================

function RiskScoreGauge({ score }: { score: number }) {
  const color = riskColor(score);
  const label = riskLabel(score);
  const radius = 54;
  const circumference = Math.PI * radius;
  const progress = (score / 100) * circumference;
  const dashOffset = circumference - progress;

  return (
    <div className="flex flex-col items-center">
      <div className="relative">
        <svg width="140" height="78" viewBox="0 0 140 78">
          {/* Background arc */}
          <path
            d="M 16 70 A 54 54 0 0 1 124 70"
            fill="none"
            stroke="#1e293b"
            strokeWidth="9"
            strokeLinecap="round"
          />
          {/* Progress arc */}
          <path
            d="M 16 70 A 54 54 0 0 1 124 70"
            fill="none"
            stroke={color}
            strokeWidth="9"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            style={{ transition: 'stroke-dashoffset 0.8s ease, stroke 0.3s ease' }}
          />
          {/* Tick marks */}
          <line x1="16" y1="70" x2="16" y2="64" stroke="#475569" strokeWidth="1" />
          <line x1="70" y1="16" x2="70" y2="10" stroke="#475569" strokeWidth="1" />
          <line x1="124" y1="70" x2="124" y2="64" stroke="#475569" strokeWidth="1" />
          <text x="14" y="78" fontSize="7" fill="#475569" fontFamily="monospace">0</text>
          <text x="66" y="8" fontSize="7" fill="#475569" fontFamily="monospace">50</text>
          <text x="118" y="78" fontSize="7" fill="#475569" fontFamily="monospace">100</text>
        </svg>
        <div className="absolute inset-0 flex items-end justify-center pb-0.5">
          <span className="text-xl font-mono font-bold" style={{ color }}>
            {score}
          </span>
        </div>
      </div>
      <Badge
        className="text-[8px] h-4 px-2 font-mono mt-1"
        style={{
          backgroundColor: `${color}20`,
          color,
          borderColor: `${color}40`,
        }}
      >
        {label} RISK
      </Badge>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function RiskPreFilterPanel() {
  // ---- Zustand Store ----
  const riskPreFilterResult = useCryptoStore((s) => s.riskPreFilterResult);
  const riskPreFilterLoading = useCryptoStore((s) => s.riskPreFilterLoading);
  const setRiskPreFilterResult = useCryptoStore((s) => s.setRiskPreFilterResult);
  const setRiskPreFilterLoading = useCryptoStore((s) => s.setRiskPreFilterLoading);
  const autoPreFilterEnabled = useCryptoStore((s) => s.autoPreFilterEnabled);
  const setAutoPreFilterEnabled = useCryptoStore((s) => s.setAutoPreFilterEnabled);

  // ---- Local Form State ----
  const [form, setForm] = useState<PreFilterFormState>({
    symbol: '',
    signalType: 'BUY',
    positionSizeUsd: 1000,
    confidence: 70,
  });
  const [showDetails, setShowDetails] = useState(true);

  const updateField = useCallback(<K extends keyof PreFilterFormState>(key: K, value: PreFilterFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  // ---- Run Pre-Filter ----
  const runPreFilter = useCallback(async () => {
    if (!form.symbol.trim()) {
      toast.error('Token symbol is required');
      return;
    }
    if (form.positionSizeUsd <= 0) {
      toast.error('Position size must be positive');
      return;
    }

    setRiskPreFilterLoading(true);
    setRiskPreFilterResult(null);

    try {
      const res = await fetch('/api/risk/pre-filter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tokenAddress: form.symbol.trim().toUpperCase(),
          chain: 'SOL',
          direction: form.signalType === 'BUY' ? 'LONG' : 'SHORT',
          confidence: form.confidence / 100,
          strategyName: 'Manual Pre-Filter',
          sizeUsd: form.positionSizeUsd,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || `API error ${res.status}`);
      }

      const raw = await res.json();
      const result = mapApiResponse(raw);
      setRiskPreFilterResult(result);

      if (result.passed) {
        toast.success('Signal passed pre-filter checks');
      } else {
        toast.error('Signal blocked by pre-filter');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error(`Pre-filter failed: ${message}`);
      setRiskPreFilterResult(null);
    } finally {
      setRiskPreFilterLoading(false);
    }
  }, [form, setRiskPreFilterLoading, setRiskPreFilterResult]);

  // ---- Reset ----
  const resetForm = useCallback(() => {
    setForm({ symbol: '', signalType: 'BUY', positionSizeUsd: 1000, confidence: 70 });
    setRiskPreFilterResult(null);
  }, [setRiskPreFilterResult]);

  // ---- Derived ----
  const hasResult = riskPreFilterResult !== null;
  const failedChecks = hasResult ? riskPreFilterResult!.checks.filter((c) => !c.passed) : [];

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
            <Shield className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Risk Pre-Filter</span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              Signal validation gate
            </span>
          </div>
        </div>
        <Badge
          className={`text-[8px] h-5 px-2 font-mono ${
            autoPreFilterEnabled
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
              : 'bg-[#1e293b] text-[#64748b] border-[#2d3748]'
          }`}
        >
          {autoPreFilterEnabled ? 'AUTO ON' : 'MANUAL'}
        </Badge>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- INPUT SECTION ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 px-4 pt-3">
              <CardTitle className="text-[10px] font-mono flex items-center gap-2 text-[#94a3b8] uppercase tracking-wider">
                <Zap className="h-3 w-3 text-amber-400" />
                Signal Input
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3 space-y-3">
              {/* Row 1: Symbol + Signal Type */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b]">Token Symbol</Label>
                  <Input
                    value={form.symbol}
                    onChange={(e) => updateField('symbol', e.target.value)}
                    placeholder="e.g. BTC, ETH, SOL"
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder:text-[#475569] uppercase"
                  />
                </div>
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b]">Signal Type</Label>
                  <Select
                    value={form.signalType}
                    onValueChange={(val) => updateField('signalType', val as 'BUY' | 'SELL')}
                  >
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      <SelectItem value="BUY" className="text-[10px] font-mono">
                        <span className="text-emerald-400 font-bold">BUY</span> — Long
                      </SelectItem>
                      <SelectItem value="SELL" className="text-[10px] font-mono">
                        <span className="text-red-400 font-bold">SELL</span> — Short
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Row 2: Position Size */}
              <div>
                <Label className="text-[9px] font-mono text-[#64748b]">Position Size (USD)</Label>
                <div className="relative">
                  <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[10px] font-mono text-[#475569]">$</span>
                  <Input
                    type="number"
                    min={1}
                    step={100}
                    value={form.positionSizeUsd}
                    onChange={(e) => updateField('positionSizeUsd', Math.max(0, Number(e.target.value)))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] pl-6"
                  />
                </div>
              </div>

              {/* Row 3: Confidence Slider */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <Label className="text-[9px] font-mono text-[#64748b]">Confidence Score</Label>
                  <span
                    className="text-[11px] font-mono font-bold"
                    style={{ color: riskColor(form.confidence) }}
                  >
                    {form.confidence}%
                  </span>
                </div>
                <Slider
                  value={[form.confidence]}
                  min={0}
                  max={100}
                  step={1}
                  onValueChange={([val]) => updateField('confidence', val)}
                  className="py-1"
                />
                <div className="flex justify-between mt-0.5">
                  <span className="text-[8px] font-mono text-[#475569]">0</span>
                  <span className="text-[8px] font-mono text-[#475569]">50</span>
                  <span className="text-[8px] font-mono text-[#475569]">100</span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex gap-2 pt-1">
                <Button
                  onClick={runPreFilter}
                  disabled={riskPreFilterLoading || !form.symbol.trim()}
                  className="flex-1 h-8 bg-cyan-600 hover:bg-cyan-700 text-white text-[10px] font-mono font-bold disabled:opacity-50"
                >
                  {riskPreFilterLoading ? (
                    <>
                      <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                      Filtering...
                    </>
                  ) : (
                    <>
                      <Play className="h-3 w-3 mr-1.5" />
                      Run Pre-Filter
                    </>
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={resetForm}
                  disabled={riskPreFilterLoading}
                  className="h-8 px-3 text-[10px] font-mono text-[#64748b] hover:text-[#94a3b8] border border-[#1e293b]"
                >
                  <RotateCcw className="h-3 w-3 mr-1" />
                  Reset
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* ---- AUTO-FILTER TOGGLE ---- */}
        <div className="px-3 py-2.5 border-b border-[#1e293b]">
          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardContent className="px-4 py-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className={`h-3.5 w-3.5 ${autoPreFilterEnabled ? 'text-emerald-400' : 'text-[#64748b]'}`} />
                  <div>
                    <span className="text-[10px] font-mono font-semibold text-[#e2e8f0]">
                      Auto Pre-Filter
                    </span>
                    <p className="text-[8px] font-mono text-[#475569] mt-0.5">
                      Automatically filter all signals before Decision Engine
                    </p>
                  </div>
                </div>
                <Switch
                  checked={autoPreFilterEnabled}
                  onCheckedChange={setAutoPreFilterEnabled}
                />
              </div>
              {autoPreFilterEnabled && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="mt-2 flex items-center gap-1.5 px-2 py-1.5 bg-emerald-500/5 border border-emerald-500/20 rounded"
                >
                  <ShieldCheck className="h-3 w-3 text-emerald-400 shrink-0" />
                  <span className="text-[8px] font-mono text-emerald-400">
                    All incoming signals will be pre-filtered automatically
                  </span>
                </motion.div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* ---- RESULTS SECTION ---- */}
        <AnimatePresence mode="wait">
          {hasResult && riskPreFilterResult && (
            <motion.div
              key="results"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.25 }}
              className="px-3 py-3"
            >
              {/* Status Banner */}
              <Card
                className={`border-2 ${
                  riskPreFilterResult.passed
                    ? 'border-emerald-500/50 bg-emerald-950/10'
                    : 'border-red-500/50 bg-red-950/10'
                }`}
              >
                <CardContent className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    {riskPreFilterResult.passed ? (
                      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30">
                        <ShieldCheck className="h-5 w-5 text-emerald-400" />
                      </div>
                    ) : (
                      <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-red-500/15 border border-red-500/30">
                        <ShieldX className="h-5 w-5 text-red-400" />
                      </div>
                    )}
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`text-lg font-mono font-bold ${
                            riskPreFilterResult.passed ? 'text-emerald-400' : 'text-red-400'
                          }`}
                        >
                          {riskPreFilterResult.passed ? 'PASS' : 'FAIL'}
                        </span>
                        <Badge
                          className={`text-[8px] h-4 px-2 font-mono ${
                            riskPreFilterResult.passed
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                              : 'bg-red-500/10 text-red-400 border-red-500/30'
                          }`}
                        >
                          {riskPreFilterResult.passed ? 'Signal Approved' : 'Signal Blocked'}
                        </Badge>
                      </div>
                      {riskPreFilterResult.reason && (
                        <p className="text-[9px] font-mono text-red-300/80 mt-0.5 leading-relaxed">
                          {riskPreFilterResult.reason}
                        </p>
                      )}
                    </div>
                    <RiskScoreGauge score={riskPreFilterResult.riskScore} />
                  </div>
                </CardContent>
              </Card>

              {/* Individual Checks */}
              <Card className="bg-[#0d1117] border-[#1e293b] mt-3">
                <CardHeader
                  className="pb-1 px-4 pt-3 cursor-pointer"
                  onClick={() => setShowDetails(!showDetails)}
                >
                  <CardTitle className="text-[10px] font-mono flex items-center gap-2 text-[#94a3b8] uppercase tracking-wider">
                    <Shield className="h-3 w-3 text-cyan-400" />
                    Check Details
                    <span className="text-[8px] font-mono text-[#475569] ml-1">
                      ({riskPreFilterResult.checks.filter((c) => c.passed).length}/{riskPreFilterResult.checks.length} passed)
                    </span>
                    <div className="ml-auto">
                      {showDetails ? (
                        <ChevronUp className="h-3.5 w-3.5 text-[#475569]" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 text-[#475569]" />
                      )}
                    </div>
                  </CardTitle>
                </CardHeader>
                <AnimatePresence>
                  {showDetails && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <CardContent className="px-4 pb-3 space-y-1.5">
                        {riskPreFilterResult.checks.map((check) => (
                          <CheckRow key={check.name} check={check} />
                        ))}
                      </CardContent>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>

              {/* Failed Check Reasons */}
              {failedChecks.length > 0 && (
                <Card className="bg-[#0d1117] border border-red-500/20 mt-3">
                  <CardContent className="px-4 py-3">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertTriangle className="h-3.5 w-3.5 text-red-400" />
                      <span className="text-[10px] font-mono font-semibold text-red-400 uppercase tracking-wider">
                        Failed Check Reasons
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {failedChecks.map((check) => (
                        <div
                          key={check.name}
                          className="flex items-start gap-2 p-2 rounded bg-red-950/20 border border-red-500/15"
                        >
                          <ShieldX className="h-3 w-3 text-red-400 shrink-0 mt-0.5" />
                          <div>
                            <span className="text-[9px] font-mono font-bold text-red-300">
                              {check.name}
                            </span>
                            {check.reason && (
                              <p className="text-[8px] font-mono text-red-300/60 mt-0.5 leading-relaxed">
                                {check.reason}
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Warnings (if passed but with warnings) */}
              {riskPreFilterResult.passed && riskPreFilterResult.reason && (
                <Card className="bg-[#0d1117] border border-amber-500/20 mt-3">
                  <CardContent className="px-4 py-3">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                      <span className="text-[10px] font-mono font-semibold text-amber-400 uppercase tracking-wider">
                        Warnings
                      </span>
                    </div>
                    <p className="text-[9px] font-mono text-amber-300/70 leading-relaxed">
                      {riskPreFilterResult.reason}
                    </p>
                  </CardContent>
                </Card>
              )}

              {/* Risk Score Progress */}
              <Card className="bg-[#0d1117] border-[#1e293b] mt-3">
                <CardContent className="px-4 py-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                      Risk Score
                    </span>
                    <span
                      className="text-[12px] font-mono font-bold"
                      style={{ color: riskColor(riskPreFilterResult.riskScore) }}
                    >
                      {riskPreFilterResult.riskScore}/100
                    </span>
                  </div>
                  <div className="h-2.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{
                        width: `${riskPreFilterResult.riskScore}%`,
                        backgroundColor: riskColor(riskPreFilterResult.riskScore),
                      }}
                    />
                  </div>
                  <div className="flex justify-between mt-1">
                    <span className="text-[7px] font-mono text-emerald-500/60">Safe</span>
                    <span className="text-[7px] font-mono text-amber-500/60">Caution</span>
                    <span className="text-[7px] font-mono text-red-500/60">Danger</span>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---- EMPTY STATE ---- */}
        {!hasResult && !riskPreFilterLoading && (
          <div className="flex-1 flex flex-col items-center justify-center px-6 py-12">
            <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-cyan-500/10 border border-cyan-500/20 mb-4">
              <Shield className="h-6 w-6 text-cyan-400" />
            </div>
            <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">Risk Pre-Filter</h3>
            <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md leading-relaxed">
              Enter a trading signal above to run through the pre-filter chain.
              Checks include drawdown budget, concentration limits, correlation limits,
              volatility thresholds, and liquidity requirements.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// CHECK ROW SUB-COMPONENT
// ============================================================

function CheckRow({ check }: { check: { name: string; passed: boolean; reason?: string } }) {
  const Icon = check.passed ? ShieldCheck : ShieldX;
  const iconColor = check.passed ? 'text-emerald-400' : 'text-red-400';
  const bgColor = check.passed ? 'bg-emerald-500/5' : 'bg-red-500/5';
  const borderColor = check.passed ? 'border-emerald-500/15' : 'border-red-500/15';

  return (
    <div className={`flex items-center gap-2.5 p-2 rounded border ${bgColor} ${borderColor}`}>
      <Icon className={`h-3.5 w-3.5 ${iconColor} shrink-0`} />
      <span className="text-[10px] font-mono text-[#e2e8f0] flex-1">{check.name}</span>
      <Badge
        className={`text-[8px] h-4 px-2 font-mono ${
          check.passed
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
            : 'bg-red-500/10 text-red-400 border-red-500/30'
        }`}
      >
        {check.passed ? 'PASS' : 'FAIL'}
      </Badge>
    </div>
  );
}

export default RiskPreFilterPanel;
