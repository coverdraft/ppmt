'use client';

import React, { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search,
  Star,
  Download,
  Eye,
  ChevronLeft,
  ChevronRight,
  TrendingUp,
  BarChart3,
  Shield,
  Zap,
  X,
  Check,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  CATEGORY_META,
  DIFFICULTY_META,
  type TemplateCategory,
  type TemplateDifficulty,
} from '@/lib/services/strategy/strategy-templates';

// ============================================================
// TYPES
// ============================================================

interface StrategyTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  difficulty: string;
  author: string;
  tags: string[];
  rating: number;
  downloads: number;
  isFeatured: boolean;
  isBuiltIn: boolean;
  strategyConfig: {
    indicators: Array<{ type: string; params: Record<string, number | string> }>;
    entryRules: { conditions: string[]; logic: string };
    exitRules: {
      takeProfitPct: number;
      stopLossPct: number;
      trailingStopPct?: number;
    };
    riskManagement: {
      maxPositionSizePct: number;
      maxOpenPositions: number;
    };
    timeframe: string;
    direction: string;
  };
  expectedWinRate: number;
  expectedProfitFactor: number;
  expectedAvgTrades: number;
  expectedMaxDrawdown: number;
  applicableChains: string[];
  applicableTimeframes: string[];
}

// ============================================================
// HELPER COMPONENTS
// ============================================================

function StarRating({ rating, size = 'sm' }: { rating: number; size?: 'sm' | 'md' }) {
  const stars: React.ReactElement[] = [];
  const fullStars = Math.floor(rating);
  const hasHalf = rating - fullStars >= 0.3;

  for (let i = 0; i < 5; i++) {
    if (i < fullStars) {
      stars.push(
        <Star
          key={i}
          className={`${size === 'sm' ? 'h-3 w-3' : 'h-4 w-4'} fill-amber-400 text-amber-400`}
        />
      );
    } else if (i === fullStars && hasHalf) {
      stars.push(
        <Star
          key={i}
          className={`${size === 'sm' ? 'h-3 w-3' : 'h-4 w-4'} fill-amber-400/50 text-amber-400`}
        />
      );
    } else {
      stars.push(
        <Star
          key={i}
          className={`${size === 'sm' ? 'h-3 w-3' : 'h-4 w-4'} text-[#475569]`}
        />
      );
    }
  }

  return (
    <div className="flex items-center gap-0.5">
      {stars}
      <span className={`ml-1 ${size === 'sm' ? 'text-[9px]' : 'text-[10px]'} font-mono text-[#94a3b8]`}>
        {rating.toFixed(1)}
      </span>
    </div>
  );
}

function ChainIcon({ chain }: { chain: string }) {
  const chainColors: Record<string, string> = {
    SOL: 'text-purple-400',
    ETH: 'text-blue-400',
    BASE: 'text-blue-300',
  };
  const chainLabels: Record<string, string> = {
    SOL: 'SOL',
    ETH: 'ETH',
    BASE: 'BASE',
  };
  return (
    <span className={`text-[8px] font-mono font-bold ${chainColors[chain] || 'text-[#94a3b8]'}`}>
      {chainLabels[chain] || chain}
    </span>
  );
}

// ============================================================
// FEATURED CAROUSEL
// ============================================================

function FeaturedCarousel({
  templates,
  onPreview,
  onImport,
}: {
  templates: StrategyTemplate[];
  onPreview: (t: StrategyTemplate) => void;
  onImport: (t: StrategyTemplate) => void;
}) {
  const [scrollIdx, setScrollIdx] = useState(0);
  const featured = templates.filter((t) => t.isFeatured);

  if (featured.length === 0) return null;

  const prev = () => setScrollIdx((i) => Math.max(0, i - 1));
  const next = () => setScrollIdx((i) => Math.min(featured.length - 1, i + 1));

  const catMeta = CATEGORY_META[featured[scrollIdx]?.category as TemplateCategory];

  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-amber-400" />
          <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">
            Featured Strategies
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-[#64748b] hover:text-[#f1f5f9]"
            onClick={prev}
            disabled={scrollIdx === 0}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="text-[9px] font-mono text-[#475569]">
            {scrollIdx + 1}/{featured.length}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-[#64748b] hover:text-[#f1f5f9]"
            onClick={next}
            disabled={scrollIdx === featured.length - 1}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={featured[scrollIdx]?.id}
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -20 }}
          transition={{ duration: 0.2 }}
        >
          <Card
            className={`relative overflow-hidden border border-[#1e293b] bg-gradient-to-r ${catMeta?.gradient || 'from-[#1a1f2e] to-[#111827]'} cursor-pointer`}
            onClick={() => onPreview(featured[scrollIdx])}
          >
            <div className="p-4 sm:p-5">
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge
                      variant="outline"
                      className={`text-[9px] font-mono border-0 ${catMeta?.bg || 'bg-[#1a1f2e]'} ${catMeta?.color || 'text-[#94a3b8]'}`}
                    >
                      {catMeta?.icon} {catMeta?.label}
                    </Badge>
                    <Badge
                      variant="outline"
                      className={`text-[9px] font-mono border-0 ${
                        DIFFICULTY_META[featured[scrollIdx]?.difficulty as TemplateDifficulty]?.bg ||
                        'bg-[#1a1f2e]'
                      } ${
                        DIFFICULTY_META[featured[scrollIdx]?.difficulty as TemplateDifficulty]?.color ||
                        'text-[#94a3b8]'
                      }`}
                    >
                      {DIFFICULTY_META[featured[scrollIdx]?.difficulty as TemplateDifficulty]?.label}
                    </Badge>
                  </div>
                  <h3 className="text-base font-bold text-[#f1f5f9] mb-1">
                    {featured[scrollIdx]?.name}
                  </h3>
                  <p className="text-[11px] text-[#94a3b8] leading-relaxed line-clamp-2 mb-2">
                    {featured[scrollIdx]?.description}
                  </p>
                  <div className="flex items-center gap-3">
                    <StarRating rating={featured[scrollIdx]?.rating || 0} size="md" />
                    <span className="text-[9px] font-mono text-[#64748b]">
                      <Download className="h-3 w-3 inline mr-0.5" />
                      {featured[scrollIdx]?.downloads || 0}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-[10px] font-mono text-[#94a3b8] hover:text-[#f1f5f9]"
                    onClick={(e) => {
                      e.stopPropagation();
                      onPreview(featured[scrollIdx]);
                    }}
                  >
                    <Eye className="h-3.5 w-3.5 mr-1" />
                    Preview
                  </Button>
                  <Button
                    size="sm"
                    className="h-7 text-[10px] font-mono bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      onImport(featured[scrollIdx]);
                    }}
                  >
                    <Download className="h-3.5 w-3.5 mr-1" />
                    Import
                  </Button>
                </div>
              </div>

              {/* Metrics row */}
              <div className="grid grid-cols-4 gap-2 mt-3 pt-3 border-t border-[#1e293b]/50">
                {[
                  { label: 'Win Rate', value: `${featured[scrollIdx]?.expectedWinRate || 0}%`, icon: TrendingUp },
                  { label: 'PF', value: (featured[scrollIdx]?.expectedProfitFactor || 0).toFixed(1), icon: BarChart3 },
                  { label: 'Avg Trades', value: `${featured[scrollIdx]?.expectedAvgTrades || 0}/mo`, icon: Zap },
                  { label: 'Max DD', value: `${featured[scrollIdx]?.expectedMaxDrawdown || 0}%`, icon: Shield },
                ].map((m) => (
                  <div key={m.label} className="text-center">
                    <m.icon className="h-3 w-3 mx-auto mb-0.5 text-[#475569]" />
                    <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{m.value}</div>
                    <div className="text-[8px] font-mono text-[#475569]">{m.label}</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// TEMPLATE CARD
// ============================================================

function TemplateCard({
  template,
  onPreview,
  onImport,
}: {
  template: StrategyTemplate;
  onPreview: (t: StrategyTemplate) => void;
  onImport: (t: StrategyTemplate) => void;
}) {
  const catMeta = CATEGORY_META[template.category as TemplateCategory];
  const diffMeta = DIFFICULTY_META[template.difficulty as TemplateDifficulty];

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
    >
      <Card className="h-full flex flex-col border border-[#1e293b] bg-[#111827] hover:border-[#334155] transition-all duration-200 cursor-pointer group">
        <div className="p-3 sm:p-4 flex-1 flex flex-col" onClick={() => onPreview(template)}>
          {/* Header */}
          <div className="flex items-start justify-between mb-2">
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge
                variant="outline"
                className={`text-[8px] font-mono border-0 ${catMeta?.bg || 'bg-[#1a1f2e]'} ${catMeta?.color || 'text-[#94a3b8]'}`}
              >
                {catMeta?.icon} {catMeta?.label}
              </Badge>
              <Badge
                variant="outline"
                className={`text-[8px] font-mono border-0 ${diffMeta?.bg || 'bg-[#1a1f2e]'} ${diffMeta?.color || 'text-[#94a3b8]'}`}
              >
                {diffMeta?.label}
              </Badge>
            </div>
            <StarRating rating={template.rating} />
          </div>

          {/* Name + Description */}
          <h4 className="text-[12px] font-bold text-[#f1f5f9] mb-1 group-hover:text-amber-400 transition-colors">
            {template.name}
          </h4>
          <p className="text-[10px] text-[#94a3b8] leading-relaxed line-clamp-2 mb-2 flex-1">
            {template.description}
          </p>

          {/* Metrics */}
          <div className="grid grid-cols-2 gap-1.5 mb-2">
            <div className="bg-[#0a0e17] rounded px-2 py-1">
              <div className="text-[8px] font-mono text-[#475569]">Win Rate</div>
              <div className="text-[10px] font-mono font-bold text-emerald-400">{template.expectedWinRate}%</div>
            </div>
            <div className="bg-[#0a0e17] rounded px-2 py-1">
              <div className="text-[8px] font-mono text-[#475569]">Profit Factor</div>
              <div className="text-[10px] font-mono font-bold text-amber-400">{template.expectedProfitFactor.toFixed(1)}</div>
            </div>
            <div className="bg-[#0a0e17] rounded px-2 py-1">
              <div className="text-[8px] font-mono text-[#475569]">Max DD</div>
              <div className="text-[10px] font-mono font-bold text-red-400">{template.expectedMaxDrawdown}%</div>
            </div>
            <div className="bg-[#0a0e17] rounded px-2 py-1">
              <div className="text-[8px] font-mono text-[#475569]">Trades/mo</div>
              <div className="text-[10px] font-mono font-bold text-cyan-400">{template.expectedAvgTrades}</div>
            </div>
          </div>

          {/* Chains + Footer */}
          <div className="flex items-center justify-between pt-2 border-t border-[#1e293b]">
            <div className="flex items-center gap-1.5">
              {template.applicableChains.map((c) => (
                <ChainIcon key={c} chain={c} />
              ))}
              <span className="text-[8px] font-mono text-[#475569] ml-1">
                {template.applicableTimeframes.join('/')}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-[9px] font-mono text-[#94a3b8] hover:text-[#f1f5f9]"
                onClick={(e) => {
                  e.stopPropagation();
                  onPreview(template);
                }}
              >
                <Eye className="h-3 w-3 mr-0.5" />
                Preview
              </Button>
              <Button
                size="sm"
                className="h-6 px-2 text-[9px] font-mono bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 border-0"
                onClick={(e) => {
                  e.stopPropagation();
                  onImport(template);
                }}
              >
                <Download className="h-3 w-3 mr-0.5" />
                Import
              </Button>
            </div>
          </div>
        </div>
      </Card>
    </motion.div>
  );
}

// ============================================================
// PREVIEW MODAL
// ============================================================

function PreviewModal({
  template,
  open,
  onClose,
  onImport,
  importing,
}: {
  template: StrategyTemplate | null;
  open: boolean;
  onClose: () => void;
  onImport: (t: StrategyTemplate) => void;
  importing: boolean;
}) {
  if (!template) return null;

  const catMeta = CATEGORY_META[template.category as TemplateCategory];
  const diffMeta = DIFFICULTY_META[template.difficulty as TemplateDifficulty];

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto bg-[#0d1117] border-[#1e293b] text-[#f1f5f9]">
        <DialogHeader>
          <div className="flex items-center gap-2 mb-1">
            <Badge
              variant="outline"
              className={`text-[9px] font-mono border-0 ${catMeta?.bg} ${catMeta?.color}`}
            >
              {catMeta?.icon} {catMeta?.label}
            </Badge>
            <Badge
              variant="outline"
              className={`text-[9px] font-mono border-0 ${diffMeta?.bg} ${diffMeta?.color}`}
            >
              {diffMeta?.label}
            </Badge>
            <StarRating rating={template.rating} size="md" />
          </div>
          <DialogTitle className="text-lg font-bold text-[#f1f5f9]">
            {template.name}
          </DialogTitle>
          <DialogDescription className="text-[11px] text-[#94a3b8] leading-relaxed">
            {template.description}
          </DialogDescription>
        </DialogHeader>

        {/* Performance Metrics */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2">
          {[
            { label: 'Win Rate', value: `${template.expectedWinRate}%`, color: 'text-emerald-400' },
            { label: 'Profit Factor', value: template.expectedProfitFactor.toFixed(1), color: 'text-amber-400' },
            { label: 'Avg Trades/mo', value: `${template.expectedAvgTrades}`, color: 'text-cyan-400' },
            { label: 'Max Drawdown', value: `${template.expectedMaxDrawdown}%`, color: 'text-red-400' },
          ].map((m) => (
            <div key={m.label} className="bg-[#0a0e17] rounded-lg p-2.5 text-center border border-[#1e293b]">
              <div className={`text-sm font-mono font-bold ${m.color}`}>{m.value}</div>
              <div className="text-[9px] font-mono text-[#475569] mt-0.5">{m.label}</div>
            </div>
          ))}
        </div>

        {/* Strategy Configuration */}
        <div className="mt-4 space-y-3">
          <h4 className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">
            Strategy Configuration
          </h4>

          {/* Indicators */}
          <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
            <div className="text-[10px] font-mono text-[#64748b] mb-1.5">Indicators</div>
            <div className="flex flex-wrap gap-1.5">
              {template.strategyConfig.indicators.map((ind, i) => (
                <Badge
                  key={i}
                  variant="outline"
                  className="text-[9px] font-mono border-[#334155] bg-[#1a1f2e] text-[#e2e8f0]"
                >
                  {ind.type}
                  {Object.keys(ind.params).length > 0 && (
                    <span className="text-[#64748b] ml-1">
                      ({Object.entries(ind.params).map(([k, v]) => `${k}:${v}`).join(', ')})
                    </span>
                  )}
                </Badge>
              ))}
            </div>
          </div>

          {/* Entry Rules */}
          <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
            <div className="text-[10px] font-mono text-[#64748b] mb-1.5">
              Entry Rules ({template.strategyConfig.entryRules.logic})
            </div>
            <div className="space-y-1">
              {template.strategyConfig.entryRules.conditions.map((c, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <Check className="h-3 w-3 text-emerald-400 shrink-0" />
                  <span className="text-[10px] font-mono text-[#94a3b8]">{c}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Exit Rules */}
          <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
            <div className="text-[10px] font-mono text-[#64748b] mb-1.5">Exit Rules</div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <div className="text-[8px] font-mono text-[#475569]">Take Profit</div>
                <div className="text-[10px] font-mono font-bold text-emerald-400">
                  +{template.strategyConfig.exitRules.takeProfitPct}%
                </div>
              </div>
              <div>
                <div className="text-[8px] font-mono text-[#475569]">Stop Loss</div>
                <div className="text-[10px] font-mono font-bold text-red-400">
                  -{template.strategyConfig.exitRules.stopLossPct}%
                </div>
              </div>
              <div>
                <div className="text-[8px] font-mono text-[#475569]">Trailing Stop</div>
                <div className="text-[10px] font-mono font-bold text-amber-400">
                  {template.strategyConfig.exitRules.trailingStopPct
                    ? `${template.strategyConfig.exitRules.trailingStopPct}%`
                    : 'N/A'}
                </div>
              </div>
            </div>
          </div>

          {/* Risk Management */}
          <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
            <div className="text-[10px] font-mono text-[#64748b] mb-1.5">Risk Management</div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[8px] font-mono text-[#475569]">Max Position Size</div>
                <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                  {template.strategyConfig.riskManagement.maxPositionSizePct}%
                </div>
              </div>
              <div>
                <div className="text-[8px] font-mono text-[#475569]">Max Open Positions</div>
                <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                  {template.strategyConfig.riskManagement.maxOpenPositions}
                </div>
              </div>
            </div>
          </div>

          {/* Applicable Chains & Timeframes */}
          <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
            <div className="text-[10px] font-mono text-[#64748b] mb-1.5">Applicable Markets</div>
            <div className="flex items-center gap-3">
              <div>
                <div className="text-[8px] font-mono text-[#475569] mb-0.5">Chains</div>
                <div className="flex items-center gap-2">
                  {template.applicableChains.map((c) => (
                    <ChainIcon key={c} chain={c} />
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[8px] font-mono text-[#475569] mb-0.5">Timeframes</div>
                <div className="flex items-center gap-1.5">
                  {template.applicableTimeframes.map((tf) => (
                    <Badge
                      key={tf}
                      variant="outline"
                      className="text-[8px] font-mono border-[#334155] bg-[#1a1f2e] text-[#94a3b8]"
                    >
                      {tf}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Tags */}
        <div className="flex flex-wrap gap-1 mt-2">
          {template.tags.map((tag) => (
            <Badge
              key={tag}
              variant="outline"
              className="text-[8px] font-mono border-[#1e293b] text-[#64748b]"
            >
              #{tag}
            </Badge>
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 mt-4 pt-3 border-t border-[#1e293b]">
          <Button
            className="flex-1 h-8 text-[11px] font-mono bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border-0"
            onClick={() => onImport(template)}
            disabled={importing}
          >
            {importing ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5 mr-1.5" />
            )}
            {importing ? 'Importing...' : 'Import Strategy'}
          </Button>
          <Button
            variant="outline"
            className="h-8 text-[11px] font-mono border-[#1e293b] text-[#94a3b8] hover:text-[#f1f5f9]"
            onClick={onClose}
          >
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// MAIN MARKETPLACE COMPONENT
// ============================================================

export default function StrategyMarketplace() {
  const queryClient = useQueryClient();
  const [categoryFilter, setCategoryFilter] = useState<string>('ALL');
  const [difficultyFilter, setDifficultyFilter] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [previewTemplate, setPreviewTemplate] = useState<StrategyTemplate | null>(null);
  const [importingId, setImportingId] = useState<string | null>(null);

  // Fetch templates
  const { data: templatesData, isLoading } = useQuery({
    queryKey: ['strategy-templates', categoryFilter, difficultyFilter, searchQuery],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (categoryFilter !== 'ALL') params.set('category', categoryFilter);
      if (difficultyFilter !== 'ALL') params.set('difficulty', difficultyFilter);
      if (searchQuery) params.set('search', searchQuery);
      const res = await fetch(`/api/templates?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch templates');
      const json = await res.json();
      return json.data as StrategyTemplate[];
    },
    staleTime: 30000,
  });

  // Import mutation
  const importMutation = useMutation({
    mutationFn: async (templateId: string) => {
      const res = await fetch(`/api/templates/${templateId}/import`, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to import template');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Strategy imported successfully!', {
        description: 'Find it in your Strategy Lab → Classic tab',
      });
      setImportingId(null);
      setPreviewTemplate(null);
      // Invalidate trading systems query so it appears
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
      // Invalidate templates to update download count
      queryClient.invalidateQueries({ queryKey: ['strategy-templates'] });
    },
    onError: () => {
      toast.error('Failed to import strategy template');
      setImportingId(null);
    },
  });

  const handleImport = useCallback(
    (template: StrategyTemplate) => {
      setImportingId(template.id);
      importMutation.mutate(template.id);
    },
    [importMutation],
  );

  const templates = templatesData || [];
  const categories: Array<{ value: string; label: string; icon: string }> = [
    { value: 'ALL', label: 'All', icon: '🎯' },
    ...Object.entries(CATEGORY_META).map(([key, meta]) => ({
      value: key,
      label: meta.label,
      icon: meta.icon,
    })),
  ];
  const difficulties: Array<{ value: string; label: string }> = [
    { value: 'ALL', label: 'All Levels' },
    { value: 'BEGINNER', label: '🌱 Beginner' },
    { value: 'INTERMEDIATE', label: '⚡ Intermediate' },
    { value: 'ADVANCED', label: '🔥 Advanced' },
  ];

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-amber-400" />
          <h2 className="text-[12px] font-bold text-[#f1f5f9] font-mono">Strategy Marketplace</h2>
          <span className="text-[9px] font-mono text-[#475569] bg-[#1a1f2e] px-1.5 py-0.5 rounded">
            {templates.length} templates
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Featured Carousel */}
        <FeaturedCarousel
          templates={templates}
          onPreview={setPreviewTemplate}
          onImport={handleImport}
        />

        {/* Filters Row */}
        <div className="space-y-2">
          {/* Category pills */}
          <div className="flex items-center gap-1 overflow-x-auto pb-1">
            {categories.map((cat) => (
              <button
                key={cat.value}
                onClick={() => setCategoryFilter(cat.value)}
                className={`shrink-0 flex items-center gap-1 px-2 py-1 rounded-md text-[9px] font-mono transition-all ${
                  categoryFilter === cat.value
                    ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                    : 'bg-[#1a1f2e] text-[#94a3b8] border border-[#1e293b] hover:border-[#334155]'
                }`}
              >
                <span>{cat.icon}</span>
                {cat.label}
              </button>
            ))}
          </div>

          {/* Difficulty + Search */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 shrink-0">
              {difficulties.map((diff) => (
                <button
                  key={diff.value}
                  onClick={() => setDifficultyFilter(diff.value)}
                  className={`shrink-0 px-2 py-1 rounded-md text-[9px] font-mono transition-all ${
                    difficultyFilter === diff.value
                      ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                      : 'bg-[#1a1f2e] text-[#94a3b8] border border-[#1e293b] hover:border-[#334155]'
                  }`}
                >
                  {diff.label}
                </button>
              ))}
            </div>
            <div className="flex-1 min-w-0">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search strategies..."
                  className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder-[#475569] pl-7"
                />
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[#475569] hover:text-[#94a3b8]"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Template Grid */}
        {isLoading ? (
          <div className="flex items-center justify-center h-40">
            <Loader2 className="h-6 w-6 text-amber-400 animate-spin" />
            <span className="ml-2 text-[11px] font-mono text-[#64748b]">Loading templates...</span>
          </div>
        ) : templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40">
            <Search className="h-8 w-8 text-[#475569] mb-2" />
            <span className="text-[11px] font-mono text-[#64748b]">No templates found</span>
            <span className="text-[9px] font-mono text-[#475569]">Try adjusting your filters</span>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {templates
              .filter((t) => !t.isFeatured || categoryFilter !== 'ALL')
              .map((template) => (
                <TemplateCard
                  key={template.id}
                  template={template}
                  onPreview={setPreviewTemplate}
                  onImport={handleImport}
                />
              ))}
          </div>
        )}
      </div>

      {/* Preview Modal */}
      <PreviewModal
        template={previewTemplate}
        open={!!previewTemplate}
        onClose={() => setPreviewTemplate(null)}
        onImport={handleImport}
        importing={importingId === previewTemplate?.id && importMutation.isPending}
      />
    </div>
  );
}
