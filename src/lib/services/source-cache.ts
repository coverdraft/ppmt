/**
 * Simple per-instance cache for services (minute-based TTL API).
 *
 * ══════════════════════════════════════════════════════════════════════════
 * RELATIONSHIP TO unified-cache.ts (at ../unified-cache):
 *
 *   - THIS module (source-cache.ts) provides a simple, per-instance cache
 *     with a minute-based TTL constructor: `new UnifiedCache(30)`.
 *     Used by services that need their own isolated cache instance
 *     (e.g. dune-client, footprint-client, sqd-client).
 *
 *   - unified-cache.ts provides the COMPREHENSIVE application-wide singleton
 *     cache with: request deduplication, rate-limit awareness, cache stats,
 *     memory tracking, eviction policies, and millisecond-based TTL.
 *     Used via the `unifiedCache` singleton and `cacheKey` helpers
 *     (e.g. dexscreener-client, coingecko-client, dexpaprika-client).
 *
 *   Implementation: This module wraps the comprehensive UnifiedCache from
 *   ../unified-cache with a simpler minute-based API for backward
 *   compatibility with existing service consumers.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Usage:
 *   const cache = new UnifiedCache(30); // 30 min default TTL
 *   cache.set('key', data);              // Uses default TTL
 *   cache.set('key', data, 5);           // 5 minute custom TTL
 *   const result = cache.get<Type>('key');
 */

import { UnifiedCache as ComprehensiveCache } from '../unified-cache';

export class UnifiedCache {
  private inner: ComprehensiveCache;

  constructor(defaultTtlMinutes = 30) {
    this.inner = new ComprehensiveCache({
      defaultTtl: defaultTtlMinutes * 60 * 1000,
      maxEntries: 1000,
      maxMemoryBytes: 10 * 1024 * 1024,
      sourceTtls: {},
      verbose: false,
    });
  }

  get<T>(key: string): T | null {
    return this.inner.get<T>(key);
  }

  /**
   * Set a cache entry with an optional custom TTL (in minutes).
   * If no custom TTL is provided, uses the default.
   */
  set(key: string, data: unknown, ttlMinutes?: number): void {
    this.inner.set(
      key,
      data,
      undefined,
      ttlMinutes !== undefined ? ttlMinutes * 60 * 1000 : undefined,
    );
  }

  /** Invalidate all entries matching a key prefix */
  invalidate(prefix: string): void {
    this.inner.invalidate(prefix + '*');
  }

  /** Clear all cache entries */
  clear(): void {
    this.inner.clear();
  }

  /** Get the number of entries currently in cache */
  size(): number {
    return this.inner.getStats().entries;
  }
}

/** @deprecated Use UnifiedCache instead. Kept for backward compatibility. */
export const SourceCache = UnifiedCache;
