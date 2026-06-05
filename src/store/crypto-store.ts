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

export type ActiveTab = 'dashboard' | 'charts' | 'multi-chain' | 'signals' | 'smart-money' | 'deep-analysis' | 'dna-scanner' | 'predictive' | 'brain' | 'strategy-lab' | 'backtesting' | 'paper-trading' | 'patterns' | 'kill-switches' | 'capital-allocation' | 'portfolio' | 'risk' | 'decisions' | 'export-import' | 'risk-pre-filter' | 'portfolio-intelligence' | 'execution-cost' | 'market-regime' | 'meta-model' | 'alpha-ranking' | 'event-bus';

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

export interface ImpactAnalysisResult {
  approved: boolean;
  impactScore: number;
  riskContribution: number;
  diversificationDelta: number;
  varDelta: number;
  correlationWithExisting: number;
  recommendations: string[];
}

export interface StressTestScenarioResult {
  scenarioId: string;
  scenarioName: string;
  portfolioImpactUsd: number;
  portfolioImpactPct: number;
  positionImpacts: Record<string, number>;
  recoveryDaysEstimate: number;
}

export interface StressTestResult {
  scenarioResults: StressTestScenarioResult[];
  worstCase: StressTestScenarioResult | null;
  averageImpactPct: number;
  computedAt: string;
}

export interface OptimizationResult {
  weights: Record<string, number>;
  expectedReturn: number;
  expectedVol: number;
  sharpeRatio: number;
  method: string;
}

export interface PortfolioIntelligenceState {
  impactResult: ImpactAnalysisResult | null;
  impactLoading: boolean;
  stressResult: StressTestResult | null;
  stressLoading: boolean;
  optimizationResult: OptimizationResult | null;
  optimizationLoading: boolean;
  currentWeights: Record<string, number>;
}

export interface MetaModelEngine {
  name: string;
  accuracy: number;
  weight: number;
  predictions: number;
  last24hAccuracy: number;
  status: 'active' | 'retraining' | 'flagged' | 'idle';
  weightChange: number;
  rollingD7: number;
  rollingD30: number;
  rollingD90: number;
}

export interface MetaModelReport {
  engines: MetaModelEngine[];
  overallAccuracy: number;
  lastUpdated: string;
}

export interface ExecutionCostResult {
  totalCostPct: number;
  slippagePct: number;
  marketImpactPct: number;
  feePct: number;
  networkFeePct: number;
  estimatedTimeSec: number;
  recommendation: {
    orderType: string;
    timeHorizon: string;
    splitCount?: number;
  };
}

export interface ExecutionLogEntry {
  id: string;
  time: string;
  token: string;
  side: 'BUY' | 'SELL';
  size: number;
  estimatedCost: number;
  actualCost: number | null;
}

export interface EventBusEvent {
  id: string;
  type: string;
  source: string;
  priority: 'SYNC' | 'SEMI_SYNC' | 'ASYNC';
  payload: string;
  timestamp: number;
}

export interface MarketRegimeData {
  regime: string;
  confidence: number;
  transitionProbabilities: Record<string, number>;
  durationEstimate: 'hours' | 'days' | 'weeks';
  keyIndicators: { name: string; value: number; signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL' }[];
  lastChangedAt: string;
  assessedAt: string;
}

export interface PreFilterCheckResult {
  name: string;
  passed: boolean;
  reason?: string;
}

export interface PreFilterResult {
  passed: boolean;
  checks: PreFilterCheckResult[];
  riskScore: number;
  reason?: string;
  warnings?: string[];
}

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

  // Execution Cost
  executionCost: ExecutionCostResult | null;
  setExecutionCost: (result: ExecutionCostResult | null) => void;
  executionLog: ExecutionLogEntry[];
  addExecutionLog: (entry: ExecutionLogEntry) => void;

  // Alerts
  alerts: AlertSummary[];
  addAlert: (alert: AlertSummary) => void;
  unreadAlertCount: number;
  markAlertRead: (id: string) => void;
  clearAlerts: () => void;

  // Risk Pre-Filter
  riskPreFilterResult: PreFilterResult | null;
  riskPreFilterLoading: boolean;
  setRiskPreFilterResult: (result: PreFilterResult | null) => void;
  setRiskPreFilterLoading: (loading: boolean) => void;
  autoPreFilterEnabled: boolean;
  setAutoPreFilterEnabled: (enabled: boolean) => void;

  // Event Bus
  eventBusEvents: EventBusEvent[];
  eventBusConnected: boolean;
  addEventBusEvent: (event: EventBusEvent) => void;
  setEventBusEvents: (events: EventBusEvent[]) => void;
  setEventBusConnected: (connected: boolean) => void;
  clearEventBusEvents: () => void;

  // Market Regime
  marketRegime: MarketRegimeData | null;
  setMarketRegime: (data: MarketRegimeData | null) => void;
  marketRegimeLoading: boolean;
  setMarketRegimeLoading: (loading: boolean) => void;

  // Portfolio Intelligence
  portfolioIntelligence: PortfolioIntelligenceState;
  setPortfolioIntelligence: (state: Partial<PortfolioIntelligenceState>) => void;
  clearPortfolioIntelligence: () => void;

  // Meta Model
  metaModelReport: MetaModelReport | null;
  setMetaModelReport: (report: MetaModelReport | null) => void;
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

  // Execution Cost
  executionCost: null,
  setExecutionCost: (result) => set({ executionCost: result }),
  executionLog: [],
  addExecutionLog: (entry) =>
    set((state) => ({
      executionLog: [entry, ...state.executionLog].slice(0, 5),
    })),

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

  // Risk Pre-Filter
  riskPreFilterResult: null,
  riskPreFilterLoading: false,
  setRiskPreFilterResult: (result) => set({ riskPreFilterResult: result }),
  setRiskPreFilterLoading: (loading) => set({ riskPreFilterLoading: loading }),
  autoPreFilterEnabled: false,
  setAutoPreFilterEnabled: (enabled) => set({ autoPreFilterEnabled: enabled }),

  // Event Bus
  eventBusEvents: [],
  eventBusConnected: false,
  addEventBusEvent: (event) =>
    set((state) => ({
      eventBusEvents: [...state.eventBusEvents, event].slice(-200),
    })),
  setEventBusEvents: (events) => set({ eventBusEvents: events.slice(-200) }),
  setEventBusConnected: (connected) => set({ eventBusConnected: connected }),
  clearEventBusEvents: () => set({ eventBusEvents: [] }),

  // Market Regime
  marketRegime: null,
  setMarketRegime: (data) => set({ marketRegime: data }),
  marketRegimeLoading: false,
  setMarketRegimeLoading: (loading) => set({ marketRegimeLoading: loading }),

  // Portfolio Intelligence
  portfolioIntelligence: {
    impactResult: null,
    impactLoading: false,
    stressResult: null,
    stressLoading: false,
    optimizationResult: null,
    optimizationLoading: false,
    currentWeights: {},
  },
  setPortfolioIntelligence: (partial) =>
    set((state) => ({
      portfolioIntelligence: { ...state.portfolioIntelligence, ...partial },
    })),
  clearPortfolioIntelligence: () =>
    set({
      portfolioIntelligence: {
        impactResult: null,
        impactLoading: false,
        stressResult: null,
        stressLoading: false,
        optimizationResult: null,
        optimizationLoading: false,
        currentWeights: {},
      },
    }),

  // Meta Model
  metaModelReport: null,
  setMetaModelReport: (report) => set({ metaModelReport: report }),
}));
