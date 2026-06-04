'use client';

import { useState, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Download,
  Upload,
  FileJson,
  FileSpreadsheet,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  FolderDown,
  Settings2,
  History,
  ChevronDown,
  ChevronUp,
  X,
  Copy,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';

// ── Types ──

interface ImportPreview {
  strategies: number;
  sessions: number;
  positions: number;
  trades: number;
  alertRules: number;
  webhookConfigs: number;
  conflicts: number;
}

interface ImportHistoryEntry {
  id: string;
  timestamp: string;
  mode: string;
  imported: {
    strategies: number;
    trades: number;
    rules: number;
    webhooks: number;
  };
  skipped: number;
  errors: string[];
}

interface TradingSystemSummary {
  id: string;
  name: string;
  category: string;
  icon: string;
}

// ── Component ──

export default function ExportImportPanel() {
  // Export state
  const [includeBacktests, setIncludeBacktests] = useState(true);
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<string[]>([]);
  const [tradeFormat, setTradeFormat] = useState<'json' | 'csv'>('json');
  const [exporting, setExporting] = useState<string | null>(null);

  // Import state
  const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge');
  const [importTypes, setImportTypes] = useState<string[]>(['strategies', 'trades', 'config']);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{
    success: boolean;
    imported: Record<string, number>;
    skipped: number;
    errors: string[];
  } | null>(null);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Import history (local state, persisted in session)
  const [importHistory, setImportHistory] = useState<ImportHistoryEntry[]>([]);

  // Expand/collapse sections
  const [expandedSection, setExpandedSection] = useState<string | null>('export');

  // Fetch trading systems for strategy selection
  const { data: systemsData } = useQuery({
    queryKey: ['trading-systems-for-export'],
    queryFn: async () => {
      const res = await fetch('/api/trading-systems');
      if (!res.ok) return [];
      const json = await res.json();
      return (json.data || []) as TradingSystemSummary[];
    },
    staleTime: 30000,
  });

  const systems = systemsData || [];

  // ── Export handlers ──

  const handleExport = useCallback(async (type: string) => {
    setExporting(type);
    try {
      let url = '';
      switch (type) {
        case 'strategies': {
          const params = new URLSearchParams();
          if (selectedStrategyIds.length > 0) {
            params.set('ids', selectedStrategyIds.join(','));
          }
          if (includeBacktests) {
            params.set('includeBacktests', 'true');
          }
          url = `/api/export/strategies?${params.toString()}`;
          break;
        }
        case 'trades':
          url = `/api/export/trades?format=${tradeFormat}`;
          break;
        case 'config':
          url = '/api/export/config';
          break;
        case 'full':
          url = '/api/export/full';
          break;
        default:
          return;
      }

      const res = await fetch(url);
      if (!res.ok) {
        throw new Error('Export failed');
      }

      // Get filename from Content-Disposition header
      const disposition = res.headers.get('Content-Disposition') || '';
      const filenameMatch = disposition.match(/filename="(.+)"/);
      const filename = filenameMatch ? filenameMatch[1] : `cryptoquant-${type}-export.json`;

      const blob = await res.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(downloadUrl);
    } catch (err) {
      console.error('Export error:', err);
    } finally {
      setExporting(null);
    }
  }, [selectedStrategyIds, includeBacktests, tradeFormat]);

  // ── Import handlers ──

  const parseImportFile = useCallback(async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);

      const preview: ImportPreview = {
        strategies: Array.isArray(data.strategies) ? data.strategies.length : 0,
        sessions: Array.isArray(data.sessions) ? data.sessions.length : 0,
        positions: Array.isArray(data.positions) ? data.positions.length : 0,
        trades: Array.isArray(data.trades) ? data.trades.length : 0,
        alertRules: Array.isArray(data.alertRules) ? data.alertRules.length : 0,
        webhookConfigs: Array.isArray(data.webhookConfigs) ? data.webhookConfigs.length : 0,
        conflicts: 0,
      };

      // Detect potential conflicts by checking if export has an ID that may exist
      // (simplified: we count items with IDs)
      const allItems = [
        ...(data.strategies || []),
        ...(data.alertRules || []),
        ...(data.webhookConfigs || []),
      ].filter((item: Record<string, unknown>) => !!item.id);

      preview.conflicts = allItems.length;

      setImportPreview(preview);
      setImportFile(file);
      setImportResult(null);
    } catch {
      setImportPreview(null);
      setImportFile(null);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file && (file.type === 'application/json' || file.name.endsWith('.json'))) {
        parseImportFile(file);
      }
    },
    [parseImportFile],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        parseImportFile(file);
      }
    },
    [parseImportFile],
  );

  const handleImport = useCallback(async () => {
    if (!importFile) return;
    setImporting(true);
    setShowConfirmDialog(false);

    try {
      const formData = new FormData();
      formData.append('file', importFile);

      const typesParam = importTypes.join(',');
      const res = await fetch(`/api/import?mode=${importMode}&types=${typesParam}`, {
        method: 'POST',
        body: formData,
      });

      const result = await res.json();
      setImportResult(result);

      // Add to import history
      const historyEntry: ImportHistoryEntry = {
        id: Date.now().toString(),
        timestamp: new Date().toISOString(),
        mode: importMode,
        imported: {
          strategies: result.imported?.strategies || 0,
          trades: (result.imported?.trades || 0) + (result.imported?.sessions || 0) + (result.imported?.positions || 0),
          rules: result.imported?.rules || 0,
          webhooks: result.imported?.webhooks || 0,
        },
        skipped: result.skipped || 0,
        errors: result.errors || [],
      };
      setImportHistory((prev) => [historyEntry, ...prev].slice(0, 20));
    } catch (err) {
      setImportResult({
        success: false,
        imported: {},
        skipped: 0,
        errors: [err instanceof Error ? err.message : 'Import failed'],
      });
    } finally {
      setImporting(false);
    }
  }, [importFile, importMode, importTypes]);

  const toggleImportType = useCallback((type: string) => {
    setImportTypes((prev) =>
      prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type],
    );
  }, []);

  const toggleStrategySelection = useCallback((id: string) => {
    setSelectedStrategyIds((prev) =>
      prev.includes(id) ? prev.filter((sid) => sid !== id) : [...prev, id],
    );
  }, []);

  // ── Render ──

  return (
    <div className="flex flex-col h-full overflow-y-auto p-3 gap-3">
      {/* ── Export Section ── */}
      <Card className="bg-[#111827] border-[#1e293b]">
        <CardHeader
          className="py-3 px-4 cursor-pointer hover:bg-[#1a2332] transition-colors"
          onClick={() => setExpandedSection(expandedSection === 'export' ? null : 'export')}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Download className="h-4 w-4 text-emerald-400" />
              <CardTitle className="text-sm font-mono text-[#f1f5f9]">Export Data</CardTitle>
            </div>
            {expandedSection === 'export' ? (
              <ChevronUp className="h-4 w-4 text-[#64748b]" />
            ) : (
              <ChevronDown className="h-4 w-4 text-[#64748b]" />
            )}
          </div>
        </CardHeader>

        <AnimatePresence>
          {expandedSection === 'export' && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <CardContent className="px-4 pb-4 pt-0 space-y-3">
                {/* Export Strategies */}
                <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b] space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <FolderDown className="h-3.5 w-3.5 text-amber-400" />
                      <span className="text-xs font-mono text-[#94a3b8]">Strategies</span>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-[10px] font-mono border-[#1e293b] text-emerald-400 hover:bg-emerald-400/10 hover:text-emerald-300"
                      onClick={() => handleExport('strategies')}
                      disabled={exporting !== null}
                    >
                      {exporting === 'strategies' ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Download className="h-3 w-3 mr-1" />
                      )}
                      Export
                    </Button>
                  </div>

                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-2">
                      <Checkbox
                        id="includeBacktests"
                        checked={includeBacktests}
                        onCheckedChange={(checked) => setIncludeBacktests(checked === true)}
                        className="border-[#475569] data-[state=checked]:bg-emerald-500 data-[state=checked]:border-emerald-500"
                      />
                      <label htmlFor="includeBacktests" className="text-[10px] font-mono text-[#94a3b8]">
                        Include backtests
                      </label>
                    </div>
                  </div>

                  {/* Strategy selection */}
                  {systems.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          id="selectAllStrategies"
                          checked={selectedStrategyIds.length === systems.length && systems.length > 0}
                          onCheckedChange={(checked) => {
                            if (checked) {
                              setSelectedStrategyIds(systems.map((s) => s.id));
                            } else {
                              setSelectedStrategyIds([]);
                            }
                          }}
                          className="border-[#475569] data-[state=checked]:bg-emerald-500 data-[state=checked]:border-emerald-500"
                        />
                        <label htmlFor="selectAllStrategies" className="text-[10px] font-mono text-[#64748b]">
                          Select specific ({selectedStrategyIds.length}/{systems.length})
                        </label>
                      </div>
                      <div className="max-h-24 overflow-y-auto space-y-1 pl-5 custom-scrollbar">
                        {systems.map((system) => (
                          <div key={system.id} className="flex items-center gap-2">
                            <Checkbox
                              id={`strat-${system.id}`}
                              checked={selectedStrategyIds.includes(system.id)}
                              onCheckedChange={() => toggleStrategySelection(system.id)}
                              className="border-[#475569] data-[state=checked]:bg-amber-500 data-[state=checked]:border-amber-500 h-3 w-3"
                            />
                            <label
                              htmlFor={`strat-${system.id}`}
                              className="text-[9px] font-mono text-[#94a3b8] truncate cursor-pointer"
                            >
                              {system.icon} {system.name}
                            </label>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Export Trade History */}
                <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b] space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <History className="h-3.5 w-3.5 text-cyan-400" />
                      <span className="text-xs font-mono text-[#94a3b8]">Trade History</span>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-[10px] font-mono border-[#1e293b] text-cyan-400 hover:bg-cyan-400/10 hover:text-cyan-300"
                      onClick={() => handleExport('trades')}
                      disabled={exporting !== null}
                    >
                      {exporting === 'trades' ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Download className="h-3 w-3 mr-1" />
                      )}
                      Export
                    </Button>
                  </div>

                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-[#64748b]">Format:</span>
                    <Select value={tradeFormat} onValueChange={(v) => setTradeFormat(v as 'json' | 'csv')}>
                      <SelectTrigger className="h-6 w-24 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#94a3b8]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-[#111827] border-[#1e293b]">
                        <SelectItem value="json" className="text-[10px] font-mono">
                          <div className="flex items-center gap-1.5">
                            <FileJson className="h-3 w-3" />
                            JSON
                          </div>
                        </SelectItem>
                        <SelectItem value="csv" className="text-[10px] font-mono">
                          <div className="flex items-center gap-1.5">
                            <FileSpreadsheet className="h-3 w-3" />
                            CSV
                          </div>
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* Export Configuration */}
                <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b]">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Settings2 className="h-3.5 w-3.5 text-violet-400" />
                      <span className="text-xs font-mono text-[#94a3b8]">Configuration</span>
                      <Badge variant="outline" className="h-4 text-[8px] font-mono border-[#475569] text-[#64748b]">
                        Alerts + Webhooks
                      </Badge>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-[10px] font-mono border-[#1e293b] text-violet-400 hover:bg-violet-400/10 hover:text-violet-300"
                      onClick={() => handleExport('config')}
                      disabled={exporting !== null}
                    >
                      {exporting === 'config' ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Download className="h-3 w-3 mr-1" />
                      )}
                      Export
                    </Button>
                  </div>
                </div>

                {/* Export Everything */}
                <div className="bg-[#0a0e17] rounded-lg p-3 border border-emerald-500/20 bg-gradient-to-r from-emerald-500/5 to-transparent">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Copy className="h-3.5 w-3.5 text-emerald-400" />
                      <span className="text-xs font-mono text-emerald-300 font-bold">Full Backup</span>
                      <Badge variant="outline" className="h-4 text-[8px] font-mono border-emerald-500/30 text-emerald-400/80">
                        All Data
                      </Badge>
                    </div>
                    <Button
                      size="sm"
                      className="h-7 text-[10px] font-mono bg-emerald-600 hover:bg-emerald-500 text-white"
                      onClick={() => handleExport('full')}
                      disabled={exporting !== null}
                    >
                      {exporting === 'full' ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Download className="h-3 w-3 mr-1" />
                      )}
                      Export Everything
                    </Button>
                  </div>
                </div>
              </CardContent>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>

      {/* ── Import Section ── */}
      <Card className="bg-[#111827] border-[#1e293b]">
        <CardHeader
          className="py-3 px-4 cursor-pointer hover:bg-[#1a2332] transition-colors"
          onClick={() => setExpandedSection(expandedSection === 'import' ? null : 'import')}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Upload className="h-4 w-4 text-amber-400" />
              <CardTitle className="text-sm font-mono text-[#f1f5f9]">Import Data</CardTitle>
            </div>
            {expandedSection === 'import' ? (
              <ChevronUp className="h-4 w-4 text-[#64748b]" />
            ) : (
              <ChevronDown className="h-4 w-4 text-[#64748b]" />
            )}
          </div>
        </CardHeader>

        <AnimatePresence>
          {expandedSection === 'import' && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <CardContent className="px-4 pb-4 pt-0 space-y-3">
                {/* Drag & Drop Zone */}
                <div
                  className={`border-2 border-dashed rounded-lg p-6 text-center transition-all cursor-pointer ${
                    isDragOver
                      ? 'border-amber-400 bg-amber-400/5'
                      : importFile
                        ? 'border-emerald-500/30 bg-emerald-500/5'
                        : 'border-[#1e293b] hover:border-[#475569]'
                  }`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragOver(true);
                  }}
                  onDragLeave={() => setIsDragOver(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".json,application/json"
                    className="hidden"
                    onChange={handleFileSelect}
                  />

                  {importFile ? (
                    <div className="space-y-2">
                      <CheckCircle2 className="h-8 w-8 text-emerald-400 mx-auto" />
                      <p className="text-xs font-mono text-emerald-300">{importFile.name}</p>
                      <p className="text-[10px] font-mono text-[#64748b]">
                        {(importFile.size / 1024).toFixed(1)} KB
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <Upload className="h-8 w-8 text-[#475569] mx-auto" />
                      <p className="text-xs font-mono text-[#94a3b8]">
                        Drop a JSON file here or click to browse
                      </p>
                      <p className="text-[10px] font-mono text-[#475569]">
                        Accepts CryptoQuant export files (.json)
                      </p>
                    </div>
                  )}
                </div>

                {/* Import Preview */}
                {importPreview && (
                  <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b] space-y-2">
                    <div className="flex items-center gap-2 mb-2">
                      <FileJson className="h-3.5 w-3.5 text-amber-400" />
                      <span className="text-xs font-mono text-amber-300 font-bold">Import Preview</span>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      {importPreview.strategies > 0 && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-[#1e293b]">
                          <div className="text-sm font-bold font-mono text-amber-400">{importPreview.strategies}</div>
                          <div className="text-[8px] font-mono text-[#64748b]">STRATEGIES</div>
                        </div>
                      )}
                      {importPreview.trades > 0 && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-[#1e293b]">
                          <div className="text-sm font-bold font-mono text-cyan-400">{importPreview.trades}</div>
                          <div className="text-[8px] font-mono text-[#64748b]">TRADES</div>
                        </div>
                      )}
                      {(importPreview.alertRules > 0 || importPreview.webhookConfigs > 0) && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-[#1e293b]">
                          <div className="text-sm font-bold font-mono text-violet-400">
                            {importPreview.alertRules + importPreview.webhookConfigs}
                          </div>
                          <div className="text-[8px] font-mono text-[#64748b]">RULES/WEBHOOKS</div>
                        </div>
                      )}
                      {importPreview.sessions > 0 && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-[#1e293b]">
                          <div className="text-sm font-bold font-mono text-emerald-400">{importPreview.sessions}</div>
                          <div className="text-[8px] font-mono text-[#64748b]">SESSIONS</div>
                        </div>
                      )}
                      {importPreview.positions > 0 && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-[#1e293b]">
                          <div className="text-sm font-bold font-mono text-rose-400">{importPreview.positions}</div>
                          <div className="text-[8px] font-mono text-[#64748b]">POSITIONS</div>
                        </div>
                      )}
                      {importPreview.conflicts > 0 && (
                        <div className="bg-[#111827] rounded px-2 py-1.5 text-center border border-amber-500/30">
                          <div className="text-sm font-bold font-mono text-amber-400">{importPreview.conflicts}</div>
                          <div className="text-[8px] font-mono text-amber-400/80">CONFLICTS</div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Import Options */}
                <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b] space-y-3">
                  <div className="space-y-2">
                    <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Mode</span>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setImportMode('merge')}
                        className={`flex-1 px-3 py-1.5 rounded text-[10px] font-mono transition-all border ${
                          importMode === 'merge'
                            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                            : 'bg-[#111827] border-[#1e293b] text-[#64748b] hover:text-[#94a3b8]'
                        }`}
                      >
                        Merge
                      </button>
                      <button
                        onClick={() => setImportMode('replace')}
                        className={`flex-1 px-3 py-1.5 rounded text-[10px] font-mono transition-all border ${
                          importMode === 'replace'
                            ? 'bg-red-500/10 border-red-500/30 text-red-300'
                            : 'bg-[#111827] border-[#1e293b] text-[#64748b] hover:text-[#94a3b8]'
                        }`}
                      >
                        Replace
                      </button>
                    </div>
                    <p className="text-[9px] font-mono text-[#475569]">
                      {importMode === 'merge'
                        ? 'Keep existing data, add new records with new IDs'
                        : 'Delete existing data before importing (destructive!)'}
                    </p>
                  </div>

                  <div className="space-y-2">
                    <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Import Types</span>
                    <div className="space-y-1.5">
                      {[
                        { id: 'strategies', label: 'Strategies + Backtests', color: 'text-amber-400' },
                        { id: 'trades', label: 'Trade History + Sessions', color: 'text-cyan-400' },
                        { id: 'config', label: 'Configuration (Rules + Webhooks)', color: 'text-violet-400' },
                      ].map((type) => (
                        <div key={type.id} className="flex items-center gap-2">
                          <Checkbox
                            id={`import-type-${type.id}`}
                            checked={importTypes.includes(type.id)}
                            onCheckedChange={() => toggleImportType(type.id)}
                            className="border-[#475569] data-[state=checked]:bg-emerald-500 data-[state=checked]:border-emerald-500"
                          />
                          <label
                            htmlFor={`import-type-${type.id}`}
                            className={`text-[10px] font-mono ${type.color} cursor-pointer`}
                          >
                            {type.label}
                          </label>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Import Button */}
                <Button
                  className="w-full h-8 text-[11px] font-mono bg-amber-600 hover:bg-amber-500 text-white"
                  disabled={!importFile || importTypes.length === 0 || importing}
                  onClick={() => setShowConfirmDialog(true)}
                >
                  {importing ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
                      Importing...
                    </>
                  ) : (
                    <>
                      <Upload className="h-3.5 w-3.5 mr-2" />
                      Import Data
                    </>
                  )}
                </Button>

                {/* Import Result */}
                {importResult && (
                  <motion.div
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`rounded-lg p-3 border ${
                      importResult.success
                        ? 'bg-emerald-500/5 border-emerald-500/20'
                        : 'bg-red-500/5 border-red-500/20'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      {importResult.success ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                      ) : (
                        <XCircle className="h-4 w-4 text-red-400" />
                      )}
                      <span className={`text-xs font-mono font-bold ${importResult.success ? 'text-emerald-300' : 'text-red-300'}`}>
                        {importResult.success ? 'Import Complete' : 'Import Failed'}
                      </span>
                    </div>

                    {importResult.success && (
                      <div className="grid grid-cols-2 gap-1.5">
                        {(importResult.imported?.strategies ?? 0) > 0 && (
                          <div className="text-[10px] font-mono text-[#94a3b8]">
                            <span className="text-amber-400 font-bold">{importResult.imported.strategies}</span> strategies
                          </div>
                        )}
                        {((importResult.imported?.trades ?? 0) + (importResult.imported?.sessions ?? 0) + (importResult.imported?.positions ?? 0)) > 0 && (
                          <div className="text-[10px] font-mono text-[#94a3b8]">
                            <span className="text-cyan-400 font-bold">{(importResult.imported.trades || 0) + (importResult.imported.sessions || 0) + (importResult.imported.positions || 0)}</span> trades/sessions
                          </div>
                        )}
                        {(importResult.imported?.rules ?? 0) > 0 && (
                          <div className="text-[10px] font-mono text-[#94a3b8]">
                            <span className="text-violet-400 font-bold">{importResult.imported.rules}</span> rules
                          </div>
                        )}
                        {(importResult.imported?.webhooks ?? 0) > 0 && (
                          <div className="text-[10px] font-mono text-[#94a3b8]">
                            <span className="text-violet-400 font-bold">{importResult.imported.webhooks}</span> webhooks
                          </div>
                        )}
                        {importResult.skipped > 0 && (
                          <div className="text-[10px] font-mono text-[#94a3b8]">
                            <span className="text-amber-400 font-bold">{importResult.skipped}</span> skipped
                          </div>
                        )}
                      </div>
                    )}

                    {importResult.errors.length > 0 && (
                      <div className="mt-2 space-y-0.5 max-h-20 overflow-y-auto custom-scrollbar">
                        {importResult.errors.map((err, i) => (
                          <div key={i} className="text-[9px] font-mono text-red-400/80 flex items-start gap-1">
                            <AlertTriangle className="h-2.5 w-2.5 shrink-0 mt-0.5" />
                            {err}
                          </div>
                        ))}
                      </div>
                    )}

                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-2 h-5 text-[9px] font-mono text-[#64748b] hover:text-[#94a3b8]"
                      onClick={() => setImportResult(null)}
                    >
                      <X className="h-2.5 w-2.5 mr-1" />
                      Dismiss
                    </Button>
                  </motion.div>
                )}
              </CardContent>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>

      {/* ── Import History ── */}
      <Card className="bg-[#111827] border-[#1e293b]">
        <CardHeader
          className="py-3 px-4 cursor-pointer hover:bg-[#1a2332] transition-colors"
          onClick={() => setExpandedSection(expandedSection === 'history' ? null : 'history')}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-[#64748b]" />
              <CardTitle className="text-sm font-mono text-[#f1f5f9]">Import History</CardTitle>
              {importHistory.length > 0 && (
                <Badge variant="outline" className="h-4 text-[8px] font-mono border-[#475569] text-[#64748b]">
                  {importHistory.length}
                </Badge>
              )}
            </div>
            {expandedSection === 'history' ? (
              <ChevronUp className="h-4 w-4 text-[#64748b]" />
            ) : (
              <ChevronDown className="h-4 w-4 text-[#64748b]" />
            )}
          </div>
        </CardHeader>

        <AnimatePresence>
          {expandedSection === 'history' && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <CardContent className="px-4 pb-4 pt-0">
                {importHistory.length === 0 ? (
                  <div className="text-center py-6">
                    <History className="h-6 w-6 text-[#475569] mx-auto mb-2" />
                    <p className="text-[10px] font-mono text-[#475569]">No imports yet</p>
                  </div>
                ) : (
                  <div className="max-h-48 overflow-y-auto space-y-2 custom-scrollbar">
                    {importHistory.map((entry) => (
                      <div
                        key={entry.id}
                        className="bg-[#0a0e17] rounded-lg p-2.5 border border-[#1e293b]"
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[9px] font-mono text-[#64748b]">
                            {new Date(entry.timestamp).toLocaleString()}
                          </span>
                          <Badge
                            variant="outline"
                            className={`h-3.5 text-[8px] font-mono ${
                              entry.mode === 'replace'
                                ? 'border-red-500/30 text-red-400'
                                : 'border-emerald-500/30 text-emerald-400'
                            }`}
                          >
                            {entry.mode.toUpperCase()}
                          </Badge>
                        </div>
                        <div className="flex gap-3">
                          {entry.imported.strategies > 0 && (
                            <span className="text-[9px] font-mono text-amber-400">
                              {entry.imported.strategies} strategies
                            </span>
                          )}
                          {entry.imported.trades > 0 && (
                            <span className="text-[9px] font-mono text-cyan-400">
                              {entry.imported.trades} trades
                            </span>
                          )}
                          {entry.imported.rules > 0 && (
                            <span className="text-[9px] font-mono text-violet-400">
                              {entry.imported.rules} rules
                            </span>
                          )}
                          {entry.skipped > 0 && (
                            <span className="text-[9px] font-mono text-amber-400/60">
                              {entry.skipped} skipped
                            </span>
                          )}
                        </div>
                        {entry.errors.length > 0 && (
                          <div className="mt-1">
                            <span className="text-[8px] font-mono text-red-400/60">
                              {entry.errors.length} error(s)
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>

      {/* ── Confirmation Dialog ── */}
      <Dialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <DialogContent className="bg-[#111827] border-[#1e293b] text-[#f1f5f9]">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm flex items-center gap-2">
              <AlertTriangle className={`h-4 w-4 ${importMode === 'replace' ? 'text-red-400' : 'text-amber-400'}`} />
              Confirm Import
            </DialogTitle>
            <DialogDescription className="text-[#94a3b8] text-xs font-mono">
              {importMode === 'replace'
                ? 'This will DELETE all existing data before importing. This action cannot be undone.'
                : 'This will merge the imported data with your existing data. Conflicting records will be skipped.'}
            </DialogDescription>
          </DialogHeader>

          {importPreview && (
            <div className="bg-[#0a0e17] rounded-lg p-3 border border-[#1e293b] space-y-1.5">
              <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Will import:</span>
              {importPreview.strategies > 0 && (
                <div className="text-[10px] font-mono text-[#94a3b8]">
                  <span className="text-amber-400">{importPreview.strategies}</span> strategies
                </div>
              )}
              {importPreview.trades > 0 && (
                <div className="text-[10px] font-mono text-[#94a3b8]">
                  <span className="text-cyan-400">{importPreview.trades}</span> trades
                </div>
              )}
              {(importPreview.alertRules + importPreview.webhookConfigs) > 0 && (
                <div className="text-[10px] font-mono text-[#94a3b8]">
                  <span className="text-violet-400">{importPreview.alertRules + importPreview.webhookConfigs}</span> config items
                </div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button
              variant="ghost"
              size="sm"
              className="text-[#94a3b8] font-mono text-xs"
              onClick={() => setShowConfirmDialog(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              className={`font-mono text-xs ${
                importMode === 'replace'
                  ? 'bg-red-600 hover:bg-red-500 text-white'
                  : 'bg-amber-600 hover:bg-amber-500 text-white'
              }`}
              onClick={handleImport}
              disabled={importing}
            >
              {importing ? (
                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              ) : null}
              {importMode === 'replace' ? 'Replace & Import' : 'Merge & Import'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
