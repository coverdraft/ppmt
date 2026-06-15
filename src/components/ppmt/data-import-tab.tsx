'use client';

import { useState } from 'react';
import { Download, Upload, FileSpreadsheet, AlertCircle, CheckCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

interface ImportResult {
  symbol: string;
  rows: number;
  status: 'success' | 'error';
  message: string;
}

export function DataImportTab() {
  const [csvPath, setCsvPath] = useState('');
  const [symbol, setSymbol] = useState('BTC/USDT');
  const [timeframe, setTimeframe] = useState('1m');
  const [format, setFormat] = useState('auto');
  const [importing, setImporting] = useState(false);
  const [results, setResults] = useState<ImportResult[]>([]);

  const handleImport = async () => {
    if (!csvPath.trim()) {
      toast.error('Enter a CSV file path');
      return;
    }
    setImporting(true);
    try {
      const res = await fetch('/api/ppmt/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv_path: csvPath, symbol, timeframe, format }),
      });
      const json = await res.json();
      if (json.success) {
        toast.success(`Imported ${symbol} data`);
        setResults((prev) => [...prev, {
          symbol,
          rows: json.data?.candles || 0,
          status: 'success',
          message: `Imported ${json.data?.candles || 0} candles`,
        }]);
      } else {
        toast.error(json.error || 'Import failed');
        setResults((prev) => [...prev, {
          symbol,
          rows: 0,
          status: 'error',
          message: json.error || 'Import failed',
        }]);
      }
    } catch (err) {
      toast.error('Import request failed');
      setResults((prev) => [...prev, {
        symbol,
        rows: 0,
        status: 'error',
        message: 'Request failed — is the PPMT engine running?',
      }]);
    } finally {
      setImporting(false);
    }
  };

  const handleBulkIngest = async () => {
    setImporting(true);
    try {
      const res = await fetch('/api/ppmt/bulk-ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days: 365 }),
      });
      const json = await res.json();
      if (json.success) {
        toast.success('Bulk ingest started');
      } else {
        toast.error(json.error || 'Bulk ingest failed');
      }
    } catch {
      toast.error('Bulk ingest request failed');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Download className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-bold text-zinc-50">Data Import</h2>
      </div>

      {/* CSV Import */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Import CSV File</h3>
        <p className="text-xs text-zinc-500 mb-4">
          Import 1-minute historical OHLCV data from CSV files.
          Supports Binance, CryptoDataDownload, and generic CSV formats.
        </p>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">CSV File Path</label>
            <input
              type="text"
              value={csvPath}
              onChange={(e) => setCsvPath(e.target.value)}
              placeholder="/path/to/BTCUSDT_1m.csv"
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-zinc-400 mb-1 block">Symbol</label>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="text-xs text-zinc-400 mb-1 block">Timeframe</label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
              >
                <option value="1m">1m</option>
                <option value="5m">5m</option>
                <option value="1h">1h</option>
                <option value="4h">4h</option>
                <option value="1d">1d</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-zinc-400 mb-1 block">Format</label>
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
              >
                <option value="auto">Auto-detect</option>
                <option value="binance">Binance</option>
                <option value="cdd">CryptoDataDownload</option>
                <option value="generic">Generic</option>
              </select>
            </div>
          </div>
          <Button
            onClick={handleImport}
            disabled={importing}
            className="bg-emerald-600 hover:bg-emerald-500 text-white gap-1.5"
          >
            <Upload className="h-4 w-4" />
            {importing ? 'Importing...' : 'Import CSV'}
          </Button>
        </div>
      </div>

      {/* Bulk Ingest */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Bulk Data Download</h3>
        <p className="text-xs text-zinc-500 mb-3">
          Download historical data from exchange APIs for all tracked tokens.
          Uses Bybit as primary source with Binance/OKX fallback.
        </p>
        <Button
          onClick={handleBulkIngest}
          disabled={importing}
          variant="outline"
          className="border-zinc-700 text-zinc-300 hover:bg-zinc-800 gap-1.5"
        >
          <FileSpreadsheet className="h-4 w-4" />
          Start Bulk Ingest (365 days)
        </Button>
      </div>

      {/* Import history */}
      {results.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <h3 className="text-sm font-medium text-zinc-300 mb-3">Import History</h3>
          <div className="space-y-2">
            {results.map((r, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                {r.status === 'success' ? (
                  <CheckCircle className="h-3 w-3 text-emerald-400" />
                ) : (
                  <AlertCircle className="h-3 w-3 text-rose-400" />
                )}
                <span className="text-zinc-200">{r.symbol}</span>
                <span className="text-zinc-500">—</span>
                <span className={r.status === 'success' ? 'text-zinc-400' : 'text-rose-400'}>{r.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Supported formats */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Supported CSV Formats</h3>
        <div className="space-y-3">
          <FormatCard name="Binance" desc="open_time, open, high, low, close, volume, close_time, ..." example="BTCUSDT_1m.csv" />
          <FormatCard name="CryptoDataDownload" desc="Unix, Date, Symbol, Open, High, Low, Close, Volume USDT" example="Binance_BTCUSDT_1h.csv" />
          <FormatCard name="Generic" desc="timestamp/datetime/date, open, high, low, close, volume" example="any_ohlcv.csv" />
        </div>
      </div>
    </div>
  );
}

function FormatCard({ name, desc, example }: { name: string; desc: string; example: string }) {
  return (
    <div className="flex items-start gap-3 bg-zinc-800/30 rounded-lg p-3">
      <FileSpreadsheet className="h-4 w-4 text-zinc-500 mt-0.5 flex-shrink-0" />
      <div>
        <div className="text-xs font-medium text-zinc-200">{name}</div>
        <div className="text-[10px] text-zinc-500 mt-0.5">{desc}</div>
        <div className="text-[10px] text-zinc-600 font-mono mt-0.5">{example}</div>
      </div>
    </div>
  );
}
