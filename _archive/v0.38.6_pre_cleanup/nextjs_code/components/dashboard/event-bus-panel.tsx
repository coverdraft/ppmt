'use client';

import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { useCryptoStore, type EventBusEvent } from '@/store/crypto-store';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuCheckboxItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover';
import {
  Radio,
  Activity,
  Filter,
  Pause,
  Play,
  Search,
  ChevronDown,
  Wifi,
  WifiOff,
  Trash2,
  ArrowDown,
  Zap,
  Clock,
  TrendingUp,
  AlertTriangle,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// ============================================================
// EVENT TYPE CONFIG — colors & labels
// ============================================================

const EVENT_TYPE_CONFIG: Record<string, { bg: string; text: string; border: string; label: string }> = {
  KILL_SWITCH_TRIGGER: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    border: 'border-red-500/30',
    label: 'KILL_SWITCH_TRIGGER',
  },
  KILL_SWITCH_RELEASE: {
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    border: 'border-emerald-500/30',
    label: 'KILL_SWITCH_RELEASE',
  },
  POSITION_OPENED: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    border: 'border-blue-500/30',
    label: 'POSITION_OPENED',
  },
  POSITION_CLOSED: {
    bg: 'bg-purple-500/15',
    text: 'text-purple-400',
    border: 'border-purple-500/30',
    label: 'POSITION_CLOSED',
  },
  SIGNAL_GENERATED: {
    bg: 'bg-yellow-500/15',
    text: 'text-yellow-400',
    border: 'border-yellow-500/30',
    label: 'SIGNAL_GENERATED',
  },
  REGIME_CHANGE: {
    bg: 'bg-orange-500/15',
    text: 'text-orange-400',
    border: 'border-orange-500/30',
    label: 'REGIME_CHANGE',
  },
  RISK_ALERT: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    border: 'border-red-500/30',
    label: 'RISK_ALERT',
  },
  PRICE_ANOMALY: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    border: 'border-amber-500/30',
    label: 'PRICE_ANOMALY',
  },
  WHALE_MOVEMENT: {
    bg: 'bg-cyan-500/15',
    text: 'text-cyan-400',
    border: 'border-cyan-500/30',
    label: 'WHALE_MOVEMENT',
  },
  STRATEGY_STATE_CHANGE: {
    bg: 'bg-teal-500/15',
    text: 'text-teal-400',
    border: 'border-teal-500/30',
    label: 'STRATEGY_STATE_CHANGE',
  },
  DAILY_VAR_BREACH: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    border: 'border-red-500/30',
    label: 'DAILY_VAR_BREACH',
  },
  CORRELATION_BREAK: {
    bg: 'bg-pink-500/15',
    text: 'text-pink-400',
    border: 'border-pink-500/30',
    label: 'CORRELATION_BREAK',
  },
  FEEDBACK_RECORDED: {
    bg: 'bg-slate-500/15',
    text: 'text-slate-400',
    border: 'border-slate-500/30',
    label: 'FEEDBACK_RECORDED',
  },
  OPEN_POSITION: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    border: 'border-blue-500/30',
    label: 'OPEN_POSITION',
  },
};

const DEFAULT_EVENT_CONFIG = {
  bg: 'bg-gray-500/15',
  text: 'text-gray-400',
  border: 'border-gray-500/30',
  label: 'UNKNOWN',
};

const ALL_EVENT_TYPES = Object.keys(EVENT_TYPE_CONFIG);

// Priority border styles
const PRIORITY_BORDER: Record<string, string> = {
  SYNC: 'border-l-2 border-l-red-500',
  SEMI_SYNC: 'border-l-2 border-l-yellow-500',
  ASYNC: 'border-l-2 border-l-transparent',
};

// ============================================================
// HELPERS
// ============================================================

function formatTimestamp(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const mmm = String(d.getMilliseconds()).padStart(3, '0');
  return `${hh}:${mm}:${ss}.${mmm}`;
}

function truncatePayload(payload: string, maxLen = 80): string {
  if (!payload) return '';
  return payload.length > maxLen ? payload.slice(0, maxLen) + '...' : payload;
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 1000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  return `${Math.floor(diff / 3600000)}h ago`;
}

// ============================================================
// SIMULATED EVENTS — generate realistic events for demo
// ============================================================

const SIMULATED_SOURCES = [
  'kill-switch-service',
  'paper-trading-engine',
  'signal-generator',
  'regime-engine',
  'risk-alert-engine',
  'brain-orchestrator',
  'portfolio-intelligence',
  'market-monitor',
  'whale-tracker',
  'strategy-state-mgr',
];

const SIMULATED_EVENT_TYPES: Array<{
  type: string;
  priority: 'SYNC' | 'SEMI_SYNC' | 'ASYNC';
  payloadTemplates: string[];
}> = [
  {
    type: 'KILL_SWITCH_TRIGGER',
    priority: 'SYNC',
    payloadTemplates: [
      '{"level":"PORTFOLIO","reason":"Max drawdown exceeded 20%","action":"PAUSE_ALL"}',
      '{"level":"STRATEGY","reason":"Strategy drawdown breach 30%","action":"PAUSE_STRATEGY"}',
      '{"level":"POSITION","reason":"Position loss exceeds 50%","action":"CLOSE_POSITION"}',
    ],
  },
  {
    type: 'KILL_SWITCH_RELEASE',
    priority: 'SYNC',
    payloadTemplates: [
      '{"level":"PORTFOLIO","reason":"Drawdown recovered below threshold"}',
      '{"level":"STRATEGY","reason":"Strategy metrics normalized"}',
    ],
  },
  {
    type: 'POSITION_OPENED',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"positionId":"pos_001","token":"SOL","sizeUsd":2500,"direction":"LONG"}',
      '{"positionId":"pos_002","token":"ETH","sizeUsd":5000,"direction":"SHORT"}',
      '{"positionId":"pos_003","token":"BONK","sizeUsd":800,"direction":"LONG"}',
    ],
  },
  {
    type: 'POSITION_CLOSED',
    priority: 'SEMI_SYNC',
    payloadTemplates: [
      '{"positionId":"pos_001","pnlPct":12.5,"exitReason":"TAKE_PROFIT"}',
      '{"positionId":"pos_002","pnlPct":-3.2,"exitReason":"STOP_LOSS"}',
      '{"positionId":"pos_003","pnlPct":8.1,"exitReason":"SIGNAL_EXIT"}',
    ],
  },
  {
    type: 'SIGNAL_GENERATED',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"signalId":"sig_101","type":"SMART_MONEY_ENTRY","confidence":0.85}',
      '{"signalId":"sig_102","type":"RUG_PULL","confidence":0.72}',
      '{"signalId":"sig_103","type":"V_SHAPE","confidence":0.68}',
      '{"signalId":"sig_104","type":"WHALE_MOVEMENT","confidence":0.91}',
    ],
  },
  {
    type: 'REGIME_CHANGE',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"fromRegime":"SIDEWAYS","toRegime":"BULL","confidence":0.78}',
      '{"fromRegime":"BULL","toRegime":"VOLATILE","confidence":0.65}',
      '{"fromRegime":"VOLATILE","toRegime":"BEAR","confidence":0.82}',
    ],
  },
  {
    type: 'RISK_ALERT',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"category":"CONCENTRATION","message":"Chain SOL exceeds 60% allocation"}',
      '{"category":"LIQUIDITY","message":"Token BONK liquidity below threshold"}',
      '{"category":"CORRELATION","message":"Portfolio correlation score > 0.8"}',
    ],
  },
  {
    type: 'PRICE_ANOMALY',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"tokenAddress":"So11...","chain":"SOL","priceChangePct":15.3,"timeframe":"5m"}',
      '{"tokenAddress":"0xABC...","chain":"ETH","priceChangePct":-8.7,"timeframe":"15m"}',
    ],
  },
  {
    type: 'WHALE_MOVEMENT',
    priority: 'ASYNC',
    payloadTemplates: [
      '{"walletAddr":"7xKp...","amountUsd":1200000,"direction":"BUY"}',
      '{"walletAddr":"0xDef...","amountUsd":350000,"direction":"SELL"}',
    ],
  },
  {
    type: 'DAILY_VAR_BREACH',
    priority: 'SYNC',
    payloadTemplates: [
      '{"currentVaR":8.5,"maxVaR":5.0,"message":"Daily VaR exceeded max threshold"}',
    ],
  },
];

let simEventCounter = 0;

function generateSimulatedEvent(): EventBusEvent {
  const eventDef = SIMULATED_EVENT_TYPES[Math.floor(Math.random() * SIMULATED_EVENT_TYPES.length)];
  const payload = eventDef.payloadTemplates[Math.floor(Math.random() * eventDef.payloadTemplates.length)];
  const source = SIMULATED_SOURCES[Math.floor(Math.random() * SIMULATED_SOURCES.length)];
  simEventCounter++;
  return {
    id: `sim_evt_${Date.now()}_${simEventCounter}`,
    type: eventDef.type,
    source,
    priority: eventDef.priority,
    payload,
    timestamp: Date.now(),
  };
}

// ============================================================
// EVENT ROW COMPONENT
// ============================================================

function EventRow({ event }: { event: EventBusEvent }) {
  const config = EVENT_TYPE_CONFIG[event.type] || DEFAULT_EVENT_CONFIG;

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.15, ease: 'easeOut' }}
      className={`flex items-center gap-1.5 px-2 py-1 rounded border ${config.bg} ${config.border} ${PRIORITY_BORDER[event.priority] || PRIORITY_BORDER.ASYNC}`}
    >
      {/* Timestamp */}
      <span className="font-mono text-[9px] text-[#64748b] shrink-0 w-[78px]">
        {formatTimestamp(event.timestamp)}
      </span>

      {/* Event type badge */}
      <Badge
        className={`text-[8px] h-4 px-1.5 font-mono font-bold ${config.bg} ${config.text} ${config.border} border shrink-0 max-w-[140px] truncate`}
      >
        {event.type.replace(/_/g, ' ')}
      </Badge>

      {/* Priority dot */}
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          event.priority === 'SYNC'
            ? 'bg-red-500'
            : event.priority === 'SEMI_SYNC'
            ? 'bg-yellow-500'
            : 'bg-[#475569]'
        }`}
        title={event.priority}
      />

      {/* Source */}
      <span className="font-mono text-[9px] text-[#94a3b8] shrink-0 max-w-[100px] truncate" title={event.source}>
        {event.source}
      </span>

      {/* Payload */}
      <span className="font-mono text-[8px] text-[#64748b] truncate flex-1 min-w-0" title={event.payload}>
        {truncatePayload(event.payload)}
      </span>
    </motion.div>
  );
}

// ============================================================
// STAT CARD COMPONENT
// ============================================================

function StatCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#0a0e17] border border-[#1e293b]">
      <Icon className={`h-3 w-3 ${color} shrink-0`} />
      <span className="font-mono text-[8px] text-[#64748b] uppercase tracking-wider shrink-0">{label}</span>
      <span className={`font-mono text-[10px] font-bold ${color} truncate ml-auto`}>{value}</span>
    </div>
  );
}

// ============================================================
// MULTI-SELECT FILTER DROPDOWN
// ============================================================

function MultiSelectFilter({
  label,
  options,
  selected,
  onToggle,
  icon: Icon,
}: {
  label: string;
  options: string[];
  selected: Set<string>;
  onToggle: (option: string) => void;
  icon: React.ElementType;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-[9px] font-mono text-[#64748b] hover:text-[#e2e8f0] hover:bg-[#1a1f2e] gap-1"
        >
          <Icon className="h-3 w-3" />
          {label}
          {selected.size > 0 && (
            <Badge className="text-[7px] h-3 px-1 font-mono bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/40 ml-0.5">
              {selected.size}
            </Badge>
          )}
          <ChevronDown className="h-2.5 w-2.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="bg-[#0d1117] border-[#1e293b] min-w-[200px] max-h-[300px] overflow-y-auto"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#1e293b #0a0e17' }}
      >
        <DropdownMenuLabel className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
          {label}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-[#1e293b]" />
        {options.map((option) => {
          const config = EVENT_TYPE_CONFIG[option] || DEFAULT_EVENT_CONFIG;
          return (
            <DropdownMenuCheckboxItem
              key={option}
              checked={selected.has(option)}
              onCheckedChange={() => onToggle(option)}
              className="text-[9px] font-mono text-[#94a3b8] focus:bg-[#1a1f2e] focus:text-[#e2e8f0]"
            >
              <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${config.bg} ${config.text}`} />
              {option.replace(/_/g, ' ')}
            </DropdownMenuCheckboxItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ============================================================
// SOURCE MULTI-SELECT FILTER
// ============================================================

function SourceFilterDropdown({
  sources,
  selected,
  onToggle,
}: {
  sources: string[];
  selected: Set<string>;
  onToggle: (source: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-[9px] font-mono text-[#64748b] hover:text-[#e2e8f0] hover:bg-[#1a1f2e] gap-1"
        >
          <Wifi className="h-3 w-3" />
          Source
          {selected.size > 0 && (
            <Badge className="text-[7px] h-3 px-1 font-mono bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/40 ml-0.5">
              {selected.size}
            </Badge>
          )}
          <ChevronDown className="h-2.5 w-2.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="bg-[#0d1117] border-[#1e293b] min-w-[200px] max-h-[300px] overflow-y-auto"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#1e293b #0a0e17' }}
      >
        <DropdownMenuLabel className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
          Filter by Source
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-[#1e293b]" />
        {sources.map((source) => (
          <DropdownMenuCheckboxItem
            key={source}
            checked={selected.has(source)}
            onCheckedChange={() => onToggle(source)}
            className="text-[9px] font-mono text-[#94a3b8] focus:bg-[#1a1f2e] focus:text-[#e2e8f0]"
          >
            {source}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ============================================================
// PRIORITY LEGEND
// ============================================================

function PriorityLegend() {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-1.5 text-[9px] font-mono text-[#64748b] hover:text-[#e2e8f0] hover:bg-[#1a1f2e] gap-1"
        >
          <Zap className="h-3 w-3" />
          Priority
        </Button>
      </PopoverTrigger>
      <PopoverContent className="bg-[#0d1117] border-[#1e293b] p-3 w-56" side="bottom">
        <div className="space-y-2">
          <span className="font-mono text-[9px] text-[#64748b] uppercase tracking-wider">Priority Levels</span>
          <div className="flex items-center gap-2">
            <span className="w-4 h-0.5 border-l-2 border-l-red-500 bg-transparent" />
            <span className="font-mono text-[9px] text-red-400">SYNC</span>
            <span className="font-mono text-[8px] text-[#475569]">— Critical, blocks until handled</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-4 h-0.5 border-l-2 border-l-yellow-500 bg-transparent" />
            <span className="font-mono text-[9px] text-yellow-400">SEMI_SYNC</span>
            <span className="font-mono text-[8px] text-[#475569]">— Waits for DB, then async</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-4 h-0.5 border-l-2 border-l-[#475569] bg-transparent" />
            <span className="font-mono text-[9px] text-[#94a3b8]">ASYNC</span>
            <span className="font-mono text-[8px] text-[#475569]">— Fire and forget</span>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ============================================================
// MAIN EVENT BUS PANEL
// ============================================================

export function EventBusPanel() {
  const eventBusEvents = useCryptoStore((s) => s.eventBusEvents);
  const eventBusConnected = useCryptoStore((s) => s.eventBusConnected);
  const addEventBusEvent = useCryptoStore((s) => s.addEventBusEvent);
  const setEventBusConnected = useCryptoStore((s) => s.setEventBusConnected);
  const clearEventBusEvents = useCryptoStore((s) => s.clearEventBusEvents);

  const [isPaused, setIsPaused] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set());
  const [sourceFilter, setSourceFilter] = useState<Set<string>>(new Set());

  const scrollRef = useRef<HTMLDivElement>(null);
  const eventCountRef = useRef(0);
  const eventsThisMinuteRef = useRef<EventBusEvent[]>([]);
  const seenIdsRef = useRef(new Set<string>());

  // Poll for events from API every 2 seconds
  useEffect(() => {
    let mounted = true;

    const pollEvents = async () => {
      try {
        const res = await fetch('/api/user-events?limit=50');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();

        if (mounted && !isPaused) {
          setEventBusConnected(true);

          // Transform API events to EventBusEvent format
          const apiEvents: EventBusEvent[] = (json.events || []).map(
            (e: Record<string, unknown>) => ({
              id: e.id as string,
              type: (e.eventType as string) || 'OPEN_POSITION',
              source: 'user-event-api',
              priority: 'ASYNC' as const,
              payload: JSON.stringify({
                tokenId: e.tokenId,
                walletAddress: e.walletAddress,
                entryPrice: e.entryPrice,
                stopLoss: e.stopLoss,
                takeProfit: e.takeProfit,
                pnl: e.pnl,
              }),
              timestamp: new Date(e.createdAt as string).getTime(),
            }),
          );

          // Merge with store events (API events supplement the store)
          // Only add events we don't already have (using ref to avoid stale closure)
          const newApiEvents = apiEvents.filter((e) => !seenIdsRef.current.has(e.id));
          for (const ev of newApiEvents) {
            seenIdsRef.current.add(ev.id);
            addEventBusEvent(ev);
          }
        }
      } catch {
        if (mounted) {
          setEventBusConnected(false);
        }
      }
    };

    pollEvents();
    const interval = setInterval(pollEvents, 2000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [isPaused]);

  // Simulated event generator — adds realistic events every 3-6 seconds
  useEffect(() => {
    if (isPaused) return;
    let mounted = true;

    const scheduleNext = () => {
      const delay = 3000 + Math.random() * 3000;
      return setTimeout(() => {
        if (!mounted || isPaused) return;
        const event = generateSimulatedEvent();
        addEventBusEvent(event);
        timerRef = scheduleNext();
      }, delay);
    };

    let timerRef = scheduleNext();
    return () => {
      mounted = false;
      clearTimeout(timerRef);
    };
  }, [isPaused]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [eventBusEvents, autoScroll]);

  // Track scroll position
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(isAtBottom);
  }, []);

  // Derived: available sources
  const availableSources = useMemo(() => {
    const sources = new Set<string>();
    for (const ev of eventBusEvents) {
      sources.add(ev.source);
    }
    return Array.from(sources).sort();
  }, [eventBusEvents]);

  // Filtered events
  const filteredEvents = useMemo(() => {
    return eventBusEvents.filter((ev) => {
      // Type filter
      if (typeFilter.size > 0 && !typeFilter.has(ev.type)) return false;
      // Source filter
      if (sourceFilter.size > 0 && !sourceFilter.has(ev.source)) return false;
      // Search text
      if (searchText) {
        const searchLower = searchText.toLowerCase();
        const matchesPayload = ev.payload.toLowerCase().includes(searchLower);
        const matchesType = ev.type.toLowerCase().includes(searchLower);
        const matchesSource = ev.source.toLowerCase().includes(searchLower);
        if (!matchesPayload && !matchesType && !matchesSource) return false;
      }
      return true;
    });
  }, [eventBusEvents, typeFilter, sourceFilter, searchText]);

  // Stats
  const eventsPerMinute = useMemo(() => {
    const now = Date.now();
    const oneMinuteAgo = now - 60000;
    eventsThisMinuteRef.current = eventBusEvents.filter(
      (ev) => ev.timestamp > oneMinuteAgo,
    );
    return eventsThisMinuteRef.current.length;
  }, [eventBusEvents]);

  const totalToday = useMemo(() => {
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    return eventBusEvents.filter((ev) => ev.timestamp >= todayStart.getTime()).length;
  }, [eventBusEvents]);

  const mostActiveSource = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ev of eventBusEvents) {
      counts[ev.source] = (counts[ev.source] || 0) + 1;
    }
    let maxSource = '--';
    let maxCount = 0;
    for (const [source, count] of Object.entries(counts)) {
      if (count > maxCount) {
        maxSource = source;
        maxCount = count;
      }
    }
    return maxSource;
  }, [eventBusEvents]);

  const lastEventTime = useMemo(() => {
    if (eventBusEvents.length === 0) return '--';
    return timeAgo(eventBusEvents[eventBusEvents.length - 1].timestamp);
  }, [eventBusEvents]);

  // Toggle type filter
  const toggleTypeFilter = useCallback((type: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }, []);

  // Toggle source filter
  const toggleSourceFilter = useCallback((source: string) => {
    setSourceFilter((prev) => {
      const next = new Set(prev);
      if (next.has(source)) {
        next.delete(source);
      } else {
        next.add(source);
      }
      return next;
    });
  }, []);

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* ===== TOP STATS BAR ===== */}
      <div className="grid grid-cols-4 gap-1.5 px-2 py-1.5 border-b border-[#1e293b] bg-[#0d1117]">
        <StatCard
          icon={Activity}
          label="Events/min"
          value={String(eventsPerMinute)}
          color="text-emerald-400"
        />
        <StatCard
          icon={TrendingUp}
          label="Today"
          value={String(totalToday)}
          color="text-blue-400"
        />
        <StatCard
          icon={Wifi}
          label="Top Source"
          value={mostActiveSource.length > 16 ? mostActiveSource.slice(0, 14) + '..' : mostActiveSource}
          color="text-cyan-400"
        />
        <StatCard
          icon={Clock}
          label="Last Event"
          value={lastEventTime}
          color="text-amber-400"
        />
      </div>

      {/* ===== HEADER BAR ===== */}
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[#1e293b] bg-[#0a0e17]">
        {/* Live indicator */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              isPaused
                ? 'bg-yellow-500'
                : eventBusConnected
                ? 'bg-emerald-500 animate-pulse'
                : 'bg-red-500'
            }`}
          />
          <span
            className={`font-mono text-[9px] font-bold ${
              isPaused
                ? 'text-yellow-400'
                : eventBusConnected
                ? 'text-emerald-400'
                : 'text-red-400'
            }`}
          >
            {isPaused ? 'PAUSED' : eventBusConnected ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>

        <div className="h-3.5 w-px bg-[#1e293b]" />

        <Radio className="h-3 w-3 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">
          Event Bus
        </span>

        {/* Event count badge */}
        <Badge className="text-[8px] h-3.5 px-1.5 font-mono font-bold bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/40">
          {filteredEvents.length}/{eventBusEvents.length}
        </Badge>

        <div className="ml-auto flex items-center gap-1">
          {/* Pause/Resume */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsPaused(!isPaused)}
            className={`h-6 px-2 text-[9px] font-mono gap-1 ${
              isPaused
                ? 'text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/10'
                : 'text-yellow-400 hover:text-yellow-300 hover:bg-yellow-500/10'
            }`}
          >
            {isPaused ? (
              <>
                <Play className="h-3 w-3" /> Resume
              </>
            ) : (
              <>
                <Pause className="h-3 w-3" /> Pause
              </>
            )}
          </Button>

          {/* Clear */}
          <Button
            variant="ghost"
            size="sm"
            onClick={clearEventBusEvents}
            className="h-6 px-2 text-[9px] font-mono text-[#64748b] hover:text-red-400 hover:bg-red-500/10 gap-1"
          >
            <Trash2 className="h-3 w-3" /> Clear
          </Button>

          {/* Priority legend */}
          <PriorityLegend />

          {/* Type filter */}
          <MultiSelectFilter
            label="Type"
            options={ALL_EVENT_TYPES}
            selected={typeFilter}
            onToggle={toggleTypeFilter}
            icon={Filter}
          />

          {/* Source filter */}
          <SourceFilterDropdown
            sources={availableSources}
            selected={sourceFilter}
            onToggle={toggleSourceFilter}
          />

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-1.5 top-1/2 -translate-y-1/2 h-2.5 w-2.5 text-[#475569]" />
            <Input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search payload..."
              className="h-6 w-[120px] pl-5 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder:text-[#475569] focus:border-[#d4af37]/40"
            />
          </div>
        </div>
      </div>

      {/* ===== EVENT STREAM ===== */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto max-h-96 p-1.5 space-y-0.5"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#1e293b #0a0e17' }}
      >
        <AnimatePresence initial={false}>
          {filteredEvents.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </AnimatePresence>

        {filteredEvents.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 text-[#64748b] font-mono text-[10px] gap-2">
            <AlertTriangle className="h-4 w-4 text-[#475569]" />
            <span>
              {eventBusEvents.length === 0
                ? 'Waiting for events...'
                : 'No events match current filters'}
            </span>
          </div>
        )}
      </div>

      {/* ===== AUTO-SCROLL INDICATOR ===== */}
      {!autoScroll && (
        <div className="px-2 py-1 border-t border-[#1e293b] bg-[#0a0e17]">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAutoScroll(true);
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
              }
            }}
            className="h-5 px-1.5 text-[8px] font-mono text-[#64748b] hover:text-[#d4af37] w-full gap-1"
          >
            <ArrowDown className="h-2.5 w-2.5" />
            Auto-scroll paused — click to resume
          </Button>
        </div>
      )}

      {/* ===== FOOTER BAR ===== */}
      <div className="flex items-center gap-2 px-2 py-1 border-t border-[#1e293b] bg-[#0d1117]">
        <span className="font-mono text-[8px] text-[#475569]">
          Max 200 events (FIFO)
        </span>
        <div className="h-2.5 w-px bg-[#1e293b]" />
        <span className="font-mono text-[8px] text-[#475569]">
          Polling: 2s
        </span>
        <div className="h-2.5 w-px bg-[#1e293b]" />
        <span className="font-mono text-[8px] text-[#475569]">
          {eventBusConnected ? (
            <span className="text-emerald-500">Connected</span>
          ) : (
            <span className="text-red-500">Disconnected</span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {typeFilter.size > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setTypeFilter(new Set())}
              className="h-4 px-1 text-[7px] font-mono text-[#64748b] hover:text-[#d4af37]"
            >
              Clear type filter
            </Button>
          )}
          {sourceFilter.size > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSourceFilter(new Set())}
              className="h-4 px-1 text-[7px] font-mono text-[#64748b] hover:text-[#d4af37]"
            >
              Clear source filter
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
