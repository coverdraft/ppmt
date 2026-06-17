// ============================================================
// SHARED FORMATTING & UTILITY FUNCTIONS
// Eliminates duplication across dashboard components
//
// All formatting functions handle null/undefined/NaN gracefully — no crashes.
// Consolidated from former format.ts and utils/safe-format.ts.
// ============================================================

// ============================================================
// SAFE NUMBER HELPERS
// ============================================================

/** Safe toFixed — never crashes on null/undefined/NaN */
export function safeToFixed(val: number | undefined | null | string, digits: number = 2): string {
  const num = Number(val);
  if (val == null || isNaN(num)) return '0';
  return num.toFixed(digits);
}

// ============================================================
// PRICE FORMATTING
// ============================================================

/** Safe price formatting */
export function formatPrice(price: number | undefined | null): string {
  const p = Number(price);
  if (price == null || isNaN(p) || p === 0) return '$0.00';
  if (p >= 1000) return `$${p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (p >= 1) return `$${p.toFixed(2)}`;
  if (p >= 0.001) return `$${p.toFixed(4)}`;
  if (p >= 0.00001) return `$${p.toFixed(6)}`;
  return `$${p.toFixed(8)}`;
}

/** Safe price formatting without $ */
export function formatPriceRaw(price: number | undefined | null): string {
  const p = Number(price);
  if (price == null || isNaN(p) || p === 0) return '0.00';
  if (p >= 1000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 1) return p.toFixed(2);
  if (p >= 0.001) return p.toFixed(4);
  if (p >= 0.00001) return p.toFixed(6);
  return p.toFixed(8);
}

// ============================================================
// VOLUME / MARKET CAP FORMATTING
// ============================================================

/** Safe volume formatting */
export function formatVolume(vol: number | undefined | null): string {
  const v = Number(vol);
  if (vol == null || isNaN(v) || v === 0) return '$0';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

/** Safe volume formatting without $ */
export function formatVolumeRaw(vol: number | undefined | null): string {
  const v = Number(vol);
  if (vol == null || isNaN(v) || v === 0) return '0';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

/** Safe currency formatting (for capital, PnL, etc.) */
export function formatCurrency(val: number | undefined | null): string {
  const v = Number(val);
  if (val == null || isNaN(v)) return '$0.00';
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}

/** Safe market cap formatting */
export function formatMarketCap(val: number | undefined | null): string {
  const v = Number(val);
  if (val == null || isNaN(v) || v === 0) return '$0';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

// ============================================================
// PERCENTAGE FORMATTING
// ============================================================

/** Safe percentage formatting with sign */
export function formatPct(val: number | undefined | null, digits: number = 1): string {
  const v = Number(val);
  if (val == null || isNaN(v)) return '0.0%';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

/** Safe percentage formatting without sign */
export function formatPctNoSign(val: number | undefined | null, digits: number = 1): string {
  const v = Number(val);
  if (val == null || isNaN(v)) return `0.${'0'.repeat(digits)}%`;
  return `${v.toFixed(digits)}%`;
}

/** Format a percentage change with + sign (alias for formatPct) */
export function formatChange(pct: number | undefined | null): string {
  return formatPct(pct, 1);
}

// ============================================================
// TIME / DURATION FORMATTING
// ============================================================

/** Format uptime from milliseconds */
export function formatUptime(ms: number | undefined | null): string {
  const v = Number(ms);
  if (ms == null || isNaN(v) || v <= 0) return '0s';
  const s = Math.floor(v / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** Safe duration formatting */
export function formatDuration(ms: number | undefined | null): string {
  const v = Number(ms);
  if (ms == null || isNaN(v) || v <= 0) return '—';
  if (v < 1000) return `${v}ms`;
  if (v < 60000) return `${(v / 1000).toFixed(1)}s`;
  return `${(v / 60000).toFixed(1)}m`;
}

/** Safe time ago formatting */
export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return 'Never';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 'just now';
  if (diff < 5000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

/** Safe interval formatting */
export function formatInterval(ms: number | undefined | null): string {
  const v = Number(ms);
  if (ms == null || isNaN(v) || v < 1000) return `${v ?? 0}ms`;
  if (v < 60000) return `${Math.round(v / 1000)}s`;
  if (v < 3600000) {
    const mins = Math.floor(v / 60000);
    const secs = Math.round((v % 60000) / 1000);
    return secs > 0 ? `${mins}m ${secs}s` : `${mins} min`;
  }
  const hrs = Math.floor(v / 3600000);
  const mins = Math.round((v % 3600000) / 60000);
  return mins > 0 ? `${hrs}h ${mins}m` : `${hrs}h`;
}

/** Safe countdown formatting */
export function formatCountdown(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return 'now';
  if (diff < 60000) return `${Math.ceil(diff / 1000)}s`;
  if (diff < 3600000) {
    const mins = Math.floor(diff / 60000);
    const secs = Math.ceil((diff % 60000) / 1000);
    return secs >= 60 ? `${mins + 1}m` : `${mins}m ${secs}s`;
  }
  const hrs = Math.floor(diff / 3600000);
  const mins = Math.ceil((diff % 3600000) / 60000);
  return mins >= 60 ? `${hrs + 1}h` : `${hrs}h ${mins}m`;
}

// ============================================================
// CHAIN CONFIGURATION
// ============================================================

export const CHAIN_CONFIG: Record<string, { name: string; color: string; bg: string; border: string }> = {
  SOL:   { name: 'Solana',    color: 'text-purple-400',   bg: 'bg-purple-500/10',   border: 'border-purple-500/30' },
  ETH:   { name: 'Ethereum',  color: 'text-blue-400',     bg: 'bg-blue-500/10',     border: 'border-blue-500/30' },
  BASE:  { name: 'Base',      bg: 'bg-blue-600/10',      color: 'text-blue-300',   border: 'border-blue-600/30' },
  BSC:   { name: 'BNB Chain', color: 'text-yellow-400',   bg: 'bg-yellow-500/10',   border: 'border-yellow-500/30' },
  MATIC: { name: 'Polygon',   color: 'text-violet-400',   bg: 'bg-violet-500/10',   border: 'border-violet-500/30' },
  ARB:   { name: 'Arbitrum',  color: 'text-sky-400',      bg: 'bg-sky-500/10',      border: 'border-sky-500/30' },
  OP:    { name: 'Optimism',  color: 'text-red-400',      bg: 'bg-red-500/10',      border: 'border-red-500/30' },
  AVAX:  { name: 'Avalanche', color: 'text-red-400',      bg: 'bg-red-600/10',      border: 'border-red-600/30' },
};

export const ALL_CHAINS = Object.keys(CHAIN_CONFIG);

export function getChainBadge(chain: string): { color: string; bg: string; border: string } {
  return CHAIN_CONFIG[chain] || { color: 'text-gray-400', bg: 'bg-gray-500/10', border: 'border-gray-500/30' };
}

// ============================================================
// MARKET INDICATORS
// ============================================================

/** Fear & Greed: color + label based on range */
export function getFearGreedStyle(value: number): { color: string; label: string } {
  if (value <= 25) return { color: 'text-red-400',     label: 'Extreme Fear' };
  if (value <= 45) return { color: 'text-orange-400',  label: 'Fear' };
  if (value <= 55) return { color: 'text-yellow-400',  label: 'Neutral' };
  if (value <= 75) return { color: 'text-emerald-400', label: 'Greed' };
  return { color: 'text-emerald-300', label: 'Extreme Greed' };
}

/** Market regime badge style */
export function getRegimeStyle(regime: string): { bg: string; text: string; border: string } {
  switch (regime?.toUpperCase()) {
    case 'BULL':       return { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/30' };
    case 'BEAR':       return { bg: 'bg-red-500/10',     text: 'text-red-400',     border: 'border-red-500/30' };
    case 'SIDEWAYS':   return { bg: 'bg-yellow-500/10',  text: 'text-yellow-400',  border: 'border-yellow-500/30' };
    case 'TRANSITION': return { bg: 'bg-purple-500/10',  text: 'text-purple-400',  border: 'border-purple-500/30' };
    default:           return { bg: 'bg-gray-500/10',    text: 'text-gray-400',    border: 'border-gray-500/30' };
  }
}

/** Operability level color */
export function getOperabilityColor(level: string): string {
  switch (level) {
    case 'PREMIUM':   return 'text-emerald-400';
    case 'GOOD':      return 'text-blue-400';
    case 'MARGINAL':  return 'text-yellow-400';
    case 'RISKY':     return 'text-orange-400';
    case 'UNOPERABLE': return 'text-red-400';
    default:          return 'text-gray-400';
  }
}

/** Token lifecycle phase color */
export function getPhaseColor(phase: string): string {
  switch (phase) {
    case 'GENESIS':   return 'text-violet-400';
    case 'INCIPIENT': return 'text-blue-400';
    case 'GROWTH':    return 'text-emerald-400';
    case 'FOMO':      return 'text-yellow-400';
    case 'DECLINE':   return 'text-orange-400';
    case 'LEGACY':    return 'text-gray-400';
    default:          return 'text-gray-500';
  }
}

/** Risk score color (0-100) */
export function getRiskColor(score: number): string {
  if (score <= 30) return 'text-emerald-400';
  if (score <= 50) return 'text-yellow-400';
  if (score <= 70) return 'text-orange-400';
  return 'text-red-400';
}

// ============================================================
// DEXSCREENER CHAIN NORMALIZATION
// ============================================================

/**
 * Comprehensive chain normalization map.
 * Maps every known variant (full name, short code, platform ID, lowercase)
 * to the canonical SHORT chain code.
 *
 * Canonical forms: SOL, ETH, BSC, ARB, OP, BASE, MATIC, AVAX, FTM
 */
const CHAIN_NORMALIZE_MAP: Record<string, string> = {
  // Solana variants
  solana: 'SOL', sol: 'SOL',
  // Ethereum variants
  ethereum: 'ETH', eth: 'ETH',
  // BNB Chain variants
  'binance-smart-chain': 'BSC', 'binance': 'BSC', bsc: 'BSC', bnb: 'BSC',
  // Arbitrum variants
  arbitrum: 'ARB', arb: 'ARB', 'arbitrum-one': 'ARB',
  // Optimism variants
  'optimistic-ethereum': 'OP', optimism: 'OP', op: 'OP',
  // Base
  base: 'BASE',
  // Polygon variants
  'polygon-pos': 'MATIC', polygon: 'MATIC', matic: 'MATIC', poly: 'MATIC',
  // Avalanche variants
  avalanche: 'AVAX', avax: 'AVAX', 'avalanche-c': 'AVAX',
  // Fantom variants
  fantom: 'FTM', ftm: 'FTM',
  // Common display names that should map to short codes
  'solana ': 'SOL', 'ethereum ': 'ETH',
};

/** Normalize chain names to our canonical short form.
 *  Examples:
 *    'solana' → 'SOL', 'SOLANA' → 'SOL', 'Solana' → 'SOL'
 *    'ethereum' → 'ETH', 'ETHEREUM' → 'ETH'
 *    'binance-smart-chain' → 'BSC', 'bnb' → 'BSC'
 *    'polygon-pos' → 'MATIC', 'polygon' → 'MATIC'
 *    unknown → uppercased input
 */
export function normalizeChain(raw: string): string {
  if (!raw) return 'SOL'; // Safe default
  const lower = raw.toLowerCase().trim();
  return CHAIN_NORMALIZE_MAP[lower] || raw.toUpperCase().trim();
}

/** Get all chain variants that map to the same canonical code.
 *  Useful for DB queries where data may have been stored under
 *  different chain names before normalization was added.
 *
 *  Example: getChainVariants('SOL') → ['SOL', 'SOLANA', 'sol', 'solana', 'Solana']
 */
export function getChainVariants(canonical: string): string[] {
  const upper = canonical.toUpperCase();
  const variants: string[] = [upper];
  for (const [key, value] of Object.entries(CHAIN_NORMALIZE_MAP)) {
    if (value === upper) {
      if (!variants.includes(key)) variants.push(key);
      if (!variants.includes(key.toUpperCase())) variants.push(key.toUpperCase());
      // Capitalize first letter
      const capitalized = key.charAt(0).toUpperCase() + key.slice(1);
      if (!variants.includes(capitalized)) variants.push(capitalized);
    }
  }
  return variants;
}
