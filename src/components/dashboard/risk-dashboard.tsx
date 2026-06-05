'use client';

import dynamic from 'next/dynamic';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

const loadingFallback = () => (
  <div className="flex items-center justify-center h-64 text-muted-foreground">Loading...</div>
);

const RiskManagementPanel = dynamic(() => import('@/components/dashboard/risk-management-panel'), { ssr: false, loading: loadingFallback });
const MonteCarloPanel = dynamic(() => import('@/components/dashboard/monte-carlo-panel'), { ssr: false, loading: loadingFallback });
const WalkForwardPanel = dynamic(() => import('@/components/dashboard/walk-forward-panel'), { ssr: false, loading: loadingFallback });

export default function RiskDashboard() {
  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Tabs defaultValue="controls" className="flex-1 flex flex-col min-h-0">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
          <TabsList className="bg-[#1a1f2e] h-7">
            <TabsTrigger value="controls" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#3b82f6]/20 data-[state=active]:text-[#3b82f6]">
              🛡️ Risk Controls
            </TabsTrigger>
            <TabsTrigger value="monte-carlo" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#3b82f6]/20 data-[state=active]:text-[#3b82f6]">
              🎲 Monte Carlo
            </TabsTrigger>
            <TabsTrigger value="walk-forward" className="text-[10px] font-mono h-6 px-3 data-[state=active]:bg-[#3b82f6]/20 data-[state=active]:text-[#3b82f6]">
              🔀 Walk-Forward
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="controls" className="flex-1 min-h-0 mt-0">
          <RiskManagementPanel />
        </TabsContent>
        <TabsContent value="monte-carlo" className="flex-1 min-h-0 mt-0">
          <MonteCarloPanel />
        </TabsContent>
        <TabsContent value="walk-forward" className="flex-1 min-h-0 mt-0">
          <WalkForwardPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
