'use client';

import dynamic from 'next/dynamic';

// ============================================================
// DYNAMIC IMPORTS — Code-split all tab components
// Extracted from page.tsx to reduce Turbopack module graph
// complexity and prevent "Failed to write app endpoint /page"
// panic when using Turbopack dev server.
// ============================================================

const loadingFallback = () => (
  <div className="flex items-center justify-center h-64 text-muted-foreground">Loading...</div>
);

// Named exports — require .then() remapping
export const TokenFlow = dynamic(() => import('@/components/dashboard/token-flow').then(m => ({ default: m.TokenFlow })), { ssr: false, loading: loadingFallback });
export const SignalCenter = dynamic(() => import('@/components/dashboard/signal-center').then(m => ({ default: m.SignalCenter })), { ssr: false, loading: loadingFallback });
export const DNAScanner = dynamic(() => import('@/components/dashboard/dna-scanner').then(m => ({ default: m.DNAScanner })), { ssr: false, loading: loadingFallback });
export const PatternBuilder = dynamic(() => import('@/components/dashboard/pattern-builder').then(m => ({ default: m.PatternBuilder })), { ssr: false, loading: loadingFallback });
export const IntelligenceModules = dynamic(() => import('@/components/dashboard/intelligence-modules').then(m => ({ default: m.IntelligenceModules })), { ssr: false, loading: loadingFallback });
export const TraderIntelligencePanel = dynamic(() => import('@/components/dashboard/trader-intelligence').then(m => ({ default: m.TraderIntelligencePanel })), { ssr: false, loading: loadingFallback });
export const OHLCVChart = dynamic(() => import('@/components/dashboard/ohlcv-chart').then(m => ({ default: m.OHLCVChart })), { ssr: false, loading: loadingFallback });
export const DataStatusBar = dynamic(() => import('@/components/dashboard/data-status-bar').then(m => ({ default: m.DataStatusBar })), { ssr: false, loading: loadingFallback });
export const DeepAnalysisPanel = dynamic(() => import('@/components/dashboard/deep-analysis-panel').then(m => ({ default: m.DeepAnalysisPanel })), { ssr: false, loading: loadingFallback });
export const NotificationCenter = dynamic(() => import('@/components/dashboard/notification-center').then(m => ({ default: m.NotificationCenter })), { ssr: false, loading: loadingFallback });
export const HeaderBar = dynamic(() => import('@/components/dashboard/header-bar').then(m => ({ default: m.HeaderBar })), { ssr: false, loading: loadingFallback });
export const ExecutiveDashboard = dynamic(() => import('@/components/dashboard/executive-dashboard').then(m => ({ default: m.ExecutiveDashboard })), { ssr: false, loading: loadingFallback });
export const ExecutionCostPanel = dynamic(() => import('@/components/dashboard/execution-cost-panel').then(m => ({ default: m.ExecutionCostPanel })), { ssr: false, loading: loadingFallback });
export const MetaModelPanel = dynamic(() => import('@/components/dashboard/meta-model-panel').then(m => ({ default: m.MetaModelPanel })), { ssr: false, loading: loadingFallback });
export const AlphaRankingPanel = dynamic(() => import('@/components/dashboard/alpha-ranking-panel').then(m => ({ default: m.AlphaRankingPanel })), { ssr: false, loading: loadingFallback });
export const RiskPreFilterPanel = dynamic(() => import('@/components/dashboard/risk-pre-filter-panel').then(m => ({ default: m.RiskPreFilterPanel })), { ssr: false, loading: loadingFallback });
export const PortfolioIntelligencePanel = dynamic(() => import('@/components/dashboard/portfolio-intelligence-panel').then(m => ({ default: m.PortfolioIntelligencePanel })), { ssr: false, loading: loadingFallback });
export const MarketRegimePanel = dynamic(() => import('@/components/dashboard/market-regime-panel').then(m => ({ default: m.MarketRegimePanel })), { ssr: false, loading: loadingFallback });
export const EventBusPanel = dynamic(() => import('@/components/dashboard/event-bus-panel').then(m => ({ default: m.EventBusPanel })), { ssr: false, loading: loadingFallback });

// Default exports — simpler dynamic import
export const BacktestingLab = dynamic(() => import('@/components/dashboard/backtesting-lab'), { ssr: false, loading: loadingFallback });
export const BigDataPredictive = dynamic(() => import('@/components/dashboard/big-data-predictive'), { ssr: false, loading: loadingFallback });
export const BrainControl = dynamic(() => import('@/components/dashboard/brain-control'), { ssr: false, loading: loadingFallback });
export const MultiChainDashboard = dynamic(() => import('@/components/dashboard/multi-chain-dashboard'), { ssr: false, loading: loadingFallback });
export const StrategyLabContent = dynamic(() => import('@/components/dashboard/strategy-lab-content'), { ssr: false, loading: loadingFallback });
export const KillSwitchPanel = dynamic(() => import('@/components/dashboard/kill-switch-panel'), { ssr: false, loading: loadingFallback });
export const AllocationDashboard = dynamic(() => import('@/components/dashboard/allocation-dashboard'), { ssr: false, loading: loadingFallback });
export const PortfolioView = dynamic(() => import('@/components/dashboard/portfolio-view'), { ssr: false, loading: loadingFallback });
export const DecisionDashboard = dynamic(() => import('@/components/dashboard/decision-dashboard'), { ssr: false, loading: loadingFallback });
export const RiskDashboard = dynamic(() => import('@/components/dashboard/risk-dashboard'), { ssr: false, loading: loadingFallback });
export const PaperTradingPanel = dynamic(() => import('@/components/dashboard/paper-trading-panel'), { ssr: false, loading: loadingFallback });
export const ExportImportPanel = dynamic(() => import('@/components/dashboard/export-import-panel'), { ssr: false, loading: loadingFallback });
