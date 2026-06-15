'use client';

import { usePPMTStore, type DashboardTab } from '@/store/ppmt-strategy-store';
import { motion, AnimatePresence } from 'framer-motion';
import {
  LayoutDashboard,
  Briefcase,
  GitBranch,
  User,
  Download,
  Settings,
  ChevronLeft,
  ChevronRight,
  Activity,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const NAV_ITEMS: { id: DashboardTab; label: string; icon: React.ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
  { id: 'strategies', label: 'Strategies', icon: <Briefcase className="h-4 w-4" /> },
  { id: 'trie', label: 'Trie Explorer', icon: <GitBranch className="h-4 w-4" /> },
  { id: 'profiles', label: 'Token Profiles', icon: <User className="h-4 w-4" /> },
  { id: 'data', label: 'Data Import', icon: <Download className="h-4 w-4" /> },
  { id: 'settings', label: 'Settings', icon: <Settings className="h-4 w-4" /> },
];

export function PPMPSidebar() {
  const { activeTab, setActiveTab, sidebarCollapsed, toggleSidebar, strategies } = usePPMTStore();

  const liveCount = strategies.filter((s) => s.status === 'live').length;
  const activeCount = strategies.filter(
    (s) => s.status === 'paper_trading' || s.status === 'forward_testing' || s.status === 'live'
  ).length;

  return (
    <motion.aside
      initial={false}
      animate={{ width: sidebarCollapsed ? 64 : 240 }}
      transition={{ duration: 0.2, ease: 'easeInOut' }}
      className="flex flex-col h-screen bg-zinc-950 border-r border-zinc-800 overflow-hidden flex-shrink-0"
    >
      {/* Header */}
      <div className="flex items-center h-14 px-3 border-b border-zinc-800 gap-2">
        <Activity className="h-5 w-5 text-emerald-500 flex-shrink-0" />
        <AnimatePresence>
          {!sidebarCollapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="flex flex-col overflow-hidden"
            >
              <span className="text-sm font-bold text-zinc-50 whitespace-nowrap">PPMT Terminal</span>
              <span className="text-[10px] text-zinc-500 whitespace-nowrap">Strategy Lifecycle Engine</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Status indicator */}
      {!sidebarCollapsed && (
        <div className="px-3 py-2 border-b border-zinc-800">
          <div className="flex items-center gap-2 text-xs">
            <span className={cn(
              'h-2 w-2 rounded-full',
              activeCount > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'
            )} />
            <span className="text-zinc-400">
              {activeCount} active · {liveCount} live
            </span>
          </div>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 py-2 px-1.5 space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={cn(
                'flex items-center gap-3 w-full rounded-lg text-sm transition-all duration-150',
                sidebarCollapsed ? 'justify-center px-2 py-2.5' : 'px-3 py-2',
                isActive
                  ? 'bg-emerald-500/10 text-emerald-400'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
              )}
              title={sidebarCollapsed ? item.label : undefined}
            >
              <span className={cn('flex-shrink-0', isActive && 'text-emerald-400')}>{item.icon}</span>
              <AnimatePresence>
                {!sidebarCollapsed && (
                  <motion.span
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="whitespace-nowrap"
                  >
                    {item.label}
                  </motion.span>
                )}
              </AnimatePresence>
              {!sidebarCollapsed && item.id === 'strategies' && (
                <span className="ml-auto text-[10px] font-mono bg-zinc-800 text-zinc-400 rounded px-1.5 py-0.5">
                  {strategies.length}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Collapse toggle */}
      <div className="p-2 border-t border-zinc-800">
        <button
          onClick={toggleSidebar}
          className="flex items-center justify-center w-full py-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50 transition-colors"
        >
          {sidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>
    </motion.aside>
  );
}
