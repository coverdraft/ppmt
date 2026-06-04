/**
 * WS Bridge - Connects Next.js backend to the WebSocket server
 *
 * When the Brain Scheduler produces signals or completes cycles,
 * this module pushes those events to the WS server (port 3010),
 * which then broadcasts them to all connected Socket.IO clients.
 *
 * Usage:
 *   import { wsBridge } from '@/lib/ws-bridge';
 *   await wsBridge.pushBrainSignal({ type: 'SMART_MONEY_ENTRY', ... });
 */

const WS_BRIDGE_URL = process.env.WS_BRIDGE_URL || 'http://localhost:3010';

class WSBridge {
  private url: string;
  private enabled: boolean;

  constructor(url: string = WS_BRIDGE_URL) {
    this.url = url;
    this.enabled = true;
  }

  /**
   * Push a Brain-generated signal to all WS clients.
   */
  async pushBrainSignal(signal: {
    type: string;
    tokenSymbol?: string;
    tokenAddress?: string;
    chain?: string;
    confidence: number;
    direction: string;
    description: string;
    priceTarget?: number;
    [key: string]: unknown;
  }): Promise<boolean> {
    return this.emit('brain-signal', signal);
  }

  /**
   * Push a Brain cycle completion event.
   */
  async pushBrainCycle(data: {
    cyclesCompleted: number;
    tokensScanned: number;
    signalsGenerated: number;
    capitalUsd?: number;
    [key: string]: unknown;
  }): Promise<boolean> {
    return this.emit('brain-cycle', data);
  }

  /**
   * Push a scheduler status change.
   */
  async pushSchedulerStatus(data: {
    status: string;
    uptime?: number;
    totalCyclesCompleted?: number;
    [key: string]: unknown;
  }): Promise<boolean> {
    return this.emit('scheduler-status', data);
  }

  /**
   * Push an alert notification to all WS clients.
   */
  async pushAlert(alert: {
    id: string;
    title: string;
    message: string;
    category: string;
    severity: string;
    metadata?: Record<string, unknown>;
    linkTo?: string;
    createdAt: string;
  }): Promise<boolean> {
    return this.emit('alert', alert);
  }

  private async emit(type: string, data: Record<string, unknown>): Promise<boolean> {
    if (!this.enabled) return false;

    try {
      const res = await fetch(this.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, data }),
        signal: AbortSignal.timeout(3000), // 3s timeout
      });
      return res.ok;
    } catch (error) {
      // Silently fail - WS bridge is optional, not critical
      // Disable after repeated failures to avoid spam
      return false;
    }
  }

  /**
   * Enable/disable the bridge (for testing or when WS server is down)
   */
  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
  }
}

export const wsBridge = new WSBridge();
