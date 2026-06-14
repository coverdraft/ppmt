import { create } from 'zustand';

export type StrategyStatus = 'draft' | 'backtesting' | 'paper_trading' | 'forward_testing' | 'live';
export type DashboardTab = 'overview' | 'strategies' | 'trie' | 'profiles' | 'data' | 'settings';

export interface PPMTStrategy {
  id: string;
  symbol: string;
  timeframe: string;
  assetClass: string;
  status: StrategyStatus;
  saxAlpha: number;
  saxWindow: number;
  catastrophicLossPct: number;
  fuzzyThreshold: number;
  totalPnl: number;
  totalPnlPct: number;
  winRate: number;
  sharpeRatio: number;
  maxDrawdown: number;
  profitFactor: number;
  totalTrades: number;
  capitalAllocated: number;
  patternCount: number;
  trieLevel: string;
  initialCapital: number;
  patternLength: number;
  minConfidence: number;
  livingTrie: boolean;
  regimeAware: boolean;
  pruningInterval: number;
  recalibrationInterval: number;
  createdAt: string;
  updatedAt: string;
  lastRunAt: string | null;
  runs: PPMTStrategyRun[];
}

export interface PPMTStrategyRun {
  id: string;
  strategyId: string;
  runType: string;
  status: string;
  totalPnl: number;
  totalPnlPct: number;
  winRate: number;
  sharpeRatio: number;
  maxDrawdown: number;
  profitFactor: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  candlesProcessed: number;
  recalibrations: number;
  pruningRuns: number;
  equityCurve: string | null;
  tradesJson: string | null;
  startedAt: string;
  completedAt: string | null;
}

interface PPMTStore {
  // Data
  strategies: PPMTStrategy[];
  selectedStrategyId: string | null;

  // UI
  activeTab: DashboardTab;
  sidebarCollapsed: boolean;
  isLoading: boolean;
  isRunningStrategy: string | null; // strategy id being run

  // Actions
  setActiveTab: (tab: DashboardTab) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  selectStrategy: (id: string | null) => void;
  fetchStrategies: () => Promise<void>;
  createStrategy: (data: Record<string, unknown>) => Promise<PPMTStrategy | null>;
  deleteStrategy: (id: string) => Promise<boolean>;
  deployStrategy: (id: string) => Promise<boolean>;
  runStrategy: (id: string, runType: string) => Promise<PPMTStrategyRun | null>;
  setIsRunningStrategy: (id: string | null) => void;
}

export const usePPMTStore = create<PPMTStore>((set, get) => ({
  strategies: [],
  selectedStrategyId: null,
  activeTab: 'overview',
  sidebarCollapsed: false,
  isLoading: false,
  isRunningStrategy: null,

  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  selectStrategy: (id) => set({ selectedStrategyId: id }),
  setIsRunningStrategy: (id) => set({ isRunningStrategy: id }),

  fetchStrategies: async () => {
    set({ isLoading: true });
    try {
      const res = await fetch('/api/strategies');
      const json = await res.json();
      if (json.success) {
        set({ strategies: json.data, isLoading: false });
      } else {
        set({ isLoading: false });
      }
    } catch {
      set({ isLoading: false });
    }
  },

  createStrategy: async (data) => {
    try {
      const res = await fetch('/api/strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const json = await res.json();
      if (json.success) {
        await get().fetchStrategies();
        return json.data;
      }
      return null;
    } catch {
      return null;
    }
  },

  deleteStrategy: async (id) => {
    try {
      const res = await fetch(`/api/strategies/${id}`, { method: 'DELETE' });
      const json = await res.json();
      if (json.success) {
        if (get().selectedStrategyId === id) {
          set({ selectedStrategyId: null });
        }
        await get().fetchStrategies();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  },

  deployStrategy: async (id) => {
    try {
      const res = await fetch(`/api/strategies/${id}/deploy`, { method: 'POST' });
      const json = await res.json();
      if (json.success) {
        await get().fetchStrategies();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  },

  runStrategy: async (id, runType) => {
    set({ isRunningStrategy: id });
    try {
      const res = await fetch(`/api/strategies/${id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ runType }),
      });
      const json = await res.json();
      await get().fetchStrategies();
      set({ isRunningStrategy: null });
      return json.success ? json.data : null;
    } catch {
      set({ isRunningStrategy: null });
      return null;
    }
  },
}));
