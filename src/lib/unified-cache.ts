/**
 * UnifiedCache - CryptoQuant Terminal
 *
 * Centralized caching layer for ALL data sources.
 * Provides TTL-based in-memory caching, request deduplication,
 * rate-limit awareness, and cache statistics.
 *
 * Replaces per-client ad-hoc caching with a single, consistent system.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * RELATIONSHIP TO source-cache.ts (at ./services/source-cache):
 *
 *   This module provides the COMPREHENSIVE application-wide singleton cache
 *   with: request deduplication, rate-limit awareness, cache stats,
 *   memory tracking, eviction policies, and millisecond-based TTL.
 *   Used via the `unifiedCache` singleton and `cacheKey` helpers
 *   (e.g. dexscreener-client, coingecko-client, dexpaprika-client).
 *
 *   source-cache.ts provides a SIMPLE per-instance cache with a minute-based
 *   TTL constructor: `new UnifiedCache(30)`. It delegates to THIS global
 *   singleton, so ALL cache instances share the same underlying store.
 *   No more duplicate entries or wasted memory.
 *
 *   When adding a new service:
 *   - Use `unifiedCache` singleton + `cacheKey` helpers for shared caching
 *   - Use `new UnifiedCache(minutes)` from source-cache.ts for a convenient
 *     minute-based API that still uses the shared global cache
 * ══════════════════════════════════════════════════════════════════════════
 */

// ============================================================
// TYPES
// ============================================================

export interface CacheEntry<T = unknown> {
  data: T;
  timestamp: number;
  ttl: number;        // Time-to-live in ms
  source: string;     // e.g. 'dexpaprika', 'dexscreener', 'coingecko'
  key: string;
  hitCount: number;
  sizeEstimate: number; // Approximate bytes
}

export interface CacheConfig {
  /** Default TTL in ms (default: 60000 = 1 min) */
  defaultTtl: number;
  /** Maximum cache entries before eviction (default: 5000) */
  maxEntries: number;
  /** Maximum memory estimate in bytes (default: 50MB) */
  maxMemoryBytes: number;
  /** Per-source TTL overrides */
  sourceTtls: Record<string, number>;
  /** Whether to log cache hits/misses (default: false) */
  verbose: boolean;
}

export interface CacheStats {
  hits: number;
  misses: number;
  evictions: number;
  entries: number;
  memoryEstimateBytes: number;
  hitRate: number;
  topSources: Record<string, { hits: number; misses: number; entries: number }>;
}

interface PendingRequest<T = unknown> {
  promise: Promise<T>;
  timestamp: number;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

export const DEFAULT_CACHE_CONFIG: CacheConfig = {
  defaultTtl: 60_000,       // 1 min default
  maxEntries: 2000, // Reduced from 5000 for memory safety
  maxMemoryBytes: 20 * 1024 * 1024, // 20MB (reduced from 50MB)
  sourceTtls: {
    // Price data changes fast
    'dexpaprika:price': 15_000,         // 15s
    'dexpaprika:pools': 30_000,         // 30s
    'dexpaprika:pool-detail': 60_000,   // 1 min
    'dexpaprika:swaps': 10_000,         // 10s (real-time)
    'dexpaprika:buy-sell-ratio': 30_000, // 30s
    'dexpaprika:search': 300_000,       // 5 min
    'dexpaprika:tokens': 120_000,       // 2 min
    'dexpaprika:chains': 86400_000,     // 24h (rarely changes)
    'dexpaprika:ohlcv': 60_000,         // 1 min
    // DexScreener
    'dexscreener:search': 120_000,      // 2 min
    'dexscreener:token': 60_000,        // 1 min
    'dexscreener:trending': 300_000,    // 5 min
    // CoinGecko
    'coingecko:markets': 60_000,           // 1 min
    'coingecko:coin-detail': 120_000,      // 2 min
    'coingecko:ohlcv': 300_000,            // 5 min
    'coingecko:global': 60_000,            // 1 min
    'coingecko:search': 600_000,           // 10 min
    'coingecko:trending': 300_000,         // 5 min
    'coingecko:contract-token': 120_000,   // 2 min
    // Internal computations
    'brain:analysis': 300_000,          // 5 min
    'brain:regime': 600_000,            // 10 min
    'brain:lifecycle': 300_000,         // 5 min
    'operability:score': 120_000,       // 2 min
  },
  verbose: false,
};

// ============================================================
// UNIFIED CACHE CLASS
// ============================================================

export class UnifiedCache {
  private cache = new Map<string, CacheEntry>();
  private pending = new Map<string, PendingRequest<unknown>>();
  private config: CacheConfig;
  private stats = {
    hits: 0,
    misses: 0,
    evictions: 0,
  };
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;
  private memoryEstimate = 0;

  constructor(config?: Partial<CacheConfig>) {
    this.config = {
      ...DEFAULT_CACHE_CONFIG,
      ...config,
      sourceTtls: { ...DEFAULT_CACHE_CONFIG.sourceTtls, ...(config?.sourceTtls ?? {}) },
    };

    // Run cleanup every 60 seconds
    this.cleanupTimer = setInterval(() => this.evictExpired(), 60_000);

    // Don't prevent process exit
    if (this.cleanupTimer?.unref) {
      this.cleanupTimer.unref();
    }
  }

  // ----------------------------------------------------------
  // CORE OPERATIONS
  // ----------------------------------------------------------

  /**
   * Get a value from cache. Returns null if not found or expired.
   */
  get<T = unknown>(key: string): T | null {
    const entry = this.cache.get(key);
    if (!entry) {
      this.stats.misses++;
      this.trackSourceMiss(key);
      return null;
    }

    const age = Date.now() - entry.timestamp;
    if (age > entry.ttl) {
      // Expired
      this.cache.delete(key);
      this.memoryEstimate -= entry.sizeEstimate;
      this.stats.misses++;
      this.trackSourceMiss(key);
      return null;
    }

    // Cache hit
    entry.hitCount++;
    this.stats.hits++;

    if (this.config.verbose) {
      console.debug(`[UnifiedCache] HIT: ${key} (age: ${age}ms, ttl: ${entry.ttl}ms)`);
    }

    return entry.data as T;
  }

  /**
   * Set a value in cache with optional TTL override.
   */
  set<T = unknown>(
    key: string,
    data: T,
    source?: string,
    ttlOverride?: number,
  ): void {
    // Determine TTL: explicit > source-specific > default
    const ttl = ttlOverride
      ?? this.resolveSourceTtl(key, source)
      ?? this.config.defaultTtl;

    // Estimate memory size (rough: JSON string length * 2 for safety)
    const sizeEstimate = typeof data === 'string'
      ? data.length * 2
      : JSON.stringify(data).length * 2;

    // Evict old entry if overwriting
    const existing = this.cache.get(key);
    if (existing) {
      this.memoryEstimate -= existing.sizeEstimate;
    }

    // Check memory limits and evict if needed
    while (
      this.cache.size >= this.config.maxEntries ||
      this.memoryEstimate + sizeEstimate > this.config.maxMemoryBytes
    ) {
      this.evictOldest();
    }

    const entry: CacheEntry<T> = {
      data,
      timestamp: Date.now(),
      ttl,
      source: source ?? this.extractSourceFromKey(key),
      key,
      hitCount: 0,
      sizeEstimate,
    };

    this.cache.set(key, entry);
    this.memoryEstimate += sizeEstimate;
  }

  /**
   * Get from cache, or compute and cache the value.
   * Includes request deduplication: if a request for this key is already
   * in-flight, returns the same promise.
   */
  async getOrFetch<T = unknown>(
    key: string,
    fetcher: () => Promise<T>,
    source?: string,
    ttlOverride?: number,
  ): Promise<T> {
    // 1. Check cache
    const cached = this.get<T>(key);
    if (cached !== null) return cached;

    // 2. Check for in-flight request (dedup)
    const pending = this.pending.get(key);
    if (pending && Date.now() - pending.timestamp < 30_000) {
      // Wait for the existing request (max 30s)
      if (this.config.verbose) {
        console.debug(`[UnifiedCache] DEDUP: ${key} - reusing in-flight request`);
      }
      return pending.promise as Promise<T>;
    }

    // 3. Create new request
    let resolveFunc: (value: T) => void;
    let rejectFunc: (reason: unknown) => void;

    const promise = new Promise<T>((resolve, reject) => {
      resolveFunc = resolve;
      rejectFunc = reject;
    });

    this.pending.set(key, {
      promise,
      timestamp: Date.now(),
      resolve: resolveFunc! as (value: unknown) => void,
      reject: rejectFunc!,
    });

    try {
      const result = await fetcher();
      this.set(key, result, source, ttlOverride);
      this.pending.get(key)?.resolve(result);
      return result;
    } catch (error) {
      this.pending.get(key)?.reject(error);
      throw error;
    } finally {
      this.pending.delete(key);
    }
  }

  /**
   * Invalidate a specific key or pattern.
   * Supports prefix matching: invalidate('dexpaprika:price:*')
   */
  invalidate(pattern: string): number {
    let count = 0;

    if (pattern.endsWith('*')) {
      const prefix = pattern.slice(0, -1);
      for (const [key, entry] of this.cache) {
        if (key.startsWith(prefix)) {
          this.memoryEstimate -= entry.sizeEstimate;
          this.cache.delete(key);
          count++;
        }
      }
    } else {
      const entry = this.cache.get(pattern);
      if (entry) {
        this.memoryEstimate -= entry.sizeEstimate;
        this.cache.delete(pattern);
        count = 1;
      }
    }

    return count;
  }

  /**
   * Invalidate all entries for a given source.
   */
  invalidateSource(source: string): number {
    let count = 0;
    for (const [key, entry] of this.cache) {
      if (entry.source === source || key.startsWith(source + ':')) {
        this.memoryEstimate -= entry.sizeEstimate;
        this.cache.delete(key);
        count++;
      }
    }
    return count;
  }

  /**
   * Clear the entire cache.
   */
  clear(): void {
    this.cache.clear();
    this.pending.clear();
    this.memoryEstimate = 0;
  }

  // ----------------------------------------------------------
  // STATISTICS
  // ----------------------------------------------------------

  getStats(): CacheStats {
    const topSources: Record<string, { hits: number; misses: number; entries: number }> = {};

    // Count entries by source
    for (const [, entry] of this.cache) {
      if (!topSources[entry.source]) {
        topSources[entry.source] = { hits: 0, misses: 0, entries: 0 };
      }
      topSources[entry.source].entries++;
      topSources[entry.source].hits += entry.hitCount;
    }

    return {
      hits: this.stats.hits,
      misses: this.stats.misses,
      evictions: this.stats.evictions,
      entries: this.cache.size,
      memoryEstimateBytes: this.memoryEstimate,
      hitRate: this.stats.hits + this.stats.misses > 0
        ? this.stats.hits / (this.stats.hits + this.stats.misses)
        : 0,
      topSources,
    };
  }

  /**
   * Get a summary string suitable for logging.
   */
  getSummary(): string {
    const stats = this.getStats();
    const memMB = (stats.memoryEstimateBytes / 1024 / 1024).toFixed(1);
    return `[UnifiedCache] ${stats.entries} entries | ${memMB}MB | hit rate: ${(stats.hitRate * 100).toFixed(1)}% | ${stats.hits} hits / ${stats.misses} misses`;
  }

  // ----------------------------------------------------------
  // RATE LIMIT AWARENESS
  // ----------------------------------------------------------

  private rateLimitBackoff = new Map<string, number>();

  /**
   * Mark a source as rate-limited until a given time.
   */
  markRateLimited(source: string, retryAfterMs: number): void {
    this.rateLimitBackoff.set(source, Date.now() + retryAfterMs);
  }

  /**
   * Check if a source is currently rate-limited.
   */
  isRateLimited(source: string): boolean {
    const backoff = this.rateLimitBackoff.get(source);
    if (!backoff) return false;
    if (Date.now() > backoff) {
      this.rateLimitBackoff.delete(source);
      return false;
    }
    return true;
  }

  /**
   * Get the remaining rate-limit backoff time in ms.
   */
  getRateLimitRemaining(source: string): number {
    const backoff = this.rateLimitBackoff.get(source);
    if (!backoff) return 0;
    return Math.max(0, backoff - Date.now());
  }

  // ----------------------------------------------------------
  // PRIVATE HELPERS
  // ----------------------------------------------------------

  private resolveSourceTtl(key: string, source?: string): number | undefined {
    // Try exact key match first (e.g. "dexpaprika:price")
    const exactMatch = this.config.sourceTtls[key];
    if (exactMatch !== undefined) return exactMatch;

    // Try source prefix match
    if (source) {
      const segments = key.split(':');
      // Second segment is typically the type (e.g., "dexpaprika:price:SoL1...")
      const typeSegment = segments.length >= 2 ? segments[1] : segments[0];
      const sourceMatch = this.config.sourceTtls[`${source}:${typeSegment}`];
      if (sourceMatch !== undefined) return sourceMatch;

      // Try source alone
      for (const [pattern, ttl] of Object.entries(this.config.sourceTtls)) {
        if (pattern.startsWith(source + ':') && key.includes(pattern.split(':')[1])) {
          return ttl;
        }
      }
    }

    return undefined;
  }

  private extractSourceFromKey(key: string): string {
    const parts = key.split(':');
    return parts[0] || 'unknown';
  }

  private trackSourceMiss(key: string): void {
    if (this.config.verbose) {
      console.debug(`[UnifiedCache] MISS: ${key}`);
    }
  }

  private evictExpired(): void {
    const now = Date.now();
    for (const [key, entry] of this.cache) {
      if (now - entry.timestamp > entry.ttl) {
        this.memoryEstimate -= entry.sizeEstimate;
        this.cache.delete(key);
        this.stats.evictions++;
      }
    }
  }

  private evictOldest(): void {
    // Find and remove the oldest entry
    let oldestKey: string | null = null;
    let oldestTime = Infinity;

    for (const [key, entry] of this.cache) {
      if (entry.timestamp < oldestTime) {
        oldestTime = entry.timestamp;
        oldestKey = key;
      }
    }

    if (oldestKey) {
      const entry = this.cache.get(oldestKey);
      if (entry) {
        this.memoryEstimate -= entry.sizeEstimate;
      }
      this.cache.delete(oldestKey);
      this.stats.evictions++;
    }
  }

  /**
   * Destroy the cache and cleanup timers.
   */
  destroy(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
    this.cache.clear();
    this.pending.clear();
    this.rateLimitBackoff.clear();
    this.memoryEstimate = 0;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const unifiedCache = new UnifiedCache();

/**
 * Helper to create a cache key with consistent format.
 * Format: "source:type:identifier"
 */
export function cacheKey(source: string, type: string, identifier: string): string {
  return `${source}:${type}:${identifier}`;
}

/**
 * Helper to create a cache key with chain context.
 * Format: "source:type:chain:identifier"
 */
export function cacheKeyWithChain(
  source: string,
  type: string,
  chain: string,
  identifier: string,
): string {
  return `${source}:${type}:${chain}:${identifier}`;
}
