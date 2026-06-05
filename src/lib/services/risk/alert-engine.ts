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
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

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
          // Unknown condition type — do NOT match by default, log warning
          console.warn(`[AlertEngine] Unknown condition type: ${condition.type}, not matching`);
          return false;
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
  // ESCALATION ENGINE
  // ============================================================

  /**
   * Evaluate the severity level for a category based on recent alert frequency.
   * Counts alerts in the last 60 minutes for the same category:
   *   0-2 recent alerts → INFO
   *   3-5 recent alerts → WARNING
   *   6+ recent alerts → CRITICAL
   * Falls back to INFO on DB errors (fail-safe).
   */
  async evaluateEscalation(category: AlertCategory, metadata: Record<string, unknown>): Promise<AlertSeverity> {
    try {
      const sixtyMinAgo = new Date(Date.now() - 60 * 60 * 1000);
      const recentCount = await db.alert.count({
        where: {
          category,
          createdAt: { gte: sixtyMinAgo },
        },
      });

      if (recentCount >= 6) return 'CRITICAL';
      if (recentCount >= 3) return 'WARNING';
      return 'INFO';
    } catch {
      return 'INFO'; // Fail safe — default to INFO on DB errors
    }
  }

  /**
   * Escalate an alert event based on recent frequency, then process it.
   * - Only escalates UP (never downgrades severity)
   * - On CRITICAL + RISK/STRATEGY, triggers global auto-pause via kill switch
   */
  async escalateAndAlert(event: AlertEvent): Promise<AlertPayload | null> {
    const escalatedSeverity = await this.evaluateEscalation(event.category, event.metadata || {});

    // Only escalate UP, never down
    const severityOrder: Record<AlertSeverity, number> = { INFO: 0, WARNING: 1, CRITICAL: 2 };
    const currentSeverity = event.severity || 'INFO';
    const finalSeverity: AlertSeverity = severityOrder[escalatedSeverity] > severityOrder[currentSeverity]
      ? escalatedSeverity
      : currentSeverity;

    const escalatedEvent = { ...event, severity: finalSeverity };

    const payload = await this.processEvent(escalatedEvent);

    // AUTO_PAUSE on CRITICAL RISK/STRATEGY alerts
    if (finalSeverity === 'CRITICAL' && (event.category === 'RISK' || event.category === 'STRATEGY')) {
      try {
        killSwitchService.setGlobalPause(true, `AUTO_ESCALATION: ${event.title} — ${event.message}`);
        console.error(`[AlertEngine] AUTO_ESCALATION: Global pause triggered by CRITICAL ${event.category} alert: ${event.title}`);
      } catch (err) {
        console.error('[AlertEngine] Failed to auto-pause on escalation:', err);
      }
    }

    return payload;
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
    positionSizeUsd?: number,
  ): Promise<AlertPayload | null> {
    // Compute percentage-based PnL when position size is available;
    // otherwise fall back to absolute dollar threshold with a documented limitation.
    // NOTE: Using absolute dollar thresholds (e.g. pnl < -5) is position-size-dependent
    // and unreliable — a $5 loss on a $10 position (50%) is far more severe than on a $1000
    // position (0.5%). Prefer passing positionSizeUsd for accurate severity classification.
    const pnlPct = positionSizeUsd && positionSizeUsd > 0
      ? (pnl / positionSizeUsd) * 100
      : null;

    const severity: AlertSeverity =
      pnlPct !== null
        ? pnlPct < -10 ? 'CRITICAL'    // >10% loss is critical
        : pnlPct < 0  ? 'WARNING'      // any % loss is a warning
        : 'INFO'
        : pnl < -5  ? 'CRITICAL'       // Fallback: absolute dollar (LIMITATION: position-size-dependent)
        : pnl < 0   ? 'WARNING'
        : 'INFO';

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

  /**
   * Alert when a kill switch is automatically triggered.
   * Escalation: PORTFOLIO and STRATEGY are CRITICAL, POSITION is WARNING.
   */
  async onKillSwitchTriggered(
    level: 'PORTFOLIO' | 'STRATEGY' | 'POSITION',
    details: Record<string, unknown>,
  ): Promise<AlertPayload | null> {
    const severity: AlertSeverity = level === 'POSITION' ? 'WARNING' : 'CRITICAL';

    return this.processEvent({
      category: 'RISK',
      title: `Kill Switch: ${level} Level`,
      message: `Automatic kill switch triggered at ${level} level. ${JSON.stringify(details)}`,
      severity,
      metadata: { level, autoTriggered: true, ...details },
      linkTo: 'trading-systems:paper-trading',
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
