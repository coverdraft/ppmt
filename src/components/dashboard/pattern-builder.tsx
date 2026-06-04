'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Plus, Trash2, Play, Zap } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface ConditionBlock {
  id: string;
  category: string;
  field: string;
  operator: string;
  value: number | string;
}

const CATEGORY_COLORS: Record<string, string> = {
  Market: '#22d3ee',
  'On-chain': '#d4af37',
  Sentiment: '#8b5cf6',
  User: '#f59e0b',
};

const CONDITION_TEMPLATES: { category: string; field: string; operators: string[]; defaultValue: number }[] = [
  { category: 'Market', field: 'Volume', operators: ['>', '<', '>=', '<='], defaultValue: 100000 },
  { category: 'Market', field: 'Price Change', operators: ['>', '<', '>=', '<='], defaultValue: 10 },
  { category: 'Market', field: 'Liquidity', operators: ['>', '<', '>=', '<='], defaultValue: 50000 },
  { category: 'On-chain', field: 'Smart Money Buying', operators: ['>', '<', '>=', '<='], defaultValue: 3 },
  { category: 'On-chain', field: 'Sniper %', operators: ['>', '<', '>=', '<='], defaultValue: 20 },
  { category: 'On-chain', field: 'Wallet Count', operators: ['>', '<', '>=', '<='], defaultValue: 100 },
  { category: 'Sentiment', field: 'Social Mentions', operators: ['>', '<', '>=', '<='], defaultValue: 500 },
  { category: 'Sentiment', field: 'Long/Short Ratio', operators: ['>', '<', '>=', '<='], defaultValue: 1.5 },
  { category: 'User', field: 'Stop Loss Density', operators: ['>', '<', '>=', '<='], defaultValue: 60 },
  { category: 'User', field: 'FOMO Index', operators: ['>', '<', '>=', '<='], defaultValue: 70 },
];

export function PatternBuilder() {
  const queryClient = useQueryClient();
  const [conditions, setConditions] = useState<ConditionBlock[]>([]);
  const [logicOp, setLogicOp] = useState<'AND' | 'OR'>('AND');
  const [patternName, setPatternName] = useState('');
  const [showBacktest, setShowBacktest] = useState(false);

  const { data: patternsData } = useQuery({
    queryKey: ['patterns'],
    queryFn: async () => {
      const res = await fetch('/api/patterns');
      return res.json();
    },
  });

  const createPattern = useMutation({
    mutationFn: async (data: { name: string; conditions: ConditionBlock[] }) => {
      const res = await fetch('/api/patterns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['patterns'] }),
  });

  const runBacktest = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/patterns/${id}/backtest`, { method: 'POST' });
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['patterns'] }),
  });

  const addCondition = (template: typeof CONDITION_TEMPLATES[0]) => {
    const block: ConditionBlock = {
      id: `cond_${Date.now()}_${Math.random().toString(36).substr(2, 4)}`,
      category: template.category,
      field: template.field,
      operator: template.operators[0],
      value: template.defaultValue,
    };
    setConditions([...conditions, block]);
  };

  const removeCondition = (id: string) => {
    setConditions(conditions.filter((c) => c.id !== id));
  };

  const updateCondition = (id: string, updates: Partial<ConditionBlock>) => {
    setConditions(conditions.map((c) => (c.id === id ? { ...c, ...updates } : c)));
  };

  const handleSave = () => {
    if (!patternName || conditions.length === 0) return;
    createPattern.mutate({ name: patternName, conditions });
    setPatternName('');
    setConditions([]);
  };

  const patterns = patternsData?.patterns || [];

  return (
    <div className="flex h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Left: Condition Blocks */}
      <div className="w-56 border-r border-[#1e293b] flex flex-col">
        <div className="p-3 border-b border-[#1e293b]">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Condition Blocks</span>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {CONDITION_TEMPLATES.map((template, i) => (
            <button
              key={i}
              onClick={() => addCondition(template)}
              className="w-full flex items-center gap-2 p-2 rounded text-left hover:bg-[#1a1f2e] transition-colors group"
            >
              <div
                className="w-1.5 h-6 rounded-full"
                style={{ backgroundColor: CATEGORY_COLORS[template.category] }}
              />
              <div>
                <div className="text-[10px] font-mono text-[#94a3b8]">{template.category}</div>
                <div className="text-xs font-mono text-[#e2e8f0] group-hover:text-[#d4af37]">{template.field}</div>
              </div>
              <Plus className="h-3 w-3 text-[#64748b] ml-auto opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>
          ))}
        </div>
      </div>

      {/* Right: Builder + Results */}
      <div className="flex-1 flex flex-col">
        {/* Builder Area */}
        <div className="p-3 border-b border-[#1e293b]">
          <div className="flex items-center gap-2 mb-3">
            <input
              type="text"
              placeholder="Pattern name..."
              value={patternName}
              onChange={(e) => setPatternName(e.target.value)}
              className="bg-[#1a1f2e] border border-[#2d3748] rounded px-2 py-1 text-xs font-mono text-[#e2e8f0] placeholder-[#64748b] flex-1 focus:outline-none focus:border-[#d4af37]/50"
            />
            <Button
              onClick={handleSave}
              disabled={!patternName || conditions.length === 0}
              className="h-7 px-3 text-xs font-mono bg-[#d4af37] text-[#0a0e17] hover:bg-[#f5c842] disabled:opacity-50"
            >
              <Zap className="h-3 w-3 mr-1" /> Save
            </Button>
          </div>

          {/* Logic operator toggle */}
          {conditions.length > 1 && (
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] font-mono text-[#64748b]">Logic:</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setLogicOp('AND')}
                className={`h-5 px-2 text-[10px] font-mono ${logicOp === 'AND' ? 'bg-[#d4af37]/20 text-[#d4af37]' : 'text-[#94a3b8]'}`}
              >
                AND
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setLogicOp('OR')}
                className={`h-5 px-2 text-[10px] font-mono ${logicOp === 'OR' ? 'bg-[#22d3ee]/20 text-[#22d3ee]' : 'text-[#94a3b8]'}`}
              >
                OR
              </Button>
            </div>
          )}

          {/* Conditions */}
          <div className="space-y-2 min-h-[60px]">
            <AnimatePresence>
              {conditions.map((cond, i) => (
                <motion.div
                  key={cond.id}
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: -20 }}
                  className="flex items-center gap-2 bg-[#111827] border border-[#1e293b] rounded p-2"
                >
                  <div
                    className="w-1 h-6 rounded-full shrink-0"
                    style={{ backgroundColor: CATEGORY_COLORS[cond.category] }}
                  />
                  <span className="text-[10px] font-mono text-[#64748b]">{cond.category}</span>
                  <span className="text-xs font-mono text-[#e2e8f0]">{cond.field}</span>
                  <select
                    value={cond.operator}
                    onChange={(e) => updateCondition(cond.id, { operator: e.target.value })}
                    className="bg-[#1a1f2e] border border-[#2d3748] rounded px-1 py-0.5 text-[10px] font-mono text-[#d4af37] focus:outline-none"
                  >
                    {CONDITION_TEMPLATES.find(t => t.field === cond.field)?.operators.map(op => (
                      <option key={op} value={op}>{op}</option>
                    ))}
                  </select>
                  <input
                    type="number"
                    value={cond.value}
                    onChange={(e) => updateCondition(cond.id, { value: parseFloat(e.target.value) || 0 })}
                    className="bg-[#1a1f2e] border border-[#2d3748] rounded px-1 py-0.5 text-xs font-mono text-[#e2e8f0] w-20 focus:outline-none focus:border-[#d4af37]/50"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeCondition(cond.id)}
                    className="h-5 w-5 p-0 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                  {i < conditions.length - 1 && (
                    <span className={`text-[10px] font-mono font-bold ${
                      logicOp === 'AND' ? 'text-[#d4af37]' : 'text-[#22d3ee]'
                    }`}>
                      {logicOp}
                    </span>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>

            {conditions.length === 0 && (
              <div className="flex items-center justify-center h-12 text-[#475569] font-mono text-xs border border-dashed border-[#2d3748] rounded">
                Add conditions from the left panel
              </div>
            )}
          </div>
        </div>

        {/* Saved Patterns with Backtest */}
        <div className="flex-1 overflow-y-auto p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Saved Patterns</span>
          <div className="space-y-2 mt-2">
            {patterns.map((pattern: any) => {
              const backtestResults = pattern.backtestResults ? JSON.parse(pattern.backtestResults) : null;

              return (
                <Card key={pattern.id} className="bg-[#111827] border-[#1e293b] p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-xs font-bold text-[#e2e8f0]">{pattern.name}</span>
                    <div className="flex items-center gap-2">
                      <Badge className="text-[9px] font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748]">
                        {pattern.occurrences} trades
                      </Badge>
                      <Button
                        onClick={() => {
                          runBacktest.mutate(pattern.id);
                          setShowBacktest(true);
                        }}
                        className="h-5 px-2 text-[10px] font-mono bg-[#22d3ee]/20 text-[#22d3ee] hover:bg-[#22d3ee]/30 border border-[#22d3ee]/30"
                        size="sm"
                      >
                        <Play className="h-2.5 w-2.5 mr-1" /> Backtest
                      </Button>
                    </div>
                  </div>

                  {backtestResults && backtestResults.winRate > 0 && (
                    <div className="grid grid-cols-4 gap-2 mt-2">
                      <div className="bg-[#0a0e17] rounded p-2 text-center">
                        <div className="text-[9px] font-mono text-[#64748b]">Win Rate</div>
                        <div className={`mono-data text-sm font-bold ${backtestResults.winRate > 0.6 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {(backtestResults.winRate * 100).toFixed(1)}%
                        </div>
                      </div>
                      <div className="bg-[#0a0e17] rounded p-2 text-center">
                        <div className="text-[9px] font-mono text-[#64748b]">Avg Return</div>
                        <div className={`mono-data text-sm font-bold ${backtestResults.avgReturn > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {backtestResults.avgReturn > 0 ? '+' : ''}{backtestResults.avgReturn?.toFixed(1)}%
                        </div>
                      </div>
                      <div className="bg-[#0a0e17] rounded p-2 text-center">
                        <div className="text-[9px] font-mono text-[#64748b]">Max DD</div>
                        <div className="mono-data text-sm font-bold text-red-400">
                          {backtestResults.maxDrawdown?.toFixed(1)}%
                        </div>
                      </div>
                      <div className="bg-[#0a0e17] rounded p-2 text-center">
                        <div className="text-[9px] font-mono text-[#64748b]">Sharpe</div>
                        <div className={`mono-data text-sm font-bold ${backtestResults.sharpeRatio > 1 ? 'text-emerald-400' : 'text-yellow-400'}`}>
                          {backtestResults.sharpeRatio?.toFixed(2)}
                        </div>
                      </div>
                    </div>
                  )}

                  {backtestResults?.distribution && (
                    <div className="h-16 mt-2">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={backtestResults.distribution.map((v: number, i: number) => ({ i, v }))}>
                          <Bar dataKey="v" fill="#d4af37" opacity={0.6} radius={[2, 2, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
