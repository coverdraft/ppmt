'use client';

import { usePPMTStore } from '@/store/ppmt-strategy-store';
import { Settings as SettingsIcon, Database, Terminal, Server } from 'lucide-react';
import { useEffect, useState } from 'react';

interface PPMTStatus {
  initialized: boolean;
  dbExists: boolean;
  pythonPath: string;
  venvExists: boolean;
  totalAssets: number;
}

export function SettingsTab() {
  const { strategies } = usePPMTStore();
  const [ppmtStatus, setPpmtStatus] = useState<PPMTStatus | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch('/api/ppmt/status');
        const json = await res.json();
        if (json.success) {
          setPpmtStatus(json.data);
        }
      } catch {
        // PPMT may not be available
      }
    }
    load();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <SettingsIcon className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-bold text-zinc-50">Settings</h2>
      </div>

      {/* PPMT Engine Status */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Server className="h-4 w-4 text-cyan-400" />
          <h3 className="text-sm font-medium text-zinc-300">PPMT Engine Status</h3>
        </div>
        {ppmtStatus ? (
          <div className="grid grid-cols-2 gap-3 text-sm">
            <SettingRow label="Database" value={ppmtStatus.dbExists ? 'Connected' : 'Not found'} good={ppmtStatus.dbExists} />
            <SettingRow label="Python" value={ppmtStatus.pythonPath || 'Not found'} good={!!ppmtStatus.pythonPath} />
            <SettingRow label="Venv" value={ppmtStatus.venvExists ? 'Active' : 'Not set up'} good={ppmtStatus.venvExists} />
            <SettingRow label="Tracked Assets" value={String(ppmtStatus.totalAssets)} good={ppmtStatus.totalAssets > 0} />
          </div>
        ) : (
          <p className="text-sm text-zinc-600">Checking PPMT status...</p>
        )}
      </div>

      {/* Database info */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Database className="h-4 w-4 text-emerald-400" />
          <h3 className="text-sm font-medium text-zinc-300">Dashboard Database</h3>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <SettingRow label="Strategies" value={String(strategies.length)} good={strategies.length > 0} />
          <SettingRow label="Total Runs" value={String(strategies.reduce((sum, s) => sum + s.runs.length, 0))} good={false} neutral />
        </div>
      </div>

      {/* Python Engine */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Terminal className="h-4 w-4 text-amber-400" />
          <h3 className="text-sm font-medium text-zinc-300">Python PPMT Package</h3>
        </div>
        <p className="text-xs text-zinc-500 mb-3">
          The PPMT engine is a Python package that runs PaperTrading and backtests.
          When Python is available and data exists for a token, runs will use the real
          PPMT engine. Otherwise, runs will fail with an engine-unavailable error.
        </p>
        <div className="bg-zinc-800/50 rounded-lg p-3 text-xs font-mono text-zinc-400">
          <div>Package: /home/z/my-project/ppmt/</div>
          <div>DB: ~/.ppmt/ppmt.db</div>
          <div>Run: PYTHONPATH=src python3 -m ppmt.engine.paper_trader</div>
        </div>
      </div>

      {/* About */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-2">About PPMT Terminal</h3>
        <p className="text-xs text-zinc-500">
          PPMT (Progressive Pattern Matching Trie) Strategy Terminal v0.12.0.
          Strategy lifecycle management: Create → Backtest → Paper → Forward → Live.
          Built with Next.js, TypeScript, and the PPMT Python engine.
        </p>
      </div>
    </div>
  );
}

function SettingRow({ label, value, good, neutral }: { label: string; value: string; good: boolean; neutral?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-zinc-500 text-xs">{label}</span>
      <span className={neutral ? 'text-zinc-300 text-xs font-mono' : `text-xs font-mono ${good ? 'text-emerald-400' : 'text-rose-400'}`}>
        {value}
      </span>
    </div>
  );
}
