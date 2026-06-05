'use client';

import dynamic from 'next/dynamic';
import { useCryptoStore, type ActiveTab } from '@/store/crypto-store';
import { useEffect, useCallback, useState, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Brain,
  BarChart3,
  Dna,
  Radio,
  FlaskConical,
  Wallet,
  Eye,
  Zap,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Activity,
  DollarSign,
  Layers,
  Sparkles,
  CandlestickChart,
  Puzzle,
  Globe,
  LogOut,
  User,
  ShieldAlert,
  Shield,
  Gavel,
  ArrowRightLeft,
  PieChart,
  TestTube,
  LayoutDashboard,
  Briefcase,
  Code2,
  Trophy,
} from 'lucide-react';
import { UserProvider, useUser } from '@/components/auth/user-provider';
import { DashboardLevelProvider, useDashboardLevel, type DashboardLevel } from '@/components/dashboard/dashboard-level-provider';

// ============================================================
// DYNAMIC IMPORTS — Code-split all tab components
// ============================================================

const loadingFallback = () => (
  <div className="flex items-center justify-center h-64 text-muted-foreground">Loading...</div>
);

const TokenFlow = dynamic(() => import('@/components/dashboard/token-flow').then(m => ({ default: m.TokenFlow })), { ssr: false, loading: loadingFallback });
const SignalCenter = dynamic(() => import('@/components/dashboard/signal-center').then(m => ({ default: m.SignalCenter })), { ssr: false, loading: loadingFallback });
const DNAScanner = dynamic(() => import('@/components/dashboard/dna-scanner').then(m => ({ default: m.DNAScanner })), { ssr: false, loading: loadingFallback });
const PatternBuilder = dynamic(() => import('@/components/dashboard/pattern-builder').then(m => ({ default: m.PatternBuilder })), { ssr: false, loading: loadingFallback });
const IntelligenceModules = dynamic(() => import('@/components/dashboard/intelligence-modules').then(m => ({ default: m.IntelligenceModules })), { ssr: false, loading: loadingFallback });
const TraderIntelligencePanel = dynamic(() => import('@/components/dashboard/trader-intelligence').then(m => ({ default: m.TraderIntelligencePanel })), { ssr: false, loading: loadingFallback });

// Default exports (used directly in main tabs)
const BacktestingLab = dynamic(() => import('@/components/dashboard/backtesting-lab'), { ssr: false, loading: loadingFallback });
const BigDataPredictive = dynamic(() => import('@/components/dashboard/big-data-predictive'), { ssr: false, loading: loadingFallback });
const BrainControl = dynamic(() => import('@/components/dashboard/brain-control'), { ssr: false, loading: loadingFallback });
const MultiChainDashboard = dynamic(() => import('@/components/dashboard/multi-chain-dashboard'), { ssr: false, loading: loadingFallback });

const OHLCVChart = dynamic(() => import('@/components/dashboard/ohlcv-chart').then(m => ({ default: m.OHLCVChart })), { ssr: false, loading: loadingFallback });
const WebSocketProvider = dynamic(() => import('@/components/dashboard/websocket-provider').then(m => ({ default: m.WebSocketProvider })), { ssr: false, loading: loadingFallback });
const SimulationProvider = dynamic(() => import('@/components/dashboard/simulation-provider').then(m => ({ default: m.SimulationProvider })), { ssr: false, loading: loadingFallback });
const DataStatusBar = dynamic(() => import('@/components/dashboard/data-status-bar').then(m => ({ default: m.DataStatusBar })), { ssr: false, loading: loadingFallback });
const DeepAnalysisPanel = dynamic(() => import('@/components/dashboard/deep-analysis-panel').then(m => ({ default: m.DeepAnalysisPanel })), { ssr: false, loading: loadingFallback });
const NotificationCenter = dynamic(() => import('@/components/dashboard/notification-center').then(m => ({ default: m.NotificationCenter })), { ssr: false, loading: loadingFallback });
const HeaderBar = dynamic(() => import('@/components/dashboard/header-bar').then(m => ({ default: m.HeaderBar })), { ssr: false, loading: loadingFallback });

// Strategy Lab sub-tabs — also lazy loaded
const StrategyLabContent = dynamic(() => import('@/components/dashboard/strategy-lab-content'), { ssr: false, loading: loadingFallback });

// Promoted top-level tabs
const KillSwitchPanel = dynamic(() => import('@/components/dashboard/kill-switch-panel'), { ssr: false, loading: loadingFallback });
const AllocationDashboard = dynamic(() => import('@/components/dashboard/allocation-dashboard'), { ssr: false, loading: loadingFallback });
const PortfolioView = dynamic(() => import('@/components/dashboard/portfolio-view'), { ssr: false, loading: loadingFallback });
const DecisionDashboard = dynamic(() => import('@/components/dashboard/decision-dashboard'), { ssr: false, loading: loadingFallback });
const RiskDashboard = dynamic(() => import('@/components/dashboard/risk-dashboard'), { ssr: false, loading: loadingFallback });
const PaperTradingPanel = dynamic(() => import('@/components/dashboard/paper-trading-panel'), { ssr: false, loading: loadingFallback });
const ExportImportPanel = dynamic(() => import('@/components/dashboard/export-import-panel'), { ssr: false, loading: loadingFallback });

// Executive Dashboard
const ExecutiveDashboard = dynamic(() => import('@/components/dashboard/executive-dashboard').then(m => ({ default: m.ExecutiveDashboard })), { ssr: false, loading: loadingFallback });

// Execution Cost Panel
const ExecutionCostPanel = dynamic(() => import('@/components/dashboard/execution-cost-panel').then(m => ({ default: m.ExecutionCostPanel })), { ssr: false, loading: loadingFallback });

// Meta-Model Panel
const MetaModelPanel = dynamic(() => import('@/components/dashboard/meta-model-panel').then(m => ({ default: m.MetaModelPanel })), { ssr: false, loading: loadingFallback });

// Alpha Ranking Panel
const AlphaRankingPanel = dynamic(() => import('@/components/dashboard/alpha-ranking-panel').then(m => ({ default: m.AlphaRankingPanel })), { ssr: false, loading: loadingFallback });

// Risk Pre-Filter Panel
const RiskPreFilterPanel = dynamic(() => import('@/components/dashboard/risk-pre-filter-panel').then(m => ({ default: m.RiskPreFilterPanel })), { ssr: false, loading: loadingFallback });

// Portfolio Intelligence Panel
const PortfolioIntelligencePanel = dynamic(() => import('@/components/dashboard/portfolio-intelligence-panel').then(m => ({ default: m.PortfolioIntelligencePanel })), { ssr: false, loading: loadingFallback });

// Market Regime Panel
const MarketRegimePanel = dynamic(() => import('@/components/dashboard/market-regime-panel').then(m => ({ default: m.MarketRegimePanel })), { ssr: false, loading: loadingFallback });

// Event Bus Panel
const EventBusPanel = dynamic(() => import('@/components/dashboard/event-bus-panel').then(m => ({ default: m.EventBusPanel })), { ssr: false, loading: loadingFallback });

// ============================================================
// SIDEBAR NAVIGATION CONFIG
// ============================================================

interface NavItem {
  id: ActiveTab;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  shortcut: string;
  description: string;
}

interface NavGroup {
  id: string;
  label: string;
  emoji: string;
  items: NavItem[];
}

// Tabs visible at each dashboard level
const PROFESSIONAL_HIDDEN_TABS: ActiveTab[] = ['dna-scanner', 'predictive', 'patterns', 'export-import', 'event-bus'];
const ENGINEER_ONLY_TABS: ActiveTab[] = ['event-bus'];

function getFilteredNavGroups(level: DashboardLevel): NavGroup[] {
  const allGroups: NavGroup[] = [
    {
      id: 'market',
      label: 'MARKET',
      emoji: '📡',
      items: [
        { id: 'dashboard', label: 'Dashboard', icon: BarChart3, shortcut: '1', description: 'Live token feed & prices' },
        { id: 'charts', label: 'Charts', icon: CandlestickChart, shortcut: '2', description: 'OHLCV candlestick charts' },
        { id: 'multi-chain', label: 'Multi-Chain', icon: Globe, shortcut: '3', description: 'Cross-chain comparison & ranking' },
        { id: 'market-regime', label: 'Market Regime', icon: Activity, shortcut: 'g', description: 'Regime detection & HMM analysis' },
      ],
    },
    {
      id: 'intelligence',
      label: 'INTELLIGENCE',
      emoji: '🧠',
      items: [
        { id: 'signals', label: 'Signals', icon: Radio, shortcut: '4', description: 'Live signal feed' },
        { id: 'brain', label: 'Brain', icon: Brain, shortcut: '5', description: 'Control center' },
        { id: 'meta-model', label: 'Meta-Model', icon: Layers, shortcut: 'm', description: 'Engine performance & weights' },
        { id: 'alpha-ranking', label: 'Alpha Rank', icon: Trophy, shortcut: 'a', description: 'Top alpha opportunities' },
        { id: 'smart-money', label: 'Smart Money', icon: Eye, shortcut: '6', description: 'Trader intelligence' },
        { id: 'deep-analysis', label: 'Deep Analysis', icon: Sparkles, shortcut: '7', description: 'Deep token analysis' },
        { id: 'dna-scanner', label: 'DNA Scanner', icon: Dna, shortcut: '8', description: 'Token DNA analysis' },
        { id: 'predictive', label: 'Predictive', icon: Zap, shortcut: '9', description: 'AI predictions' },
      ],
    },
    {
      id: 'risk-portfolio',
      label: 'RISK & PORTFOLIO',
      emoji: '🛡️',
      items: [
        { id: 'risk-pre-filter', label: 'Pre-Filter', icon: Shield, shortcut: 'f', description: 'Risk pre-filter for signals' },
        { id: 'kill-switches', label: 'Kill Switches', icon: ShieldAlert, shortcut: 'r', description: 'Emergency kill switches' },
        { id: 'risk', label: 'Risk', icon: Shield, shortcut: 'u', description: 'Risk management & simulation' },
        { id: 'portfolio', label: 'Portfolio', icon: PieChart, shortcut: 'y', description: 'Portfolio view' },
        { id: 'portfolio-intelligence', label: 'Portfolio AI', icon: Briefcase, shortcut: 'b', description: 'Impact analysis & optimization' },
        { id: 'capital-allocation', label: 'Capital Alloc', icon: DollarSign, shortcut: 't', description: 'Capital allocation dashboard' },
        { id: 'decisions', label: 'SDE Decisions', icon: Gavel, shortcut: 'i', description: 'Strategic decision engine' },
        { id: 'execution-cost', label: 'Exec Cost', icon: DollarSign, shortcut: 'p', description: 'Execution cost estimator' },
      ],
    },
    {
      id: 'strategy',
      label: 'STRATEGY',
      emoji: '⚙️',
      items: [
        { id: 'strategy-lab', label: 'Strategy Lab', icon: FlaskConical, shortcut: '0', description: 'Trading system lab & AI optimizer' },
        { id: 'backtesting', label: 'Backtesting', icon: TestTube, shortcut: 'q', description: 'Strategy backtesting' },
        { id: 'paper-trading', label: 'Paper Trading', icon: Wallet, shortcut: 'w', description: 'Simulated trading' },
        { id: 'patterns', label: 'Patterns', icon: Puzzle, shortcut: 'e', description: 'Pattern builder & detection' },
      ],
    },
    {
      id: 'system',
      label: 'SYSTEM',
      emoji: '🔧',
      items: [
        { id: 'event-bus', label: 'Event Bus', icon: Radio, shortcut: 'v', description: 'Real-time event monitor' },
        { id: 'export-import', label: 'Export/Import', icon: ArrowRightLeft, shortcut: 'o', description: 'Export & import data' },
      ],
    },
  ];

  if (level === 'engineer') return allGroups;

  // For professional, hide engineer-only tabs
  if (level === 'professional') {
    return allGroups.map(g => ({
      ...g,
      items: g.items.filter(item => !PROFESSIONAL_HIDDEN_TABS.includes(item.id)),
    })).filter(g => g.items.length > 0);
  }

  // Executive level doesn't use sidebar at all
  return [];
}

// Flatten for keyboard shortcut mapping (computed dynamically)
const ALL_NAV_ITEMS_FLAT: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', icon: BarChart3, shortcut: '1', description: '' },
  { id: 'charts', label: 'Charts', icon: CandlestickChart, shortcut: '2', description: '' },
  { id: 'multi-chain', label: 'Multi-Chain', icon: Globe, shortcut: '3', description: '' },
  { id: 'market-regime', label: 'Market Regime', icon: Activity, shortcut: 'g', description: '' },
  { id: 'signals', label: 'Signals', icon: Radio, shortcut: '4', description: '' },
  { id: 'brain', label: 'Brain', icon: Brain, shortcut: '5', description: '' },
  { id: 'meta-model', label: 'Meta-Model', icon: Layers, shortcut: 'm', description: '' },
  { id: 'alpha-ranking', label: 'Alpha Rank', icon: Trophy, shortcut: 'a', description: '' },
  { id: 'smart-money', label: 'Smart Money', icon: Eye, shortcut: '6', description: '' },
  { id: 'deep-analysis', label: 'Deep Analysis', icon: Sparkles, shortcut: '7', description: '' },
  { id: 'dna-scanner', label: 'DNA Scanner', icon: Dna, shortcut: '8', description: '' },
  { id: 'predictive', label: 'Predictive', icon: Zap, shortcut: '9', description: '' },
  { id: 'risk-pre-filter', label: 'Pre-Filter', icon: Shield, shortcut: 'f', description: '' },
  { id: 'kill-switches', label: 'Kill Switches', icon: ShieldAlert, shortcut: 'r', description: '' },
  { id: 'risk', label: 'Risk', icon: Shield, shortcut: 'u', description: '' },
  { id: 'portfolio', label: 'Portfolio', icon: PieChart, shortcut: 'y', description: '' },
  { id: 'portfolio-intelligence', label: 'Portfolio AI', icon: Briefcase, shortcut: 'b', description: '' },
  { id: 'capital-allocation', label: 'Capital Alloc', icon: DollarSign, shortcut: 't', description: '' },
  { id: 'decisions', label: 'SDE Decisions', icon: Gavel, shortcut: 'i', description: '' },
  { id: 'execution-cost', label: 'Exec Cost', icon: DollarSign, shortcut: 'p', description: '' },
  { id: 'strategy-lab', label: 'Strategy Lab', icon: FlaskConical, shortcut: '0', description: '' },
  { id: 'backtesting', label: 'Backtesting', icon: TestTube, shortcut: 'q', description: '' },
  { id: 'paper-trading', label: 'Paper Trading', icon: Wallet, shortcut: 'w', description: '' },
  { id: 'patterns', label: 'Patterns', icon: Puzzle, shortcut: 'e', description: '' },
  { id: 'event-bus', label: 'Event Bus', icon: Radio, shortcut: 'v', description: '' },
  { id: 'export-import', label: 'Export/Import', icon: ArrowRightLeft, shortcut: 'o', description: '' },
];

// ============================================================
// KEYBOARD SHORTCUTS
// ============================================================

const SHORTCUT_TAB_MAP: Record<string, ActiveTab> = {};
ALL_NAV_ITEMS_FLAT.forEach((item) => {
  if (item.shortcut) {
    SHORTCUT_TAB_MAP[item.shortcut] = item.id;
  }
});

const TAB_HISTORY: ActiveTab[] = [];

function useKeyboardShortcuts() {
  const setActiveTab = useCryptoStore((s) => s.setActiveTab);
  const activeTab = useCryptoStore((s) => s.activeTab);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // Don't capture when typing in inputs
    if (
      e.target instanceof HTMLInputElement ||
      e.target instanceof HTMLTextAreaElement ||
      e.target instanceof HTMLSelectElement
    ) {
      return;
    }

    if (SHORTCUT_TAB_MAP[e.key]) {
      e.preventDefault();
      TAB_HISTORY.push(activeTab);
      setActiveTab(SHORTCUT_TAB_MAP[e.key]);
      return;
    }

    if (e.key === 'Escape') {
      const prevTab = TAB_HISTORY.pop() || 'dashboard';
      setActiveTab(prevTab);
      return;
    }
  }, [setActiveTab, activeTab]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}

// ============================================================
// TOP BAR COMPONENT
// ============================================================

function TopBar() {
  const isConnected = useCryptoStore((s) => s.isConnected);
  const marketSummary = useCryptoStore((s) => s.marketSummary);
  const [utcTime, setUtcTime] = useState('');
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const { user } = useUser();

  // Close user menu when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const { data: brainStatus } = useQuery({
    queryKey: ['brain-status-topbar'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/brain/status');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          totalSignals: number;
          brainHealth: string;
          brainStatusMessage?: string;
          tokensTracked: number;
          brainCycles: number;
        } | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: schedulerStatus } = useQuery({
    queryKey: ['scheduler-status-topbar'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/brain/scheduler');
        if (!res.ok) return null;
        const json = await res.json();
        return (json.data || json) as {
          status: string;
          totalCyclesCompleted: number;
          capitalStrategy?: { totalCapital: number; growthPct: number };
          persisted?: { totalCycles: number; capitalUsd: number };
        } | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  useEffect(() => {
    const timer = setInterval(() => {
      setUtcTime(new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC');
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const brainHealth = brainStatus?.brainHealth ?? 'UNKNOWN';
  const totalSignals = brainStatus?.totalSignals ?? 0;
  const tokensTracked = brainStatus?.tokensTracked ?? 0;
  const brainCycles = schedulerStatus?.totalCyclesCompleted ?? schedulerStatus?.persisted?.totalCycles ?? 0;
  const capital = schedulerStatus?.capitalStrategy?.totalCapital ?? schedulerStatus?.persisted?.capitalUsd ?? 0;
  const growthPct = schedulerStatus?.capitalStrategy?.growthPct ?? 0;
  const schedulerRunning = schedulerStatus?.status === 'RUNNING';

  const safeGrowthPct = growthPct ?? 0;

  const formatCapital = (v: number) => {
    if (v == null || isNaN(v)) return '$0';
    if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  };

  return (
    <div className="flex items-center justify-between px-2 sm:px-3 h-9 bg-[#080b12] border-b border-[#1e293b] shrink-0">
      {/* Left: Logo + Status */}
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[#3b82f6] font-mono text-xs font-bold tracking-wider blue-glow">
            CryptoQuant
          </span>
          <span className="text-[#475569] font-mono text-[8px] hidden sm:inline">TERMINAL</span>
        </div>
        <div className="h-4 w-px bg-[#1e293b]" />
        <div className="flex items-center gap-1">
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500 live-pulse' : 'bg-red-500'}`} />
          <span className={`font-mono text-[9px] ${isConnected ? 'text-emerald-400' : 'text-red-400'}`}>
            {isConnected ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>
        <div className="h-4 w-px bg-[#1e293b]" />
        {/* Brain Status */}
        <div className="flex items-center gap-1.5">
          <Brain className={`h-3 w-3 ${schedulerRunning ? 'text-emerald-400' : 'text-[#475569]'}`} />
          <span className={`font-mono text-[9px] font-bold ${
            brainHealth === 'HEALTHY' || brainHealth === 'ACTIVE' ? 'text-emerald-400' :
            brainHealth === 'LEARNING' ? 'text-cyan-400' :
            brainHealth === 'IDLE' ? 'text-gray-400' :
            'text-[#64748b]'
          }`}>
            {schedulerRunning ? 'ACTIVE' :
              brainHealth === 'HEALTHY' || brainHealth === 'ACTIVE' ? 'ACTIVE' :
              brainHealth === 'LEARNING' ? 'LEARNING' :
              brainHealth === 'IDLE' ? 'IDLE' :
              brainHealth}
          </span>
        </div>
      </div>

      {/* Center: Key Metrics - responsive */}
      <div className="flex items-center gap-1.5 sm:gap-3 lg:gap-4 overflow-x-auto">
        {/* Capital */}
        <div className="flex items-center gap-1 sm:gap-1.5 bg-[#0a0e17] px-1.5 sm:px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
          <DollarSign className="h-3 w-3 text-[#3b82f6]" />
          <span className="text-[8px] font-mono text-[#64748b] hidden md:inline">CAPITAL</span>
          <span className="mono-data text-[10px] font-bold text-[#e2e8f0]">{formatCapital(capital)}</span>
          <span className={`mono-data text-[9px] font-bold ${safeGrowthPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {safeGrowthPct >= 0 ? '+' : ''}{safeGrowthPct.toFixed(1)}%
          </span>
        </div>

        {/* Cycles */}
        <div className="flex items-center gap-1 sm:gap-1.5 bg-[#0a0e17] px-1.5 sm:px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
          <Activity className="h-3 w-3 text-cyan-400" />
          <span className="text-[8px] font-mono text-[#64748b] hidden lg:inline">CYCLES</span>
          <span className="mono-data text-[10px] font-bold text-cyan-400">{brainCycles}</span>
        </div>

        {/* Tokens */}
        <div className="flex items-center gap-1 sm:gap-1.5 bg-[#0a0e17] px-1.5 sm:px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
          <BarChart3 className="h-3 w-3 text-[#3b82f6]" />
          <span className="text-[8px] font-mono text-[#64748b] hidden lg:inline">TOKENS</span>
          <span className="mono-data text-[10px] font-bold text-[#e2e8f0]">{tokensTracked.toLocaleString()}</span>
        </div>

        {/* Signals */}
        <div className="flex items-center gap-1 sm:gap-1.5 bg-[#0a0e17] px-1.5 sm:px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
          <Radio className="h-3 w-3 text-amber-400" />
          <span className="text-[8px] font-mono text-[#64748b] hidden lg:inline">SIGNALS</span>
          <span className="mono-data text-[10px] font-bold text-amber-400">{totalSignals}</span>
        </div>

        {/* Market - hide on small screens */}
        {marketSummary && (
          <>
            <div className="h-4 w-px bg-[#1e293b] hidden xl:block" />
            <div className="flex items-center gap-1 hidden xl:flex">
              <span className="text-[#f59e0b] font-mono text-[9px] font-bold">BTC</span>
              <span className="mono-data text-[10px] text-[#e2e8f0]">${(marketSummary.btcPrice ?? 0).toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1 hidden xl:flex">
              <span className="text-[#627eea] font-mono text-[9px] font-bold">ETH</span>
              <span className="mono-data text-[10px] text-[#e2e8f0]">${(marketSummary.ethPrice ?? 0).toLocaleString()}</span>
            </div>
          </>
        )}
      </div>

      {/* Center-Right: Dashboard Level Selector */}
      <DashboardLevelSelector />

      {/* Right: Notifications + User Menu + Time */}
      <div className="flex items-center gap-2 shrink-0">
        <NotificationCenter />
        <span className="mono-data text-[9px] text-[#475569] hidden md:inline">{utcTime}</span>
        
        {/* User Menu */}
        {user && (
          <div className="relative" ref={userMenuRef}>
            <button
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              className="flex items-center gap-1.5 px-1.5 py-0.5 rounded border border-[#1e293b] bg-[#0a0e17] hover:border-[#3b82f6]/30 transition-colors"
            >
              <div className="flex items-center justify-center w-4 h-4 rounded-full bg-[#3b82f6]/20 border border-[#3b82f6]/30">
                <User className="h-2.5 w-2.5 text-[#3b82f6]" />
              </div>
              <span className="font-mono text-[9px] text-[#94a3b8] max-w-[80px] truncate hidden sm:inline">
                {user.name || user.email}
              </span>
              <ChevronDown className="h-2.5 w-2.5 text-[#475569]" />
            </button>

            {userMenuOpen && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-[#111827] border border-[#1e293b] rounded-lg shadow-xl shadow-black/50 z-50 py-1">
                <div className="px-3 py-2 border-b border-[#1e293b]">
                  <p className="font-mono text-[10px] text-[#e2e8f0] truncate">{user.name || 'User'}</p>
                  <p className="font-mono text-[9px] text-[#64748b] truncate">{user.email}</p>
                  <span className="inline-block mt-1 px-1.5 py-0.5 rounded bg-[#3b82f6]/10 border border-[#3b82f6]/20 font-mono text-[8px] text-[#3b82f6]">
                    {user.role}
                  </span>
                </div>
                <button
                  onClick={() => {
                    setUserMenuOpen(false);
                    // Auth disabled — no sign out action
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[#1e293b] transition-colors opacity-50"
                >
                  <LogOut className="h-3 w-3 text-[#475569]" />
                  <span className="font-mono text-[10px] text-[#64748b]">Auth Disabled</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// DASHBOARD LEVEL SELECTOR
// ============================================================

function DashboardLevelSelector() {
  const { level, setLevel } = useDashboardLevel();

  const levels: Array<{ id: DashboardLevel; label: string; icon: React.ComponentType<{ className?: string }>; shortcut: string }> = [
    { id: 'executive', label: 'Executive', icon: LayoutDashboard, shortcut: '⌘1' },
    { id: 'professional', label: 'Professional', icon: Briefcase, shortcut: '⌘2' },
    { id: 'engineer', label: 'Engineer', icon: Code2, shortcut: '⌘3' },
  ];

  return (
    <div className="flex items-center gap-0.5 bg-[#0a0e17] rounded border border-[#1e293b] px-0.5 py-0.5">
      {levels.map((l) => {
        const Icon = l.icon;
        const isActive = level === l.id;
        return (
          <button
            key={l.id}
            onClick={() => setLevel(l.id)}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-mono transition-all ${
              isActive
                ? 'bg-[#1e293b] text-[#f1f5f9]'
                : 'text-[#64748b] hover:text-[#94a3b8] hover:bg-[#1e293b]/50'
            }`}
            title={`${l.label} view (${l.shortcut})`}
          >
            <Icon className="h-3 w-3" />
            <span className="hidden sm:inline">{l.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// SIDEBAR COMPONENT
// ============================================================

function Sidebar() {
  const activeTab = useCryptoStore((s) => s.activeTab);
  const setActiveTab = useCryptoStore((s) => s.setActiveTab);
  const { level } = useDashboardLevel();
  const [collapsed, setCollapsed] = useState(false);

  const navGroups = getFilteredNavGroups(level);

  // Don't render sidebar for executive level
  if (level === 'executive') return null;

  return (
    <nav
      className={`flex flex-col h-full bg-[#0d1117] border-r border-[#1e293b] shrink-0 transition-all duration-200 ${
        collapsed ? 'w-10 sm:w-12' : 'w-[140px] sm:w-[180px]'
      }`}
    >
      {/* Nav Items */}
      <div className="flex-1 overflow-y-auto py-1">
        {navGroups.map((group) => (
          <div key={group.id}>
            {/* Group header */}
            {!collapsed && (
              <div className="px-3 py-1.5 mt-2 mb-0.5">
                <span className="text-[9px] font-mono text-[#475569] uppercase tracking-wider">
                  {group.emoji} {group.label}
                </span>
              </div>
            )}
            {collapsed && (
              <div className="flex justify-center py-1 mt-2">
                <span className="text-[10px]">{group.emoji}</span>
              </div>
            )}
            {/* Group items */}
            {group.items.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`sidebar-nav-item w-full flex items-center gap-2 px-3 py-2 text-left ${
                    isActive ? 'active' : ''
                  }`}
                  title={`${item.label} (${item.shortcut})`}
                >
                  <Icon className={`nav-icon h-4 w-4 shrink-0 ${
                    isActive ? 'text-[#3b82f6]' : 'text-[#64748b]'
                  }`} />
                  {!collapsed && (
                    <div className="flex flex-col min-w-0">
                      <span className={`nav-label text-[11px] font-medium truncate ${
                        isActive ? 'text-[#f1f5f9]' : 'text-[#94a3b8]'
                      }`}>
                        {item.label}
                      </span>
                    </div>
                  )}
                  {!collapsed && (
                    <span className={`ml-auto text-[8px] font-mono ${
                      isActive ? 'text-[#3b82f6]/60' : 'text-[#475569]'
                    }`}>
                      {item.shortcut}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* Collapse Toggle */}
      <div className="border-t border-[#1e293b] p-1">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center w-full py-1.5 text-[#475569] hover:text-[#94a3b8] transition-colors"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </nav>
  );
}

// ============================================================
// QUICK START GUIDE
// ============================================================

function QuickStartGuide() {
  const steps = [
    { icon: Brain, label: 'Start the Brain', desc: 'Go to Brain tab and click Start to begin automated analysis' },
    { icon: BarChart3, label: 'Browse Token Flow', desc: 'Monitor live token prices and market movements' },
    { icon: Radio, label: 'Watch Signals', desc: 'Get real-time trading signals from multiple sources' },
    { icon: Dna, label: 'Scan DNA', desc: 'Select a token and analyze its DNA risk profile' },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col h-full"
    >
      {/* Welcome Header */}
      <div className="px-6 pt-6 pb-4">
        <div className="flex items-center gap-2 mb-2">
          <Sparkles className="h-5 w-5 text-[#3b82f6]" />
          <h1 className="text-xl font-bold text-[#f1f5f9] font-mono">CryptoQuant Terminal</h1>
        </div>
        <p className="text-sm text-[#94a3b8] max-w-xl">
          Professional-grade crypto analytics. Real-time signals, DNA risk scanning, smart money tracking, and AI-powered predictions — all in one terminal.
        </p>
      </div>

      {/* Quick Start Steps */}
      <div className="px-6 pb-4">
        <h2 className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider mb-3">Quick Start</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {steps.map((step, i) => {
            const Icon = step.icon;
            return (
              <div
                key={i}
                className="quick-start-step bg-[#111827] border border-[#1e293b] rounded-lg p-4 cursor-pointer hover:border-[#3b82f6]/30 transition-all"
              >
                <div className="flex items-center gap-2 mb-2">
                  <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#3b82f6]/10 border border-[#3b82f6]/20">
                    <Icon className="h-4 w-4 text-[#3b82f6]" />
                  </div>
                  <span className="text-[9px] font-mono text-[#3b82f6]/60">STEP {i + 1}</span>
                </div>
                <h3 className="text-sm font-bold text-[#f1f5f9] mb-1">{step.label}</h3>
                <p className="text-[11px] text-[#94a3b8] leading-relaxed">{step.desc}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main Dashboard Content - Token Flow + Signals */}
      <div className="flex-1 flex gap-1.5 min-h-0 px-1.5 pb-1.5">
        <div className="w-[45%] shrink-0">
          <TokenFlow />
        </div>
        <div className="flex-1 flex flex-col gap-1.5 min-h-0">
          <div className="flex-1 min-h-0">
            <SignalCenter />
          </div>
          <div className="shrink-0">
            <IntelligenceModules />
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// MAIN CONTENT AREA
// ============================================================

function MainContent() {
  const activeTab = useCryptoStore((s) => s.activeTab);
  const selectedToken = useCryptoStore((s) => s.selectedToken);

  const contentMap: Record<ActiveTab, React.ReactNode> = {
    dashboard: <QuickStartGuide />,
    charts: (
      <div className="flex-1 flex gap-1.5 min-h-0 h-full">
        <div className="w-[35%] shrink-0">
          <TokenFlow />
        </div>
        <div className="flex-1">
          {selectedToken ? (
            <OHLCVChart tokenAddress={(selectedToken as any).address ?? selectedToken.id} chain={selectedToken.chain} />
          ) : (
            <div className="flex flex-col items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg">
              <BarChart3 className="h-8 w-8 text-[#475569] mb-2" />
              <span className="text-[#64748b] font-mono text-sm">Select a token to view charts</span>
            </div>
          )}
        </div>
      </div>
    ),
    'multi-chain': (
      <div className="flex-1 min-h-0 h-full">
        <MultiChainDashboard />
      </div>
    ),
    signals: (
      <div className="flex-1 min-h-0 h-full">
        <SignalCenter />
      </div>
    ),
    'smart-money': (
      <div className="flex-1 min-h-0 h-full">
        <TraderIntelligencePanel />
      </div>
    ),
    'deep-analysis': (
      <div className="flex-1 min-h-0 h-full overflow-y-auto">
        <DeepAnalysisPanel />
      </div>
    ),
    'dna-scanner': (
      <div className="flex-1 flex gap-1.5 min-h-0 h-full">
        <div className="w-[35%] shrink-0">
          <TokenFlow />
        </div>
        <div className="flex-1">
          <DNAScanner />
        </div>
      </div>
    ),
    predictive: (
      <div className="flex-1 min-h-0 h-full">
        <BigDataPredictive />
      </div>
    ),
    brain: (
      <div className="flex-1 min-h-0 h-full">
        <BrainControl />
      </div>
    ),
    'strategy-lab': (
      <div className="flex-1 min-h-0 h-full flex flex-col">
        <StrategyLabContent />
      </div>
    ),
    backtesting: (
      <div className="flex-1 min-h-0 h-full">
        <BacktestingLab />
      </div>
    ),
    'paper-trading': (
      <div className="flex-1 min-h-0 h-full">
        <PaperTradingPanel />
      </div>
    ),
    patterns: (
      <div className="flex-1 min-h-0 h-full">
        <PatternBuilder />
      </div>
    ),
    'kill-switches': (
      <div className="flex-1 min-h-0 h-full">
        <KillSwitchPanel />
      </div>
    ),
    'capital-allocation': (
      <div className="flex-1 min-h-0 h-full">
        <AllocationDashboard />
      </div>
    ),
    portfolio: (
      <div className="flex-1 min-h-0 h-full">
        <PortfolioView />
      </div>
    ),
    risk: (
      <div className="flex-1 min-h-0 h-full">
        <RiskDashboard />
      </div>
    ),
    decisions: (
      <div className="flex-1 min-h-0 h-full">
        <DecisionDashboard />
      </div>
    ),
    'export-import': (
      <div className="flex-1 min-h-0 h-full">
        <ExportImportPanel />
      </div>
    ),
    'execution-cost': (
      <div className="flex-1 min-h-0 h-full">
        <ExecutionCostPanel />
      </div>
    ),
    'risk-pre-filter': <div className="flex-1 min-h-0 h-full"><RiskPreFilterPanel /></div>,
    'portfolio-intelligence': <div className="flex-1 min-h-0 h-full"><PortfolioIntelligencePanel /></div>,
    'market-regime': <div className="flex-1 min-h-0 h-full"><MarketRegimePanel /></div>,
    'meta-model': <div className="flex-1 min-h-0 h-full"><MetaModelPanel /></div>,
    'alpha-ranking': <div className="flex-1 min-h-0 h-full"><AlphaRankingPanel /></div>,
    'event-bus': <div className="flex-1 min-h-0 h-full"><EventBusPanel /></div>,
  };

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={activeTab}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.15 }}
        className="flex-1 flex min-h-0 p-1.5"
      >
        {contentMap[activeTab]}
      </motion.div>
    </AnimatePresence>
  );
}

// ============================================================
// DASHBOARD CONTENT
// ============================================================

function DashboardContent() {
  useKeyboardShortcuts();
  const { level } = useDashboardLevel();

  return (
    <div className="flex flex-col h-screen bg-[#0a0e17] overflow-hidden">
      {/* Top Bar */}
      <TopBar />

      {/* Ticker Strip */}
      <HeaderBar />

      {/* Main Layout: Sidebar + Content */}
      <div className="flex-1 flex min-h-0">
        <Sidebar />
        {level === 'executive' ? (
          <div className="flex-1 min-h-0">
            <ExecutiveDashboard />
          </div>
        ) : (
          <MainContent />
        )}
      </div>

      {/* Bottom Status Bar */}
      <DataStatusBar />
    </div>
  );
}

// ============================================================
// HOME PAGE
// ============================================================

export default function HomePage() {
  return (
    <UserProvider>
      <DashboardLevelProvider>
        <WebSocketProvider>
          <SimulationProvider>
            <DashboardContent />
          </SimulationProvider>
        </WebSocketProvider>
      </DashboardLevelProvider>
    </UserProvider>
  );
}
