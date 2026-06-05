import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface EquityPoint {
  timestamp: string;
  capital: number;
  drawdown: number;
}

/**
 * GET /api/risk/overview
 * Comprehensive risk dashboard data for the paper trading portfolio.
 * Query params:
 *   ?sessionId=xxx  — specific paper trading session (default: latest)
 *   ?includeHistory=true — include full equity curve
 */
export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const searchParams = request.nextUrl.searchParams;
    const sessionId = searchParams.get('sessionId');
    const includeHistory = searchParams.get('includeHistory') === 'true';

    // Get session
    const session = sessionId
      ? await db.paperTradingSession.findUnique({ where: { id: sessionId } })
      : await db.paperTradingSession.findFirst({
          orderBy: { createdAt: 'desc' },
        });

    if (!session) {
      return NextResponse.json({
        data: {
          portfolioRisk: {
            totalExposureUsd: 0,
            maxExposureUsd: 0,
            exposurePct: 0,
            openPositions: 0,
            maxPositions: 0,
            concentrationByChain: {},
            concentrationByDirection: { LONG: 0, SHORT: 0 },
          },
          pnlMetrics: {
            realizedPnl: 0,
            unrealizedPnl: 0,
            totalPnl: 0,
            winRate: 0,
            avgWinUsd: 0,
            avgLossUsd: 0,
            profitFactor: 0,
            expectancyUsd: 0,
            maxConsecutiveWins: 0,
            maxConsecutiveLosses: 0,
          },
          drawdown: {
            currentDrawdownPct: 0,
            maxDrawdownPct: 0,
            maxDrawdownUsd: 0,
            peakCapital: 0,
            currentCapital: 0,
            recoveryFactor: 0,
            timeToRecoveryEstMin: 0,
          },
          riskControls: {
            maxPositionSizePct: 10,
            maxPortfolioRiskPct: 25,
            stopLossDefaultPct: 5,
            dailyLossLimitPct: 10,
            currentDailyPnlPct: 0,
          },
          tradeAnalysis: {
            avgHoldTimeMin: 0,
            bestTrade: null,
            worstTrade: null,
            avgMfe: 0,
            avgMae: 0,
            mfeMaeRatio: 0,
          },
          equityCurve: [],
        },
      });
    }

    const runId = session.id;

    // Get open positions
    const openPositions = await db.paperTradingPosition.findMany({
      where: { status: 'OPEN', runId },
      take: 50,
    });

    // Get closed trades (all for this session)
    const closedTrades = await db.paperTradingTrade.findMany({
      where: { position: { runId } },
      orderBy: { closedAt: 'asc' },
      take: 500,
    });

    // ---- PORTFOLIO RISK ----
    const totalExposureUsd = openPositions.reduce((sum, p) => sum + p.sizeUsd, 0);
    const maxExposureUsd = session.initialCapital; // max exposure = full capital
    const exposurePct = maxExposureUsd > 0 ? (totalExposureUsd / maxExposureUsd) * 100 : 0;

    // Concentration by chain
    const chainMap: Record<string, number> = {};
    openPositions.forEach((p) => {
      chainMap[p.chain] = (chainMap[p.chain] || 0) + p.sizeUsd;
    });
    // Convert to percentages
    const concentrationByChain: Record<string, number> = {};
    if (totalExposureUsd > 0) {
      for (const [chain, val] of Object.entries(chainMap)) {
        concentrationByChain[chain] = Math.round((val / totalExposureUsd) * 100);
      }
    }

    // Concentration by direction
    const dirMap: Record<string, number> = { LONG: 0, SHORT: 0 };
    openPositions.forEach((p) => {
      const d = p.direction === 'SHORT' ? 'SHORT' : 'LONG';
      dirMap[d] = (dirMap[d] || 0) + p.sizeUsd;
    });
    const concentrationByDirection: Record<string, number> = {};
    if (totalExposureUsd > 0) {
      concentrationByDirection.LONG = Math.round((dirMap.LONG / totalExposureUsd) * 100);
      concentrationByDirection.SHORT = Math.round((dirMap.SHORT / totalExposureUsd) * 100);
    } else {
      concentrationByDirection.LONG = 0;
      concentrationByDirection.SHORT = 0;
    }

    // ---- P&L METRICS ----
    const realizedPnl = closedTrades.reduce((sum, t) => sum + t.pnlUsd, 0);
    const unrealizedPnl = openPositions.reduce((sum, p) => sum + p.pnlUsd, 0);
    const totalPnl = realizedPnl + unrealizedPnl;

    const winningTrades = closedTrades.filter((t) => t.pnlUsd > 0);
    const losingTrades = closedTrades.filter((t) => t.pnlUsd <= 0);
    const winRate = closedTrades.length > 0 ? winningTrades.length / closedTrades.length : 0;

    const avgWinUsd = winningTrades.length > 0
      ? winningTrades.reduce((s, t) => s + t.pnlUsd, 0) / winningTrades.length
      : 0;
    const avgLossUsd = losingTrades.length > 0
      ? losingTrades.reduce((s, t) => s + t.pnlUsd, 0) / losingTrades.length
      : 0;

    const grossProfit = winningTrades.reduce((s, t) => s + t.pnlUsd, 0);
    const grossLoss = Math.abs(losingTrades.reduce((s, t) => s + t.pnlUsd, 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    const expectancyUsd = closedTrades.length > 0
      ? totalPnl / closedTrades.length
      : 0;

    // Consecutive wins/losses
    let maxConsecutiveWins = 0;
    let maxConsecutiveLosses = 0;
    let cw = 0;
    let cl = 0;
    for (const t of closedTrades) {
      if (t.pnlUsd > 0) {
        cw++;
        cl = 0;
        maxConsecutiveWins = Math.max(maxConsecutiveWins, cw);
      } else {
        cl++;
        cw = 0;
        maxConsecutiveLosses = Math.max(maxConsecutiveLosses, cl);
      }
    }

    // ---- DRAWDOWN ----
    let peakCapital = session.initialCapital;
    let maxDrawdownUsd = 0;
    let maxDrawdownPct = 0;

    // Compute equity curve from closed trades
    const equityCurve: EquityPoint[] = [];
    let runningCapital = session.initialCapital;
    let runningPeak = session.initialCapital;

    // Starting point
    equityCurve.push({
      timestamp: session.startedAt?.toISOString() || session.createdAt.toISOString(),
      capital: Math.round(runningCapital * 100) / 100,
      drawdown: 0,
    });

    for (const t of closedTrades) {
      runningCapital += t.pnlUsd;
      if (runningCapital > runningPeak) runningPeak = runningCapital;

      const dd = runningPeak > 0 ? runningPeak - runningCapital : 0;
      const ddPct = runningPeak > 0 ? (dd / runningPeak) * 100 : 0;

      if (dd > maxDrawdownUsd) maxDrawdownUsd = dd;
      if (ddPct > maxDrawdownPct) maxDrawdownPct = ddPct;

      if (runningCapital > peakCapital) peakCapital = runningCapital;

      equityCurve.push({
        timestamp: t.closedAt.toISOString(),
        capital: Math.round(runningCapital * 100) / 100,
        drawdown: Math.round(ddPct * 100) / 100,
      });
    }

    // Add unrealized PnL to current capital
    const currentCapital = session.currentCapital + unrealizedPnl;
    if (currentCapital > peakCapital) peakCapital = currentCapital;

    const currentDrawdownPct = peakCapital > 0
      ? ((peakCapital - currentCapital) / peakCapital) * 100
      : 0;

    // Update max drawdown from session
    const sessionMaxDd = session.peakCapital > 0
      ? ((session.peakCapital - session.currentCapital) / session.peakCapital) * 100
      : 0;
    if (sessionMaxDd > maxDrawdownPct) {
      maxDrawdownPct = sessionMaxDd;
    }

    const recoveryFactor = maxDrawdownPct > 0
      ? (session.initialCapital > 0
        ? ((currentCapital - session.initialCapital) / session.initialCapital) / (maxDrawdownPct / 100)
        : 0)
      : 0;

    // Time to recovery estimate (based on average trade frequency and PnL)
    const avgTradePnlUsd = closedTrades.length > 0
      ? realizedPnl / closedTrades.length
      : 0;
    const currentDrawdownUsd = peakCapital - currentCapital;
    let timeToRecoveryEstMin = 0;
    if (avgTradePnlUsd > 0 && currentDrawdownUsd > 0) {
      const tradesToRecovery = Math.ceil(currentDrawdownUsd / avgTradePnlUsd);
      const avgHoldTimeMin = closedTrades.length > 0
        ? closedTrades.reduce((s, t) => s + (t.holdTimeMin || 0), 0) / closedTrades.length
        : 60;
      timeToRecoveryEstMin = Math.round(tradesToRecovery * avgHoldTimeMin);
    }

    // ---- RISK CONTROLS ----
    // Priority: RiskControlsConfig table > TradingSystem > defaults
    let maxPositionSizePct = 10;
    let maxPortfolioRiskPct = 25;
    let stopLossDefaultPct = 5;
    let dailyLossLimitPct = 10;

    try {
      const savedConfig = await db.riskControlsConfig.findFirst({
        where: { userId: null },
        orderBy: { updatedAt: 'desc' },
      });
      if (savedConfig) {
        maxPositionSizePct = savedConfig.maxPositionSizePct;
        maxPortfolioRiskPct = savedConfig.maxPortfolioRiskPct;
        stopLossDefaultPct = savedConfig.stopLossDefaultPct;
        dailyLossLimitPct = savedConfig.dailyLossLimitPct;
      } else if (session.strategyName) {
        // Fallback: try TradingSystem if no explicit config saved
        const system = await db.tradingSystem.findFirst({
          where: { name: session.strategyName },
        });
        if (system) {
          maxPositionSizePct = system.maxPositionPct;
          maxPortfolioRiskPct = system.cashReservePct;
          stopLossDefaultPct = system.stopLossPct;
          // dailyLossLimitPct - not a direct field, derive from cash reserve
          dailyLossLimitPct = Math.min(system.cashReservePct, 15);
        }
      }
    } catch {
      // Use defaults
    }

    // Daily PnL
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const dailyTrades = closedTrades.filter(
      (t) => new Date(t.closedAt) >= todayStart
    );
    const dailyRealizedPnl = dailyTrades.reduce((s, t) => s + t.pnlUsd, 0);
    const currentDailyPnlPct = session.initialCapital > 0
      ? (dailyRealizedPnl / session.initialCapital) * 100
      : 0;

    // ---- TRADE ANALYSIS ----
    const avgHoldTimeMin = closedTrades.length > 0
      ? closedTrades.reduce((s, t) => s + (t.holdTimeMin || 0), 0) / closedTrades.length
      : 0;

    const bestTrade = closedTrades.length > 0
      ? closedTrades.reduce((best, t) => t.pnlUsd > best.pnlUsd ? t : best, closedTrades[0])
      : null;
    const worstTrade = closedTrades.length > 0
      ? closedTrades.reduce((worst, t) => t.pnlUsd < worst.pnlUsd ? t : worst, closedTrades[0])
      : null;

    const avgMfe = closedTrades.length > 0
      ? closedTrades.reduce((s, t) => s + t.mfe, 0) / closedTrades.length
      : 0;
    const avgMae = closedTrades.length > 0
      ? closedTrades.reduce((s, t) => s + t.mae, 0) / closedTrades.length
      : 0;
    const mfeMaeRatio = Math.abs(avgMae) > 0 ? avgMfe / Math.abs(avgMae) : avgMfe > 0 ? Infinity : 0;

    // Build response
    const data = {
      portfolioRisk: {
        totalExposureUsd: Math.round(totalExposureUsd * 100) / 100,
        maxExposureUsd: Math.round(maxExposureUsd * 100) / 100,
        exposurePct: Math.round(exposurePct * 100) / 100,
        openPositions: openPositions.length,
        maxPositions: session.maxOpenPositions,
        concentrationByChain,
        concentrationByDirection,
      },
      pnlMetrics: {
        realizedPnl: Math.round(realizedPnl * 100) / 100,
        unrealizedPnl: Math.round(unrealizedPnl * 100) / 100,
        totalPnl: Math.round(totalPnl * 100) / 100,
        winRate: Math.round(winRate * 10000) / 10000,
        avgWinUsd: Math.round(avgWinUsd * 100) / 100,
        avgLossUsd: Math.round(avgLossUsd * 100) / 100,
        profitFactor: profitFactor === Infinity ? -1 : Math.round(profitFactor * 100) / 100,
        expectancyUsd: Math.round(expectancyUsd * 100) / 100,
        maxConsecutiveWins,
        maxConsecutiveLosses,
      },
      drawdown: {
        currentDrawdownPct: Math.round(currentDrawdownPct * 100) / 100,
        maxDrawdownPct: Math.round(maxDrawdownPct * 100) / 100,
        maxDrawdownUsd: Math.round(-maxDrawdownUsd * 100) / 100,
        peakCapital: Math.round(peakCapital * 100) / 100,
        currentCapital: Math.round(currentCapital * 100) / 100,
        recoveryFactor: Math.round(recoveryFactor * 100) / 100,
        timeToRecoveryEstMin,
      },
      riskControls: {
        maxPositionSizePct,
        maxPortfolioRiskPct,
        stopLossDefaultPct,
        dailyLossLimitPct,
        currentDailyPnlPct: Math.round(currentDailyPnlPct * 100) / 100,
      },
      tradeAnalysis: {
        avgHoldTimeMin: Math.round(avgHoldTimeMin * 100) / 100,
        bestTrade: bestTrade ? {
          symbol: bestTrade.tokenSymbol,
          pnlUsd: Math.round(bestTrade.pnlUsd * 100) / 100,
          pnlPct: Math.round(bestTrade.pnlPct * 100) / 100,
        } : null,
        worstTrade: worstTrade ? {
          symbol: worstTrade.tokenSymbol,
          pnlUsd: Math.round(worstTrade.pnlUsd * 100) / 100,
          pnlPct: Math.round(worstTrade.pnlPct * 100) / 100,
        } : null,
        avgMfe: Math.round(avgMfe * 100) / 100,
        avgMae: Math.round(avgMae * 100) / 100,
        mfeMaeRatio: mfeMaeRatio === Infinity ? -1 : Math.round(mfeMaeRatio * 100) / 100,
      },
      equityCurve: includeHistory ? equityCurve : equityCurve.slice(-50),
    };

    return NextResponse.json({ data });
  } catch (error) {
    console.error('Error computing risk overview:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to compute risk overview' },
      { status: 500 }
    );
  }
}
