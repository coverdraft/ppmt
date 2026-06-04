/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  Shared Rate Limiter — Token-bucket style with queue                    ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * Single source of truth for rate limiting across all data source clients.
 * Uses a token-bucket algorithm with a request queue for fair scheduling.
 *
 * Usage:
 *   const limiter = new RateLimiter(10, 20); // 10 RPS, burst of 20
 *   await limiter.acquire();
 *   // ... make API call
 */

export class RateLimiter {
  private tokens: number;
  private maxTokens: number;
  private refillRate: number; // tokens per second
  private lastRefill: number;
  private queue: Array<{ resolve: () => void }> = [];
  private processing = false;

  constructor(maxRps: number, burstSize?: number) {
    this.maxTokens = burstSize ?? Math.ceil(maxRps * 2);
    this.tokens = this.maxTokens;
    this.refillRate = maxRps;
    this.lastRefill = Date.now();
  }

  async acquire(): Promise<void> {
    this.refill();
    if (this.tokens >= 1) {
      this.tokens -= 1;
      return;
    }
    return new Promise<void>((resolve) => {
      this.queue.push({ resolve });
      this.processQueue();
    });
  }

  private refill(): void {
    const now = Date.now();
    const elapsed = (now - this.lastRefill) / 1000;
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillRate);
    this.lastRefill = now;
  }

  private processQueue(): void {
    if (this.processing) return;
    this.processing = true;
    const tick = () => {
      this.refill();
      while (this.queue.length > 0 && this.tokens >= 1) {
        this.tokens -= 1;
        const next = this.queue.shift()!;
        next.resolve();
      }
      if (this.queue.length > 0) {
        const waitMs = Math.max(50, Math.ceil(1000 / this.refillRate));
        setTimeout(tick, waitMs);
      } else {
        this.processing = false;
      }
    };
    tick();
  }
}
