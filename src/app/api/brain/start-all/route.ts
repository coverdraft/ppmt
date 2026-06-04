import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/brain/start-all
 * One-click bootstrap: seeds DB, scans tokens, computes DNA, starts scheduler, runs first cycle.
 * This is the "Make it work" button.
 */
export async function POST(request: Request) {
  const steps: { step: string; status: string; detail?: string }[] = [];
  const { db } = await import('@/lib/db');

  try {
    // Step 1: Ensure demo user exists
    try {
      const { getCurrentUserId } = await import('@/lib/services/shared/user-data-filter');
      const userId = await getCurrentUserId();
      steps.push({ step: '1. Demo User', status: 'OK', detail: userId });
    } catch (e: any) {
      steps.push({ step: '1. Demo User', status: 'ERROR', detail: e.message });
    }

    // Step 2: Scan tokens from CoinGecko (top 5000+) + DexScreener (top 50)
    try {
      const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');

      // CoinGecko: top 5000+ by market cap (paginated — 20 pages × 250)
      const cgTokens = await coinGeckoClient.getTopTokensPaginated(5000);
      let cgUpserted = 0;
      for (const token of cgTokens) {
        try {
          await db.token.upsert({
            where: { address: token.coinId },
            update: {
              symbol: (token.symbol || '').toUpperCase(),
              name: token.name || token.symbol || '',
              priceUsd: token.priceUsd ?? 0,
              volume24h: token.volume24h ?? 0,
              marketCap: token.marketCap ?? 0,
              priceChange1h: token.priceChange1h ?? 0,
              priceChange24h: token.priceChange24h ?? 0,
            },
            create: {
              address: token.coinId,
              symbol: (token.symbol || '').toUpperCase(),
              name: token.name || token.symbol || '',
              chain: 'SOL',
              priceUsd: token.priceUsd ?? 0,
              volume24h: token.volume24h ?? 0,
              marketCap: token.marketCap ?? 0,
              priceChange1h: token.priceChange1h ?? 0,
              priceChange24h: token.priceChange24h ?? 0,
              liquidity: 0,
              priceChange5m: 0,
              priceChange15m: 0,
            },
          });
          cgUpserted++;
        } catch { /* skip */ }
      }

      // DexScreener: top 50 Solana tokens (for liquidity + DEX data)
      // NOTE: 'solana' is the DexScreener platform ID, NOT the chain value we store
      const dsChainId = 'solana';
      const pairs = await dexScreenerClient.getTopChainTokens(dsChainId, 50);
      let dsUpserted = 0;
      for (const pair of pairs) {
        try {
          const address = pair.baseToken?.address || pair.pairAddress;
          if (!address) continue;
          await db.token.upsert({
            where: { address },
            update: {
              symbol: pair.baseToken?.symbol || '???',
              name: pair.baseToken?.name || pair.baseToken?.symbol || 'Unknown',
              priceUsd: parseFloat(pair.priceUsd || '0'),
              volume24h: pair.volume?.h24 || 0,
              liquidity: pair.liquidity?.usd || 0,
              marketCap: pair.marketCap || pair.fdv || 0,
              priceChange24h: pair.priceChange?.h24 || 0,
              chain: 'SOL', // Canonical short code (was chain.toUpperCase() = 'SOLANA')
              dexId: pair.dexId,
              pairAddress: pair.pairAddress,
              dex: pair.dexId,
              pairUrl: `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}`,
            },
            create: {
              address,
              symbol: pair.baseToken?.symbol || '???',
              name: pair.baseToken?.name || pair.baseToken?.symbol || 'Unknown',
              priceUsd: parseFloat(pair.priceUsd || '0'),
              volume24h: pair.volume?.h24 || 0,
              liquidity: pair.liquidity?.usd || 0,
              marketCap: pair.marketCap || pair.fdv || 0,
              priceChange24h: pair.priceChange?.h24 || 0,
              chain: 'SOL', // Canonical short code (was chain.toUpperCase() = 'SOLANA')
              dexId: pair.dexId,
              pairAddress: pair.pairAddress,
              dex: pair.dexId,
              pairUrl: `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}`,
            },
          });
          dsUpserted++;
        } catch { /* skip */ }
      }
      steps.push({ step: '2. Token Scan', status: 'OK', detail: `CoinGecko: ${cgUpserted}, DexScreener: ${dsUpserted}` });
    } catch (e: any) {
      steps.push({ step: '2. Token Scan', status: 'ERROR', detail: e.message });
    }

    // Step 3: Compute Token DNA for all tokens (using real market data)
    try {
      const tokensWithoutDna = await db.token.findMany({
        where: { dna: { is: null } },
        take: 500,
      });

      let dnaCreated = 0;
      for (const token of tokensWithoutDna) {
        try {
          const pc24 = token.priceChange24h ?? 0;
          const liq = token.liquidity ?? 0;
          const mcap = token.marketCap ?? 0;
          const vol = token.volume24h ?? 0;

          // Risk score from REAL market data
          let volatilityRisk = 0;
          if (Math.abs(pc24) > 50) volatilityRisk = 40;
          else if (Math.abs(pc24) > 20) volatilityRisk = 30;
          else if (Math.abs(pc24) > 10) volatilityRisk = 20;
          else if (Math.abs(pc24) > 5) volatilityRisk = 10;

          let liquidityRisk = 0;
          if (liq > 0 && liq < 50000) liquidityRisk = 30;
          else if (liq > 0 && liq < 200000) liquidityRisk = 20;
          else if (liq > 0 && liq < 1000000) liquidityRisk = 10;
          else if (liq === 0 && vol > 0) liquidityRisk = 35;

          let mcapRisk = 0;
          if (mcap > 0 && mcap < 1000000) mcapRisk = 25;
          else if (mcap > 0 && mcap < 10000000) mcapRisk = 15;
          else if (mcap > 0 && mcap < 50000000) mcapRisk = 5;

          let washRisk = 0;
          if (liq > 0 && vol > 0) {
            const volLiqRatio = vol / liq;
            if (volLiqRatio > 10) washRisk = 20;
            else if (volLiqRatio > 5) washRisk = 15;
            else if (volLiqRatio > 2) washRisk = 5;
          }

          let momentumRisk = 0;
          if (pc24 < -30) momentumRisk = 25;
          else if (pc24 < -15) momentumRisk = 20;
          else if (pc24 < -5) momentumRisk = 10;

          let riskScore = 20 + volatilityRisk + liquidityRisk + mcapRisk + washRisk + momentumRisk;
          riskScore = Math.min(98, Math.max(5, riskScore));

          // Trader scores derived from REAL market data (not pseudo-random)
          // High volume/mcap ratio = more institutional participation
          // Low liquidity + high volume = bot/MEV activity
          // Large mcap + high liquidity = smart money + whale territory
          const volMcapRatio = mcap > 0 ? vol / mcap : 0;
          const volLiqRatio = liq > 0 ? vol / liq : 0;
          const liqMcapRatio = mcap > 0 ? liq / mcap : 0;

          // Smart Money Score: higher for established tokens with institutional volume
          const smartMoneyScore = Math.min(95,
            (mcap > 1e9 ? 40 : mcap > 1e8 ? 30 : mcap > 1e7 ? 15 : 5) +
            (volMcapRatio > 0.1 ? 25 : volMcapRatio > 0.05 ? 15 : 5) +
            (liqMcapRatio > 0.5 ? 15 : liqMcapRatio > 0.1 ? 10 : 3)
          );

          // Whale Score: higher for tokens with deep liquidity and large mcap
          const whaleScore = Math.min(90,
            (mcap > 1e9 ? 35 : mcap > 1e8 ? 25 : mcap > 1e7 ? 10 : 3) +
            (liq > 1e7 ? 25 : liq > 1e6 ? 15 : liq > 1e5 ? 5 : 1) +
            (volMcapRatio > 0.05 ? 15 : volMcapRatio > 0.01 ? 8 : 2)
          );

          // Bot Activity Score: higher for volatile, low-liquidity tokens
          const botActivityScore = Math.min(95,
            (Math.abs(pc24) > 20 ? 30 : Math.abs(pc24) > 10 ? 20 : 5) +
            (volLiqRatio > 5 ? 25 : volLiqRatio > 2 ? 15 : volLiqRatio > 1 ? 8 : 3) +
            (liq > 0 && liq < 1e5 ? 20 : liq > 0 && liq < 5e5 ? 10 : 3)
          );

          // Retail Score: higher for tokens with moderate metrics
          const retailScore = Math.min(90,
            (mcap > 1e6 && mcap < 1e9 ? 35 : mcap < 1e6 ? 20 : 10) +
            (Math.abs(pc24) < 10 ? 25 : Math.abs(pc24) < 20 ? 15 : 5) +
            (volLiqRatio < 2 ? 15 : 5)
          );

          // Wash Trade Probability: based on volume/liquidity anomalies
          const washTradeProb = Math.min(0.9,
            (volLiqRatio > 10 ? 0.5 : volLiqRatio > 5 ? 0.3 : volLiqRatio > 2 ? 0.1 : 0.02) +
            (mcap > 0 && liq > 0 && liq / mcap < 0.01 ? 0.2 : 0)
          );

          // Sniper %: higher for new, volatile tokens
          const sniperPct = Math.min(50,
            (Math.abs(pc24) > 30 ? 20 : Math.abs(pc24) > 15 ? 10 : 2) +
            (liq > 0 && liq < 5e4 ? 15 : liq > 0 && liq < 2e5 ? 8 : 1)
          );

          // MEV %: higher for high-volume DEX tokens
          const mevPct = Math.min(40,
            (volLiqRatio > 5 ? 15 : volLiqRatio > 2 ? 8 : 2) +
            (Math.abs(pc24) > 15 ? 10 : 3)
          );

          // Copy Bot %: moderate for trending tokens
          const copyBotPct = Math.min(30,
            (volMcapRatio > 0.2 ? 12 : volMcapRatio > 0.1 ? 6 : 2) +
            (Math.abs(pc24) > 10 ? 8 : 2)
          );

          // Trader composition from risk profile
          const traderComposition = JSON.stringify({
            smartMoney: Math.round(smartMoneyScore / 10),
            whale: Math.round(whaleScore / 10),
            bot_mev: Math.round(mevPct / 2),
            bot_sniper: Math.round(sniperPct / 2),
            bot_copy: Math.round(copyBotPct),
            retail: Math.round(retailScore / 5),
          });

          await db.tokenDNA.create({
            data: {
              tokenId: token.id,
              riskScore,
              botActivityScore: Math.round(botActivityScore * 100) / 100,
              smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
              retailScore: Math.round(retailScore * 100) / 100,
              whaleScore: Math.round(whaleScore * 100) / 100,
              washTradeProb: Math.round(washTradeProb * 1000) / 1000,
              sniperPct: Math.round(sniperPct * 100) / 100,
              mevPct: Math.round(mevPct * 100) / 100,
              copyBotPct: Math.round(copyBotPct * 100) / 100,
              traderComposition,
              topWallets: JSON.stringify([]),
            },
          });
          dnaCreated++;
        } catch { /* skip duplicates */ }
      }
      steps.push({ step: '3. Token DNA', status: 'OK', detail: `${dnaCreated}/${tokensWithoutDna.length} DNA computed` });
    } catch (e: any) {
      steps.push({ step: '3. Token DNA', status: 'ERROR', detail: e.message });
    }

    // Step 4: Generate signals from real market data + technical indicators
    try {
      const { generateAllSignalsWithTechnicals, saveSignalsToDb } = await import('@/lib/services/strategy/signal-generators');

      const allTokens = await db.token.findMany({
        where: { volume24h: { gt: 0 } },
        orderBy: { volume24h: 'desc' },
        take: 200,
      });

      if (allTokens.length > 0) {
        const tokensWithMarketData = allTokens.map(token => ({
          tokenId: token.id,
          marketData: {
            symbol: token.symbol,
            name: token.name,
            chain: token.chain,
            priceUsd: token.priceUsd,
            volume24h: token.volume24h,
            liquidityUsd: token.liquidity,
            marketCap: token.marketCap,
            fdv: token.marketCap,
            priceChange1h: token.priceChange1h,
            priceChange6h: 0,
            priceChange24h: token.priceChange24h,
            txns24h: { buys: 0, sells: 0 },
            pairCreatedAt: 0,
            dexId: '',
          },
        }));

        const signals = await generateAllSignalsWithTechnicals(tokensWithMarketData);
        const saved = await saveSignalsToDb(signals);
        steps.push({ step: '4. Signals', status: 'OK', detail: `${signals.length} generated, ${saved} saved` });
      } else {
        steps.push({ step: '4. Signals', status: 'SKIP', detail: 'No tokens with volume > 0' });
      }
    } catch (e: any) {
      steps.push({ step: '4. Signals', status: 'ERROR', detail: e.message });
    }

    // Step 5: Start Brain Scheduler
    try {
      const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');
      const wasRunning = brainScheduler.getStatus().status === 'RUNNING';

      if (!wasRunning) {
        await brainScheduler.start({
          capitalUsd: 10,
          initialCapitalUsd: 10,
          chain: 'SOL',
          scanLimit: 50,
        });
      }
      steps.push({
        step: '5. Scheduler',
        status: 'OK',
        detail: wasRunning ? 'Already running' : 'Started',
      });
    } catch (e: any) {
      steps.push({ step: '5. Scheduler', status: 'ERROR', detail: e.message });
    }

    // Step 6: OHLCV Backfill for top 30 tokens (creates candles)
    try {
      const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
      const result = await ohlcvPipeline.backfillTopTokens(30);
      steps.push({ step: '6. OHLCV Backfill', status: 'OK', detail: `${result.totalCandlesStored} candles for ${result.totalTokens} tokens (${result.failedTokens.length} failed)` });
    } catch (e: any) {
      steps.push({ step: '6. OHLCV Backfill', status: 'ERROR', detail: e.message });
    }

    // Step 7: Run first brain cycle (background - don't await)
    try {
      fetch(new URL('/api/brain/pipeline', request.url), { method: 'POST' }).catch(() => {});
      steps.push({ step: '7. First Cycle', status: 'STARTED', detail: 'Running in background' });
    } catch (e: any) {
      steps.push({ step: '7. First Cycle', status: 'ERROR', detail: e.message });
    }

    // Step 8: Get current DB stats
    let dbStats: Record<string, number> = {};
    try {
      const [tokens, candles, signals, traders, dna, patterns] = await Promise.all([
        db.token.count().catch(() => 0),
        db.priceCandle.count().catch(() => 0),
        db.signal.count().catch(() => 0),
        db.trader.count().catch(() => 0),
        db.tokenDNA.count().catch(() => 0),
        db.signal.count({ where: { type: 'PATTERN' } }).catch(() => 0),
      ]);
      dbStats = { tokens, candles, signals, traders, dna, patterns };
    } catch {
      // Stats not critical
    }

    const hasErrors = steps.some(s => s.status === 'ERROR');
    return NextResponse.json({
      success: !hasErrors,
      message: hasErrors ? 'Some steps failed' : 'All systems started!',
      steps,
      dbStats,
    });
  } catch (error: any) {
    return NextResponse.json({
      success: false,
      error: error.message,
      steps,
    }, { status: 500 });
  }
}
