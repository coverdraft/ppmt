'use client';

import { useCryptoStore, type SignalData } from '@/store/crypto-store';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

const SIGNAL_TYPES = ['RUG_PULL', 'SMART_MONEY_ENTRY', 'LIQUIDITY_TRAP', 'V_SHAPE', 'DIVERGENCE', 'CUSTOM'];
const DIRECTIONS = ['LONG', 'SHORT', 'AVOID'];

const SIGNAL_DESCRIPTIONS: Record<string, string[]> = {
  RUG_PULL: [
    'Liquidity removal detected — 40% of pool drained in last 5 blocks',
    'Creator wallet moving tokens to exchange — high probability exit scam',
    'Dev wallet distributing to multiple wallets — rug preparation pattern',
  ],
  SMART_MONEY_ENTRY: [
    'Top-10 smart money wallet accumulated 2.3% of supply',
    '3 wallets with >85% win rate entered within 10 minutes',
    'Institutional-grade accumulation pattern detected',
  ],
  LIQUIDITY_TRAP: [
    'False breakout pattern — liquidity above range, stops likely to be hunted',
    'Concentrated liquidity at key level — stop hunt imminent',
    'Equal highs/lows formed — liquidity pool targeting retail stops',
  ],
  V_SHAPE: [
    'Sharp rejection from support with volume spike — V-shape forming',
    'Capitulation candle followed by aggressive buying — reversal pattern',
    'Liquidation cascade complete — strong bid wall absorbing sells',
  ],
  DIVERGENCE: [
    'Price making lower lows while RSI making higher lows — bullish divergence',
    'On-chain divergence — price up but smart money exiting',
    'MACD divergence on 4H timeframe — trend exhaustion signal',
  ],
  CUSTOM: [
    'Multi-signal confluence — 3 indicators aligned',
    'Pattern break with volume confirmation',
  ],
};

function randomChoice<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomBetween(min: number, max: number) {
  return Math.random() * (max - min) + min;
}

export function SimulationProvider({ children }: { children: React.ReactNode }) {
  const tokens = useCryptoStore((s) => s.tokens);
  const addSignal = useCryptoStore((s) => s.addSignal);
  const setMarketSummary = useCryptoStore((s) => s.setMarketSummary);
  const isConnected = useCryptoStore((s) => s.isConnected);
  const hasLoadedRef = useRef(false);
  const brainInitRef = useRef(false);

  // Auto-initialize DISABLED: was causing OOM crashes by re-seeding 5000+ tokens on every page load.
  // Use the Sync button in the UI or run: curl http://localhost:3000/api/brain/init
  // useEffect(() => {
  //   if (!brainInitRef.current) {
  //     brainInitRef.current = true;
  //     fetch('/api/brain/init').catch(() => {});
  //   }
  // }, []);

  // Fetch real market summary from CoinGecko
  const { data: marketSummaryData } = useQuery({
    queryKey: ['market-summary'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/market/summary');
        if (!res.ok) throw new Error('Failed');
        const json = await res.json();
        return json.data as { btcPrice: number; ethPrice: number; solPrice: number; totalMarketCap: number; fearGreedIndex: number } | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 120000, // Refresh every 120s (reduced for stability)
    staleTime: 90000,
  });

  // Update store with real market summary
  useEffect(() => {
    if (marketSummaryData && marketSummaryData.btcPrice > 0) {
      setMarketSummary({
        btcPrice: marketSummaryData.btcPrice,
        ethPrice: marketSummaryData.ethPrice,
        totalMarketCap: marketSummaryData.totalMarketCap,
        fearGreedIndex: marketSummaryData.fearGreedIndex || 50,
      });
    }
  }, [marketSummaryData, setMarketSummary]);

  // Load initial signals from API
  const { data: signalsData } = useQuery({
    queryKey: ['signals'],
    queryFn: async () => {
      const res = await fetch('/api/signals?limit=20');
      return res.json();
    },
    staleTime: 60000,
  });

  // Convert API signals to store format
  useEffect(() => {
    if (signalsData?.signals && !hasLoadedRef.current) {
      hasLoadedRef.current = true;
      const apiSignals: SignalData[] = signalsData.signals.map((s: any) => ({
        id: s.id,
        type: s.type,
        tokenId: s.token?.symbol || s.tokenId,
        tokenSymbol: s.token?.symbol || '???',
        tokenPrice: s.token?.priceUsd || 0,
        chain: s.token?.chain || 'SOL',
        confidence: s.confidence,
        direction: s.direction,
        description: s.description,
        priceTarget: s.priceTarget,
        timestamp: new Date(s.createdAt).getTime(),
      }));
      apiSignals.reverse().forEach((sig) => addSignal(sig));
    }
  }, [signalsData, addSignal]);

  // Simulate signals only when we have tokens and WS is NOT connected
  useEffect(() => {
    if (tokens.length === 0) return;
    if (isConnected) return;

    // Generate signals every 15 seconds (reduced frequency for stability)
    const signalInterval = setInterval(() => {
      const type = randomChoice(SIGNAL_TYPES);
      const token = randomChoice(tokens);
      const direction = type === 'RUG_PULL' ? 'AVOID' : randomChoice(DIRECTIONS);
      const descriptions = SIGNAL_DESCRIPTIONS[type] || SIGNAL_DESCRIPTIONS.CUSTOM;

      addSignal({
        id: `sim_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`,
        type,
        tokenId: token.symbol,
        tokenSymbol: token.symbol,
        tokenPrice: token.priceUsd,
        chain: token.chain,
        confidence: Math.floor(randomBetween(35, 98)),
        direction,
        description: randomChoice(descriptions),
        priceTarget: token.priceUsd * randomBetween(0.8, 1.4),
        timestamp: Date.now(),
      });
    }, 15000);

    return () => {
      clearInterval(signalInterval);
    };
  }, [tokens.length, isConnected, addSignal]);

  return <>{children}</>;
}
