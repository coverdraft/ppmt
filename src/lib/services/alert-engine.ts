/**
 * Alert Engine - CryptoQuant Terminal
 *
 * Central alert processing service that:
 * - Evaluates alert rules against incoming events
 * - Persists alerts to DB with cooldown enforcement
 * - Pushes real-time notifications via WS bridge
 * - Delivers to webhook channels if configured
 * - Provides convenience methods for common events
 */

import { db } from '@/lib/db';
import { wsBridge } from '@/lib/ws-bridge';

// ============================================================
// TYPES
// ============================================================

export type AlertCategory = 'PRICE' | 'SIGNAL' | 'STRATEGY' | 'RISK' | 'SMART_MONEY' | 'SYSTEM';
export type AlertSeverity = 'INFO' | 'WARNING' | 'CRITICAL';

export interface AlertEvent {
  category: AlertCategory;
  title: string;
  message: string;
  severity?: AlertSeverity;
  metadata?: Record<string, unknown>;
  linkTo?: string;
}

export interface AlertPayload {
  id: string;
  title: string;
  message: string;
  category: string;
  severity: string;
  metadata?: Record<string, unknown>;
  linkTo?: string;
  createdAt: string;
}

// ============================================================
// ALERT ENGINE SERVICE
// ============================================================

class AlertEngine {
  /**
   * Process an alert event: evaluate rules, persist, push, deliver.
   */
  async processEvent(event: AlertEvent): Promise<AlertPayload | null> {
    try {
      const severity = event.severity || 'INFO';

      // Create alert record in DB
      const alert = await db.alert.create({
        data: {
          title: event.title,
          message: event.message,
          category: event.category,
          severity,
          metadata: event.metadata ? JSON.stringify(event.metadata) : null,
          linkTo: event.linkTo || null,
        },
      });

      // Check enabled rules for matching and update their lastTriggeredAt
      await this.evaluateRules(event);

      const payload: AlertPayload = {
        id: alert.id,
        title: alert.title,
        message: alert.message,
        category: alert.category,
        severity: alert.severity,
        metadata: event.metadata,
        linkTo: alert.linkTo || undefined,
        createdAt: alert.createdAt.toISOString(),
      };

      // Push to frontend via WS bridge
      await wsBridge.pushAlert(payload);

      // Deliver to webhooks if configured
      await this.deliverToWebhooks(event);

      return payload;
    } catch (error) {
      console.error('[AlertEngine] Error processing event:', error);
      return null;
    }
  }

  /**
   * Evaluate all enabled alert rules against the event.
   * Updates lastTriggeredAt for matching rules (respecting cooldown).
   */
  private async evaluateRules(event: AlertEvent): Promise<void> {
    try {
      const rules = await db.alertRule.findMany({
        where: { enabled: true, category: event.category },
      });

      for (const rule of rules) {
        // Check cooldown
        if (rule.lastTriggeredAt) {
          const elapsed = Date.now() - rule.lastTriggeredAt.getTime();
          if (elapsed < rule.cooldownMin * 60 * 1000) {
            continue; // Still in cooldown
          }
        }

        // Evaluate rule condition against event
        if (this.matchesCondition(rule.condition, event)) {
          await db.alertRule.update({
            where: { id: rule.id },
            data: { lastTriggeredAt: new Date() },
          });
        }
      }
    } catch (error) {
      console.warn('[AlertEngine] Error evaluating rules:', error);
    }
  }

  /**
   * Check if an event matches a rule's condition JSON.
   */
  private matchesCondition(conditionJson: string, event: AlertEvent): boolean {
    try {
      const condition = JSON.parse(conditionJson);

      // Match by category (already filtered in query, but check type-specific fields)
      switch (condition.type) {
        case 'price_above':
          return event.metadata?.price !== undefined &&
            Number(event.metadata.price) > condition.threshold;
        case 'price_below':
          return event.metadata?.price !== undefined &&
            Number(event.metadata.price) < condition.threshold;
        case 'state_change':
          return event.metadata?.newStatus === condition.targetStatus;
        case 'pnl_threshold':
          return event.metadata?.pnl !== undefined &&
            Math.abs(Number(event.metadata.pnl)) > condition.threshold;
        case 'risk_level':
          return event.metadata?.riskLevel === condition.riskLevel;
        case 'category_match':
          return event.category === condition.category;
        default:
          // Default: always match if no specific type
          return true;
      }
    } catch {
      // If condition can't be parsed, don't match
      return false;
    }
  }

  /**
   * Deliver alert to configured webhooks.
   */
  private async deliverToWebhooks(event: AlertEvent): Promise<void> {
    try {
      const webhooks = await db.webhookConfig.findMany({
        where: { enabled: true },
      });

      for (const webhook of webhooks) {
        const events: string[] = JSON.parse(webhook.events);
        // If events list is empty, deliver all; otherwise check category match
        if (events.length > 0 && !events.includes(event.category)) {
          continue;
        }

        try {
          const response = await fetch(webhook.url, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(webhook.secret ? { 'X-Webhook-Secret': webhook.secret } : {}),
            },
            body: JSON.stringify({
              event: 'alert',
              data: {
                category: event.category,
                severity: event.severity || 'INFO',
                title: event.title,
                message: event.message,
                metadata: event.metadata,
                timestamp: new Date().toISOString(),
              },
            }),
            signal: AbortSignal.timeout(5000),
          });

          await db.webhookConfig.update({
            where: { id: webhook.id },
            data: {
              lastDeliveryAt: new Date(),
              lastStatus: response.ok ? 'SUCCESS' : 'FAILED',
              failureCount: response.ok ? 0 : webhook.failureCount + 1,
            },
          });
        } catch {
          await db.webhookConfig.update({
            where: { id: webhook.id },
            data: {
              lastDeliveryAt: new Date(),
              lastStatus: 'FAILED',
              failureCount: webhook.failureCount + 1,
            },
          });
        }
      }
    } catch (error) {
      console.warn('[AlertEngine] Webhook delivery error:', error);
    }
  }

  // ============================================================
  // CONVENIENCE METHODS
  // ============================================================

  /**
   * Alert when a strategy changes state.
   */
  async onStrategyStateChanged(
    systemId: string,
    oldStatus: string,
    newStatus: string,
    reason: string,
  ): Promise<AlertPayload | null> {
    const severity: AlertSeverity =
      newStatus === 'ERROR' ? 'CRITICAL' :
      newStatus === 'PAUSED' && reason === 'RISK_LIMIT' ? 'WARNING' :
      'INFO';

    return this.processEvent({
      category: 'STRATEGY',
      title: `Strategy ${newStatus}`,
      message: `Strategy transitioned from ${oldStatus || 'none'} to ${newStatus} (${reason})`,
      severity,
      metadata: { systemId, oldStatus, newStatus, reason },
      linkTo: 'trading-systems:strategy-states',
    });
  }

  /**
   * Alert when a trade is opened.
   */
  async onTradeOpened(
    symbol: string,
    direction: string,
    price: number,
    size: number,
  ): Promise<AlertPayload | null> {
    return this.processEvent({
      category: 'STRATEGY',
      title: `Position Opened: ${direction} ${symbol}`,
      message: `Opened ${direction} position on ${symbol} at $${price.toFixed(6)} | Size: $${size.toFixed(2)}`,
      severity: 'INFO',
      metadata: { symbol, direction, price, size, action: 'OPEN' },
      linkTo: 'trading-systems:paper-trading',
    });
  }

  /**
   * Alert when a trade is closed.
   */
  async onTradeClosed(
    symbol: string,
    direction: string,
    pnl: number,
    reason: string,
  ): Promise<AlertPayload | null> {
    const severity: AlertSeverity =
      pnl < -5 ? 'CRITICAL' :
      pnl < 0 ? 'WARNING' :
      'INFO';

    return this.processEvent({
      category: 'STRATEGY',
      title: `Position Closed: ${direction} ${symbol}`,
      message: `Closed ${direction} ${symbol} | PnL: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} | Reason: ${reason}`,
      severity,
      metadata: { symbol, direction, pnl, reason, action: 'CLOSE' },
      linkTo: 'trading-systems:paper-trading',
    });
  }

  /**
   * Alert when a risk limit is triggered.
   */
  async onRiskLimitTriggered(
    systemId: string,
    details: Record<string, unknown>,
  ): Promise<AlertPayload | null> {
    return this.processEvent({
      category: 'RISK',
      title: 'Risk Limit Triggered',
      message: `Risk limit triggered for strategy: ${JSON.stringify(details)}`,
      severity: 'CRITICAL',
      metadata: { systemId, ...details },
      linkTo: 'trading-systems:strategy-states',
    });
  }

  /**
   * Alert on smart money movement.
   */
  async onSmartMoneyMovement(
    token: string,
    amount: number,
    direction: string,
  ): Promise<AlertPayload | null> {
    const severity: AlertSeverity = amount > 100000 ? 'WARNING' : 'INFO';

    return this.processEvent({
      category: 'SMART_MONEY',
      title: `Smart Money ${direction}: ${token}`,
      message: `Smart money ${direction.toLowerCase()} detected on ${token} | Amount: $${amount.toLocaleString()}`,
      severity,
      metadata: { token, amount, direction },
      linkTo: 'trader-intel',
    });
  }

  /**
   * Alert on market regime change.
   */
  async onRegimeChange(
    token: string,
    oldRegime: string,
    newRegime: string,
  ): Promise<AlertPayload | null> {
    return this.processEvent({
      category: 'PRICE',
      title: `Regime Change: ${token}`,
      message: `${token} regime changed from ${oldRegime} to ${newRegime}`,
      severity: 'WARNING',
      metadata: { token, oldRegime, newRegime },
    });
  }

  // ============================================================
  // QUERY HELPERS
  // ============================================================

  /**
   * Get unread alert count.
   */
  async getUnreadCount(): Promise<number> {
    return db.alert.count({
      where: { isRead: false, isDismissed: false },
    });
  }

  /**
   * Get recent alerts with optional filters.
   */
  async getAlerts(options?: {
    category?: string;
    severity?: string;
    isRead?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<{ alerts: any[]; total: number }> {
    const { category, severity, isRead, limit = 50, offset = 0 } = options || {};

    const where: Record<string, unknown> = { isDismissed: false };
    if (category) where.category = category;
    if (severity) where.severity = severity;
    if (isRead !== undefined) where.isRead = isRead;

    const [alerts, total] = await Promise.all([
      db.alert.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take: limit,
        skip: offset,
      }),
      db.alert.count({ where }),
    ]);

    return { alerts, total };
  }
}

// Singleton instance
export const alertEngine = new AlertEngine();
