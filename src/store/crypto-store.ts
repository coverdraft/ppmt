import { create } from 'zustand';

export interface TokenData {
  id: string;
  address?: string;
  symbol: string;
  name: string;
  chain: string;
  priceUsd: number;
  volume24h: number;
  liquidity: number;
  marketCap: number;
  priceChange5m: number;
  priceChange15m: number;
  priceChange1h: number;
  priceChange24h: number;
  riskScore?: number;
  priceHistory?: number[];
}

export interface SignalData {
  id: string;
  type: string;
  tokenId: string;
  tokenSymbol?: string;
  tokenPrice?: number;
  chain?: string;
  confidence: number;
  direction: string;
  description: string;
  priceTarget?: number;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

export interface SmartMoneyAlert {
  id: string;
  walletLabel: string;
  walletAddress?: string;
  tokenSymbol: string;
  chain: string;
  action: string;
  amount: number;
  price: number;
  timestamp: number;
  smartMoneyScore?: number;
  walletType?: string;
}

export interface BotAlert {
  id: string;
  botLabel: string;
  botAddress: string;
  botType: string;
  confidence: number;
  tokenSymbol: string;
  chain: string;
  action: string;
  amount: number;
  price: number;
  timestamp: number;
  mevExtracted?: number;
  isFrontrun?: boolean;
  isSandwich?: boolean;
  isWashTrade?: boolean;
  slippageBps?: number;
}

export interface TraderStats {
  totalTraders: number;
  totalBots: number;
  totalSmartMoney: number;
  totalWhales: number;
  totalSnipers: number;
  avgWinRate: number;
  totalVolume: number;
  totalMevExtracted: number;
  totalFrontruns: number;
  totalSandwiches: number;
  botTypeBreakdown: Record<string, number>;
  chainBreakdown: Record<string, { traders: number; bots: number }>;
}

export interface MarketSummary {
  btcPrice: number;
  ethPrice: number;
  totalMarketCap: number;
  fearGreedIndex: number;
}

export type ActiveTab = 'dashboard' | 'charts' | 'multi-chain' | 'signals' | 'smart-money' | 'deep-analysis' | 'dna-scanner' | 'predictive' | 'brain' | 'strategy-lab' | 'backtesting' | 'paper-trading' | 'patterns' | 'kill-switches' | 'capital-allocation' | 'portfolio' | 'risk' | 'decisions' | 'export-import';

export interface AlertSummary {
  id: string;
  title: string;
  message: string;
  category: string;
  severity: string;
  isRead: boolean;
  createdAt: string;
  metadata?: Record<string, unknown>;
  linkTo?: string;
}

export type TraderIntelFilter = 'ALL' | 'BOTS' | 'SMART_MONEY' | 'WHALES' | 'SNIPERS';

interface CryptoStore {
  // Tokens
  tokens: TokenData[];
  selectedToken: TokenData | null;
  setTokens: (tokens: TokenData[]) => void;
  updateToken: (token: Partial<TokenData> & { symbol: string }) => void;
  selectToken: (token: TokenData | null) => void;

  // Signals
  signals: SignalData[];
  addSignal: (signal: SignalData) => void;

  // Smart Money Alerts
  smartMoneyAlerts: SmartMoneyAlert[];
  addSmartMoneyAlert: (alert: SmartMoneyAlert) => void;

  // Bot Alerts
  botAlerts: BotAlert[];
  addBotAlert: (alert: BotAlert) => void;

  // Trader Stats
  traderStats: TraderStats | null;
  setTraderStats: (stats: TraderStats) => void;

  // Market
  marketSummary: MarketSummary | null;
  setMarketSummary: (summary: MarketSummary) => void;

  // UI State
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  isConnected: boolean;
  setConnected: (connected: boolean) => void;

  // Filters
  chainFilter: string;
  setChainFilter: (chain: string) => void;
  riskFilter: string;
  setRiskFilter: (risk: string) => void;
  sortBy: string;
  setSortBy: (sort: string) => void;
  signalFilter: string;
  setSignalFilter: (filter: string) => void;

  // Trader Intelligence
  traderIntelFilter: TraderIntelFilter;
  setTraderIntelFilter: (filter: TraderIntelFilter) => void;
  selectedTraderId: string | null;
  setSelectedTraderId: (id: string | null) => void;

  // Alerts
  alerts: AlertSummary[];
  addAlert: (alert: AlertSummary) => void;
  unreadAlertCount: number;
  markAlertRead: (id: string) => void;
  clearAlerts: () => void;
}

export const useCryptoStore = create<CryptoStore>((set) => ({
  // Tokens
  tokens: [],
  selectedToken: null,
  setTokens: (tokens) => set({ tokens }),
  updateToken: (tokenUpdate) =>
    set((state) => ({
      tokens: state.tokens.map((t) =>
        t.symbol === tokenUpdate.symbol ? { ...t, ...tokenUpdate } : t
      ),
      selectedToken:
        state.selectedToken?.symbol === tokenUpdate.symbol
          ? { ...state.selectedToken, ...tokenUpdate }
          : state.selectedToken,
    })),
  selectToken: (token) => set({ selectedToken: token }),

  // Signals
  signals: [],
  addSignal: (signal) =>
    set((state) => ({
      signals: [signal, ...state.signals].slice(0, 50),
    })),

  // Smart Money Alerts
  smartMoneyAlerts: [],
  addSmartMoneyAlert: (alert) =>
    set((state) => ({
      smartMoneyAlerts: [alert, ...state.smartMoneyAlerts].slice(0, 20),
    })),

  // Bot Alerts
  botAlerts: [],
  addBotAlert: (alert) =>
    set((state) => ({
      botAlerts: [alert, ...state.botAlerts].slice(0, 30),
    })),

  // Trader Stats
  traderStats: null,
  setTraderStats: (stats) => set({ traderStats: stats }),

  // Market
  marketSummary: null,
  setMarketSummary: (summary) => set({ marketSummary: summary }),

  // UI State
  activeTab: 'dashboard',
  setActiveTab: (tab) => set({ activeTab: tab }),
  isConnected: false,
  setConnected: (connected) => set({ isConnected: connected }),

  // Filters
  chainFilter: 'ALL',
  setChainFilter: (chain) => set({ chainFilter: chain }),
  riskFilter: 'ALL',
  setRiskFilter: (risk) => set({ riskFilter: risk }),
  sortBy: 'volume',
  setSortBy: (sort) => set({ sortBy: sort }),
  signalFilter: 'ALL',
  setSignalFilter: (filter) => set({ signalFilter: filter }),

  // Trader Intelligence
  traderIntelFilter: 'ALL',
  setTraderIntelFilter: (filter) => set({ traderIntelFilter: filter }),
  selectedTraderId: null,
  setSelectedTraderId: (id) => set({ selectedTraderId: id }),

  // Alerts
  alerts: [],
  addAlert: (alert) =>
    set((state) => {
      const alerts = [alert, ...state.alerts].slice(0, 50);
      const unreadAlertCount = alerts.filter((a) => !a.isRead).length;
      return { alerts, unreadAlertCount };
    }),
  unreadAlertCount: 0,
  markAlertRead: (id) =>
    set((state) => {
      const alerts = state.alerts.map((a) =>
        a.id === id ? { ...a, isRead: true } : a,
      );
      const unreadAlertCount = alerts.filter((a) => !a.isRead).length;
      return { alerts, unreadAlertCount };
    }),
  clearAlerts: () => set({ alerts: [], unreadAlertCount: 0 }),
}));
