/**
 * Simple per-instance cache for services (minute-based TTL API).
 *
 * Now backed by the GLOBAL unifiedCache singleton from ../../unified-cache.
 * This ensures ALL clients share the same cache — no more duplicate entries
 * or separate cache instances that waste memory and API budget.
 *
 * Usage:
 *   const cache = new UnifiedCache(30); // 30 min default TTL
 *   cache.set('key', data);              // Uses default TTL
 *   cache.set('key', data, 5);           // 5 minute custom TTL
 *   const result = cache.get<Type>('key');
 */

import { unifiedCache as globalCache } from '../../unified-cache';

export class UnifiedCache {
  private defaultTtlMs: number;

  constructor(defaultTtlMinutes = 30) {
    this.defaultTtlMs = defaultTtlMinutes * 60 * 1000;
  }

  get<T>(key: string): T | null {
    return globalCache.get<T>(key);
  }

  /**
   * Set a cache entry with an optional custom TTL (in minutes).
   * If no custom TTL is provided, uses the default.
   */
  set(key: string, data: unknown, ttlMinutes?: number): void {
    const ttlMs = ttlMinutes !== undefined ? ttlMinutes * 60 * 1000 : this.defaultTtlMs;
    globalCache.set(key, data, undefined, ttlMs);
  }

  /** Invalidate all entries matching a key prefix */
  invalidate(prefix: string): void {
    globalCache.invalidate(prefix + '*');
  }

  /** Clear all cache entries */
  clear(): void {
    globalCache.clear();
  }

  /** Get the number of entries currently in cache */
  size(): number {
    return globalCache.getStats().entries;
  }
}

/** @deprecated Use UnifiedCache instead. Kept for backward compatibility. */
export const SourceCache = UnifiedCache;
