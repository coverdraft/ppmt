import { useEffect, useRef, useState, useCallback } from 'react';
import * as d3 from 'd3';
import type { HierarchyPointNode } from 'd3-hierarchy';
import { mockTrieData, type TrieNodeMock } from '../mock/trieData';

interface TrieLabProps {
  /** Active path IDs from brain_update — e.g. ["root", "a", "a-b", "a-b-c"] */
  activePathIds?: string[];
}

/**
 * TrieLab — D3.js Radial Tree visualization of the PPMT Trie.
 *
 * Visual rules:
 * - Link thickness: historical_count → 1px–5px stroke
 * - Node color: confidence → red (<0.4) → yellow (0.5) → green (>0.8)
 * - Node radius: scales slightly with historical_count
 * - Active path: glow filter + full opacity vs 0.5 for inactive
 * - Tooltip on hover showing pattern, observations, confidence, win_rate
 * - Responsive via ResizeObserver
 */
export default function TrieLab({ activePathIds }: TrieLabProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    pattern: string;
    count: number;
    confidence: number;
    winRate?: number;
  } | null>(null);

  // ─── Build the full pattern path for a node ───────────────
  const getPatternPath = useCallback((d: HierarchyPointNode<TrieNodeMock>): string => {
    const ancestors = d.ancestors().reverse();
    return ancestors
      .slice(1)
      .map((a) => a.data.symbol ?? 'root')
      .join(' \u2192 ');
  }, []);

  // ─── D3 Radial Tree Render ────────────────────────────────
  const renderTree = useCallback(() => {
    if (!svgRef.current || !containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    if (width === 0 || height === 0) return;

    // Clear previous render
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3
      .select(svgRef.current)
      .attr('width', width)
      .attr('height', height);

    // ─── SVG Defs: Glow filter for active path ────────────
    const defs = svg.append('defs');

    const glowFilter = defs.append('filter').attr('id', 'glow');
    glowFilter
      .append('feGaussianBlur')
      .attr('stdDeviation', '3')
      .attr('result', 'coloredBlur');
    const feMerge = glowFilter.append('feMerge');
    feMerge.append('feMergeNode').attr('in', 'coloredBlur');
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    const pulseFilter = defs.append('filter').attr('id', 'pulse-glow');
    pulseFilter
      .append('feGaussianBlur')
      .attr('stdDeviation', '5')
      .attr('result', 'coloredBlur');
    const pulseMerge = pulseFilter.append('feMerge');
    pulseMerge.append('feMergeNode').attr('in', 'coloredBlur');
    pulseMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // ─── Scales ────────────────────────────────────────────
    const maxCount = Math.max(
      ...d3.hierarchy(mockTrieData)
        .descendants()
        .map((d) => d.data.historical_count)
    );

    const strokeWidthScale = d3
      .scaleLinear()
      .domain([1, maxCount])
      .range([1, 5]);

    const colorScale = d3
      .scaleLinear<string>()
      .domain([0, 0.4, 0.6, 0.8, 1.0])
      .range(['#ef4444', '#f59e0b', '#eab308', '#84cc16', '#10b981']);

    const radiusScale = d3
      .scaleSqrt()
      .domain([1, maxCount])
      .range([4, 10]);

    // ─── Compute which IDs are active ─────────────────────
    const activeSet = new Set(activePathIds ?? []);

    // Mark nodes as active based on prop
    function isActive(node: TrieNodeMock): boolean {
      return activeSet.has(node.id) || !!node.is_active_path;
    }

    // ─── Hierarchy ─────────────────────────────────────────
    const hierarchy = d3.hierarchy(mockTrieData);

    const radius = Math.min(width, height) / 2 - 40;

    const tree = d3
      .tree<TrieNodeMock>()
      .size([2 * Math.PI, Math.max(radius, 50)])
      .separation((a, b) => (a.parent === b.parent ? 1 : 2) / a.depth);

    const root = tree(hierarchy);

    // ─── Main group centered ───────────────────────────────
    const g = svg
      .append('g')
      .attr('transform', `translate(${width / 2}, ${height / 2})`);

    // ─── Links ─────────────────────────────────────────────
    g.selectAll('.link')
      .data(root.links())
      .join('path')
      .attr('class', 'link')
      .attr(
        'd',
        d3
          .linkRadial<
            d3.HierarchyLink<TrieNodeMock>,
            HierarchyPointNode<TrieNodeMock>
          >()
          .angle((d) => d.x)
          .radius((d) => d.y) as any
      )
      .attr('fill', 'none')
      .attr('stroke', (d) => {
        return isActive(d.target.data) ? colorScale(d.target.data.confidence) : '#2a2a3e';
      })
      .attr('stroke-width', (d) => strokeWidthScale(d.target.data.historical_count))
      .attr('stroke-opacity', (d) =>
        isActive(d.target.data) ? 1.0 : 0.4
      )
      .attr('filter', (d) =>
        isActive(d.target.data) ? 'url(#glow)' : null
      );

    // ─── Nodes ─────────────────────────────────────────────
    const nodes = g
      .selectAll('.node')
      .data(root.descendants())
      .join('g')
      .attr('class', 'node')
      .attr(
        'transform',
        (d) => `
        rotate(${(d.x * 180) / Math.PI - 90})
        translate(${d.y}, 0)
      `
      );

    nodes
      .append('circle')
      .attr('r', (d) => radiusScale(d.data.historical_count))
      .attr('fill', (d) => colorScale(d.data.confidence))
      .attr('fill-opacity', (d) => (isActive(d.data) ? 1.0 : 0.6))
      .attr('stroke', (d) =>
        isActive(d.data) ? '#ffffff' : 'transparent'
      )
      .attr('stroke-width', (d) => (isActive(d.data) ? 1.5 : 0))
      .attr('filter', (d) =>
        isActive(d.data) ? 'url(#pulse-glow)' : null
      );

    // Active path pulse animation
    nodes
      .filter((d) => isActive(d.data))
      .select('circle')
      .style('animation', 'trie-pulse 2s ease-in-out infinite');

    // ─── Labels ────────────────────────────────────────────
    nodes
      .filter(
        (d) => d.data.historical_count >= 80 || isActive(d.data)
      )
      .append('text')
      .attr('dy', '0.31em')
      .attr('x', (d) => (d.x < Math.PI === !d.children ? 8 : -8))
      .attr('text-anchor', (d) =>
        d.x < Math.PI === !d.children ? 'start' : 'end'
      )
      .attr('transform', (d) =>
        d.x >= Math.PI ? 'rotate(180)' : null
      )
      .text((d) => d.data.symbol ?? 'ROOT')
      .attr('fill', (d) =>
        isActive(d.data) ? '#ffffff' : '#9ca3af'
      )
      .attr('font-size', '9px')
      .attr('font-family', 'JetBrains Mono, monospace')
      .attr('font-weight', (d) =>
        isActive(d.data) ? '600' : '400'
      );

    // ─── Hover interactions ────────────────────────────────
    nodes
      .on('mouseenter', (event: MouseEvent, d: HierarchyPointNode<TrieNodeMock>) => {
        const pattern = getPatternPath(d);
        setTooltip({
          x: event.clientX,
          y: event.clientY,
          pattern,
          count: d.data.historical_count,
          confidence: d.data.confidence,
          winRate: d.data.win_rate,
        });

        d3.select(event.currentTarget as SVGGElement)
          .select('circle')
          .attr('stroke', '#ffffff')
          .attr('stroke-width', 2);
      })
      .on('mouseleave', (event: MouseEvent, d: HierarchyPointNode<TrieNodeMock>) => {
        setTooltip(null);

        d3.select(event.currentTarget as SVGGElement)
          .select('circle')
          .attr('stroke', isActive(d.data) ? '#ffffff' : 'transparent')
          .attr('stroke-width', isActive(d.data) ? 1.5 : 0);
      });

  }, [getPatternPath, activePathIds]);

  // ─── Initial render + ResizeObserver ──────────────────────
  useEffect(() => {
    renderTree();

    if (!containerRef.current) return;

    const observer = new ResizeObserver(() => {
      renderTree();
    });
    observer.observe(containerRef.current);

    return () => observer.disconnect();
  }, [renderTree]);

  return (
    <div className="relative w-full h-full" ref={containerRef}>
      <svg ref={svgRef} className="w-full h-full" />
      
      {/* Tooltip */}
      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none bg-[#1a1a2e] border border-[#2e2e4e] rounded-lg px-3 py-2 shadow-xl"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 10,
          }}
        >
          <div className="font-mono text-xs space-y-1">
            <div className="text-gray-400">
              Patrón: <span className="text-white">{tooltip.pattern}</span>
            </div>
            <div className="text-gray-400">
              Observaciones: <span className="text-white">{tooltip.count}</span>
            </div>
            <div className="text-gray-400">
              Confianza: <span className="text-emerald-400">{tooltip.confidence.toFixed(2)}</span>
            </div>
            {tooltip.winRate !== undefined && (
              <div className="text-gray-400">
                Win Rate: <span className="text-yellow-400">{tooltip.winRate}%</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
