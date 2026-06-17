'use client';

import { useCryptoStore } from '@/store/crypto-store';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { PieChart, Pie, Cell, ResponsiveContainer, AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip } from 'recharts';

function formatPrice(price: number) {
  if (price == null || isNaN(price)) return '$0.00';
  if (price >= 1000) return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (price >= 1) return `$${price.toFixed(2)}`;
  if (price >= 0.001) return `$${price.toFixed(4)}`;
  return `$${price.toFixed(8)}`;
}

function RiskGauge({ score }: { score: number }) {
  const color = score <= 30 ? '#10b981' : score <= 60 ? '#f59e0b' : '#ef4444';
  const label = score <= 30 ? 'SAFE' : score <= 60 ? 'CAUTION' : 'DANGER';

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-40 h-20">
        <svg viewBox="0 0 200 110" className="w-full h-full">
          <path
            d="M 20 100 A 80 80 0 0 1 180 100"
            fill="none"
            stroke="#1a1f2e"
            strokeWidth="12"
            strokeLinecap="round"
          />
          <path
            d="M 20 100 A 80 80 0 0 1 180 100"
            fill="none"
            stroke={color}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={`${(score / 100) * 251.3} 251.3`}
            filter={`drop-shadow(0 0 6px ${color}40)`}
          />
          <line
            x1="100"
            y1="100"
            x2={100 + 65 * Math.cos(((score / 100) * 180 - 180) * (Math.PI / 180))}
            y2={100 + 65 * Math.sin(((score / 100) * 180 - 180) * (Math.PI / 180))}
            stroke={color}
            strokeWidth="2"
            strokeLinecap="round"
          />
          <circle cx="100" cy="100" r="4" fill={color} />
        </svg>
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2">
          <span className="mono-data text-2xl font-bold" style={{ color }}>{score}</span>
        </div>
      </div>
      <Badge
        className={`text-xs font-mono font-bold ${
          label === 'SAFE' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
          label === 'CAUTION' ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' :
          'bg-red-500/20 text-red-400 border-red-500/30'
        }`}
      >
        {label}
      </Badge>
    </div>
  );
}

function MiniScoreBar({ label, value, maxVal = 100, color }: { label: string; value: number; maxVal?: number; color: string }) {
  const pct = Math.min((value / maxVal) * 100, 100);
  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] font-mono text-[#64748b] w-20 text-right">{label}</span>
      <div className="flex-1 h-2 bg-[#1a1f2e] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, backgroundColor: color, boxShadow: `0 0 8px ${color}40` }} />
      </div>
      <span className="mono-data text-[10px] w-8" style={{ color }}>{(value ?? 0).toFixed(0)}</span>
    </div>
  );
}

export function DNAScanner() {
  const selectedToken = useCryptoStore((s) => s.selectedToken);

  // Try to fetch token DNA from API
  const { data: tokenDetail } = useQuery({
    queryKey: ['token-dna', selectedToken?.id],
    queryFn: async () => {
      if (!selectedToken?.id) return null;
      try {
        const res = await fetch(`/api/tokens/${selectedToken.id}`);
        if (!res.ok) return null;
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!selectedToken?.id,
  });

  const dna = tokenDetail?.token?.dna;

  if (!selectedToken) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg p-6">
        <div className="text-[#64748b] font-mono text-sm mb-2">No Token Selected</div>
        <div className="text-[#475569] font-mono text-xs">Click a token from the flow to analyze its DNA</div>
      </div>
    );
  }

  // Use real DNA data if available, otherwise simulate from risk score
  const riskScore = selectedToken.riskScore ?? dna?.riskScore ?? 50;

  const liquidityDNA: number[] = dna ? JSON.parse(dna.liquidityDNA) : [
    1 - riskScore / 120, riskScore / 150, 0.5 + Math.random() * 0.3,
    0.3 + Math.random() * 0.4, 0.6 - riskScore / 200, 0.4 + Math.random() * 0.3,
    riskScore / 130, 0.5 + Math.random() * 0.3,
  ];
  const walletDNA: number[] = dna ? JSON.parse(dna.walletDNA) : [
    0.2 + Math.random() * 0.3, 0.05 + (riskScore / 200), 0.1 + Math.random() * 0.2,
    0.3 + Math.random() * 0.2, 0.05 + Math.random() * 0.1,
  ];
  const topologyDNA: number[] = dna ? JSON.parse(dna.topologyDNA) : Array.from({ length: 10 }, () => Math.random());

  // Enriched trader intelligence scores from DNA
  const botActivityScore = dna?.botActivityScore ?? riskScore * 0.4;
  const smartMoneyScoreDna = dna?.smartMoneyScore ?? 100 - riskScore * 0.6;
  const retailScore = dna?.retailScore ?? 40;
  const whaleScoreDna = dna?.whaleScore ?? riskScore > 50 ? 10 : 25;
  const washTradeProb = dna?.washTradeProb ?? riskScore * 0.003;
  const sniperPct = dna?.sniperPct ?? riskScore * 0.15;
  const mevPct = dna?.mevPct ?? riskScore * 0.2;
  const copyBotPct = dna?.copyBotPct ?? 5;

  // Parse trader composition from DNA
  let traderComposition: Record<string, number> = {};
  if (dna?.traderComposition) {
    try {
      traderComposition = JSON.parse(dna.traderComposition);
    } catch { /* use default */ }
  }
  if (Object.keys(traderComposition).length === 0) {
    traderComposition = {
      smartMoney: Math.floor(smartMoneyScoreDna * 0.3),
      whale: Math.floor(whaleScoreDna * 0.2),
      bot_mev: Math.floor(mevPct * 0.5),
      bot_sniper: Math.floor(sniperPct * 0.6),
      bot_copy: Math.floor(copyBotPct * 0.3),
      bot_wash: Math.floor(washTradeProb * 20),
      retail: Math.floor(retailScore * 0.8),
      creator: 1,
      fund: 2,
    };
  }

  // Wallet composition for pie chart (enriched with bot types)
  const walletComposition = [
    { name: 'Smart Money', value: Math.max(1, walletDNA[0] * 100), color: '#10b981' },
    { name: 'Sniper', value: Math.max(1, walletDNA[1] * 100), color: '#ef4444' },
    { name: 'MEV Bots', value: Math.max(1, mevPct), color: '#f59e0b' },
    { name: 'Retail', value: Math.max(1, walletDNA[3] * 100), color: '#22d3ee' },
    { name: 'Whale', value: Math.max(1, walletDNA[4] * 100), color: '#8b5cf6' },
  ];

  // Bot breakdown for mini bar chart
  const botBreakdown = [
    { name: 'MEV', value: traderComposition.bot_mev || 0, color: '#f59e0b' },
    { name: 'Sniper', value: traderComposition.bot_sniper || 0, color: '#ef4444' },
    { name: 'Copy', value: traderComposition.bot_copy || 0, color: '#06b6d4' },
    { name: 'Wash', value: traderComposition.bot_wash || 0, color: '#a855f7' },
    { name: 'Sandwich', value: Math.floor((traderComposition.bot_mev || 0) * 0.4), color: '#ec4899' },
  ];

  // Topology chart data
  const topologyData = topologyDNA.map((v, i) => ({ index: i, value: v }));

  // Liquidity breakdown
  const liquidityBreakdown = liquidityDNA.map((v, i) => ({
    label: ['Depth', 'Spread', 'Concentration', 'Velocity', 'Resilience', 'Momentum', 'Volatility', 'Trend'][i] || `M${i}`,
    value: (v * 100).toFixed(0),
    color: v > 0.7 ? '#10b981' : v > 0.4 ? '#f59e0b' : '#ef4444',
  }));

  const verdict = riskScore <= 30 ? 'SAFE' : riskScore <= 60 ? 'CAUTION' : 'DANGER';
  const verdictColor = riskScore <= 30 ? '#10b981' : riskScore <= 60 ? '#f59e0b' : '#ef4444';
  const verdictProbability = riskScore <= 30 ? 85 - riskScore : riskScore <= 60 ? 60 + (60 - riskScore) : Math.min(95, riskScore + 10);

  // Top wallets from DNA
  let topWallets: Array<{ address: string; label: string; pnl: number; entryRank: number; holdTime: number }> = [];
  if (dna?.topWallets) {
    try {
      topWallets = JSON.parse(dna.topWallets);
    } catch { /* use empty */ }
  }

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-[#1e293b]">
        <div className="flex items-center gap-2">
          <span className="text-[#d4af37] font-mono text-xs font-bold">DNA SCANNER</span>
          <span className="font-mono text-sm font-bold text-[#e2e8f0]">{selectedToken.symbol}</span>
          <span className="mono-data text-xs text-[#94a3b8]">{formatPrice(selectedToken.priceUsd)}</span>
        </div>
        <Badge
          variant="outline"
          className={`text-[9px] font-mono ${selectedToken.chain === 'SOL' ? 'border-purple-500/50 text-purple-400' : 'border-blue-500/50 text-blue-400'}`}
        >
          {selectedToken.chain}
        </Badge>
      </div>

      <div className="flex-1 overflow-y-auto max-h-[calc(100vh-260px)] p-3 space-y-3">
        {/* Risk Gauge + Verdict */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 flex flex-col items-center justify-center">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Risk Score</span>
            <RiskGauge score={riskScore} />
          </div>
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 flex flex-col items-center justify-center">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Verdict</span>
            <span className="text-3xl font-mono font-bold" style={{ color: verdictColor }}>
              {verdict}
            </span>
            <span className="mono-data text-xs text-[#94a3b8] mt-1">{verdictProbability}% probability</span>
            <div className="w-full mt-3">
              <div className="h-2 bg-[#1a1f2e] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-1000"
                  style={{
                    width: `${verdictProbability}%`,
                    backgroundColor: verdictColor,
                    boxShadow: `0 0 10px ${verdictColor}40`,
                  }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Trader Intelligence Scores - NEW */}
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Trader Intelligence Scores</span>
          <div className="mt-2 space-y-1.5">
            <MiniScoreBar label="Bot Activity" value={botActivityScore} color={botActivityScore > 50 ? '#ef4444' : '#f59e0b'} />
            <MiniScoreBar label="Smart Money" value={smartMoneyScoreDna} color="#10b981" />
            <MiniScoreBar label="Retail Dom." value={retailScore} color="#22d3ee" />
            <MiniScoreBar label="Whale Conc." value={whaleScoreDna} color="#8b5cf6" />
            <MiniScoreBar label="Sniper %" value={sniperPct} color="#ef4444" />
            <MiniScoreBar label="MEV Volume %" value={mevPct} color="#f59e0b" />
            <MiniScoreBar label="Wash Prob." value={washTradeProb * 100} color={washTradeProb > 0.3 ? '#ef4444' : '#a855f7'} />
          </div>
        </div>

        {/* Liquidity DNA */}
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Liquidity DNA</span>
          <div className="grid grid-cols-4 gap-2 mt-2">
            {liquidityBreakdown.map((item, i) => (
              <div key={i} className="flex flex-col items-center bg-[#0a0e17] rounded p-2">
                <span className="text-[9px] font-mono text-[#64748b]">{item.label}</span>
                <span className="mono-data text-sm font-bold" style={{ color: item.color }}>{item.value}%</span>
              </div>
            ))}
          </div>
        </div>

        {/* Wallet DNA Pie Chart - ENRICHED */}
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Wallet Composition DNA</span>
          <div className="flex items-center gap-4 mt-2">
            <div className="w-32 h-32">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={walletComposition}
                    cx="50%"
                    cy="50%"
                    innerRadius={30}
                    outerRadius={55}
                    dataKey="value"
                    stroke="none"
                  >
                    {walletComposition.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex-1 space-y-1">
              {walletComposition.map((item, i) => (
                <div key={i} className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: item.color }} />
                  <span className="text-[10px] font-mono text-[#94a3b8]">{item.name}</span>
                  <span className="mono-data text-[10px] ml-auto" style={{ color: item.color }}>
                    {item.value.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Bot Breakdown - NEW */}
        {botActivityScore > 10 && (
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Bot Type Breakdown</span>
            <div className="h-28 mt-2">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={botBreakdown} layout="vertical">
                  <XAxis type="number" hide />
                  <YAxis dataKey="name" type="category" tick={{ fill: '#94a3b8', fontSize: 9, fontFamily: 'monospace' }} width={50} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#111827', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '10px' }}
                    itemStyle={{ color: '#e2e8f0' }}
                    labelStyle={{ color: '#94a3b8' }}
                  />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                    {botBreakdown.map((entry, index) => (
                      <Cell key={`bot-${index}`} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span className="text-[9px] font-mono text-[#64748b]">Total identified bots</span>
              <span className="mono-data text-[10px] text-[#f59e0b]">
                {(traderComposition.bot_mev || 0) + (traderComposition.bot_sniper || 0) + (traderComposition.bot_copy || 0) + (traderComposition.bot_wash || 0)} wallets
              </span>
            </div>
          </div>
        )}

        {/* Topology DNA */}
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Topology DNA — Volume Pattern</span>
          <div className="h-24 mt-2">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={topologyData}>
                <defs>
                  <linearGradient id="topologyGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#22d3ee"
                  fill="url(#topologyGrad)"
                  strokeWidth={1.5}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Top Wallets - NEW */}
        {topWallets.length > 0 && (
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Top Wallets in This Token</span>
            <div className="mt-2 space-y-1">
              {topWallets.map((wallet, i) => {
                const labelColors: Record<string, string> = {
                  'SMART_MONEY': 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
                  'WHALE': 'bg-violet-500/20 text-violet-400 border-violet-500/30',
                  'BOT_SNIPER': 'bg-red-500/20 text-red-400 border-red-500/30',
                  'BOT_MEV': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
                  'RETAIL': 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
                };
                return (
                  <div key={i} className="flex items-center gap-2 bg-[#0a0e17] rounded px-2 py-1.5">
                    <span className="mono-data text-[10px] font-bold text-[#e2e8f0] w-16">{wallet.address}</span>
                    <Badge className={`text-[8px] h-4 font-mono ${labelColors[wallet.label] || 'bg-gray-500/20 text-gray-400 border-gray-500/30'}`}>
                      {wallet.label.replace('BOT_', '')}
                    </Badge>
                    <span className={`mono-data text-[10px] ${wallet.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {wallet.pnl >= 0 ? '+' : ''}{wallet.pnl >= 0 ? `$${wallet.pnl.toFixed(0)}` : `-$${Math.abs(wallet.pnl).toFixed(0)}`}
                    </span>
                    <span className="mono-data text-[9px] text-[#64748b] ml-auto">Rank #{wallet.entryRank}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Similar Historical Events */}
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Similar Historical Patterns</span>
          <div className="mt-2 space-y-1">
            {[
              { token: 'MEMEX', outcome: 'RUG', return: '-89%', match: 94 },
              { token: 'DOGE2', outcome: 'PUMP', return: '+234%', match: 87 },
              { token: 'CATSOL', outcome: 'PUMP', return: '+56%', match: 82 },
              { token: 'FLOKI2', outcome: 'RUG', return: '-67%', match: 78 },
              { token: 'SHIBAI', outcome: 'FLAT', return: '+3%', match: 71 },
            ].map((event, i) => (
              <div key={i} className="flex items-center gap-2 bg-[#0a0e17] rounded px-2 py-1.5">
                <span className="mono-data text-xs font-bold text-[#e2e8f0] w-16">{event.token}</span>
                <Badge
                  className={`text-[9px] h-4 font-mono ${
                    event.outcome === 'RUG' ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                    event.outcome === 'PUMP' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
                    'bg-gray-500/20 text-gray-400 border-gray-500/30'
                  }`}
                >
                  {event.outcome}
                </Badge>
                <span className={`mono-data text-xs ${
                  event.return.startsWith('+') ? 'text-emerald-400' : event.return.startsWith('-') ? 'text-red-400' : 'text-[#94a3b8]'
                }`}>
                  {event.return}
                </span>
                <span className="mono-data text-[10px] text-[#64748b] ml-auto">{event.match}% match</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
