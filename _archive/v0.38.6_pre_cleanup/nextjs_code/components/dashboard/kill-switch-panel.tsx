'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle,
  Play,
  Pause,
  Shield,
  ShieldAlert,
  ShieldOff,
  Save,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Zap,
  History,
  Settings,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';

// ============================================================
// TYPES
// ============================================================

interface KillSwitchStateData {
  globalPause: boolean;
  globalPauseReason: string | null;
  globalPauseAt: string | null;
  strategyPauses: Record<string, { paused: boolean; reason: string; pausedAt: string }>;
  portfolioDDTriggered: boolean;
  strategyDDTriggered: string[];
  positionLossTriggered: string[];
  lastEvaluatedAt: string | null;
  lastTriggeredKillSwitches: Array<{
    level: string;
    reason: string;
    triggeredAt: string;
  }>;
}

interface RiskBudgetData {
  id?: string;
  maxPortfolioDrawdownPct: number;
  maxStrategyDrawdownPct: number;
  maxPositionLossPct: number;
  maxConcentrationPct: number;
  maxSectorPct: number;
  maxChainPct: number;
  maxCorrelatedPct: number;
  riskProfile: string;
  updatedAt?: string;
}

// ============================================================
// KILL SWITCH PANEL
// ============================================================

export default function KillSwitchPanel() {
  const queryClient = useQueryClient();
  const [budgetForm, setBudgetForm] = useState<RiskBudgetData>(() => ({
    maxPortfolioDrawdownPct: 20,
    maxStrategyDrawdownPct: 30,
    maxPositionLossPct: 50,
    maxConcentrationPct: 15,
    maxSectorPct: 30,
    maxChainPct: 50,
    maxCorrelatedPct: 40,
    riskProfile: 'MODERATE',
  }));
  const [budgetEdited, setBudgetEdited] = useState(false);
  const [showBudgetForm, setShowBudgetForm] = useState(false);
  const [pauseReason, setPauseReason] = useState('');
  const [strategyInputs, setStrategyInputs] = useState<Record<string, { id: string; action: 'PAUSE' | 'RESUME'; reason: string }>>({});

  // Fetch kill switch state
  const { data: killSwitchData, isLoading: ksLoading } = useQuery({
    queryKey: ['kill-switch-state'],
    queryFn: async () => {
      const res = await fetch('/api/kill-switch');
      if (!res.ok) throw new Error('Failed to fetch kill switch state');
      const json = await res.json();
      return json.data as KillSwitchStateData;
    },
    refetchInterval: 5000,
    staleTime: 3000,
  });

  // Fetch risk budget
  const { data: budgetData } = useQuery({
    queryKey: ['risk-budget'],
    queryFn: async () => {
      const res = await fetch('/api/risk-budget');
      if (!res.ok) throw new Error('Failed to fetch risk budget');
      const json = await res.json();
      return json.data as RiskBudgetData;
    },
    staleTime: 30000,
  });

  // Compute effective budget values: server data takes precedence until user edits
  const effectiveBudget: RiskBudgetData = budgetEdited
    ? budgetForm
    : (budgetData ?? budgetForm);

  // Track when user manually edits a field
  const handleBudgetFieldChange = useCallback(<K extends keyof RiskBudgetData>(key: K, value: RiskBudgetData[K]) => {
    setBudgetEdited(true);
    setBudgetForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Mutations
  const globalPauseMutation = useMutation({
    mutationFn: async (action: 'PAUSE' | 'RESUME') => {
      const res = await fetch('/api/kill-switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reason: action === 'PAUSE' ? pauseReason || 'Manual emergency pause' : undefined }),
      });
      if (!res.ok) throw new Error('Failed to toggle global pause');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kill-switch-state'] });
      setPauseReason('');
    },
  });

  const strategyPauseMutation = useMutation({
    mutationFn: async ({ strategyId, action, reason }: { strategyId: string; action: 'PAUSE' | 'RESUME'; reason: string }) => {
      const res = await fetch('/api/kill-switch/strategy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategyId, action, reason }),
      });
      if (!res.ok) throw new Error('Failed to toggle strategy pause');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kill-switch-state'] });
    },
  });

  const updateBudgetMutation = useMutation({
    mutationFn: async (data: RiskBudgetData) => {
      const res = await fetch('/api/risk-budget', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error('Failed to update risk budget');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['risk-budget'] });
      queryClient.invalidateQueries({ queryKey: ['kill-switch-state'] });
    },
  });

  const ks = killSwitchData;
  const isGloballyPaused = ks?.globalPause ?? false;
  const strategyPauses = ks?.strategyPauses ?? {};
  const pausedStrategyIds = Object.entries(strategyPauses).filter(([, v]) => v.paused);
  const triggeredHistory = ks?.lastTriggeredKillSwitches ?? [];

  return (
    <div className="flex-1 flex flex-col gap-3 p-3 overflow-y-auto">
      {/* HEADER */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isGloballyPaused || ks?.portfolioDDTriggered ? (
            <ShieldAlert className="h-5 w-5 text-red-500" />
          ) : (
            <Shield className="h-5 w-5 text-emerald-400" />
          )}
          <h2 className="text-sm font-bold text-[#f1f5f9] font-mono">Kill Switches & Risk Budget</h2>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['kill-switch-state', 'risk-budget'] })}
          className="h-6 px-2 text-[9px] text-[#64748b] hover:text-[#94a3b8]"
        >
          <RefreshCw className="h-3 w-3 mr-1" /> Refresh
        </Button>
      </div>

      {/* EMERGENCY GLOBAL PAUSE */}
      <Card className={`border-2 ${isGloballyPaused ? 'border-red-500/60 bg-red-950/20' : 'border-[#1e293b] bg-[#0d1117]'}`}>
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="text-xs font-mono flex items-center gap-2">
            <AlertTriangle className={`h-4 w-4 ${isGloballyPaused ? 'text-red-500' : 'text-amber-400'}`} />
            Emergency Global Pause
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-3">
          {isGloballyPaused && (
            <motion.div
              initial={{ opacity: 0, y: -5 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-red-900/30 border border-red-500/30 rounded-md p-2"
            >
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                <span className="text-[11px] font-mono text-red-400 font-bold">GLOBALLY PAUSED</span>
              </div>
              {ks?.globalPauseReason && (
                <p className="text-[10px] text-red-300/70 font-mono mt-1">Reason: {ks.globalPauseReason}</p>
              )}
              {ks?.globalPauseAt && (
                <p className="text-[9px] text-red-300/50 font-mono mt-0.5">
                  Since: {new Date(ks.globalPauseAt).toLocaleString()}
                </p>
              )}
            </motion.div>
          )}

          {!isGloballyPaused && (
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <Label className="text-[9px] font-mono text-[#64748b]">Reason (optional)</Label>
                <Input
                  value={pauseReason}
                  onChange={(e) => setPauseReason(e.target.value)}
                  placeholder="Emergency pause reason..."
                  className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                />
              </div>
              <Button
                onClick={() => globalPauseMutation.mutate('PAUSE')}
                disabled={globalPauseMutation.isPending}
                className="h-7 px-4 bg-red-600 hover:bg-red-700 text-white text-[10px] font-mono font-bold"
              >
                <Pause className="h-3 w-3 mr-1" /> EMERGENCY PAUSE
              </Button>
            </div>
          )}

          {isGloballyPaused && (
            <Button
              onClick={() => globalPauseMutation.mutate('RESUME')}
              disabled={globalPauseMutation.isPending}
              className="h-7 px-4 bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] font-mono font-bold w-full"
            >
              <Play className="h-3 w-3 mr-1" /> RESUME ALL TRADING
            </Button>
          )}
        </CardContent>
      </Card>

      {/* ACTIVE KILL SWITCHES STATUS */}
      <Card className="border border-[#1e293b] bg-[#0d1117]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="text-xs font-mono flex items-center gap-2">
            <Zap className="h-4 w-4 text-amber-400" />
            Active Kill Switch Status
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className={`flex items-center gap-2 p-2 rounded border ${
              ks?.portfolioDDTriggered ? 'border-red-500/40 bg-red-950/20' : 'border-[#1e293b] bg-[#0a0e17]'
            }`}>
              <div className={`w-2 h-2 rounded-full ${ks?.portfolioDDTriggered ? 'bg-red-500 animate-pulse' : 'bg-emerald-500'}`} />
              <span className="text-[10px] font-mono text-[#94a3b8]">Portfolio DD</span>
              <Badge variant="outline" className={`ml-auto text-[8px] px-1.5 py-0 h-4 ${
                ks?.portfolioDDTriggered
                  ? 'border-red-500/40 text-red-400'
                  : 'border-emerald-500/40 text-emerald-400'
              }`}>
                {ks?.portfolioDDTriggered ? 'TRIGGERED' : 'OK'}
              </Badge>
            </div>

            <div className={`flex items-center gap-2 p-2 rounded border ${
              (ks?.strategyDDTriggered?.length ?? 0) > 0 ? 'border-amber-500/40 bg-amber-950/20' : 'border-[#1e293b] bg-[#0a0e17]'
            }`}>
              <div className={`w-2 h-2 rounded-full ${(ks?.strategyDDTriggered?.length ?? 0) > 0 ? 'bg-amber-500' : 'bg-emerald-500'}`} />
              <span className="text-[10px] font-mono text-[#94a3b8]">Strategy DD</span>
              <Badge variant="outline" className={`ml-auto text-[8px] px-1.5 py-0 h-4 ${
                (ks?.strategyDDTriggered?.length ?? 0) > 0
                  ? 'border-amber-500/40 text-amber-400'
                  : 'border-emerald-500/40 text-emerald-400'
              }`}>
                {(ks?.strategyDDTriggered?.length ?? 0) > 0 ? `${ks!.strategyDDTriggered.length} TRIGGERED` : 'OK'}
              </Badge>
            </div>

            <div className={`flex items-center gap-2 p-2 rounded border ${
              (ks?.positionLossTriggered?.length ?? 0) > 0 ? 'border-red-500/40 bg-red-950/20' : 'border-[#1e293b] bg-[#0a0e17]'
            }`}>
              <div className={`w-2 h-2 rounded-full ${(ks?.positionLossTriggered?.length ?? 0) > 0 ? 'bg-red-500' : 'bg-emerald-500'}`} />
              <span className="text-[10px] font-mono text-[#94a3b8]">Position Loss</span>
              <Badge variant="outline" className={`ml-auto text-[8px] px-1.5 py-0 h-4 ${
                (ks?.positionLossTriggered?.length ?? 0) > 0
                  ? 'border-red-500/40 text-red-400'
                  : 'border-emerald-500/40 text-emerald-400'
              }`}>
                {(ks?.positionLossTriggered?.length ?? 0) > 0 ? `${ks!.positionLossTriggered.length} TRIGGERED` : 'OK'}
              </Badge>
            </div>

            <div className={`flex items-center gap-2 p-2 rounded border ${
              isGloballyPaused ? 'border-red-500/40 bg-red-950/20' : 'border-[#1e293b] bg-[#0a0e17]'
            }`}>
              <div className={`w-2 h-2 rounded-full ${isGloballyPaused ? 'bg-red-500 animate-pulse' : 'bg-emerald-500'}`} />
              <span className="text-[10px] font-mono text-[#94a3b8]">Global Pause</span>
              <Badge variant="outline" className={`ml-auto text-[8px] px-1.5 py-0 h-4 ${
                isGloballyPaused
                  ? 'border-red-500/40 text-red-400'
                  : 'border-emerald-500/40 text-emerald-400'
              }`}>
                {isGloballyPaused ? 'PAUSED' : 'OFF'}
              </Badge>
            </div>
          </div>

          {/* Paused strategies detail */}
          {pausedStrategyIds.length > 0 && (
            <div className="mt-2">
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Paused Strategies</span>
              <div className="mt-1 space-y-1">
                {pausedStrategyIds.map(([id, info]) => (
                  <div key={id} className="flex items-center gap-2 p-1.5 rounded bg-amber-950/20 border border-amber-500/20">
                    <ShieldOff className="h-3 w-3 text-amber-400" />
                    <span className="text-[10px] font-mono text-amber-300 truncate flex-1">{id}</span>
                    <span className="text-[9px] font-mono text-amber-400/60 truncate max-w-[200px]">{info.reason}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => strategyPauseMutation.mutate({ strategyId: id, action: 'RESUME', reason: 'Manual resume' })}
                      className="h-5 px-2 text-[8px] text-emerald-400 hover:text-emerald-300"
                    >
                      <Play className="h-2.5 w-2.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* PER-STRATEGY PAUSE CONTROL */}
      <Card className="border border-[#1e293b] bg-[#0d1117]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="text-xs font-mono flex items-center gap-2">
            <Shield className="h-4 w-4 text-cyan-400" />
            Strategy Pause Control
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-2">
          <p className="text-[9px] font-mono text-[#64748b]">
            Manually pause or resume individual strategies. Auto-paused strategies will resume when drawdown recovers.
          </p>
          <div className="flex gap-2 items-end">
            <div className="flex-1">
              <Label className="text-[9px] font-mono text-[#64748b]">Strategy ID</Label>
              <Input
                value={strategyInputs.manual?.id ?? ''}
                onChange={(e) =>
                  setStrategyInputs((prev) => ({
                    ...prev,
                    manual: { ...(prev.manual ?? {}), id: e.target.value, action: 'PAUSE', reason: prev.manual?.reason ?? '' },
                  }))
                }
                placeholder="e.g., Smart Entry Mirror"
                className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
              />
            </div>
            <div className="flex-1">
              <Label className="text-[9px] font-mono text-[#64748b]">Reason</Label>
              <Input
                value={strategyInputs.manual?.reason ?? ''}
                onChange={(e) =>
                  setStrategyInputs((prev) => ({
                    ...prev,
                    manual: { id: prev.manual?.id ?? '', action: 'PAUSE', reason: e.target.value },
                  }))
                }
                placeholder="Pause reason..."
                className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
              />
            </div>
            <Button
              onClick={() => {
                const inp = strategyInputs.manual;
                if (inp?.id) {
                  strategyPauseMutation.mutate({ strategyId: inp.id, action: 'PAUSE', reason: inp.reason || 'Manual pause' });
                }
              }}
              disabled={!strategyInputs.manual?.id || strategyPauseMutation.isPending}
              className="h-7 px-3 bg-amber-600 hover:bg-amber-700 text-white text-[10px] font-mono"
            >
              <Pause className="h-3 w-3 mr-1" /> Pause
            </Button>
            <Button
              onClick={() => {
                const inp = strategyInputs.manual;
                if (inp?.id) {
                  strategyPauseMutation.mutate({ strategyId: inp.id, action: 'RESUME', reason: 'Manual resume' });
                }
              }}
              disabled={!strategyInputs.manual?.id || strategyPauseMutation.isPending}
              className="h-7 px-3 bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] font-mono"
            >
              <Play className="h-3 w-3 mr-1" /> Resume
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* KILL SWITCH HISTORY */}
      <Card className="border border-[#1e293b] bg-[#0d1117]">
        <CardHeader className="pb-2 px-4 pt-3">
          <CardTitle className="text-xs font-mono flex items-center gap-2">
            <History className="h-4 w-4 text-[#64748b]" />
            Kill Switch History
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3">
          {triggeredHistory.length === 0 ? (
            <p className="text-[10px] font-mono text-[#475569] text-center py-3">No kill switches triggered yet</p>
          ) : (
            <ScrollArea className="max-h-40">
              <div className="space-y-1">
                {triggeredHistory.map((entry, i) => (
                  <div key={i} className="flex items-start gap-2 p-1.5 rounded bg-[#0a0e17] border border-[#1e293b]">
                    <Badge
                      variant="outline"
                      className={`text-[8px] px-1.5 py-0 h-4 shrink-0 ${
                        entry.level === 'PORTFOLIO'
                          ? 'border-red-500/40 text-red-400'
                          : entry.level === 'STRATEGY'
                          ? 'border-amber-500/40 text-amber-400'
                          : entry.level === 'POSITION'
                          ? 'border-orange-500/40 text-orange-400'
                          : 'border-[#475569] text-[#94a3b8]'
                      }`}
                    >
                      {entry.level}
                    </Badge>
                    <span className="text-[9px] font-mono text-[#94a3b8] flex-1">{entry.reason}</span>
                    <span className="text-[8px] font-mono text-[#475569] shrink-0">
                      {new Date(entry.triggeredAt).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      {/* RISK BUDGET CONFIG */}
      <Card className="border border-[#1e293b] bg-[#0d1117]">
        <CardHeader
          className="pb-2 px-4 pt-3 cursor-pointer"
          onClick={() => setShowBudgetForm(!showBudgetForm)}
        >
          <CardTitle className="text-xs font-mono flex items-center gap-2">
            <Settings className="h-4 w-4 text-[#64748b]" />
            Risk Budget Configuration
            <div className="ml-auto">
              {showBudgetForm ? (
                <ChevronUp className="h-3.5 w-3.5 text-[#475569]" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 text-[#475569]" />
              )}
            </div>
          </CardTitle>
        </CardHeader>
        <AnimatePresence>
          {showBudgetForm && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <CardContent className="px-4 pb-3 space-y-3">
                {/* Risk Profile */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b]">Risk Profile</Label>
                  <Select
                    value={effectiveBudget.riskProfile}
                    onValueChange={(val) => handleBudgetFieldChange('riskProfile', val)}
                  >
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      <SelectItem value="CONSERVATIVE" className="text-[10px] font-mono">Conservative</SelectItem>
                      <SelectItem value="MODERATE" className="text-[10px] font-mono">Moderate</SelectItem>
                      <SelectItem value="AGGRESSIVE" className="text-[10px] font-mono">Aggressive</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <Separator className="bg-[#1e293b]" />

                {/* Thresholds Grid */}
                <div className="grid grid-cols-2 gap-3">
                  <ThresholdInput
                    label="Max Portfolio DD %"
                    value={effectiveBudget.maxPortfolioDrawdownPct}
                    onChange={(v) => handleBudgetFieldChange('maxPortfolioDrawdownPct', v)}
                    color="red"
                  />
                  <ThresholdInput
                    label="Max Strategy DD %"
                    value={effectiveBudget.maxStrategyDrawdownPct}
                    onChange={(v) => handleBudgetFieldChange('maxStrategyDrawdownPct', v)}
                    color="amber"
                  />
                  <ThresholdInput
                    label="Max Position Loss %"
                    value={effectiveBudget.maxPositionLossPct}
                    onChange={(v) => handleBudgetFieldChange('maxPositionLossPct', v)}
                    color="orange"
                  />
                  <ThresholdInput
                    label="Max Concentration %"
                    value={effectiveBudget.maxConcentrationPct}
                    onChange={(v) => handleBudgetFieldChange('maxConcentrationPct', v)}
                    color="cyan"
                  />
                  <ThresholdInput
                    label="Max Sector %"
                    value={effectiveBudget.maxSectorPct}
                    onChange={(v) => handleBudgetFieldChange('maxSectorPct', v)}
                    color="cyan"
                  />
                  <ThresholdInput
                    label="Max Chain %"
                    value={effectiveBudget.maxChainPct}
                    onChange={(v) => handleBudgetFieldChange('maxChainPct', v)}
                    color="cyan"
                  />
                  <ThresholdInput
                    label="Max Correlated %"
                    value={effectiveBudget.maxCorrelatedPct}
                    onChange={(v) => handleBudgetFieldChange('maxCorrelatedPct', v)}
                    color="cyan"
                  />
                </div>

                {/* Save */}
                <div className="flex justify-end gap-2 pt-1">
                  <Button
                    onClick={() => {
                      setBudgetEdited(false);
                    }}
                    variant="ghost"
                    size="sm"
                    className="h-7 px-3 text-[10px] font-mono text-[#64748b] hover:text-[#94a3b8]"
                  >
                    Reset
                  </Button>
                  <Button
                    onClick={() => updateBudgetMutation.mutate(effectiveBudget)}
                    disabled={updateBudgetMutation.isPending}
                    className="h-7 px-4 bg-[#d4af37] hover:bg-[#c9a230] text-black text-[10px] font-mono font-bold"
                  >
                    <Save className="h-3 w-3 mr-1" /> Save Risk Budget
                  </Button>
                </div>
              </CardContent>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </div>
  );
}

// ============================================================
// THRESHOLD INPUT COMPONENT
// ============================================================

function ThresholdInput({
  label,
  value,
  onChange,
  color,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  color: string;
}) {
  const colorMap: Record<string, string> = {
    red: 'text-red-400',
    amber: 'text-amber-400',
    orange: 'text-orange-400',
    cyan: 'text-cyan-400',
    emerald: 'text-emerald-400',
  };

  return (
    <div>
      <Label className={`text-[9px] font-mono ${colorMap[color] ?? 'text-[#64748b]'}`}>{label}</Label>
      <Input
        type="number"
        min={1}
        max={100}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
      />
    </div>
  );
}
