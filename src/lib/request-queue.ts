/**
 * Client-side request queue - prevents concurrent API requests from
 * overwhelming the server. Serializes requests with a small delay.
 */

class RequestQueue {
  private queue: Array<() => void> = [];
  private active = 0;
  private maxActive = 2; // Max 2 concurrent requests
  private minInterval = 500; // 500ms between requests
  private lastRequest = 0;

  async acquire(): Promise<void> {
    // Enforce minimum interval between requests
    const elapsed = Date.now() - this.lastRequest;
    if (elapsed < this.minInterval) {
      await new Promise(r => setTimeout(r, this.minInterval - elapsed));
    }

    if (this.active < this.maxActive) {
      this.active++;
      this.lastRequest = Date.now();
      return;
    }

    return new Promise<void>((resolve) => {
      this.queue.push(() => {
        this.active++;
        this.lastRequest = Date.now();
        resolve();
      });
    });
  }

  release(): void {
    this.active--;
    const next = this.queue.shift();
    if (next) next();
  }
}

export const requestQueue = new RequestQueue();

/**
 * Fetch with request queuing - use this instead of raw fetch()
 * for API calls that go to the CryptoQuant backend.
 */
export async function queuedFetch(url: string, options?: RequestInit): Promise<Response> {
  await requestQueue.acquire();
  try {
    const response = await fetch(url, options);
    return response;
  } finally {
    requestQueue.release();
  }
}
