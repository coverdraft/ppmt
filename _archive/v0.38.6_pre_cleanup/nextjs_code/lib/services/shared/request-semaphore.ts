/**
 * Request Semaphore - Prevents OOM by limiting concurrent heavy operations.
 *
 * Next.js serverless functions each load their own copy of heavy modules.
 * When many requests hit concurrently, all load at once = OOM.
 * This semaphore ensures only N heavy operations run simultaneously.
 */

class RequestSemaphore {
  private running = 0;
  private queue: Array<() => void> = [];
  private maxConcurrent: number;

  constructor(maxConcurrent: number = 2) {
    this.maxConcurrent = maxConcurrent;
  }

  async acquire(): Promise<void> {
    if (this.running < this.maxConcurrent) {
      this.running++;
      return;
    }
    // Queue the request
    return new Promise<void>((resolve) => {
      this.queue.push(resolve);
    });
  }

  release(): void {
    this.running--;
    const next = this.queue.shift();
    if (next) {
      this.running++;
      next();
    }
  }

  get status() {
    return { running: this.running, queued: this.queue.length, max: this.maxConcurrent };
  }
}

// Singleton: max 2 concurrent heavy operations
export const heavyOpSemaphore = new RequestSemaphore(2);

// Light semaphore for DB-only reads (more permissive)
export const lightOpSemaphore = new RequestSemaphore(6);
