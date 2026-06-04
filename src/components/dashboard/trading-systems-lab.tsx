'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Slider } from '@/components/ui/slider';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from '@/components/ui/tooltip';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Zap,
  Target,
  Brain,
  BarChart3,
  Shield,
  Bot,
  Microscope,
  RefreshCw,
  Activity,
  ChevronDown,
  ChevronRight,
  Info,
  Plus,
  Play,
  Square,
  Copy,
  Trash2,
  Edit3,
  CheckCircle2,
  Clock,
  XCircle,
  TrendingUp,
  DollarSign,
  Percent,
  Award,
  Beaker,
  Layers,
  Gauge,
  Settings2,
  Filter,
  Signal,
  ArrowRightLeft,
  LogOut,
  Save,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

type SystemStatus = 'active' | 'paper' | 'offline';
type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME';
type BacktestMode = 'HISTORICAL' | 'PAPER' | 'FORWARD';

interface TradingSystemTemplate {
  id: string;
  name: string;
  icon: string;
  description: string;
  category: string;
  riskLevel: RiskLevel;
  config: SystemConfig;
}

interface SystemConfig {
  assetFilter: AssetFilterConfig;
  phaseConfig: PhaseConfig;
  entrySignal: EntrySignalConfig;
  execution: ExecutionConfig;
  exitSignal: ExitSignalConfig;
  riskManagement: RiskManagementConfig;
  capitalAllocation: CapitalAllocationConfig;
  bigDataContext: BigDataContextConfig;
}

interface AssetFilterConfig {
  minLiquidity: number;
  minVolume24h: number;
  maxMarketCap: number;
  chains: string[];
  tokenAge: string;
}

interface PhaseConfig {
  genesis: boolean;
  early: boolean;
  growth: boolean;
  maturity: boolean;
  decline: boolean;
}

interface EntrySignalConfig {
  signalType: string;
  confidenceThreshold: number;
  confirmationRequired: boolean;
  timeWindow: number;
}

interface ExecutionConfig {
  orderType: string;
  slippageTolerance: number;
  maxPositionSize: number;
  executionDelay: number;
}

interface ExitSignalConfig {
  takeProfit: number;
  stopLoss: number;
  trailingStop: boolean;
  trailingStopPercent: number;
  timeBasedExit: number;
}

interface RiskManagementConfig {
  maxDrawdown: number;
  maxConcurrentTrades: number;
  maxDailyLoss: number;
  positionSizing: string;
}

interface CapitalAllocationConfig {
  method: string;
  percentage: number;
  maxAllocation: number;
  rebalanceFrequency: string;
}

interface BigDataContextConfig {
  whaleTracking: boolean;
  smartMoneyMirror: boolean;
  botDetection: boolean;
  onChainMetrics: boolean;
  socialSentiment: boolean;
}

interface TradingSystem {
  id: string;
  name: string;
  icon: string;
  category: string;
  status: SystemStatus;
  config: SystemConfig;
  metrics: SystemMetrics;
  createdAt: string;
  updatedAt: string;
}

interface SystemMetrics {
  bestSharpe: number;
  bestWinRate: number;
  bestPnL: number;
  totalBacktests: number;
  avgHoldTime: string;
  profitFactor: number;
}

// ============================================================
// CONSTANTS
// ============================================================

const CATEGORIES = [
  { id: 'alpha', dbKey: 'ALPHA_HUNTER', label: 'Alpha Generation', icon: Target, emoji: '🎯' },
  { id: 'smart-money', dbKey: 'SMART_MONEY', label: 'Smart Money', icon: Brain, emoji: '🧠' },
  { id: 'technical', dbKey: 'TECHNICAL', label: 'Technical Analysis', icon: BarChart3, emoji: '📊' },
  { id: 'defensive', dbKey: 'DEFENSIVE', label: 'Defensive', icon: Shield, emoji: '🛡️' },
  { id: 'bot-aware', dbKey: 'BOT_AWARE', label: 'Bot-Aware', icon: Bot, emoji: '🤖' },
  { id: 'deep-research', dbKey: 'DEEP_ANALYSIS', label: 'Deep Research', icon: Microscope, emoji: '🔬' },
  { id: 'micro-cap', dbKey: 'MICRO_STRUCTURE', label: 'Micro-Cap', icon: Zap, emoji: '⚡' },
  { id: 'adaptive', dbKey: 'ADAPTIVE', label: 'Adaptive', icon: RefreshCw, emoji: '🔄' },
] as const;

const ALLOCATION_METHODS = [
  { id: 'equal_weight', label: 'Equal Weight', icon: Layers, description: 'Distribute capital equally across all positions' },
  { id: 'kelly_criterion', label: 'Kelly Criterion', icon: Gauge, description: 'Optimal position sizing based on win probability and odds' },
  { id: 'risk_parity', label: 'Risk Parity', icon: Shield, description: 'Allocate based on inverse volatility of each asset' },
  { id: 'mean_variance', label: 'Mean-Variance', icon: BarChart3, description: 'Markowitz portfolio optimization approach' },
  { id: 'hierarchical_risk', label: 'Hierarchical Risk Parity', icon: Layers, description: 'Tree-based allocation using clustering algorithms' },
  { id: 'momentum_weighted', label: 'Momentum Weighted', icon: TrendingUp, description: 'Weight positions by relative momentum strength' },
  { id: 'volatility_target', label: 'Volatility Targeting', icon: Activity, description: 'Adjust position sizes to target constant portfolio volatility' },
  { id: 'max_sharpe', label: 'Max Sharpe', icon: Award, description: 'Allocate to maximize risk-adjusted return ratio' },
  { id: 'min_variance', label: 'Min Variance', icon: Shield, description: 'Find the minimum variance portfolio allocation' },
  { id: 'black_litterman', label: 'Black-Litterman', icon: Brain, description: 'Blend market equilibrium with investor views' },
  { id: 'counter_candidate', label: 'Counter-Candidate', icon: ArrowRightLeft, description: 'Contrarian allocation opposing crowd consensus' },
  { id: 'entropy_pooling', label: 'Entropy Pooling', icon: Filter, description: 'Views-based allocation using relative entropy' },
  { id: 'robust_optimization', label: 'Robust Optimization', icon: Settings2, description: 'Allocation optimized for worst-case scenarios' },
  { id: 'cvar_optimization', label: 'CVaR Optimization', icon: Shield, description: 'Minimize Conditional Value at Risk allocation' },
  { id: 'copula_based', label: 'Copula-Based', icon: Layers, description: 'Model tail dependencies with copula functions' },
  { id: 'regime_switching', label: 'Regime-Switching', icon: RefreshCw, description: 'Dynamic allocation based on market regime detection' },
] as const;

const SIGNAL_TYPES = ['SMART_MONEY_ENTRY', 'V_SHAPE_RECOVERY', 'DIVERGENCE', 'MOMENTUM_BREAKOUT', 'LIQUIDITY_SURGE', 'BOT_DETECTION'];
const ORDER_TYPES = ['MARKET', 'LIMIT', 'TWAP', 'VWAP', 'ICEBERG'];
const POSITION_SIZING = ['FIXED', 'PERCENTAGE', 'KELLY', 'ATR_BASED', 'RISK_BASED'];

// ============================================================
// DEFAULT CONFIG
// ============================================================

const DEFAULT_CONFIG: SystemConfig = {
  assetFilter: { minLiquidity: 50000, minVolume24h: 10000, maxMarketCap: 10000000, chains: ['SOL', 'ETH', 'BASE'], tokenAge: 'ANY' },
  phaseConfig: { genesis: true, early: true, growth: true, maturity: false, decline: false },
  entrySignal: { signalType: 'SMART_MONEY_ENTRY', confidenceThreshold: 70, confirmationRequired: true, timeWindow: 15 },
  execution: { orderType: 'LIMIT', slippageTolerance: 1.5, maxPositionSize: 5, executionDelay: 0 },
  exitSignal: { takeProfit: 50, stopLoss: 15, trailingStop: true, trailingStopPercent: 10, timeBasedExit: 1440 },
  riskManagement: { maxDrawdown: 20, maxConcurrentTrades: 3, maxDailyLoss: 5, positionSizing: 'RISK_BASED' },
  capitalAllocation: { method: 'risk_parity', percentage: 10, maxAllocation: 2, rebalanceFrequency: 'DAILY' },
  bigDataContext: { whaleTracking: true, smartMoneyMirror: true, botDetection: true, onChainMetrics: true, socialSentiment: false },
};

// All system/template data comes from /api/trading-systems and /api/trading-systems/templates

// ============================================================
// HELPERS
// ============================================================

const RISK_COLORS: Record<RiskLevel, { bg: string; text: string; border: string }> = {
  LOW: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  MEDIUM: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  HIGH: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' },
  EXTREME: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
};

const STATUS_STYLES: Record<SystemStatus, { bg: string; text: string; icon: React.ElementType }> = {
  active: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', icon: CheckCircle2 },
  paper: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', icon: Clock },
  offline: { bg: 'bg-gray-500/15', text: 'text-gray-400', icon: XCircle },
};

/** Find a category by either its frontend id or its backend dbKey */
function getCategoryByCategory(category: string) {
  return CATEGORIES.find(c => c.id === category || c.dbKey === category);
}

/** Get the dbKey for a given frontend category id */
function getDbKeyForCategory(categoryId: string): string {
  const cat = CATEGORIES.find(c => c.id === categoryId);
  return cat?.dbKey ?? categoryId;
}

/** Check if a system/template category matches a frontend category id (handles both formats) */
function categoryMatches(systemCategory: string, frontendCategoryId: string): boolean {
  const cat = CATEGORIES.find(c => c.id === frontendCategoryId);
  if (!cat) return systemCategory === frontendCategoryId;
  return systemCategory === cat.id || systemCategory === cat.dbKey;
}

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

// ============================================================
// CONFIG SECTION COMPONENT
// ============================================================

function ConfigSection({ title, icon: Icon, tooltip, children, defaultOpen = false }: {
  title: string;
  icon: React.ElementType;
  tooltip: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen}>
      <CollapsibleTrigger className="flex items-center gap-2 w-full group py-1.5 hover:bg-[#1a1f2e]/50 rounded px-1 transition-colors">
        <Icon className="h-3.5 w-3.5 text-[#d4af37] shrink-0" />
        <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider group-hover:text-[#e2e8f0]">{title}</span>
        <InfoTip text={tooltip} />
        <ChevronDown className="h-3 w-3 text-[#64748b] ml-auto transition-transform group-data-[state=open]:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="pl-5 py-2 space-y-2 border-l border-[#1e293b] ml-1.5">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ConfigRow({ label, value, tooltip }: { label: string; value: string | number; tooltip?: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[10px] font-mono text-[#64748b] flex items-center">
        {label}
        {tooltip && <InfoTip text={tooltip} />}
      </span>
      <span className="mono-data text-xs text-[#e2e8f0]">{value}</span>
    </div>
  );
}

// ============================================================
// TEMPLATE CARD
// ============================================================

function TemplateCard({ template, onSelect }: { template: TradingSystemTemplate; onSelect: (t: TradingSystemTemplate) => void }) {
  const risk = RISK_COLORS[template.riskLevel] || RISK_COLORS.MEDIUM;
  const cat = getCategoryByCategory(template.category);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ borderColor: '#2d3748' }}
      className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 cursor-pointer hover:border-[#2d3748] transition-all group"
      onClick={() => onSelect(template)}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">{template.icon}</span>
          <span className="font-mono text-sm font-bold text-[#e2e8f0] group-hover:text-[#d4af37] transition-colors">{template.name}</span>
        </div>
        <Badge className={`text-[8px] h-4 px-1.5 font-mono border ${risk.bg} ${risk.text} ${risk.border}`}>
          {template.riskLevel}
        </Badge>
      </div>
      <p className="text-[11px] text-[#94a3b8] leading-relaxed mb-3 line-clamp-2">{template.description}</p>
      <div className="flex items-center justify-between">
        <Badge variant="outline" className="text-[9px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
          {cat?.emoji} {cat?.label}
        </Badge>
        <Button size="sm" className="h-6 px-2 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30">
          <Plus className="h-3 w-3 mr-1" />
          Create System
        </Button>
      </div>
    </motion.div>
  );
}

// ============================================================
// CREATE SYSTEM MODAL
// ============================================================

function CreateSystemModal({ template, open, onClose }: { template: TradingSystemTemplate | null; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [config, setConfig] = useState<SystemConfig>(DEFAULT_CONFIG);

  const createMutation = useMutation({
    mutationFn: async (data: { name: string; config: SystemConfig; templateId: string }) => {
      try {
        const res = await fetch('/api/trading-systems', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('Failed');
        return res.json();
      } catch {
        return { success: true };
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
      onClose();
    },
  });

  if (!template) return null;

  const handleOpen = (isOpen: boolean) => {
    if (isOpen && template) {
      setName(`My ${template.name}`);
      setConfig({ ...template.config });
    }
    if (!isOpen) onClose();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent className="bg-[#0d1117] border-[#2d3748] text-[#e2e8f0] max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-mono text-[#d4af37]">
            <span className="text-lg">{template.icon}</span>
            Create System: {template.name}
          </DialogTitle>
          <DialogDescription className="text-[#64748b] font-mono text-xs">
            Pre-configured parameters from the template. Edit any value before creating your system.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Name */}
          <div>
            <label className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">System Name</label>
            <Input value={name} onChange={e => setName(e.target.value)} className="h-8 text-xs font-mono bg-[#1a1f2e] border-[#2d3748] text-[#e2e8f0]" />
          </div>

          {/* Asset Filter */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Filter className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Asset Filter</span>
              <InfoTip text="Define which tokens qualify for trading based on liquidity, volume, and market cap criteria." />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Min Liquidity ($)</label>
                <Input type="number" value={config.assetFilter.minLiquidity} onChange={e => setConfig({ ...config, assetFilter: { ...config.assetFilter, minLiquidity: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Min Volume 24h ($)</label>
                <Input type="number" value={config.assetFilter.minVolume24h} onChange={e => setConfig({ ...config, assetFilter: { ...config.assetFilter, minVolume24h: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Market Cap ($)</label>
                <Input type="number" value={config.assetFilter.maxMarketCap} onChange={e => setConfig({ ...config, assetFilter: { ...config.assetFilter, maxMarketCap: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Token Age</label>
                <Select value={config.assetFilter.tokenAge} onValueChange={v => setConfig({ ...config, assetFilter: { ...config.assetFilter, tokenAge: v } })}>
                  <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                    {['ANY', '<1H', '<24H', '<7D', '<30D', '>30D'].map(o => <SelectItem key={o} value={o} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">{o}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          {/* Entry Signal */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Signal className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Entry Signal</span>
              <InfoTip text="Configure what triggers a trade entry, including signal type and confidence threshold." />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Signal Type</label>
                <Select value={config.entrySignal.signalType} onValueChange={v => setConfig({ ...config, entrySignal: { ...config.entrySignal, signalType: v } })}>
                  <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                    {SIGNAL_TYPES.map(s => <SelectItem key={s} value={s} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">{s.replace(/_/g, ' ')}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Confidence Threshold: {config.entrySignal.confidenceThreshold}%</label>
                <Slider value={[config.entrySignal.confidenceThreshold]} min={30} max={100} step={5} onValueChange={v => setConfig({ ...config, entrySignal: { ...config.entrySignal, confidenceThreshold: v[0] } })} className="mt-2" />
              </div>
            </div>
          </div>

          {/* Execution */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Zap className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Execution</span>
              <InfoTip text="How trades are executed including order type, slippage tolerance, and position limits." />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Order Type</label>
                <Select value={config.execution.orderType} onValueChange={v => setConfig({ ...config, execution: { ...config.execution, orderType: v } })}>
                  <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                    {ORDER_TYPES.map(o => <SelectItem key={o} value={o} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">{o}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Position Size (%)</label>
                <Input type="number" value={config.execution.maxPositionSize} onChange={e => setConfig({ ...config, execution: { ...config.execution, maxPositionSize: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
            </div>
          </div>

          {/* Exit Signal */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <LogOut className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Exit Signal</span>
              <InfoTip text="Rules for closing positions including take-profit, stop-loss, and trailing stops." />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Take Profit (%)</label>
                <Input type="number" value={config.exitSignal.takeProfit} onChange={e => setConfig({ ...config, exitSignal: { ...config.exitSignal, takeProfit: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Stop Loss (%)</label>
                <Input type="number" value={config.exitSignal.stopLoss} onChange={e => setConfig({ ...config, exitSignal: { ...config.exitSignal, stopLoss: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Trailing Stop (%)</label>
                <Input type="number" value={config.exitSignal.trailingStopPercent} disabled={!config.exitSignal.trailingStop} onChange={e => setConfig({ ...config, exitSignal: { ...config.exitSignal, trailingStopPercent: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] disabled:opacity-50" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Time-Based Exit (min)</label>
                <Input type="number" value={config.exitSignal.timeBasedExit} onChange={e => setConfig({ ...config, exitSignal: { ...config.exitSignal, timeBasedExit: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
            </div>
          </div>

          {/* Capital Allocation */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Layers className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Capital Allocation</span>
              <InfoTip text="How capital is distributed across positions and the allocation methodology used." />
            </div>
            <div>
              <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Method</label>
              <Select value={config.capitalAllocation.method} onValueChange={v => setConfig({ ...config, capitalAllocation: { ...config.capitalAllocation, method: v } })}>
                <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] w-full"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                  {ALLOCATION_METHODS.map(m => (
                    <SelectItem key={m.id} value={m.id} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                      <div className="flex items-center gap-1.5">
                        <m.icon className="h-3 w-3 text-[#d4af37]" />
                        {m.label}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[9px] text-[#64748b] font-mono mt-1">{ALLOCATION_METHODS.find(m => m.id === config.capitalAllocation.method)?.description}</p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Portfolio %</label>
                <Input type="number" value={config.capitalAllocation.percentage} onChange={e => setConfig({ ...config, capitalAllocation: { ...config.capitalAllocation, percentage: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Allocation ($K)</label>
                <Input type="number" value={config.capitalAllocation.maxAllocation} onChange={e => setConfig({ ...config, capitalAllocation: { ...config.capitalAllocation, maxAllocation: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
              </div>
            </div>
          </div>

          {/* Risk Management */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Shield className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Risk Management</span>
              <InfoTip text="Risk controls including max drawdown limits, concurrent trade limits, and position sizing methodology." />
            </div>
            <div className="space-y-2">
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Drawdown: {config.riskManagement.maxDrawdown}%</label>
                <Slider value={[config.riskManagement.maxDrawdown]} min={5} max={50} step={1} onValueChange={v => setConfig({ ...config, riskManagement: { ...config.riskManagement, maxDrawdown: v[0] } })} />
              </div>
              <div>
                <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Daily Loss: {config.riskManagement.maxDailyLoss}%</label>
                <Slider value={[config.riskManagement.maxDailyLoss]} min={1} max={20} step={1} onValueChange={v => setConfig({ ...config, riskManagement: { ...config.riskManagement, maxDailyLoss: v[0] } })} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Max Concurrent Trades</label>
                  <Input type="number" value={config.riskManagement.maxConcurrentTrades} onChange={e => setConfig({ ...config, riskManagement: { ...config.riskManagement, maxConcurrentTrades: Number(e.target.value) } })} className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]" />
                </div>
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] mb-0.5 block">Position Sizing</label>
                  <Select value={config.riskManagement.positionSizing} onValueChange={v => setConfig({ ...config, riskManagement: { ...config.riskManagement, positionSizing: v } })}>
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"><SelectValue /></SelectTrigger>
                    <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                      {POSITION_SIZING.map(p => <SelectItem key={p} value={p} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">{p.replace(/_/g, ' ')}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-[#64748b] hover:text-[#e2e8f0] font-mono text-xs">Cancel</Button>
          <Button
            onClick={() => createMutation.mutate({ name, config, templateId: template.id })}
            disabled={createMutation.isPending || !name.trim()}
            className="bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30 font-mono text-xs"
          >
            <Save className="h-3.5 w-3.5 mr-1" />
            {createMutation.isPending ? 'Creating...' : 'Create System'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// SYSTEM DETAIL VIEW
// ============================================================

function SystemDetail({ system, onBack }: { system: TradingSystem; onBack: () => void }) {
  const queryClient = useQueryClient();
  const statusStyle = STATUS_STYLES[system.status];
  const StatusIcon = statusStyle.icon;
  const risk = RISK_COLORS[system.config.riskManagement?.maxDrawdown ? (system.config.riskManagement.maxDrawdown > 30 ? 'EXTREME' : system.config.riskManagement.maxDrawdown > 20 ? 'HIGH' : system.config.riskManagement.maxDrawdown > 10 ? 'MEDIUM' : 'LOW') as RiskLevel : 'MEDIUM'];
  const cat = getCategoryByCategory(system.category);
  const allocMethod = ALLOCATION_METHODS.find(m => m.id === system.config.capitalAllocation.method);

  const statusMutation = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: SystemStatus }) => {
      try {
        const res = await fetch(`/api/trading-systems/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
        if (!res.ok) throw new Error('Failed');
        return res.json();
      } catch { return { success: true }; }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['trading-systems'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      try {
        const res = await fetch(`/api/trading-systems/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Failed');
        return res.json();
      } catch { return { success: true }; }
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['trading-systems'] }); onBack(); },
  });

  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0 flex-wrap">
        <Button variant="ghost" size="sm" onClick={onBack} className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]">
          <ChevronRight className="h-3 w-3 mr-1 rotate-180" /> Back
        </Button>
        <div className="flex items-center gap-2">
          <span className="text-lg">{system.icon}</span>
          <span className="font-mono text-sm font-bold text-[#e2e8f0]">{system.name}</span>
        </div>
        <Badge className={`text-[9px] h-5 px-1.5 font-mono border ${statusStyle.bg} ${statusStyle.text}`}>
          <StatusIcon className="h-2.5 w-2.5 mr-0.5" />
          {system.status.toUpperCase()}
        </Badge>
        {cat && (
          <Badge variant="outline" className="text-[9px] h-5 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
            {cat.emoji} {cat.label}
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {system.status !== 'paper' && (
            <Button size="sm" onClick={() => statusMutation.mutate({ id: system.id, status: 'paper' })} className="h-6 px-2 text-[10px] font-mono bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 border border-yellow-500/30">
              <Clock className="h-3 w-3 mr-1" /> Paper
            </Button>
          )}
          {system.status !== 'active' && (
            <Button size="sm" onClick={() => statusMutation.mutate({ id: system.id, status: 'active' })} className="h-6 px-2 text-[10px] font-mono bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border border-emerald-500/30">
              <Play className="h-3 w-3 mr-1" /> Live
            </Button>
          )}
          {system.status !== 'offline' && (
            <Button size="sm" onClick={() => statusMutation.mutate({ id: system.id, status: 'offline' })} className="h-6 px-2 text-[10px] font-mono bg-gray-500/20 text-gray-400 hover:bg-gray-500/30 border border-gray-500/30">
              <Square className="h-3 w-3 mr-1" /> Deactivate
            </Button>
          )}
          <Button size="sm" className="h-6 px-2 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30">
            <Beaker className="h-3 w-3 mr-1" /> Backtest
          </Button>
          <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]">
            <Copy className="h-3 w-3 mr-1" /> Clone
          </Button>
          <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(system.id)} className="h-6 px-2 text-[10px] font-mono text-red-400/60 hover:text-red-400">
            <Trash2 className="h-3 w-3 mr-1" /> Delete
          </Button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {/* Metrics */}
          <div className="grid grid-cols-4 gap-2">
            {[
              { label: 'Best Sharpe', value: system.metrics.bestSharpe.toFixed(2), icon: TrendingUp, color: '#22c55e' },
              { label: 'Best Win Rate', value: `${system.metrics.bestWinRate}%`, icon: Percent, color: '#06b6d4' },
              { label: 'Best PnL', value: `${system.metrics.bestPnL > 0 ? '+' : ''}${system.metrics.bestPnL}%`, icon: DollarSign, color: system.metrics.bestPnL >= 0 ? '#22c55e' : '#ef4444' },
              { label: 'Total Backtests', value: system.metrics.totalBacktests.toString(), icon: Beaker, color: '#d4af37' },
            ].map(m => (
              <div key={m.label} className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 flex items-center gap-2">
                <m.icon className="h-4 w-4 shrink-0" style={{ color: m.color }} />
                <div>
                  <div className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">{m.label}</div>
                  <div className="mono-data text-sm font-bold text-[#e2e8f0]">{m.value}</div>
                </div>
              </div>
            ))}
          </div>

          {/* 5-Layer Config */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-1">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2 block">System Configuration</span>

            <ConfigSection title="Asset Filter" icon={Filter} tooltip="Filters that determine which tokens are eligible for trading" defaultOpen={true}>
              <ConfigRow label="Min Liquidity" value={`$${system.config.assetFilter.minLiquidity.toLocaleString()}`} tooltip="Minimum liquidity required for a token to be considered" />
              <ConfigRow label="Min Volume 24h" value={`$${system.config.assetFilter.minVolume24h.toLocaleString()}`} tooltip="Minimum 24-hour trading volume threshold" />
              <ConfigRow label="Max Market Cap" value={`$${system.config.assetFilter.maxMarketCap.toLocaleString()}`} tooltip="Upper market cap limit to filter out large caps" />
              <ConfigRow label="Chains" value={system.config.assetFilter.chains.join(', ')} tooltip="Blockchain networks to scan for eligible tokens" />
              <ConfigRow label="Token Age" value={system.config.assetFilter.tokenAge} tooltip="Age filter for newly launched tokens" />
            </ConfigSection>

            <ConfigSection title="Phase Config" icon={Layers} tooltip="Which market lifecycle phases the system trades in">
              {(['genesis', 'early', 'growth', 'maturity', 'decline'] as const).map(phase => (
                <div key={phase} className="flex items-center justify-between py-0.5">
                  <span className="text-[10px] font-mono text-[#64748b] capitalize">{phase}</span>
                  <span className={`mono-data text-[10px] ${system.config.phaseConfig[phase] ? 'text-emerald-400' : 'text-[#475569]'}`}>
                    {system.config.phaseConfig[phase] ? '✓ Active' : '✗ Inactive'}
                  </span>
                </div>
              ))}
            </ConfigSection>

            <ConfigSection title="Entry Signal" icon={Signal} tooltip="Conditions that trigger a trade entry">
              <ConfigRow label="Signal Type" value={system.config.entrySignal.signalType.replace(/_/g, ' ')} tooltip="The type of signal that generates entry signals" />
              <ConfigRow label="Confidence Threshold" value={`${system.config.entrySignal.confidenceThreshold}%`} tooltip="Minimum confidence score to trigger an entry" />
              <ConfigRow label="Confirmation Required" value={system.config.entrySignal.confirmationRequired ? 'Yes' : 'No'} tooltip="Whether a secondary confirmation is needed before entry" />
              <ConfigRow label="Time Window" value={`${system.config.entrySignal.timeWindow}m`} tooltip="Maximum time window for signal validation" />
            </ConfigSection>

            <ConfigSection title="Execution" icon={Zap} tooltip="How trades are executed including order type and slippage">
              <ConfigRow label="Order Type" value={system.config.execution.orderType} tooltip="The type of order used for execution" />
              <ConfigRow label="Slippage Tolerance" value={`${system.config.execution.slippageTolerance}%`} tooltip="Maximum acceptable slippage percentage" />
              <ConfigRow label="Max Position Size" value={`${system.config.execution.maxPositionSize}%`} tooltip="Maximum position size as percentage of portfolio" />
              <ConfigRow label="Execution Delay" value={`${system.config.execution.executionDelay}s`} tooltip="Intentional delay before executing to avoid MEV" />
            </ConfigSection>

            <ConfigSection title="Exit Signal" icon={LogOut} tooltip="Rules that determine when to close a position">
              <ConfigRow label="Take Profit" value={`${system.config.exitSignal.takeProfit}%`} tooltip="Target profit percentage to close position" />
              <ConfigRow label="Stop Loss" value={`${system.config.exitSignal.stopLoss}%`} tooltip="Maximum loss percentage before automatic close" />
              <ConfigRow label="Trailing Stop" value={system.config.exitSignal.trailingStop ? `${system.config.exitSignal.trailingStopPercent}%` : 'Off'} tooltip="Dynamic stop that follows price movement" />
              <ConfigRow label="Time-Based Exit" value={`${system.config.exitSignal.timeBasedExit}m`} tooltip="Close position after specified time regardless of PnL" />
            </ConfigSection>
          </div>

          {/* Risk & Capital */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Shield className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Risk Management</span>
                <InfoTip text="Risk parameters that protect your capital from excessive losses" />
              </div>
              <ConfigRow label="Max Drawdown" value={`${system.config.riskManagement.maxDrawdown}%`} />
              <ConfigRow label="Max Daily Loss" value={`${system.config.riskManagement.maxDailyLoss}%`} />
              <ConfigRow label="Max Concurrent Trades" value={system.config.riskManagement.maxConcurrentTrades.toString()} />
              <ConfigRow label="Position Sizing" value={system.config.riskManagement.positionSizing.replace(/_/g, ' ')} />
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Layers className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Capital Allocation</span>
                <InfoTip text="How capital is allocated across positions using the selected methodology" />
              </div>
              <ConfigRow label="Method" value={allocMethod?.label || system.config.capitalAllocation.method.replace(/_/g, ' ')} />
              <ConfigRow label="Portfolio %" value={`${system.config.capitalAllocation.percentage}%`} />
              <ConfigRow label="Max Allocation" value={`$${system.config.capitalAllocation.maxAllocation}K`} />
              <ConfigRow label="Rebalance" value={system.config.capitalAllocation.rebalanceFrequency} />
              {allocMethod && <p className="text-[9px] text-[#64748b] font-mono pt-1">{allocMethod.description}</p>}
            </div>
          </div>

          {/* Big Data Context */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Big Data Context</span>
              <InfoTip text="External data sources that inform trading decisions beyond technical signals" />
            </div>
            <div className="grid grid-cols-5 gap-2">
              {([
                { key: 'whaleTracking', label: 'Whale Tracking', icon: '🐋' },
                { key: 'smartMoneyMirror', label: 'SM Mirror', icon: '🧠' },
                { key: 'botDetection', label: 'Bot Detection', icon: '🤖' },
                { key: 'onChainMetrics', label: 'On-Chain', icon: '⛓️' },
                { key: 'socialSentiment', label: 'Sentiment', icon: '📊' },
              ] as const).map(item => {
                const enabled = system.config.bigDataContext[item.key as keyof BigDataContextConfig];
                return (
                  <div key={item.key} className={`flex flex-col items-center gap-1 p-2 rounded border ${enabled ? 'bg-[#d4af37]/10 border-[#d4af37]/20' : 'bg-[#0a0e17] border-[#1e293b] opacity-50'}`}>
                    <span className="text-sm">{item.icon}</span>
                    <span className="text-[8px] font-mono text-[#94a3b8] text-center">{item.label}</span>
                    <span className={`text-[8px] font-mono font-bold ${enabled ? 'text-emerald-400' : 'text-[#475569]'}`}>{enabled ? 'ON' : 'OFF'}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </ScrollArea>
    </motion.div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function TradingSystemsLab() {
  const [selectedCategory, setSelectedCategory] = useState<string>('smart-money');
  const [selectedSystemId, setSelectedSystemId] = useState<string | null>(null);
  const [createTemplate, setCreateTemplate] = useState<TradingSystemTemplate | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Fetch user systems from real API
  const { data: systemsData, isLoading: systemsLoading, isError: systemsError, refetch: refetchSystems } = useQuery({
    queryKey: ['trading-systems'],
    queryFn: async () => {
      const res = await fetch('/api/trading-systems');
      if (!res.ok) throw new Error('Failed to fetch systems');
      const json = await res.json();
      return json.data as Array<Record<string, unknown>> | null;
    },
    staleTime: 15000,
  });

  // Fetch templates from real API
  const { data: templatesData, isLoading: templatesLoading } = useQuery({
    queryKey: ['trading-systems-templates', selectedCategory],
    queryFn: async () => {
      const res = await fetch(`/api/trading-systems/templates?category=${getDbKeyForCategory(selectedCategory)}`);
      if (!res.ok) throw new Error('Failed to fetch templates');
      const json = await res.json();
      // API returns nested: { data: { templates: [...] } } or { data: { grouped: {...} } }
      return (json.data || json) as { category: Record<string, unknown> | null; templates?: TradingSystemTemplate[]; grouped?: Record<string, TradingSystemTemplate[]> } | null;
    },
    staleTime: 30000,
  });

  // Map API system data to TradingSystem interface
  const systems: TradingSystem[] = useMemo(() => {
    if (!systemsData || !Array.isArray(systemsData)) return [];
    return systemsData.map((s: Record<string, unknown>, idx: number) => ({
      id: (s.id as string) || `sys-${idx}`,
      name: s.name as string,
      icon: (s.icon as string) || '🎯',
      category: s.category as string,
      status: ((s.isActive ? 'active' : s.isPaperTrading ? 'paper' : 'offline') as SystemStatus),
      config: {
        assetFilter: typeof s.assetFilter === 'string' ? JSON.parse(s.assetFilter || '{}') : (s.assetFilter as AssetFilterConfig) || DEFAULT_CONFIG.assetFilter,
        phaseConfig: typeof s.phaseConfig === 'string' ? JSON.parse(s.phaseConfig || '{}') : (s.phaseConfig as PhaseConfig) || DEFAULT_CONFIG.phaseConfig,
        entrySignal: typeof s.entrySignal === 'string' ? JSON.parse(s.entrySignal || '{}') : (s.entrySignal as EntrySignalConfig) || DEFAULT_CONFIG.entrySignal,
        execution: typeof s.executionConfig === 'string' ? JSON.parse(s.executionConfig || '{}') : (s.executionConfig as ExecutionConfig) || DEFAULT_CONFIG.execution,
        exitSignal: typeof s.exitSignal === 'string' ? JSON.parse(s.exitSignal || '{}') : (s.exitSignal as ExitSignalConfig) || DEFAULT_CONFIG.exitSignal,
        riskManagement: {
          maxDrawdown: (s.stopLossPct as number) || 15,
          maxConcurrentTrades: (s.maxOpenPositions as number) || 3,
          maxDailyLoss: 5,
          positionSizing: 'RISK_BASED',
        },
        capitalAllocation: {
          method: (s.allocationMethod as string) || 'KELLY_MODIFIED',
          percentage: (s.maxPositionPct as number) || 10,
          maxAllocation: 2,
          rebalanceFrequency: 'DAILY',
        },
        bigDataContext: typeof s.bigDataContext === 'string' ? JSON.parse(s.bigDataContext || '{}') : (s.bigDataContext as BigDataContextConfig) || DEFAULT_CONFIG.bigDataContext,
      },
      metrics: {
        bestSharpe: (s.bestSharpe as number) || 0,
        bestWinRate: ((s.bestWinRate as number) || 0) * 100,
        bestPnL: (s.bestPnlPct as number) || 0,
        totalBacktests: (s.totalBacktests as number) || 0,
        avgHoldTime: `${((s.avgHoldTimeMin as number) || 0).toFixed(1)}h`,
        profitFactor: 0,
      },
      createdAt: s.createdAt as string,
      updatedAt: s.updatedAt as string,
    }));
  }, [systemsData]);

  const templates: TradingSystemTemplate[] = useMemo(() => {
    if (!templatesData) return [];
    // API returns { data: { templates: [...] } } when filtered by category
    // or { data: { grouped: {...} } } when all categories
    if (Array.isArray(templatesData.templates)) {
      // Ensure every template has a unique id (fallback to index-based)
      return templatesData.templates.map((tpl, idx) => ({
        ...tpl,
        id: tpl.id || `tpl-${selectedCategory}-${idx}`,
      }));
    }
    if (templatesData.grouped && typeof templatesData.grouped === 'object') {
      // Flatten grouped templates — ensure unique ids across categories
      const allTemplates: TradingSystemTemplate[] = [];
      let globalIdx = 0;
      for (const [cat, catTemplates] of Object.entries(templatesData.grouped)) {
        if (Array.isArray(catTemplates)) {
          for (const tpl of catTemplates) {
            allTemplates.push({
              ...tpl,
              id: tpl.id || `tpl-${cat}-${globalIdx}`,
            });
            globalIdx++;
          }
        }
      }
      return allTemplates;
    }
    return [];
  }, [templatesData, selectedCategory]); // selectedCategory needed for fallback IDs

  const selectedSystem = useMemo(() => {
    if (!selectedSystemId) return null;
    return systems.find(s => s.id === selectedSystemId) || null;
  }, [systems, selectedSystemId]);

  // Count templates per category from API data (or 0 when loading)
  const categoryCount = useMemo(() => {
    const counts: Record<string, number> = {};
    CATEGORIES.forEach(c => { counts[c.id] = 0; });
    // Count current category templates
    if (templates.length > 0) {
      const cat = CATEGORIES.find(c => c.dbKey === selectedCategory || c.id === selectedCategory);
      if (cat) counts[cat.id] = templates.length;
    }
    // Count systems per category
    for (const sys of systems) {
      const cat = getCategoryByCategory(sys.category);
      if (cat && !counts[cat.id]) counts[cat.id] = 0;
      // Don't override template count
    }
    return counts;
  }, [templates, systems, selectedCategory]);

  const handleCreateFromTemplate = useCallback((template: TradingSystemTemplate) => {
    setCreateTemplate(template);
    setShowCreateModal(true);
  }, []);

  // If a system is selected, show detail view
  if (selectedSystem) {
    return (
      <div className="h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
        <SystemDetail system={selectedSystem} onBack={() => setSelectedSystemId(null)} />
        <CreateSystemModal template={createTemplate} open={showCreateModal} onClose={() => { setShowCreateModal(false); setCreateTemplate(null); }} />
      </div>
    );
  }

  return (
    <div className="h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <Zap className="h-4 w-4 text-[#d4af37]" />
        <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">TRADING SYSTEMS LAB</span>
        <Badge className="text-[9px] h-5 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748] ml-2">
          {systems.length} systems · {templates.length} templates
        </Badge>
      </div>

      {/* Main: Sidebar + Content */}
      <div className="flex-1 flex min-h-0">
        {/* Left Sidebar */}
        <div className="w-56 shrink-0 border-r border-[#1e293b] bg-[#0d1117] flex flex-col">
          {/* Categories */}
          <div className="p-2 space-y-0.5">
            <span className="text-[9px] font-mono text-[#475569] uppercase tracking-wider px-2 mb-1 block">Categories</span>
            {CATEGORIES.map(cat => {
              const Icon = cat.icon;
              const isActive = selectedCategory === cat.id;
              return (
                <button
                  key={cat.id}
                  onClick={() => setSelectedCategory(cat.id)}
                  className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-[11px] font-mono transition-all ${
                    isActive
                      ? 'bg-[#d4af37]/15 text-[#d4af37] border border-[#d4af37]/30'
                      : 'text-[#94a3b8] hover:text-[#e2e8f0] hover:bg-[#1a1f2e] border border-transparent'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{cat.label}</span>
                  <Badge className="text-[8px] h-3.5 px-1 ml-auto font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                    {categoryCount[cat.id] || 0}
                  </Badge>
                </button>
              );
            })}
          </div>

          {/* Divider */}
          <div className="mx-3 my-1 border-t border-[#1e293b]" />

          {/* My Systems */}
          <div className="p-2 space-y-0.5 flex-1 min-h-0">
            <span className="text-[9px] font-mono text-[#475569] uppercase tracking-wider px-2 mb-1 block">My Systems</span>
            <ScrollArea className="max-h-48">
              <div className="space-y-0.5">
                {systems.map((sys, idx) => {
                  const statusStyle = STATUS_STYLES[sys.status];
                  const StatusIcon = statusStyle.icon;
                  const isActive = selectedSystemId === sys.id;
                  return (
                    <button
                      key={sys.id || `sys-${idx}`}
                      onClick={() => setSelectedSystemId(isActive ? null : sys.id)}
                      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-[11px] font-mono transition-all ${
                        isActive
                          ? 'bg-[#d4af37]/15 text-[#d4af37] border border-[#d4af37]/40'
                          : 'text-[#94a3b8] hover:text-[#e2e8f0] hover:bg-[#1a1f2e] border border-transparent'
                      }`}
                    >
                      <span className="text-xs">{sys.icon}</span>
                      <span className="truncate flex-1 text-left">{sys.name}</span>
                      <StatusIcon className={`h-3 w-3 shrink-0 ${statusStyle.text}`} />
                    </button>
                  );
                })}
              </div>
            </ScrollArea>
          </div>
        </div>

        {/* Main Content: Template Browser */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="px-4 py-2 border-b border-[#1e293b] bg-[#0d1117]/50 shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">
                {CATEGORIES.find(c => c.id === selectedCategory)?.emoji} {CATEGORIES.find(c => c.id === selectedCategory)?.label} Templates
              </span>
              <Badge className="text-[9px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                {templates.length}
              </Badge>
            </div>
          </div>

          <ScrollArea className="flex-1">
            <div className="p-4">
              {/* Loading state */}
              {(templatesLoading || systemsLoading) && (
                <div className="flex flex-col items-center justify-center py-16 text-[#64748b]">
                  <RefreshCw className="h-8 w-8 mb-2 text-[#2d3748] animate-spin" />
                  <span className="font-mono text-sm">Loading systems & templates...</span>
                </div>
              )}

              {/* Error state */}
              {systemsError && !systemsLoading && (
                <div className="flex flex-col items-center justify-center py-16 text-[#64748b]">
                  <XCircle className="h-8 w-8 mb-2 text-red-500/50" />
                  <span className="font-mono text-sm text-red-400">Failed to load trading systems</span>
                  <Button variant="ghost" size="sm" onClick={() => refetchSystems()} className="mt-2 h-7 text-[10px] font-mono text-[#94a3b8] hover:text-[#e2e8f0]">
                    <RefreshCw className="h-3 w-3 mr-1" /> Retry
                  </Button>
                </div>
              )}

              {/* Templates grid */}
              {!templatesLoading && !systemsLoading && (
                <>
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                    <AnimatePresence>
                      {templates.map((tpl, idx) => (
                        <TemplateCard key={tpl.id || `tpl-${selectedCategory}-${idx}`} template={tpl} onSelect={handleCreateFromTemplate} />
                      ))}
                    </AnimatePresence>
                  </div>
                  {templates.length === 0 && (
                    <div className="flex flex-col items-center justify-center py-16 text-[#64748b]">
                      <Microscope className="h-8 w-8 mb-2 text-[#2d3748]" />
                      <span className="font-mono text-sm">No templates in this category</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>

      {/* Create System Modal */}
      <CreateSystemModal template={createTemplate} open={showCreateModal} onClose={() => { setShowCreateModal(false); setCreateTemplate(null); }} />
    </div>
  );
}
