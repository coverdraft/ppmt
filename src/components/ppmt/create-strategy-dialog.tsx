'use client';

import { useState } from 'react';
import { usePPMTStore } from '@/store/ppmt-strategy-store';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Plus } from 'lucide-react';
import { toast } from 'sonner';

const SYMBOLS = [
  'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
  'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT',
];

const TIMEFRAMES = ['1m', '5m', '1h', '4h', '1d'];
const ASSET_CLASSES = ['blue_chip', 'large_cap', 'defi', 'meme', 'new_launch'];

const ASSET_CLASS_MAP: Record<string, string> = {
  'BTC/USDT': 'blue_chip', 'ETH/USDT': 'blue_chip',
  'SOL/USDT': 'large_cap', 'BNB/USDT': 'large_cap', 'XRP/USDT': 'large_cap',
  'ADA/USDT': 'large_cap', 'AVAX/USDT': 'large_cap', 'DOT/USDT': 'large_cap',
  'DOGE/USDT': 'meme',
  'LINK/USDT': 'defi',
};

export function CreateStrategyDialog() {
  const { createStrategy } = usePPMTStore();
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState('BTC/USDT');
  const [timeframe, setTimeframe] = useState('1h');
  const [capital, setCapital] = useState(10000);
  const [assetClass, setAssetClass] = useState('blue_chip');
  const [advanced, setAdvanced] = useState(false);
  const [alpha, setAlpha] = useState(3);
  const [window_, setWindow_] = useState(7);
  const [patternLen, setPatternLen] = useState(5);

  const handleSymbolChange = (sym: string) => {
    setSymbol(sym);
    const autoClass = ASSET_CLASS_MAP[sym] || 'large_cap';
    setAssetClass(autoClass);
  };

  const handleCreate = async () => {
    const result = await createStrategy({
      symbol,
      timeframe,
      assetClass,
      initialCapital: capital,
      saxAlpha: alpha,
      saxWindow: window_,
      patternLength: patternLen,
    });
    if (result) {
      toast.success(`Strategy ${symbol} @ ${timeframe} created`);
      setOpen(false);
    } else {
      toast.error('Failed to create strategy');
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          className="bg-emerald-600 hover:bg-emerald-500 text-white gap-1.5"
        >
          <Plus className="h-4 w-4" />
          New Strategy
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-zinc-900 border-zinc-800 text-zinc-100 max-w-md">
        <DialogHeader>
          <DialogTitle className="text-zinc-50">Create Strategy</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Symbol */}
          <div>
            <label className="text-xs text-zinc-400 uppercase tracking-wider mb-1.5 block">Symbol</label>
            <div className="grid grid-cols-5 gap-1.5">
              {SYMBOLS.map((sym) => (
                <button
                  key={sym}
                  onClick={() => handleSymbolChange(sym)}
                  className={`text-xs py-1.5 px-2 rounded-lg border transition-colors ${
                    symbol === sym
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {sym.split('/')[0]}
                </button>
              ))}
            </div>
          </div>

          {/* Timeframe */}
          <div>
            <label className="text-xs text-zinc-400 uppercase tracking-wider mb-1.5 block">Timeframe</label>
            <div className="flex gap-2">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={`text-xs py-1.5 px-3 rounded-lg border transition-colors ${
                    timeframe === tf
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          {/* Asset Class */}
          <div>
            <label className="text-xs text-zinc-400 uppercase tracking-wider mb-1.5 block">Asset Class</label>
            <div className="flex gap-2">
              {ASSET_CLASSES.map((ac) => (
                <button
                  key={ac}
                  onClick={() => setAssetClass(ac)}
                  className={`text-xs py-1.5 px-2 rounded-lg border transition-colors capitalize ${
                    assetClass === ac
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {ac.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>

          {/* Capital */}
          <div>
            <label className="text-xs text-zinc-400 uppercase tracking-wider mb-1.5 block">Initial Capital</label>
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value))}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:border-emerald-500/50"
            />
          </div>

          {/* Advanced toggle */}
          <button
            onClick={() => setAdvanced(!advanced)}
            className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            {advanced ? '▾' : '▸'} Advanced settings
          </button>

          {advanced && (
            <div className="space-y-3 border-t border-zinc-800 pt-3">
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="text-[10px] text-zinc-500 block mb-1">SAX Alpha</label>
                  <input
                    type="number"
                    value={alpha}
                    onChange={(e) => setAlpha(Number(e.target.value))}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs text-zinc-100 focus:outline-none focus:border-emerald-500/50"
                    min={2} max={10}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 block mb-1">SAX Window</label>
                  <input
                    type="number"
                    value={window_}
                    onChange={(e) => setWindow_(Number(e.target.value))}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs text-zinc-100 focus:outline-none focus:border-emerald-500/50"
                    min={3} max={20}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 block mb-1">Pattern Len</label>
                  <input
                    type="number"
                    value={patternLen}
                    onChange={(e) => setPatternLen(Number(e.target.value))}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs text-zinc-100 focus:outline-none focus:border-emerald-500/50"
                    min={3} max={10}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Create button */}
          <Button
            onClick={handleCreate}
            className="w-full bg-emerald-600 hover:bg-emerald-500 text-white"
          >
            Create Strategy
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
