import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/traders/[id] - Get detailed trader profile
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { db } = await import('@/lib/db');
    const { id } = await params;
    
    const trader = await db.trader.findUnique({
      where: { id },
      include: {
        behaviorPatterns: { orderBy: { confidence: 'desc' } },
        labelAssignments: { orderBy: { assignedAt: 'desc' } },
        tokenHoldings: { take: 20, orderBy: { valueUsd: 'desc' } },
        crossChainLinks: {
          include: { linkedWallet: true },
        },
        linkedWallets: {
          include: { primaryWallet: true },
        },
        transactions: {
          take: 30,
          orderBy: { blockTime: 'desc' },
        },
        _count: {
          select: {
            transactions: true,
            tokenHoldings: true,
            behaviorPatterns: true,
          },
        },
      },
    });

    if (!trader) {
      return NextResponse.json({ error: 'Trader not found' }, { status: 404 });
    }

    // Compute derived metrics
    const totalTxCount = trader._count.transactions;
    const totalHoldingCount = trader._count.tokenHoldings;
    
    // Parse JSON fields
    const tradingHourPattern = JSON.parse(trader.tradingHourPattern || '[]');
    const tradingDayPattern = JSON.parse(trader.tradingDayPattern || '[]');
    const preferredChains = JSON.parse(trader.preferredChains || '[]');
    const preferredDexes = JSON.parse(trader.preferredDexes || '[]');
    const preferredTokenTypes = JSON.parse(trader.preferredTokenTypes || '[]');
    const botDetectionSignals = JSON.parse(trader.botDetectionSignals || '[]');
    const subLabels = JSON.parse(trader.subLabels || '[]');

    // Build risk assessment
    const riskFactors: string[] = [];
    let riskLevel = 'LOW';
    
    if (trader.isBot) {
      riskLevel = 'HIGH';
      riskFactors.push(`Identified as ${trader.botType || 'bot'} (${(trader.botConfidence * 100).toFixed(0)}% confidence)`);
    }
    if (trader.washTradeScore > 0.5) {
      riskLevel = 'CRITICAL';
      riskFactors.push('High wash trading probability');
    }
    if (trader.winRate < 0.3 && totalTxCount > 20) {
      riskLevel = 'MEDIUM';
      riskFactors.push('Consistently losing trader');
    }
    if (trader.isActive247) {
      riskFactors.push('24/7 trading activity');
    }

    // Build profile summary
    const profileSummary = buildProfileSummary(trader);

    return NextResponse.json({
      trader: {
        ...trader,
        tradingHourPattern,
        tradingDayPattern,
        preferredChains,
        preferredDexes,
        preferredTokenTypes,
        botDetectionSignals,
        subLabels,
      },
      derived: {
        totalTransactions: totalTxCount,
        totalHoldings: totalHoldingCount,
        riskLevel,
        riskFactors,
        profileSummary,
      },
    });
  } catch (error) {
    console.error('Error fetching trader:', error);
    return NextResponse.json({ error: 'Failed to fetch trader' }, { status: 500 });
  }
}

function buildProfileSummary(trader: {
  address: string;
  chain: string;
  primaryLabel: string;
  isBot: boolean;
  botType: string | null;
  isSmartMoney: boolean;
  isWhale: boolean;
  isSniper: boolean;
  winRate: number;
  totalPnl: number;
  totalTrades: number;
  avgHoldTimeMin: number;
  smartMoneyScore: number;
  whaleScore: number;
  sniperScore: number;
  botConfidence: number;
}): string {
  const shortAddr = `${trader.address.slice(0, 6)}...${trader.address.slice(-4)}`;
  const pnlStr = trader.totalPnl >= 0
    ? `+$${trader.totalPnl.toFixed(0)}`
    : `-$${Math.abs(trader.totalPnl).toFixed(0)}`;
  
  if (trader.isBot) {
    return `${shortAddr} | ${trader.botType?.replace(/_/g, ' ')} | Confidence: ${(trader.botConfidence * 100).toFixed(0)}% | ${trader.totalTrades} txs | PnL: ${pnlStr}`;
  }
  if (trader.isSmartMoney) {
    return `${shortAddr} | SMART MONEY | Score: ${trader.smartMoneyScore}/100 | WR: ${(trader.winRate * 100).toFixed(0)}% | ${trader.totalTrades} txs | PnL: ${pnlStr}`;
  }
  if (trader.isWhale) {
    return `${shortAddr} | WHALE | Score: ${trader.whaleScore}/100 | ${trader.totalTrades} txs | PnL: ${pnlStr}`;
  }
  if (trader.isSniper) {
    return `${shortAddr} | SNIPER | Score: ${trader.sniperScore}/100 | WR: ${(trader.winRate * 100).toFixed(0)}% | Hold: ${trader.avgHoldTimeMin.toFixed(0)}min`;
  }
  return `${shortAddr} | ${trader.primaryLabel} | WR: ${(trader.winRate * 100).toFixed(0)}% | ${trader.totalTrades} txs | PnL: ${pnlStr}`;
}
