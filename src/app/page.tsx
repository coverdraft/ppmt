'use client';

import { usePPMTStore, type DashboardTab } from '@/store/ppmt-strategy-store';
import { useEffect } from 'react';
import { PPMPSidebar } from '@/components/ppmt/sidebar';
import { OverviewTab } from '@/components/ppmt/overview-tab';
import { StrategiesTab } from '@/components/ppmt/strategies-tab';
import { TrieExplorerTab } from '@/components/ppmt/trie-explorer-tab';
import { ProfilesTab } from '@/components/ppmt/profiles-tab';
import { DataImportTab } from '@/components/ppmt/data-import-tab';
import { SettingsTab } from '@/components/ppmt/settings-tab';

const TAB_COMPONENTS: Record<DashboardTab, React.ComponentType> = {
  overview: OverviewTab,
  strategies: StrategiesTab,
  trie: TrieExplorerTab,
  profiles: ProfilesTab,
  data: DataImportTab,
  settings: SettingsTab,
};

export default function PPMTDashboard() {
  const { activeTab, fetchStrategies, sidebarCollapsed } = usePPMTStore();

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  const TabComponent = TAB_COMPONENTS[activeTab] || OverviewTab;

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
      {/* Sidebar */}
      <PPMPSidebar />

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {/* Top bar */}
        <header className="sticky top-0 z-10 bg-zinc-950/80 backdrop-blur-md border-b border-zinc-800 px-6 py-3">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-base font-semibold text-zinc-100 capitalize">{activeTab.replace('_', ' ')}</h1>
              <p className="text-[11px] text-zinc-500">
                {activeTab === 'overview' && 'Portfolio overview and strategy pipeline'}
                {activeTab === 'strategies' && 'Manage strategy lifecycle: Create → Backtest → Paper → Forward → Live'}
                {activeTab === 'trie' && 'Pattern trie statistics and exploration'}
                {activeTab === 'profiles' && 'Token profiles and calibration settings'}
                {activeTab === 'data' && 'Import historical OHLCV data'}
                {activeTab === 'settings' && 'Engine configuration and status'}
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              PPMT v0.12.0
            </div>
          </div>
        </header>

        {/* Content area */}
        <div className="p-6">
          <TabComponent />
        </div>
      </main>
    </div>
  );
}
