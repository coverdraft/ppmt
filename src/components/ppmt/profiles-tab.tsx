'use client';

import { usePPMTStore } from '@/store/ppmt-strategy-store';
import { User, CheckCircle, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

const ASSET_CLASS_PROFILES: Record<string, { catastrophic: string; examples: string }> = {
  blue_chip: { catastrophic: '8%', examples: 'BTC, ETH' },
  large_cap: { catastrophic: '10%', examples: 'SOL, BNB, XRP' },
  defi: { catastrophic: '12%', examples: 'LINK, UNI, AAVE' },
  meme: { catastrophic: '15%', examples: 'DOGE, SHIB, PEPE' },
  new_launch: { catastrophic: '20%', examples: 'New tokens' },
};

const TIMEFRAME_DEFAULTS: Record<string, { alpha: number; window: number }> = {
  '1h': { alpha: 3, window: 7 },
  '5m': { alpha: 4, window: 7 },
  '1m': { alpha: 5, window: 5 },
  '4h': { alpha: 3, window: 10 },
  '1d': { alpha: 3, window: 7 },
};

export function ProfilesTab() {
  const { strategies } = usePPMTStore();

  // Group strategies by unique symbol+timeframe profiles
  const profileMap = new Map<string, {
    symbol: string;
    timeframe: string;
    assetClass: string;
    alpha: number;
    window: number;
    catLoss: number;
    fuzzy: number;
    calibrated: boolean;
    hasRuns: boolean;
  }>();

  strategies.forEach((s) => {
    const key = `${s.symbol}-${s.timeframe}`;
    if (!profileMap.has(key)) {
      profileMap.set(key, {
        symbol: s.symbol,
        timeframe: s.timeframe,
        assetClass: s.assetClass,
        alpha: s.saxAlpha,
        window: s.saxWindow,
        catLoss: s.catastrophicLossPct,
        fuzzy: s.fuzzyThreshold,
        calibrated: s.totalTrades > 0,
        hasRuns: s.runs.length > 0,
      });
    }
  });

  const profiles = Array.from(profileMap.values());

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <User className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-bold text-zinc-50">Token Profiles</h2>
      </div>

      {/* Asset class reference */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Asset Class Risk Profiles</h3>
        <div className="grid grid-cols-5 gap-3">
          {Object.entries(ASSET_CLASS_PROFILES).map(([cls, info]) => (
            <div key={cls} className="bg-zinc-800/50 rounded-lg p-3">
              <div className="text-xs font-medium text-zinc-200 capitalize mb-1">{cls.replace('_', ' ')}</div>
              <div className="text-[10px] text-zinc-500">Cat Loss: <span className="text-amber-400">{info.catastrophic}</span></div>
              <div className="text-[10px] text-zinc-500">Examples: {info.examples}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Timeframe defaults */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Timeframe α/W Defaults</h3>
        <div className="grid grid-cols-5 gap-3">
          {Object.entries(TIMEFRAME_DEFAULTS).map(([tf, defaults]) => (
            <div key={tf} className="bg-zinc-800/50 rounded-lg p-3 text-center">
              <div className="text-xs font-bold text-zinc-200 mb-1">{tf}</div>
              <div className="text-sm text-cyan-400 font-mono">α={defaults.alpha} W={defaults.window}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Active profiles */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Active Profiles ({profiles.length})</h3>
        {profiles.length === 0 ? (
          <p className="text-sm text-zinc-600">No profiles yet — create a strategy first</p>
        ) : (
          <div className="space-y-2">
            {profiles.map((p) => (
              <div key={`${p.symbol}-${p.timeframe}`} className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-zinc-800/30">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-zinc-200 text-sm">{p.symbol}</span>
                  <span className="text-xs text-zinc-500 font-mono">@{p.timeframe}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-700 text-zinc-400 capitalize">
                    {p.assetClass.replace('_', ' ')}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-zinc-500">
                  <span>α=<span className="text-cyan-400 font-mono">{p.alpha}</span></span>
                  <span>W=<span className="text-cyan-400 font-mono">{p.window}</span></span>
                  <span>Cat=<span className="text-amber-400 font-mono">{p.catLoss}%</span></span>
                  <span>Fuzzy=<span className="text-zinc-300 font-mono">{p.fuzzy}</span></span>
                  <span className="flex items-center gap-1">
                    {p.calibrated ? (
                      <><CheckCircle className="h-3 w-3 text-emerald-400" /> Calibrated</>
                    ) : (
                      <><XCircle className="h-3 w-3 text-zinc-600" /> Not calibrated</>
                    )}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
