'use client';

import { useQuery } from '@tanstack/react-query';
import { useCryptoStore } from '@/store/crypto-store';
import { Shield, Eye, ArrowDownUp, TrendingUp } from 'lucide-react';

// ============================================================
// MINI GAUGE (compact arc)
// ============================================================

function GaugeMini({ value, maxVal = 100, color, size = 40 }: { value: number; maxVal?: number; color: string; size?: number }) {
  const percentage = Math.min(100, Math.max(0, (value / maxVal) * 100));
  const strokeDasharray = `${(percentage / 100) * 126} 126`;

  return (
    <svg viewBox="0 0 100 60" width={size} height={size * 0.6}>
      <path
        d="M 10 55 A 40 40 0 0 1 90 55"
        fill="none"
        stroke="#1a1f2e"
        strokeWidth="10"
        strokeLinecap="round"
      />
      <path
        d="M 10 55 A 40 40 0 0 1 90 55"
        fill="none"
        stroke={color}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={strokeDasharray}
        filter={`drop-shadow(0 0 3px ${color}40)`}
      />
      <text x="50" y="48" textAnchor="middle" fill={color} fontSize="16" fontFamily="monospace" fontWeight="bold">
        {Math.round(value)}
      </text>
    </svg>
  );
}

// ============================================================
// MAIN COMPONENT - Compact Single Row
// ============================================================

export function IntelligenceModules() {
  const signals = useCryptoStore((s) => s.signals);
  const smartMoneyAlerts = useCryptoStore((s) => s.smartMoneyAlerts);

  const { data: stats } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: async () => {
      const res = await fetch('/api/dashboard/stats');
      return res.json();
    },
    refetchInterval: 30000,
  });

  const rugPullCount = signals.filter(s => s.type === 'RUG_PULL').length;
  const smartMoneyCount = signals.filter(s => s.type === 'SMART_MONEY_ENTRY').length;
  const vShapeCount = signals.filter(s => s.type === 'V_SHAPE').length;

  const fomoIndex = stats?.fomoIndex ?? 45;
  const threatLevel = stats?.threatLevel ?? 'MEDIUM';

  const modules = [
    {
      title: 'Rug-Pull Predictor',
      icon: Shield,
      iconColor: '#ef4444',
      value: rugPullCount,
      label: 'Threats',
      gauge: { value: threatLevel === 'HIGH' ? 85 : threatLevel === 'MEDIUM' ? 55 : 25, color: '#ef4444' },
      badge: { text: threatLevel, color: threatLevel === 'HIGH' ? 'bg-red-500/20 text-red-400' : threatLevel === 'MEDIUM' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-emerald-500/20 text-emerald-400' },
    },
    {
      title: 'Smart Money',
      icon: Eye,
      iconColor: '#10b981',
      value: smartMoneyCount,
      label: 'Entries',
      gauge: { value: Math.min(smartMoneyCount * 12, 100), color: '#10b981' },
      badge: { text: `${smartMoneyAlerts.length} wallets`, color: 'bg-emerald-500/20 text-emerald-400' },
    },
    {
      title: 'Contrarian',
      icon: ArrowDownUp,
      iconColor: '#f59e0b',
      value: fomoIndex,
      label: 'FOMO',
      gauge: { value: fomoIndex, color: fomoIndex > 70 ? '#ef4444' : fomoIndex > 40 ? '#f59e0b' : '#10b981' },
      badge: { text: fomoIndex > 70 ? 'TRAP' : 'OK', color: fomoIndex > 70 ? 'bg-red-500/20 text-red-400' : 'bg-emerald-500/20 text-emerald-400' },
    },
    {
      title: 'V-Shape',
      icon: TrendingUp,
      iconColor: '#22d3ee',
      value: vShapeCount,
      label: 'Opps',
      gauge: { value: Math.min(vShapeCount * 20, 100), color: '#22d3ee' },
      badge: { text: `${vShapeCount}`, color: 'bg-cyan-500/20 text-cyan-400' },
    },
  ];

  return (
    <div className="grid grid-cols-4 gap-1 shrink-0">
      {modules.map((module) => {
        const Icon = module.icon;
        return (
          <div
            key={module.title}
            className="intel-card bg-[#0d1117] rounded-md px-2 py-1 flex items-center gap-2"
          >
            <div className="flex flex-col items-center shrink-0">
              <Icon className="h-3 w-3 mb-0.5" style={{ color: module.iconColor }} />
              <GaugeMini
                value={module.gauge.value}
                color={module.gauge.color}
                size={36}
              />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[8px] font-mono text-[#475569] uppercase tracking-wider truncate">{module.title}</div>
              <div className="flex items-baseline gap-0.5 mt-px">
                <span className="mono-data text-sm font-bold text-[#e2e8f0]">{module.value}</span>
                <span className="text-[7px] font-mono text-[#64748b]">{module.label}</span>
              </div>
              <span className={`inline-block text-[7px] font-mono px-1 py-px rounded ${module.badge.color}`}>
                {module.badge.text}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
