import { create } from 'zustand';
import type { DeepAnalysis as DeepAnalysisType, ThinkingDepth } from '@/lib/services/strategy/deep-analysis-engine';
import type { DeepAnalysisResult } from '@/lib/services/strategy/deep-analysis-engine';

// The store works with either DeepAnalysis (rich UI format) or DeepAnalysisResult (API format)
export type DeepAnalysis = DeepAnalysisType & Partial<DeepAnalysisResult>;

// ============================================================
// NORMALIZATION — ensures all nested objects exist
// ============================================================

function normalizeAnalysis(raw: Record<string, unknown>): DeepAnalysis {
  const a = raw as any;

  // Build verdict from either DeepAnalysis.verdict or DeepAnalysisResult fields
  const verdict = (raw.verdict && typeof raw.verdict === 'object') ? raw.verdict : {
    action: a.recommendation || 'HOLD',
    confidence: a.recommendationConfidence || 0.5,
    reasoning: a.summary || a.justification?.join('. ') || '',
    summary: a.summary || '',
  };

  // Build phaseAssessment
  const phaseAssessment = (raw.phaseAssessment && typeof raw.phaseAssessment === 'object') ? raw.phaseAssessment : {
    phase: 'GROWTH',
    confidence: a.recommendationConfidence || 0.5,
    timeInPhase: a.suggestedTimeHorizon || 'Unknown',
    narrative: a.scenarios?.base?.description || '',
  };

  // Build patternAssessment
  const patternAssessment = (raw.patternAssessment && typeof raw.patternAssessment === 'object') ? raw.patternAssessment : {
    dominantPattern: null,
    patternSentiment: 'NEUTRAL',
    multiTfConfirmed: false,
    narrative: '',
  };

  // Build traderAssessment
  const traderAssessment = (raw.traderAssessment && typeof raw.traderAssessment === 'object') ? raw.traderAssessment : {
    dominantArchetype: 'UNKNOWN',
    behaviorFlow: 'NEUTRAL',
    riskFromBots: 'LOW',
    riskFromWhales: 'MODERATE',
    narrative: '',
  };

  // Build riskAssessment — could be string (from DeepAnalysisResult) or object (from DeepAnalysis)
  const riskAssessment = (raw.riskAssessment && typeof raw.riskAssessment === 'object') ? raw.riskAssessment : {
    overallRisk: a.riskLevel || 'MEDIUM',
    keyRisks: a.bearishFactors || [],
    mitigatingFactors: a.bullishFactors || [],
    blackSwanRisk: 'LOW',
  };

  // Build strategyRecommendation
  const strategyRecommendation = (raw.strategyRecommendation && typeof raw.strategyRecommendation === 'object') ? raw.strategyRecommendation : {
    strategy: 'WAIT_AND_MONITOR',
    direction: 'NEUTRAL',
    confidenceLevel: 0.5,
    positionSizeRecommendation: '5%',
    stopLossRecommendation: '-10%',
    takeProfitRecommendation: '15%',
    entryConditions: [],
    exitConditions: [],
  };

  // Build evidence lists
  const pros = Array.isArray(raw.pros) ? raw.pros : (a.bullishFactors || []).map((f: string) => ({ factor: f, weight: 0.7, explanation: f }));
  const cons = Array.isArray(raw.cons) ? raw.cons : (a.bearishFactors || []).map((f: string) => ({ factor: f, weight: 0.6, explanation: f }));
  const neutrals = Array.isArray(raw.neutrals) ? raw.neutrals : (a.neutralFactors || []).map((f: string) => ({ factor: f, weight: 0.5, explanation: f }));
  const reasoningChain = Array.isArray(raw.reasoningChain) ? raw.reasoningChain : a.justification || [];

  return {
    ...raw,
    verdict,
    phaseAssessment,
    patternAssessment,
    traderAssessment,
    riskAssessment,
    strategyRecommendation,
    pros,
    cons,
    neutrals,
    reasoningChain,
  } as DeepAnalysis;
}

// ============================================================
// TYPES
// ============================================================

export type AnalysisStatus = 'idle' | 'loading' | 'success' | 'error';

export interface DeepAnalysisState {
  // Analysis result
  analysis: DeepAnalysis | null;
  status: AnalysisStatus;
  error: string | null;

  // Form inputs
  tokenAddress: string;
  chain: string;
  depth: ThinkingDepth;

  // UI state
  showReasoningChain: boolean;

  // History
  analysisHistory: DeepAnalysis[];

  // Actions
  setAnalysis: (analysis: DeepAnalysis) => void;
  setStatus: (status: AnalysisStatus) => void;
  setError: (error: string | null) => void;
  setTokenAddress: (tokenAddress: string) => void;
  setChain: (chain: string) => void;
  setDepth: (depth: ThinkingDepth) => void;
  toggleReasoningChain: () => void;
  reset: () => void;
  runAnalysis: () => Promise<void>;
}

// ============================================================
// STORE
// ============================================================

export const useDeepAnalysisStore = create<DeepAnalysisState>((set, get) => ({
  // Analysis result
  analysis: null,
  status: 'idle',
  error: null,

  // Form inputs
  tokenAddress: '',
  chain: 'AUTO',
  depth: 'STANDARD',

  // UI state
  showReasoningChain: false,

  // History
  analysisHistory: [],

  // Actions
  setAnalysis: (analysis) =>
    set((state) => ({
      analysis: normalizeAnalysis(analysis as unknown as Record<string, unknown>),
      status: 'success',
      error: null,
      analysisHistory: [normalizeAnalysis(analysis as unknown as Record<string, unknown>), ...state.analysisHistory].slice(0, 10),
    })),

  setStatus: (status) => set({ status }),
  setError: (error) => set({ error, status: error ? 'error' : 'idle' }),
  setTokenAddress: (tokenAddress) => set({ tokenAddress }),
  setChain: (chain) => set({ chain }),
  setDepth: (depth) => set({ depth }),
  toggleReasoningChain: () =>
    set((state) => ({ showReasoningChain: !state.showReasoningChain })),

  reset: () =>
    set({
      analysis: null,
      status: 'idle',
      error: null,
      tokenAddress: '',
      showReasoningChain: false,
    }),

  runAnalysis: async () => {
    const { tokenAddress, chain, depth } = get();
    if (!tokenAddress.trim()) {
      set({ error: 'Token address is required', status: 'error' });
      return;
    }

    set({ status: 'loading', error: null });

    try {
      // If AUTO, try to detect chain from the address or let the API figure it out
      let resolvedChain = chain;
      if (chain === 'AUTO') {
        // Try DexScreener to detect the chain first
        try {
          const detectRes = await fetch(`/api/deep-analysis/detect-chain?address=${encodeURIComponent(tokenAddress.trim())}`);
          if (detectRes.ok) {
            const detectData = await detectRes.json();
            if (detectData.chain) {
              resolvedChain = detectData.chain;
            }
          }
        } catch {
          // Detection failed, fall back to letting the API try all chains
        }
        // If still AUTO, let the API handle it (it will try DexScreener)
        if (resolvedChain === 'AUTO') {
          resolvedChain = 'SOL'; // Default fallback
        }
      }

      const res = await fetch('/api/deep-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tokenAddress: tokenAddress.trim(),
          chain: resolvedChain,
          depth,
          autoDetect: chain === 'AUTO',
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Analysis failed (${res.status})`);
      }

      const data = await res.json();
      if (data.success && data.analysis) {
        // If auto-detected, update the chain in the store
        if (chain === 'AUTO' && data.analysis.chain) {
          set({ chain: data.analysis.chain });
        }
        get().setAnalysis(data.analysis);
      } else {
        throw new Error(data.error || 'Invalid response from analysis engine');
      }
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : 'Unknown error',
        status: 'error',
      });
    }
  },
}));
