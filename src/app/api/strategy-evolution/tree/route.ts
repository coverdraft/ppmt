import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

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
// HELPER: Derive status from TradingSystem fields
// ============================================================

function deriveStatus(system: {
  isActive: boolean;
  isPaperTrading: boolean;
  totalBacktests: number;
}): string {
  if (system.isActive && system.isPaperTrading) return 'PAPER_TRADING';
  if (system.isActive) return 'ACTIVE';
  if (system.totalBacktests > 0) return 'IDLE';
  return 'IDLE';
}

// ============================================================
// HELPER: Build tree from flat list
// ============================================================

function buildTree(nodes: TreeNode[]): TreeNode[] {
  const nodeMap = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];

  for (const node of nodes) {
    nodeMap.set(node.id, { ...node, children: [] });
  }

  for (const node of nodes) {
    const treeNode = nodeMap.get(node.id)!;
    if (node.parentId && nodeMap.has(node.parentId)) {
      nodeMap.get(node.parentId)!.children.push(treeNode);
    } else {
      roots.push(treeNode);
    }
  }

  return roots;
}

// ============================================================
// HELPER: Find best lineage path
// ============================================================

function findBestLineage(trees: TreeNode[]): string {
  let bestPath = '';
  let bestScore = -Infinity;

  function dfs(node: TreeNode, path: string, cumulativeImprovement: number) {
    const currentPath = path ? `${path} → ${node.name}` : node.name;
    const currentScore = cumulativeImprovement + node.improvementPct;

    if (node.children.length === 0) {
      if (currentScore > bestScore) {
        bestScore = currentScore;
        bestPath = currentPath;
      }
      return;
    }

    for (const child of node.children) {
      dfs(child, currentPath, currentScore);
    }
  }

  for (const root of trees) {
    dfs(root, '', 0);
  }

  return bestPath || 'No evolutions yet';
}

// ============================================================
// HELPER: Get max generation depth
// ============================================================

function getMaxGeneration(trees: TreeNode[]): number {
  let max = 0;
  function dfs(node: TreeNode) {
    if (node.generation > max) max = node.generation;
    for (const child of node.children) dfs(child);
  }
  for (const root of trees) dfs(root);
  return max;
}

// ============================================================
// HELPER: Count all evolutions
// ============================================================

function countEvolutions(trees: TreeNode[]): { total: number; improved: number; degraded: number } {
  let total = 0;
  let improved = 0;
  let degraded = 0;
  function dfs(node: TreeNode) {
    if (node.parentId) {
      total++;
      if (node.improvementPct > 0) improved++;
      else if (node.improvementPct < 0) degraded++;
    }
    for (const child of node.children) dfs(child);
  }
  for (const root of trees) dfs(root);
  return { total, improved, degraded };
}

// ============================================================
// HELPER: Compute average improvement
// ============================================================

function computeAvgImprovement(trees: TreeNode[]): number {
  const improvements: number[] = [];
  function dfs(node: TreeNode) {
    if (node.parentId && node.improvementPct !== 0) {
      improvements.push(node.improvementPct);
    }
    for (const child of node.children) dfs(child);
  }
  for (const root of trees) dfs(root);
  if (improvements.length === 0) return 0;
  return improvements.reduce((a, b) => a + b, 0) / improvements.length;
}

// ============================================================
// MAIN HANDLER
// ============================================================

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const rootSystemId = searchParams.get('rootSystemId');
    const maxDepth = parseInt(searchParams.get('maxDepth') || '10', 10);
    const includeStats = searchParams.get('includeStats') !== 'false';

    // Step 1: Fetch all trading systems with their parent relationships
    const whereClause: Record<string, unknown> = {};
    if (rootSystemId) {
      whereClause.id = rootSystemId;
    }

    const systems = await db.tradingSystem.findMany({
      orderBy: { createdAt: 'asc' },
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { completedAt: 'desc' },
          take: 1,
          select: {
            winRate: true,
            profitFactor: true,
            totalTrades: true,
            sharpeRatio: true,
            totalPnlPct: true,
            maxDrawdownPct: true,
          },
        },
      },
    });

    if (systems.length === 0) {
      return NextResponse.json({
        trees: [],
        stats: {
          totalSystems: 0,
          totalEvolutions: 0,
          avgImprovement: 0,
          bestLineage: 'No systems found',
          maxGeneration: 0,
          improvedCount: 0,
          degradedCount: 0,
        },
      } satisfies TreeResponse);
    }

    // Step 2: Fetch SystemEvolution records for improvement data
    const systemIds = systems.map(s => s.id);
    const evolutions = await db.systemEvolution.findMany({
      where: {
        childSystemId: { in: systemIds },
      },
      orderBy: { createdAt: 'asc' },
    });

    // Build a map of childSystemId -> evolution data
    const evolutionMap = new Map<string, {
      evolutionType: string;
      triggerMetric: string;
      improvementPct: number;
      parentSystemId: string | null;
    }>();

    for (const evo of evolutions) {
      if (!evolutionMap.has(evo.childSystemId)) {
        evolutionMap.set(evo.childSystemId, {
          evolutionType: evo.evolutionType,
          triggerMetric: evo.triggerMetric,
          improvementPct: evo.improvementPct,
          parentSystemId: evo.parentSystemId,
        });
      }
    }

    // Step 3: Fetch latest state for each system
    const stateHistories = await db.strategyStateHistory.findMany({
      where: { systemId: { in: systemIds } },
      orderBy: { createdAt: 'desc' },
    });

    // Get only the latest state per system
    const latestStateMap = new Map<string, string>();
    for (const sh of stateHistories) {
      if (!latestStateMap.has(sh.systemId)) {
        latestStateMap.set(sh.systemId, sh.status);
      }
    }

    // Step 4: Build TreeNode objects
    const systemMap = new Map(systems.map(s => [s.id, s]));
    const treeNodes: TreeNode[] = [];

    for (const system of systems) {
      const evoData = evolutionMap.get(system.id);
      const latestStatus = latestStateMap.get(system.id) || deriveStatus(system);
      const bestBacktest = system.backtests[0];

      // Determine generation by tracing ancestry
      let generation = 1;
      let currentParentId = system.parentSystemId;
      const visited = new Set<string>();
      while (currentParentId && !visited.has(currentParentId)) {
        visited.add(currentParentId);
        generation++;
        const parentSystem = systemMap.get(currentParentId);
        currentParentId = parentSystem?.parentSystemId || null;
      }

      // Get parent name
      const parentSystem = system.parentSystemId ? systemMap.get(system.parentSystemId) : null;

      treeNodes.push({
        id: system.id,
        name: system.name,
        generation,
        createdAt: system.createdAt.toISOString(),
        backtestStats: includeStats && bestBacktest ? {
          winRate: bestBacktest.winRate,
          profitFactor: bestBacktest.profitFactor,
          totalTrades: bestBacktest.totalTrades,
          sharpeRatio: bestBacktest.sharpeRatio,
          totalPnlPct: bestBacktest.totalPnlPct,
          maxDrawdownPct: bestBacktest.maxDrawdownPct,
        } : null,
        status: latestStatus,
        improvementPct: evoData?.improvementPct ?? 0,
        parentId: system.parentSystemId,
        parentName: parentSystem?.name || null,
        evolutionType: evoData?.evolutionType || null,
        triggerMetric: evoData?.triggerMetric || null,
        category: system.category,
        icon: system.icon,
        primaryTimeframe: system.primaryTimeframe,
        isActive: system.isActive,
        isPaperTrading: system.isPaperTrading,
        children: [],
      });
    }

    // Step 5: Build trees
    let trees = buildTree(treeNodes);

    // Step 6: Apply depth filter
    if (maxDepth < 10) {
      function pruneTree(node: TreeNode, depth: number): TreeNode | null {
        if (depth > maxDepth) return null;
        const prunedChildren = node.children
          .map(child => pruneTree(child, depth + 1))
          .filter(Boolean) as TreeNode[];
        return { ...node, children: prunedChildren };
      }
      trees = trees.map(root => pruneTree(root, 1)).filter(Boolean) as TreeNode[];
    }

    // Step 7: If rootSystemId specified, filter to just that tree
    if (rootSystemId) {
      trees = trees.filter(t => t.id === rootSystemId);
    }

    // Step 8: Compute stats
    const evoCounts = countEvolutions(trees);

    const stats: EvolutionStats = {
      totalSystems: treeNodes.length,
      totalEvolutions: evoCounts.total,
      avgImprovement: computeAvgImprovement(trees),
      bestLineage: findBestLineage(trees),
      maxGeneration: getMaxGeneration(trees),
      improvedCount: evoCounts.improved,
      degradedCount: evoCounts.degraded,
    };

    return NextResponse.json({ trees, stats } satisfies TreeResponse);
  } catch (error) {
    console.error('Error fetching evolution tree:', error);
    return NextResponse.json(
      { trees: [], stats: { totalSystems: 0, totalEvolutions: 0, avgImprovement: 0, bestLineage: 'Error', maxGeneration: 0, improvedCount: 0, degradedCount: 0 } },
      { status: 500 },
    );
  }
}
