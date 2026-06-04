'use client';

import dynamic from 'next/dynamic';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

// ============================================================
// DYNAMIC IMPORTS — Lazy load Strategy Lab sub-tabs
// ============================================================

const loadingFallback = () => (
  <div className="flex items-center justify-center h-64 text-muted-foreground">Loading...</div>
);

// Named exports
const AIStrategyOptimizer = dynamic(() => import('@/components/dashboard/ai-strategy-optimizer'), { ssr: false, loading: loadingFallback });
const RiskManagementPanel = dynamic(() => import('@/components/dashboard/risk-management-panel'), { ssr: false, loading: loadingFallback });

// Default exports
const TradingSystemsLab = dynamic(() => import('@/components/dashboard/trading-systems-lab'), { ssr: false, loading: loadingFallback });
const TradeHistoryPanel = dynamic(() => import('@/components/dashboard/trade-history-panel'), { ssr: false, loading: loadingFallback });
const StrategyStateTracker = dynamic(() => import('@/components/dashboard/strategy-state-tracker'), { ssr: false, loading: loadingFallback });
const PaperTradingPanel = dynamic(() => import('@/components/dashboard/paper-trading-panel'), { ssr: false, loading: loadingFallback });
const StrategyEvolutionTree = dynamic(() => import('@/components/dashboard/strategy-evolution-tree'), { ssr: false, loading: loadingFallback });
const HistoricalChartsPanel = dynamic(() => import('@/components/dashboard/historical-charts-panel'), { ssr: false, loading: loadingFallback });
const ExportImportPanel = dynamic(() => import('@/components/dashboard/export-import-panel'), { ssr: false, loading: loadingFallback });
const StrategyMarketplace = dynamic(() => import('@/components/dashboard/strategy-marketplace'), { ssr: false, loading: loadingFallback });

// ============================================================
// STRATEGY LAB CONTENT (Classic + AI Optimizer tabs)
// ============================================================

export default function StrategyLabContent() {
  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Tabs defaultValue="ai-optimizer" className="flex-1 flex flex-col min-h-0">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
          <TabsList className="bg-[#1a1f2e] h-7">
            <TabsTrigger value="ai-optimizer" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              🤖 AI Manager
            </TabsTrigger>
            <TabsTrigger value="classic" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              Classic
            </TabsTrigger>
            <TabsTrigger value="trade-history" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              📊 Trade History
            </TabsTrigger>
            <TabsTrigger value="strategy-states" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              📋 Strategy States
            </TabsTrigger>
            <TabsTrigger value="paper-trading" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              🎰 Paper Trading
            </TabsTrigger>
            <TabsTrigger value="evolution-tree" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              🧬 Evolution Tree
            </TabsTrigger>
            <TabsTrigger value="analytics" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              📈 Analytics
            </TabsTrigger>
            <TabsTrigger value="risk-management" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              🛡️ Risk Mgmt
            </TabsTrigger>
            <TabsTrigger value="marketplace" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              🏪 Marketplace
            </TabsTrigger>
            <TabsTrigger value="export-import" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#d4af37]/20 data-[state=active]:text-[#d4af37]">
              💾 Export/Import
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="classic" className="flex-1 min-h-0 mt-0">
          <TradingSystemsLab />
        </TabsContent>
        <TabsContent value="ai-optimizer" className="flex-1 min-h-0 mt-0 overflow-hidden">
          <AIStrategyOptimizer />
        </TabsContent>
        <TabsContent value="trade-history" className="flex-1 min-h-0 mt-0">
          <TradeHistoryPanel />
        </TabsContent>
        <TabsContent value="strategy-states" className="flex-1 min-h-0 mt-0">
          <StrategyStateTracker />
        </TabsContent>
        <TabsContent value="paper-trading" className="flex-1 min-h-0 mt-0">
          <PaperTradingPanel />
        </TabsContent>
        <TabsContent value="evolution-tree" className="flex-1 min-h-0 mt-0">
          <StrategyEvolutionTree />
        </TabsContent>
        <TabsContent value="analytics" className="flex-1 min-h-0 mt-0">
          <HistoricalChartsPanel />
        </TabsContent>
        <TabsContent value="risk-management" className="flex-1 min-h-0 mt-0">
          <RiskManagementPanel />
        </TabsContent>
        <TabsContent value="marketplace" className="flex-1 min-h-0 mt-0">
          <StrategyMarketplace />
        </TabsContent>
        <TabsContent value="export-import" className="flex-1 min-h-0 mt-0">
          <ExportImportPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
