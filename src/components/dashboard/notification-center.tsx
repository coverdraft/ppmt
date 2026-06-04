'use client';

import { useCryptoStore, type AlertSummary } from '@/store/crypto-store';
import { useEffect, useState, useCallback, useRef } from 'react';
import { Bell, Check, X, AlertTriangle, Info, AlertCircle, Filter, Settings, Plus, Trash2, ExternalLink } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { motion, AnimatePresence } from 'framer-motion';

// ============================================================
// HELPERS
// ============================================================

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function severityIcon(severity: string) {
  switch (severity) {
    case 'CRITICAL':
      return <AlertCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />;
    case 'WARNING':
      return <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0" />;
    default:
      return <Info className="h-3.5 w-3.5 text-cyan-400 shrink-0" />;
  }
}

function severityColor(severity: string) {
  switch (severity) {
    case 'CRITICAL': return 'border-l-red-500 bg-red-500/5';
    case 'WARNING': return 'border-l-amber-500 bg-amber-500/5';
    default: return 'border-l-cyan-500 bg-cyan-500/5';
  }
}

function categoryLabel(cat: string) {
  const colors: Record<string, string> = {
    PRICE: 'text-emerald-400 bg-emerald-500/10',
    SIGNAL: 'text-purple-400 bg-purple-500/10',
    STRATEGY: 'text-amber-400 bg-amber-500/10',
    RISK: 'text-red-400 bg-red-500/10',
    SMART_MONEY: 'text-cyan-400 bg-cyan-500/10',
    SYSTEM: 'text-gray-400 bg-gray-500/10',
  };
  return colors[cat] || 'text-gray-400 bg-gray-500/10';
}

// ============================================================
// NOTIFICATION CENTER
// ============================================================

export function NotificationCenter() {
  const alerts = useCryptoStore((s) => s.alerts);
  const unreadAlertCount = useCryptoStore((s) => s.unreadAlertCount);
  const addAlert = useCryptoStore((s) => s.addAlert);
  const markAlertRead = useCryptoStore((s) => s.markAlertRead);
  const clearAlerts = useCryptoStore((s) => s.clearAlerts);
  const setActiveTab = useCryptoStore((s) => s.setActiveTab);
  const [open, setOpen] = useState(false);
  const [filterCategory, setFilterCategory] = useState<string>('ALL');
  const [filterSeverity, setFilterSeverity] = useState<string>('ALL');
  const [showRead, setShowRead] = useState(true);
  const [activeTab, setActiveTabLocal] = useState<'alerts' | 'rules' | 'webhooks'>('alerts');
  const panelRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);
  const { toast } = useToast();

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await fetch('/api/alerts?limit=50');
      if (res.ok) {
        const data = await res.json();
        if (data.data) {
          clearAlerts();
          for (const alert of data.data) {
            addAlert({
              id: alert.id,
              title: alert.title,
              message: alert.message,
              category: alert.category,
              severity: alert.severity,
              isRead: alert.isRead,
              createdAt: alert.createdAt,
              metadata: alert.metadata ? JSON.parse(alert.metadata) : undefined,
              linkTo: alert.linkTo || undefined,
            });
          }
        }
      }
    } catch {
      // Silently fail
    }
  }, [addAlert, clearAlerts]);

  const handleMarkRead = useCallback(async (id: string) => {
    markAlertRead(id);
    try {
      await fetch(`/api/alerts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ isRead: true }),
      });
    } catch {}
  }, [markAlertRead]);

  const handleDismiss = useCallback(async (id: string) => {
    try {
      await fetch(`/api/alerts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ isDismissed: true }),
      });
      // Remove from local store
      const updated = alerts.filter(a => a.id !== id);
      clearAlerts();
      for (const a of updated) addAlert(a);
    } catch {}
  }, [alerts, addAlert, clearAlerts]);

  const handleMarkAllRead = useCallback(async () => {
    const unread = alerts.filter(a => !a.isRead);
    for (const alert of unread) {
      markAlertRead(alert.id);
    }
    try {
      await Promise.all(
        unread.map(a =>
          fetch(`/api/alerts/${a.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ isRead: true }),
          })
        )
      );
    } catch {}
  }, [alerts, markAlertRead]);

  const handleAlertClick = useCallback((alert: AlertSummary) => {
    if (!alert.isRead) handleMarkRead(alert.id);
    if (alert.linkTo) {
      const [tab] = alert.linkTo.split(':');
      setActiveTab(tab as any);
      setOpen(false);
      toast({ title: alert.title, description: alert.message });
    }
  }, [handleMarkRead, setActiveTab, toast]);

  // Fetch alerts from API on mount
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        open &&
        panelRef.current &&
        bellRef.current &&
        !panelRef.current.contains(e.target as Node) &&
        !bellRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  // Filter alerts
  const filteredAlerts = alerts.filter(a => {
    if (filterCategory !== 'ALL' && a.category !== filterCategory) return false;
    if (filterSeverity !== 'ALL' && a.severity !== filterSeverity) return false;
    if (!showRead && a.isRead) return false;
    return true;
  });

  const categories = ['ALL', 'PRICE', 'SIGNAL', 'STRATEGY', 'RISK', 'SMART_MONEY', 'SYSTEM'];
  const severities = ['ALL', 'INFO', 'WARNING', 'CRITICAL'];

  return (
    <div className="relative">
      {/* Bell Button */}
      <button
        ref={bellRef}
        onClick={() => setOpen(!open)}
        className="relative flex items-center justify-center w-7 h-7 rounded-md hover:bg-[#1e293b] transition-colors"
        title="Notifications"
      >
        <Bell className="h-3.5 w-3.5 text-[#94a3b8]" />
        {unreadAlertCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[14px] h-[14px] px-0.5 text-[8px] font-bold font-mono text-white bg-red-500 rounded-full">
            {unreadAlertCount > 9 ? '9+' : unreadAlertCount}
          </span>
        )}
      </button>

      {/* Dropdown Panel */}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panelRef}
            initial={{ opacity: 0, y: -8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-9 w-[380px] sm:w-[440px] bg-[#0d1117] border border-[#1e293b] rounded-lg shadow-2xl z-50 overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-[#1e293b] bg-[#080b12]">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono font-bold text-[#f1f5f9]">NOTIFICATIONS</span>
                {unreadAlertCount > 0 && (
                  <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold text-red-400 bg-red-500/10 rounded">
                    {unreadAlertCount} new
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                {unreadAlertCount > 0 && (
                  <button
                    onClick={handleMarkAllRead}
                    className="flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-mono text-cyan-400 hover:bg-cyan-500/10 rounded transition-colors"
                  >
                    <Check className="h-2.5 w-2.5" />
                    Mark all read
                  </button>
                )}
                <button
                  onClick={() => setOpen(false)}
                  className="p-0.5 text-[#64748b] hover:text-[#94a3b8] transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-[#1e293b]">
              {(['alerts', 'rules', 'webhooks'] as const).map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTabLocal(tab)}
                  className={`flex-1 px-2 py-1.5 text-[9px] font-mono uppercase tracking-wider transition-colors ${
                    activeTab === tab
                      ? 'text-[#3b82f6] border-b-2 border-[#3b82f6] bg-[#3b82f6]/5'
                      : 'text-[#64748b] hover:text-[#94a3b8]'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            {/* Alerts Tab */}
            {activeTab === 'alerts' && (
              <div className="max-h-[400px] overflow-y-auto">
                {/* Filters */}
                <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[#1e293b] bg-[#080b12]/50">
                  <Filter className="h-2.5 w-2.5 text-[#64748b] shrink-0" />
                  <select
                    value={filterCategory}
                    onChange={e => setFilterCategory(e.target.value)}
                    className="h-5 px-1 text-[9px] font-mono bg-[#1a1f2e] border border-[#1e293b] rounded text-[#94a3b8] outline-none"
                  >
                    {categories.map(c => (
                      <option key={c} value={c}>{c === 'ALL' ? 'All Categories' : c}</option>
                    ))}
                  </select>
                  <select
                    value={filterSeverity}
                    onChange={e => setFilterSeverity(e.target.value)}
                    className="h-5 px-1 text-[9px] font-mono bg-[#1a1f2e] border border-[#1e293b] rounded text-[#94a3b8] outline-none"
                  >
                    {severities.map(s => (
                      <option key={s} value={s}>{s === 'ALL' ? 'All Severity' : s}</option>
                    ))}
                  </select>
                  <label className="flex items-center gap-0.5 text-[9px] font-mono text-[#64748b] ml-auto">
                    <input
                      type="checkbox"
                      checked={showRead}
                      onChange={e => setShowRead(e.target.checked)}
                      className="h-3 w-3 accent-[#3b82f6]"
                    />
                    Read
                  </label>
                </div>

                {/* Alert List */}
                {filteredAlerts.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-8 text-[#64748b]">
                    <Bell className="h-6 w-6 mb-1.5 opacity-30" />
                    <span className="text-[10px] font-mono">No notifications</span>
                  </div>
                ) : (
                  <div className="divide-y divide-[#1e293b]/50">
                    {filteredAlerts.map(alert => (
                      <div
                        key={alert.id}
                        className={`border-l-2 px-2.5 py-2 cursor-pointer hover:bg-[#1e293b]/30 transition-colors ${
                          severityColor(alert.severity)
                        } ${!alert.isRead ? '' : 'opacity-60'}`}
                        onClick={() => handleAlertClick(alert)}
                      >
                        <div className="flex items-start gap-1.5">
                          {severityIcon(alert.severity)}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1 mb-0.5">
                              <span className="text-[10px] font-mono font-bold text-[#f1f5f9] truncate">
                                {alert.title}
                              </span>
                              {!alert.isRead && (
                                <span className="w-1.5 h-1.5 rounded-full bg-[#3b82f6] shrink-0" />
                              )}
                            </div>
                            <p className="text-[9px] font-mono text-[#94a3b8] line-clamp-2 mb-1">
                              {alert.message}
                            </p>
                            <div className="flex items-center gap-1.5">
                              <span className={`px-1 py-0.5 text-[8px] font-mono rounded ${categoryLabel(alert.category)}`}>
                                {alert.category}
                              </span>
                              <span className="text-[8px] font-mono text-[#475569]">
                                {timeAgo(alert.createdAt)}
                              </span>
                              <div className="ml-auto flex items-center gap-0.5">
                                {!alert.isRead && (
                                  <button
                                    onClick={e => { e.stopPropagation(); handleMarkRead(alert.id); }}
                                    className="p-0.5 text-[#64748b] hover:text-cyan-400 transition-colors"
                                    title="Mark as read"
                                  >
                                    <Check className="h-2.5 w-2.5" />
                                  </button>
                                )}
                                <button
                                  onClick={e => { e.stopPropagation(); handleDismiss(alert.id); }}
                                  className="p-0.5 text-[#64748b] hover:text-red-400 transition-colors"
                                  title="Dismiss"
                                >
                                  <X className="h-2.5 w-2.5" />
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Rules Tab */}
            {activeTab === 'rules' && <RulesPanel />}

            {/* Webhooks Tab */}
            {activeTab === 'webhooks' && <WebhooksPanel />}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ============================================================
// RULES PANEL
// ============================================================

function RulesPanel() {
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchRules = useCallback(async () => {
    try {
      const res = await fetch('/api/alerts/rules');
      if (res.ok) {
        const data = await res.json();
        setRules(data.data || []);
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchRules();
  }, [fetchRules]);

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/alerts/rules/${id}`, { method: 'DELETE' });
      setRules(prev => prev.filter(r => r.id !== id));
    } catch {}
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/alerts/rules/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !enabled }),
      });
      setRules(prev => prev.map(r => r.id === id ? { ...r, enabled: !enabled } : r));
    } catch {}
  };

  const handleCreate = async () => {
    try {
      const res = await fetch('/api/alerts/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: 'New Rule',
          category: 'STRATEGY',
          condition: { type: 'category_match', category: 'STRATEGY' },
          severity: 'INFO',
          channels: ['IN_APP'],
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setRules(prev => [data.data, ...prev]);
      }
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-[#64748b]">
        <span className="text-[10px] font-mono">Loading rules...</span>
      </div>
    );
  }

  return (
    <div className="max-h-[400px] overflow-y-auto">
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-[#1e293b]">
        <span className="text-[9px] font-mono text-[#64748b]">{rules.length} rules</span>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-mono text-[#3b82f6] hover:bg-[#3b82f6]/10 rounded transition-colors"
        >
          <Plus className="h-2.5 w-2.5" /> Add Rule
        </button>
      </div>

      {rules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-[#64748b]">
          <Settings className="h-5 w-5 mb-1.5 opacity-30" />
          <span className="text-[10px] font-mono">No alert rules configured</span>
        </div>
      ) : (
        <div className="divide-y divide-[#1e293b]/50">
          {rules.map(rule => (
            <div key={rule.id} className="px-2.5 py-2 hover:bg-[#1e293b]/20 transition-colors">
              <div className="flex items-center gap-1.5 mb-0.5">
                <button
                  onClick={() => handleToggle(rule.id, rule.enabled)}
                  className={`w-6 h-3 rounded-full transition-colors ${rule.enabled ? 'bg-emerald-500' : 'bg-[#1e293b]'}`}
                >
                  <div className={`w-2 h-2 rounded-full bg-white transition-transform ${rule.enabled ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
                </button>
                <span className="text-[10px] font-mono font-bold text-[#f1f5f9] truncate">{rule.name}</span>
                <span className={`px-1 py-0.5 text-[8px] font-mono rounded ${categoryLabel(rule.category)}`}>
                  {rule.category}
                </span>
                <button
                  onClick={() => handleDelete(rule.id)}
                  className="p-0.5 text-[#64748b] hover:text-red-400 transition-colors ml-auto"
                >
                  <Trash2 className="h-2.5 w-2.5" />
                </button>
              </div>
              <div className="flex items-center gap-2 text-[8px] font-mono text-[#475569]">
                <span>Severity: {rule.severity}</span>
                <span>Cooldown: {rule.cooldownMin}m</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// WEBHOOKS PANEL
// ============================================================

function WebhooksPanel() {
  const [webhooks, setWebhooks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchWebhooks = useCallback(async () => {
    try {
      const res = await fetch('/api/webhooks');
      if (res.ok) {
        const data = await res.json();
        setWebhooks(data.data || []);
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchWebhooks();
  }, [fetchWebhooks]);

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/webhooks/${id}`, { method: 'DELETE' });
      setWebhooks(prev => prev.filter(w => w.id !== id));
    } catch {}
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/webhooks/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !enabled }),
      });
      setWebhooks(prev => prev.map(w => w.id === id ? { ...w, enabled: !enabled } : w));
    } catch {}
  };

  const handleCreate = async () => {
    try {
      const res = await fetch('/api/webhooks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: 'New Webhook',
          url: 'https://example.com/webhook',
          events: [],
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setWebhooks(prev => [data.data, ...prev]);
      }
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-[#64748b]">
        <span className="text-[10px] font-mono">Loading webhooks...</span>
      </div>
    );
  }

  return (
    <div className="max-h-[400px] overflow-y-auto">
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-[#1e293b]">
        <span className="text-[9px] font-mono text-[#64748b]">{webhooks.length} webhooks</span>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-mono text-[#3b82f6] hover:bg-[#3b82f6]/10 rounded transition-colors"
        >
          <Plus className="h-2.5 w-2.5" /> Add Webhook
        </button>
      </div>

      {webhooks.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-[#64748b]">
          <ExternalLink className="h-5 w-5 mb-1.5 opacity-30" />
          <span className="text-[10px] font-mono">No webhooks configured</span>
        </div>
      ) : (
        <div className="divide-y divide-[#1e293b]/50">
          {webhooks.map(wh => (
            <div key={wh.id} className="px-2.5 py-2 hover:bg-[#1e293b]/20 transition-colors">
              <div className="flex items-center gap-1.5 mb-0.5">
                <button
                  onClick={() => handleToggle(wh.id, wh.enabled)}
                  className={`w-6 h-3 rounded-full transition-colors ${wh.enabled ? 'bg-emerald-500' : 'bg-[#1e293b]'}`}
                >
                  <div className={`w-2 h-2 rounded-full bg-white transition-transform ${wh.enabled ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
                </button>
                <span className="text-[10px] font-mono font-bold text-[#f1f5f9] truncate">{wh.name}</span>
                {wh.lastStatus && (
                  <span className={`px-1 py-0.5 text-[8px] font-mono rounded ${
                    wh.lastStatus === 'SUCCESS' ? 'text-emerald-400 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'
                  }`}>
                    {wh.lastStatus}
                  </span>
                )}
                <button
                  onClick={() => handleDelete(wh.id)}
                  className="p-0.5 text-[#64748b] hover:text-red-400 transition-colors ml-auto"
                >
                  <Trash2 className="h-2.5 w-2.5" />
                </button>
              </div>
              <p className="text-[8px] font-mono text-[#475569] truncate">{wh.url}</p>
              {wh.failureCount > 0 && (
                <span className="text-[8px] font-mono text-red-400">{wh.failureCount} failures</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
