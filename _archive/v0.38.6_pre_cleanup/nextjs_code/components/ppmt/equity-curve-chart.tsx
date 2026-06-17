'use client';

import { useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

interface EquityCurveChartProps {
  equityCurve: number[];
  capital: number;
  compact?: boolean;
}

export function EquityCurveChart({ equityCurve, capital, compact }: EquityCurveChartProps) {
  const data = useMemo(() => {
    if (!equityCurve || equityCurve.length === 0) return [];
    return equityCurve.map((value, index) => ({
      index,
      value: Math.round(value * 100) / 100,
      pnl: Math.round(((value - capital) / capital) * 10000) / 100,
    }));
  }, [equityCurve, capital]);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
        No equity data — run a backtest first
      </div>
    );
  }

  const isProfit = data.length > 0 && data[data.length - 1].value >= capital;
  const color = isProfit ? '#10b981' : '#f43f5e';

  return (
    <ResponsiveContainer width="100%" height={compact ? 120 : 200}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
        {!compact && (
          <XAxis dataKey="index" stroke="#52525b" tick={{ fontSize: 10 }} />
        )}
        {!compact && (
          <YAxis stroke="#52525b" tick={{ fontSize: 10 }} tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`} />
        )}
        {!compact && (
          <Tooltip
            contentStyle={{
              backgroundColor: '#18181b',
              border: '1px solid #27272a',
              borderRadius: '8px',
              fontSize: '12px',
            }}
            labelStyle={{ color: '#a1a1aa' }}
            itemStyle={{ color: '#fafafa' }}
            formatter={(value: number) => [`$${value.toLocaleString()}`, 'Equity']}
          />
        )}
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          fill="url(#equityGradient)"
          dot={false}
          activeDot={{ r: 3, stroke: color, strokeWidth: 2, fill: '#18181b' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
