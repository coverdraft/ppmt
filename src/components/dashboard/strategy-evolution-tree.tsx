'use client';

import React, { useState, useCallback, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { motion, AnimatePresence } from 'framer-motion';
import {
  GitBranch,
  ZoomIn,
  ZoomOut,
  Maximize2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Activity,
  Play,
  Pause,
  Clock,
  Zap,
  AlertTriangle,
  FlaskConical,
  TrendingUp,
  TrendingDown,
  Layers,
  RefreshCw,
  Loader2,
  ArrowRight,
  Trophy,
  Dna,
  X,
  Eye,
  ShieldCheck,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

interface BacktestStats {
  winRate: number;
  profitFactor: number;
  totalTrades: number;
  sharpeRatio: number;
  totalPnlPct: number;
  maxDrawdownPct: number;
}

interface TreeNode {
  id: string;
  name: string;
  generation: number;
  createdAt: string;
  backtestStats: BacktestStats | null;
  status: string;
  improvementPct: number;
  parentId: string | null;
  parentName: string | null;
  evolutionType: string | null;
  triggerMetric: string | null;
  category: string;
  icon: string;
  primaryTimeframe: string;
  isActive: boolean;
  isPaperTrading: boolean;
  children: TreeNode[];
}

interface EvolutionStats {
  totalSystems: number;
  totalEvolutions: number;
  avgImprovement: number;
  bestLineage: string;
  maxGeneration: number;
  improvedCount: number;
  degradedCount: number;
}

interface TreeResponse {
  trees: TreeNode[];
  stats: EvolutionStats;
}

// ============================================================
// STATUS CONFIG
// ============================================================

const STATUS_CONFIG: Record<string, {
  label: string;
  color: string;
  bgColor: string;
  borderColor: string;
  dotColor: string;
  icon: React.ElementType;
}> = {
  ACTIVE: {
    label: 'ACTIVE',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-400/10',
    borderColor: 'border-emerald-400/40',
    dotColor: 'bg-emerald-400',
    icon: Activity,
  },
  LIVE: {
    label: 'LIVE',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-400/10',
    borderColor: 'border-emerald-400/40',
    dotColor: 'bg-emerald-400',
    icon: Activity,
  },
  PAPER_TRADING: {
    label: 'PAPER',
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-400/10',
    borderColor: 'border-cyan-400/40',
    dotColor: 'bg-cyan-400',
    icon: Play,
  },
  IDLE: {
    label: 'IDLE',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-400/10',
    borderColor: 'border-yellow-400/40',
    dotColor: 'bg-yellow-400',
    icon: Clock,
  },
  EVOLVED: {
    label: 'EVOLVED',
    color: 'text-gray-400',
    bgColor: 'bg-gray-400/10',
    borderColor: 'border-gray-400/40',
    dotColor: 'bg-gray-400',
    icon: Zap,
  },
  PAUSED: {
    label: 'PAUSED',
    color: 'text-orange-400',
    bgColor: 'bg-orange-400/10',
    borderColor: 'border-orange-400/40',
    dotColor: 'bg-orange-400',
    icon: Pause,
  },
  ERROR: {
    label: 'ERROR',
    color: 'text-red-400',
    bgColor: 'bg-red-400/10',
    borderColor: 'border-red-400/40',
    dotColor: 'bg-red-400',
    icon: AlertTriangle,
  },
  BACKTESTING: {
    label: 'TEST',
    color: 'text-purple-400',
    bgColor: 'bg-purple-400/10',
    borderColor: 'border-purple-400/40',
    dotColor: 'bg-purple-400',
    icon: FlaskConical,
  },
};

const CATEGORY_COLORS: Record<string, string> = {
  ALPHA_HUNTER: '#f59e0b',
  SMART_MONEY: '#10b981',
  TECHNICAL: '#06b6d4',
  DEFENSIVE: '#14b8a6',
  BOT_AWARE: '#8b5cf6',
  DEEP_ANALYSIS: '#ec4899',
  MICRO_STRUCTURE: '#f97316',
  ADAPTIVE: '#f43f5e',
};

const EVOLUTION_TYPE_COLORS: Record<string, string> = {
  parameter_adjust: '#06b6d4',
  phase_specialize: '#8b5cf6',
  hybrid_generate: '#f59e0b',
  synthetic_loop: '#10b981',
};

// ============================================================
// HELPERS
// ============================================================

function formatTimeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function getCategoryColor(category: string): string {
  return CATEGORY_COLORS[category] || '#64748b';
}

function countNodes(node: TreeNode): number {
  let count = 1;
  for (const child of node.children) {
    count += countNodes(child);
  }
  return count;
}

function flattenNodes(node: TreeNode): TreeNode[] {
  const result: TreeNode[] = [node];
  for (const child of node.children) {
    result.push(...flattenNodes(child));
  }
  return result;
}

// ============================================================
// STATS BAR COMPONENT
// ============================================================

function StatsBar({ stats, nodeCount }: { stats: EvolutionStats; nodeCount: number }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0 overflow-x-auto">
      <div className="flex items-center gap-1.5 shrink-0">
        <GitBranch className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[9px] font-mono text-[#64748b] uppercase">Lineage</span>
      </div>

      <div className="h-4 w-px bg-[#1e293b] shrink-0" />

      <div className="flex items-center gap-1 bg-[#111827] px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
        <Layers className="h-3 w-3 text-cyan-400" />
        <span className="text-[8px] font-mono text-[#64748b]">SYSTEMS</span>
        <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">{nodeCount}</span>
      </div>

      <div className="flex items-center gap-1 bg-[#111827] px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
        <Zap className="h-3 w-3 text-amber-400" />
        <span className="text-[8px] font-mono text-[#64748b]">EVOLVED</span>
        <span className="text-[10px] font-mono font-bold text-amber-400">{stats.totalEvolutions}</span>
      </div>

      <div className="flex items-center gap-1 bg-[#111827] px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
        <TrendingUp className="h-3 w-3 text-emerald-400" />
        <span className="text-[8px] font-mono text-[#64748b]">AVG IMPR</span>
        <span className={`text-[10px] font-mono font-bold ${stats.avgImprovement >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
          {stats.avgImprovement >= 0 ? '+' : ''}{stats.avgImprovement.toFixed(1)}%
        </span>
      </div>

      <div className="flex items-center gap-1 bg-[#111827] px-2 py-0.5 rounded border border-[#1e293b] shrink-0">
        <Trophy className="h-3 w-3 text-[#d4af37]" />
        <span className="text-[8px] font-mono text-[#64748b]">DEPTH</span>
        <span className="text-[10px] font-mono font-bold text-[#d4af37]">Gen {stats.maxGeneration}</span>
      </div>

      {stats.improvedCount > 0 && (
        <div className="flex items-center gap-1 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20 shrink-0">
          <TrendingUp className="h-3 w-3 text-emerald-400" />
          <span className="text-[10px] font-mono font-bold text-emerald-400">{stats.improvedCount}</span>
        </div>
      )}

      {stats.degradedCount > 0 && (
        <div className="flex items-center gap-1 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20 shrink-0">
          <TrendingDown className="h-3 w-3 text-red-400" />
          <span className="text-[10px] font-mono font-bold text-red-400">{stats.degradedCount}</span>
        </div>
      )}

      {stats.bestLineage && stats.bestLineage !== 'No evolutions yet' && (
        <div className="hidden lg:flex items-center gap-1 shrink-0 ml-auto">
          <span className="text-[8px] font-mono text-[#64748b]">BEST LINEAGE:</span>
          <span className="text-[8px] font-mono text-[#d4af37] truncate max-w-[300px]">{stats.bestLineage}</span>
        </div>
      )}
    </div>
  );
}

// ============================================================
// CONTROLS BAR COMPONENT
// ============================================================

function ControlsBar({
  zoom,
  onZoomIn,
  onZoomOut,
  onZoomReset,
  onExpandAll,
  onCollapseAll,
  generationFilter,
  onGenerationFilterChange,
  statusFilter,
  onStatusFilterChange,
  maxGeneration,
}: {
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onZoomReset: () => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
  generationFilter: number;
  onGenerationFilterChange: (n: number) => void;
  statusFilter: string;
  onStatusFilterChange: (s: string) => void;
  maxGeneration: number;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-1.5 bg-[#0a0e17] border-b border-[#1e293b] shrink-0">
      {/* Zoom controls */}
      <div className="flex items-center gap-0.5 bg-[#111827] rounded border border-[#1e293b] p-0.5">
        <button onClick={onZoomOut} className="p-1 hover:bg-[#1e293b] rounded transition-colors" title="Zoom Out">
          <ZoomOut className="h-3 w-3 text-[#94a3b8]" />
        </button>
        <span className="text-[8px] font-mono text-[#94a3b8] px-1 min-w-[36px] text-center">{Math.round(zoom * 100)}%</span>
        <button onClick={onZoomIn} className="p-1 hover:bg-[#1e293b] rounded transition-colors" title="Zoom In">
          <ZoomIn className="h-3 w-3 text-[#94a3b8]" />
        </button>
        <button onClick={onZoomReset} className="p-1 hover:bg-[#1e293b] rounded transition-colors" title="Reset Zoom">
          <Maximize2 className="h-3 w-3 text-[#94a3b8]" />
        </button>
      </div>

      {/* Expand/Collapse */}
      <div className="flex items-center gap-0.5 bg-[#111827] rounded border border-[#1e293b] p-0.5">
        <button onClick={onExpandAll} className="p-1 hover:bg-[#1e293b] rounded transition-colors text-[7px] font-mono text-[#94a3b8] flex items-center gap-0.5" title="Expand All">
          <ChevronDown className="h-3 w-3" />
          <span className="hidden sm:inline">EXPAND</span>
        </button>
        <button onClick={onCollapseAll} className="p-1 hover:bg-[#1e293b] rounded transition-colors text-[7px] font-mono text-[#94a3b8] flex items-center gap-0.5" title="Collapse All">
          <ChevronUp className="h-3 w-3" />
          <span className="hidden sm:inline">COLLAPSE</span>
        </button>
      </div>

      {/* Generation filter */}
      <div className="flex items-center gap-1 bg-[#111827] rounded border border-[#1e293b] px-1.5 py-0.5">
        <span className="text-[7px] font-mono text-[#64748b]">GEN</span>
        <select
          value={generationFilter}
          onChange={(e) => onGenerationFilterChange(Number(e.target.value))}
          className="bg-[#0a0e17] text-[8px] font-mono text-[#e2e8f0] border-0 rounded px-1 py-0.5 focus:ring-0 focus:outline-none cursor-pointer"
        >
          <option value={0}>ALL</option>
          {Array.from({ length: maxGeneration }, (_, i) => i + 1).map(g => (
            <option key={g} value={g}>Gen {g}</option>
          ))}
        </select>
      </div>

      {/* Status filter */}
      <div className="flex items-center gap-1 bg-[#111827] rounded border border-[#1e293b] px-1.5 py-0.5">
        <span className="text-[7px] font-mono text-[#64748b]">STATUS</span>
        <select
          value={statusFilter}
          onChange={(e) => onStatusFilterChange(e.target.value)}
          className="bg-[#0a0e17] text-[8px] font-mono text-[#e2e8f0] border-0 rounded px-1 py-0.5 focus:ring-0 focus:outline-none cursor-pointer"
        >
          <option value="">ALL</option>
          {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
            <option key={key} value={key}>{cfg.label}</option>
          ))}
        </select>
      </div>
    </div>
  );
}

// ============================================================
// TREE NODE CARD COMPONENT
// ============================================================

function TreeNodeCard({
  node,
  isSelected,
  onClick,
  isHighlighted,
}: {
  node: TreeNode;
  isSelected: boolean;
  onClick: () => void;
  isHighlighted: boolean;
}) {
  const statusConfig = STATUS_CONFIG[node.status] || STATUS_CONFIG.IDLE;
  const StatusIcon = statusConfig.icon;
  const catColor = getCategoryColor(node.category);
  const isPositive = node.improvementPct > 0;
  const isNegative = node.improvementPct < 0;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
      onClick={onClick}
      className={`relative cursor-pointer rounded-lg border p-2.5 transition-all min-w-[180px] max-w-[220px] ${
        isSelected
          ? 'border-[#d4af37]/60 bg-[#111827] shadow-lg shadow-[#d4af37]/10 ring-1 ring-[#d4af37]/30'
          : isHighlighted
            ? 'border-[#d4af37]/30 bg-[#111827] shadow-md shadow-[#d4af37]/5'
            : `border-[#1e293b] bg-[#111827] hover:border-[#2d3748] hover:shadow-sm`
      }`}
    >
      {/* Status indicator dot */}
      <div className={`absolute -top-1 -left-1 w-2.5 h-2.5 rounded-full ${statusConfig.dotColor} border-2 border-[#0a0e17] ${
        node.isActive || node.isPaperTrading ? 'animate-pulse' : ''
      }`} />

      {/* Header: Name + Status */}
      <div className="flex items-start justify-between gap-1 mb-1.5">
        <div className="flex items-center gap-1 min-w-0 flex-1">
          <span className="text-xs shrink-0">{node.icon}</span>
          <span className="text-[10px] font-mono font-bold text-[#e2e8f0] truncate">{node.name}</span>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <StatusIcon className={`h-2.5 w-2.5 ${statusConfig.color}`} />
        </div>
      </div>

      {/* Badges row */}
      <div className="flex items-center gap-1 mb-1.5 flex-wrap">
        <Badge className="text-[7px] h-3 px-1 font-mono border-0" style={{ backgroundColor: `${catColor}20`, color: catColor }}>
          {node.category.replace(/_/g, ' ')}
        </Badge>
        <Badge className="text-[7px] h-3 px-1 font-mono bg-[#d4af37]/15 text-[#d4af37] border-0">
          Gen {node.generation}
        </Badge>
        <Badge className="text-[7px] h-3 px-1 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
          {node.primaryTimeframe}
        </Badge>
      </div>

      {/* Improvement indicator */}
      {node.parentId && (
        <div className={`flex items-center gap-1 mb-1.5 px-1.5 py-0.5 rounded text-[8px] font-mono ${
          isPositive ? 'bg-emerald-500/10 text-emerald-400' : isNegative ? 'bg-red-500/10 text-red-400' : 'bg-[#1a1f2e] text-[#64748b]'
        }`}>
          {isPositive ? <TrendingUp className="h-2.5 w-2.5" /> : isNegative ? <TrendingDown className="h-2.5 w-2.5" /> : <ArrowRight className="h-2.5 w-2.5" />}
          <span className="font-bold">{isPositive ? '+' : ''}{node.improvementPct.toFixed(1)}%</span>
          <span className="text-[6px] opacity-70">vs parent</span>
        </div>
      )}

      {/* Mini stats */}
      {node.backtestStats && (
        <div className="grid grid-cols-3 gap-1 text-[7px] font-mono">
          <div className="text-center bg-[#0a0e17] rounded px-1 py-0.5">
            <div className="text-[#475569] uppercase">WR</div>
            <div className="text-[#e2e8f0] font-bold">{(node.backtestStats.winRate * 100).toFixed(0)}%</div>
          </div>
          <div className="text-center bg-[#0a0e17] rounded px-1 py-0.5">
            <div className="text-[#475569] uppercase">PF</div>
            <div className="text-[#e2e8f0] font-bold">{node.backtestStats.profitFactor.toFixed(2)}</div>
          </div>
          <div className="text-center bg-[#0a0e17] rounded px-1 py-0.5">
            <div className="text-[#475569] uppercase">Trades</div>
            <div className="text-[#e2e8f0] font-bold">{node.backtestStats.totalTrades}</div>
          </div>
        </div>
      )}

      {/* Evolution type badge */}
      {node.evolutionType && (
        <div className="mt-1.5 flex items-center gap-1">
          <Dna className="h-2.5 w-2.5" style={{ color: EVOLUTION_TYPE_COLORS[node.evolutionType] || '#64748b' }} />
          <span className="text-[6px] font-mono text-[#64748b]">{node.evolutionType.replace(/_/g, ' ')}</span>
        </div>
      )}
    </motion.div>
  );
}

// ============================================================
// RECURSIVE TREE RENDERER
// ============================================================

function TreeLevel({
  node,
  selectedId,
  onSelect,
  collapsedIds,
  generationFilter,
  statusFilter,
}: {
  node: TreeNode;
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsedIds: Set<string>;
  generationFilter: number;
  statusFilter: string;
}) {
  const isCollapsed = collapsedIds.has(node.id);
  const isSelected = selectedId === node.id;

  // Apply filters
  if (generationFilter > 0 && node.generation !== generationFilter) {
    // If this node doesn't match the generation filter, check if any children do
    const hasMatchingChildren = flattenNodes(node).some(n => n.generation === generationFilter);
    if (!hasMatchingChildren) return null;
  }

  if (statusFilter && node.status !== statusFilter && node.generation > 1) {
    const hasMatchingChildren = flattenNodes(node).some(n => n.status === statusFilter);
    if (!hasMatchingChildren) return null;
  }

  const visibleChildren = isCollapsed ? [] : node.children;

  return (
    <div className="flex flex-col items-center">
      {/* The node itself */}
      <TreeNodeCard
        node={node}
        isSelected={isSelected}
        onClick={() => onSelect(node.id)}
        isHighlighted={false}
      />

      {/* Expand/collapse toggle */}
      {node.children.length > 0 && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            // Toggle collapse by dispatching to parent
            onSelect(`toggle:${node.id}`);
          }}
          className="flex items-center justify-center w-5 h-5 rounded-full bg-[#1a1f2e] border border-[#2d3748] hover:bg-[#2d3748] hover:border-[#d4af37]/30 transition-all mt-1"
          title={isCollapsed ? `Expand (${node.children.length})` : 'Collapse'}
        >
          {isCollapsed ? (
            <ChevronRight className="h-3 w-3 text-[#94a3b8]" />
          ) : (
            <ChevronDown className="h-3 w-3 text-[#94a3b8]" />
          )}
        </button>
      )}

      {/* Vertical connector line */}
      {visibleChildren.length > 0 && (
        <div className="w-px h-4 bg-[#2d3748]" />
      )}

      {/* Children container with horizontal layout */}
      {visibleChildren.length > 0 && (
        <div className="relative">
          {/* Horizontal connector bar */}
          <div className="flex items-start">
            <div className="flex">
              {visibleChildren.map((child, idx) => (
                <div key={child.id} className="flex flex-col items-center relative">
                  {/* Horizontal line from parent to child */}
                  <div className="h-4 flex items-center">
                    <div className="w-px h-full bg-[#2d3748]" />
                  </div>
                  {/* Horizontal connector */}
                  {visibleChildren.length > 1 && (
                    <div
                      className="absolute top-0 h-px bg-[#2d3748]"
                      style={{
                        left: idx === 0 ? '50%' : 0,
                        right: idx === visibleChildren.length - 1 ? '50%' : 0,
                      }}
                    />
                  )}
                  {/* Recurse */}
                  <TreeLevel
                    node={child}
                    selectedId={selectedId}
                    onSelect={onSelect}
                    collapsedIds={collapsedIds}
                    generationFilter={generationFilter}
                    statusFilter={statusFilter}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// DETAIL PANEL COMPONENT
// ============================================================

function DetailPanel({
  node,
  onClose,
}: {
  node: TreeNode | null;
  onClose: () => void;
}) {
  const [sdeValidating, setSdeValidating] = React.useState(false);
  const [sdeResult, setSdeResult] = React.useState<{
    state: string;
    capitalAction: string;
    signalQuality: string;
    vetoResults: Array<{ veto: string; passed: boolean; reason: string }>;
  } | null>(null);

  // Reset SDE result when node changes
  React.useEffect(() => {
    setSdeResult(null);
  }, [node?.id]);

  const handleSDEValidate = React.useCallback(async () => {
    if (!node) return;
    setSdeValidating(true);
    setSdeResult(null);
    try {
      const res = await fetch(`/api/strategy-decision/validate?strategyId=${encodeURIComponent(node.id)}`);
      if (res.ok) {
        const json = await res.json();
        if (json.data) {
          setSdeResult({
            state: json.data.state,
            capitalAction: json.data.capitalAction,
            signalQuality: json.data.signalQuality,
            vetoResults: json.data.vetoResults || [],
          });
        }
      }
    } catch {
      // Silently fail
    } finally {
      setSdeValidating(false);
    }
  }, [node]);

  if (!node) return null;

  const statusConfig = STATUS_CONFIG[node.status] || STATUS_CONFIG.IDLE;
  const StatusIcon = statusConfig.icon;
  const catColor = getCategoryColor(node.category);
  const isPositive = node.improvementPct > 0;

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      className="absolute right-0 top-0 bottom-0 w-80 bg-[#0d1117] border-l border-[#1e293b] z-10 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e293b] bg-[#0a0e17] shrink-0">
        <div className="flex items-center gap-2">
          <Eye className="h-3.5 w-3.5 text-[#d4af37]" />
          <span className="text-[9px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">Strategy Details</span>
        </div>
        <button onClick={onClose} className="p-1 hover:bg-[#1e293b] rounded transition-colors">
          <X className="h-3.5 w-3.5 text-[#64748b]" />
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Name & Icon */}
          <div className="flex items-start gap-2">
            <span className="text-xl">{node.icon}</span>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-mono font-bold text-[#e2e8f0] truncate">{node.name}</h3>
              <p className="text-[9px] font-mono text-[#64748b]">Created {formatTimeAgo(node.createdAt)}</p>
            </div>
          </div>

          {/* Status & Badges */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge className={`text-[8px] h-4 px-1.5 font-mono ${statusConfig.bgColor} ${statusConfig.color} border ${statusConfig.borderColor}`}>
              <StatusIcon className="h-2.5 w-2.5 mr-0.5" />
              {statusConfig.label}
            </Badge>
            <Badge className="text-[8px] h-4 px-1.5 font-mono border-0" style={{ backgroundColor: `${catColor}20`, color: catColor }}>
              {node.category.replace(/_/g, ' ')}
            </Badge>
            <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#d4af37]/15 text-[#d4af37] border-0">
              Gen {node.generation}
            </Badge>
          </div>

          {/* Parent info */}
          {node.parentId && (
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1.5">Evolution From</div>
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-mono text-[#94a3b8]">{node.parentName || node.parentId}</span>
                <ArrowRight className="h-3 w-3 text-[#d4af37]" />
                <span className="text-[9px] font-mono text-[#e2e8f0]">{node.name}</span>
              </div>
              {node.evolutionType && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <Dna className="h-3 w-3" style={{ color: EVOLUTION_TYPE_COLORS[node.evolutionType] || '#64748b' }} />
                  <span className="text-[8px] font-mono text-[#94a3b8]">{node.evolutionType.replace(/_/g, ' ')}</span>
                </div>
              )}
              {node.triggerMetric && (
                <div className="text-[8px] font-mono text-[#64748b] mt-1">
                  Trigger: <span className="text-[#94a3b8]">{node.triggerMetric.replace(/_/g, ' ')}</span>
                </div>
              )}
            </div>
          )}

          {/* Improvement */}
          {node.parentId && (
            <div className={`rounded-lg p-3 border ${
              isPositive ? 'bg-emerald-500/5 border-emerald-500/20' : node.improvementPct < 0 ? 'bg-red-500/5 border-red-500/20' : 'bg-[#111827] border-[#1e293b]'
            }`}>
              <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Improvement vs Parent</div>
              <div className="flex items-center gap-2">
                {isPositive ? <TrendingUp className="h-5 w-5 text-emerald-400" /> : node.improvementPct < 0 ? <TrendingDown className="h-5 w-5 text-red-400" /> : <ArrowRight className="h-5 w-5 text-[#64748b]" />}
                <span className={`text-xl font-mono font-bold ${
                  isPositive ? 'text-emerald-400' : node.improvementPct < 0 ? 'text-red-400' : 'text-[#94a3b8]'
                }`}>
                  {isPositive ? '+' : ''}{node.improvementPct.toFixed(2)}%
                </span>
              </div>
            </div>
          )}

          {/* Backtest Stats */}
          {node.backtestStats && (
            <div>
              <div className="text-[8px] font-mono text-[#64748b] uppercase mb-2">Backtest Results</div>
              <div className="grid grid-cols-2 gap-2">
                <StatBlock label="Win Rate" value={`${(node.backtestStats.winRate * 100).toFixed(1)}%`} color={node.backtestStats.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400'} />
                <StatBlock label="Profit Factor" value={node.backtestStats.profitFactor.toFixed(2)} color={node.backtestStats.profitFactor >= 1 ? 'text-emerald-400' : 'text-red-400'} />
                <StatBlock label="Total Trades" value={node.backtestStats.totalTrades.toString()} color="text-[#e2e8f0]" />
                <StatBlock label="Sharpe Ratio" value={node.backtestStats.sharpeRatio.toFixed(2)} color={node.backtestStats.sharpeRatio >= 1 ? 'text-emerald-400' : 'text-[#94a3b8]'} />
                <StatBlock label="PnL" value={`${node.backtestStats.totalPnlPct >= 0 ? '+' : ''}${node.backtestStats.totalPnlPct.toFixed(1)}%`} color={node.backtestStats.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                <StatBlock label="Max Drawdown" value={`${node.backtestStats.maxDrawdownPct.toFixed(1)}%`} color="text-red-400" />
              </div>
            </div>
          )}

          {/* Strategy Parameters */}
          <div>
            <div className="text-[8px] font-mono text-[#64748b] uppercase mb-2">Parameters</div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-1.5">
              <ParamRow label="Timeframe" value={node.primaryTimeframe} />
              <ParamRow label="Category" value={node.category.replace(/_/g, ' ')} />
              <ParamRow label="Active" value={node.isActive ? 'Yes' : 'No'} />
              <ParamRow label="Paper Trading" value={node.isPaperTrading ? 'Yes' : 'No'} />
              <ParamRow label="Children" value={node.children.length.toString()} />
            </div>
          </div>

          {/* SDE Validation */}
          <div>
            <div className="text-[8px] font-mono text-[#64748b] uppercase mb-2">SDE Validation</div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleSDEValidate}
                disabled={sdeValidating}
                className="w-full h-7 text-[8px] font-mono border-[#2d3748] hover:border-[#d4af37]/50 hover:bg-[#1a1f2e] text-[#94a3b8]"
              >
                {sdeValidating ? (
                  <>
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    Validating...
                  </>
                ) : (
                  <>
                    <ShieldCheck className="h-3 w-3 mr-1 text-[#d4af37]" />
                    Validate with SDE
                  </>
                )}
              </Button>
              {sdeResult && (
                <div className="space-y-1.5 pt-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[7px] font-mono text-[#64748b] uppercase">State</span>
                    <Badge className={`text-[7px] h-3.5 px-1 font-mono border-0 ${
                      sdeResult.state === 'ACTIVE' ? 'bg-emerald-500/15 text-emerald-400' :
                      sdeResult.state === 'CONDITIONAL' ? 'bg-yellow-500/15 text-yellow-400' :
                      sdeResult.state === 'PAUSED' ? 'bg-orange-500/15 text-orange-400' :
                      'bg-red-500/15 text-red-400'
                    }`}>
                      {sdeResult.state}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[7px] font-mono text-[#64748b] uppercase">Action</span>
                    <span className="text-[8px] font-mono text-[#94a3b8]">{sdeResult.capitalAction}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[7px] font-mono text-[#64748b] uppercase">Quality</span>
                    <span className="text-[8px] font-mono text-[#94a3b8]">{sdeResult.signalQuality}</span>
                  </div>
                  {sdeResult.vetoResults.length > 0 && (
                    <div className="pt-1 border-t border-[#1e293b]">
                      <div className="text-[6px] font-mono text-[#475569] uppercase mb-1">Vetos</div>
                      {sdeResult.vetoResults.map((v, i) => (
                        <div key={i} className="flex items-center gap-1 text-[7px] font-mono">
                          <div className={`w-1.5 h-1.5 rounded-full ${v.passed ? 'bg-emerald-400' : 'bg-red-400'}`} />
                          <span className="text-[#64748b]">{v.veto}</span>
                          <span className={v.passed ? 'text-emerald-400' : 'text-red-400'}>{v.passed ? 'PASS' : 'FAIL'}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Lineage path */}
          {node.children.length > 0 && (
            <div>
              <div className="text-[8px] font-mono text-[#64748b] uppercase mb-2">Descendants</div>
              <div className="space-y-1">
                {flattenNodes(node).slice(1).map((descendant) => (
                  <div key={descendant.id} className="flex items-center gap-1.5 text-[8px] font-mono bg-[#111827] rounded px-2 py-1 border border-[#1e293b]">
                    <span className="text-[9px]">{descendant.icon}</span>
                    <span className="text-[#94a3b8] truncate flex-1">{descendant.name}</span>
                    <Badge className="text-[6px] h-3 px-0.5 font-mono bg-[#d4af37]/10 text-[#d4af37] border-0">
                      G{descendant.generation}
                    </Badge>
                    {descendant.improvementPct !== 0 && (
                      <span className={`text-[7px] font-bold ${descendant.improvementPct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {descendant.improvementPct > 0 ? '+' : ''}{descendant.improvementPct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </motion.div>
  );
}

function StatBlock({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded p-2">
      <div className="text-[7px] font-mono text-[#475569] uppercase">{label}</div>
      <div className={`text-[11px] font-mono font-bold ${color}`}>{value}</div>
    </div>
  );
}

function ParamRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-[8px] font-mono">
      <span className="text-[#475569] uppercase">{label}</span>
      <span className="text-[#94a3b8]">{value}</span>
    </div>
  );
}

// ============================================================
// EMPTY STATE COMPONENT
// ============================================================

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg">
      <div className="flex flex-col items-center gap-3 py-8">
        <div className="w-16 h-16 rounded-2xl bg-[#111827] border border-[#1e293b] flex items-center justify-center">
          <GitBranch className="h-8 w-8 text-[#2d3748]" />
        </div>
        <div className="text-center">
          <h3 className="text-sm font-mono font-bold text-[#94a3b8] mb-1">No Evolution Data</h3>
          <p className="text-[10px] font-mono text-[#475569] max-w-[280px]">
            Strategy evolution trees appear when strategies are evolved through the AI Manager.
            Run an evolution cycle to generate parent-child relationships.
          </p>
        </div>
        <div className="flex items-center gap-2 mt-2">
          <Badge className="text-[8px] h-5 px-2 font-mono bg-[#1a1f2e] text-[#64748b] border border-[#1e293b]">
            <Zap className="h-3 w-3 mr-1 text-amber-400" />
            Run AI Manager → Evolve
          </Badge>
        </div>
        <div className="flex flex-col items-center gap-1 mt-3">
          <span className="text-[8px] font-mono text-[#475569]">How evolutions work:</span>
          <div className="flex items-center gap-1 text-[7px] font-mono text-[#64748b]">
            <span>Strategy A</span>
            <ArrowRight className="h-2.5 w-2.5 text-[#d4af37]" />
            <span>mutate params</span>
            <ArrowRight className="h-2.5 w-2.5 text-[#d4af37]" />
            <span>backtest</span>
            <ArrowRight className="h-2.5 w-2.5 text-[#d4af37]" />
            <span>Strategy B (improved)</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function StrategyEvolutionTree() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [zoom, setZoom] = useState(1);
  const [generationFilter, setGenerationFilter] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');
  const treeContainerRef = useRef<HTMLDivElement>(null);

  // Fetch evolution tree data
  const { data: response, isLoading, refetch } = useQuery({
    queryKey: ['strategy-evolution-tree', generationFilter],
    queryFn: async () => {
      try {
        const params = new URLSearchParams();
        if (generationFilter > 0) params.set('maxDepth', generationFilter.toString());
        params.set('includeStats', 'true');

        const res = await fetch(`/api/strategy-evolution/tree?${params.toString()}`);
        if (!res.ok) return { trees: [], stats: { totalSystems: 0, totalEvolutions: 0, avgImprovement: 0, bestLineage: '', maxGeneration: 0, improvedCount: 0, degradedCount: 0 } };
        const json = await res.json();
        return json as TreeResponse;
      } catch {
        return { trees: [], stats: { totalSystems: 0, totalEvolutions: 0, avgImprovement: 0, bestLineage: '', maxGeneration: 0, improvedCount: 0, degradedCount: 0 } };
      }
    },
    staleTime: 15000,
    refetchInterval: 30000,
  });

  const trees = response?.trees || [];
  const stats = response?.stats || { totalSystems: 0, totalEvolutions: 0, avgImprovement: 0, bestLineage: '', maxGeneration: 0, improvedCount: 0, degradedCount: 0 };

  // Find selected node
  const selectedNode = useMemo(() => {
    if (!selectedId) return null;
    for (const tree of trees) {
      const flat = flattenNodes(tree);
      const found = flat.find(n => n.id === selectedId);
      if (found) return found;
    }
    return null;
  }, [trees, selectedId]);

  // Total node count
  const totalNodes = useMemo(() => trees.reduce((sum, t) => sum + countNodes(t), 0), [trees]);

  // Handle node selection
  const handleSelect = useCallback((id: string) => {
    if (id.startsWith('toggle:')) {
      const nodeId = id.replace('toggle:', '');
      setCollapsedIds(prev => {
        const next = new Set(prev);
        if (next.has(nodeId)) next.delete(nodeId);
        else next.add(nodeId);
        return next;
      });
    } else {
      setSelectedId(prev => prev === id ? null : id);
    }
  }, []);

  // Zoom controls
  const handleZoomIn = useCallback(() => setZoom(z => Math.min(z + 0.15, 2)), []);
  const handleZoomOut = useCallback(() => setZoom(z => Math.max(z - 0.15, 0.3)), []);
  const handleZoomReset = useCallback(() => setZoom(1), []);

  // Expand/Collapse all
  const handleExpandAll = useCallback(() => setCollapsedIds(new Set()), []);
  const handleCollapseAll = useCallback(() => {
    const allIds = new Set<string>();
    function collect(node: TreeNode) {
      if (node.children.length > 0) allIds.add(node.id);
      node.children.forEach(collect);
    }
    trees.forEach(collect);
    setCollapsedIds(allIds);
  }, [trees]);

  if (isLoading) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117]">
          <GitBranch className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">EVOLUTION TREE</span>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 text-[#d4af37] animate-spin" />
        </div>
      </div>
    );
  }

  if (trees.length === 0) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
          <GitBranch className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">EVOLUTION TREE</span>
          <div className="ml-auto">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetch()}
              className="h-6 px-2 text-[8px] font-mono text-[#64748b] hover:text-[#94a3b8]"
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              Refresh
            </Button>
          </div>
        </div>
        <div className="flex-1 p-4">
          <EmptyState />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden relative">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">EVOLUTION TREE</span>
          <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-0">
            {totalNodes} nodes
          </Badge>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refetch()}
          className="h-6 px-2 text-[8px] font-mono text-[#64748b] hover:text-[#94a3b8]"
        >
          <RefreshCw className="h-3 w-3 mr-1" />
          Refresh
        </Button>
      </div>

      {/* Stats Bar */}
      <StatsBar stats={stats} nodeCount={totalNodes} />

      {/* Controls */}
      <ControlsBar
        zoom={zoom}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onZoomReset={handleZoomReset}
        onExpandAll={handleExpandAll}
        onCollapseAll={handleCollapseAll}
        generationFilter={generationFilter}
        onGenerationFilterChange={setGenerationFilter}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        maxGeneration={stats.maxGeneration}
      />

      {/* Tree Area */}
      <div className="flex-1 relative overflow-hidden">
        {/* Main tree viewport */}
        <div
          ref={treeContainerRef}
          className="absolute inset-0 overflow-auto"
          style={{
            paddingRight: selectedNode ? '320px' : '0',
          }}
        >
          <div
            className="min-h-full p-6 transition-transform duration-200"
            style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}
          >
            <div className="flex gap-8 justify-center flex-wrap">
              {trees.map((tree) => (
                <TreeLevel
                  key={tree.id}
                  node={tree}
                  selectedId={selectedId}
                  onSelect={handleSelect}
                  collapsedIds={collapsedIds}
                  generationFilter={generationFilter}
                  statusFilter={statusFilter}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Detail Panel */}
        <AnimatePresence>
          {selectedNode && (
            <DetailPanel
              node={selectedNode}
              onClose={() => setSelectedId(null)}
            />
          )}
        </AnimatePresence>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 px-4 py-1.5 bg-[#0a0e17] border-t border-[#1e293b] shrink-0 overflow-x-auto">
        <span className="text-[7px] font-mono text-[#475569] uppercase shrink-0">Status:</span>
        {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
          <div key={key} className="flex items-center gap-1 shrink-0">
            <div className={`w-2 h-2 rounded-full ${cfg.dotColor}`} />
            <span className="text-[7px] font-mono text-[#64748b]">{cfg.label}</span>
          </div>
        ))}
        <div className="h-3 w-px bg-[#1e293b] shrink-0" />
        <span className="text-[7px] font-mono text-[#475569] uppercase shrink-0">Evo:</span>
        <div className="flex items-center gap-1 shrink-0">
          <TrendingUp className="h-2.5 w-2.5 text-emerald-400" />
          <span className="text-[7px] font-mono text-emerald-400">Improved</span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <TrendingDown className="h-2.5 w-2.5 text-red-400" />
          <span className="text-[7px] font-mono text-red-400">Degraded</span>
        </div>
      </div>
    </div>
  );
}
