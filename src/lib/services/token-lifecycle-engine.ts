/**
 * Token Lifecycle Detection Engine - CryptoQuant Terminal
 * Motor de Detección del Ciclo de Vida de Tokens
 *
 * Clasifica cada token en una de 6 fases de ciclo de vida basándose
 * en múltiples señales cuantitativas. Es un componente central de la
 * arquitectura Big Data que alimenta al motor predictivo y a los
 * sistemas de trading con contexto de fase.
 *
 * Fases del ciclo de vida:
 *   GENESIS   → Token recién creado (<1h), alta actividad bot, liquidez nula
 *   INCIPIENT → Token emergente (<24h), bots dominan, smart money entrando
 *   GROWTH    → Token en crecimiento (<30d), liquidez construyéndose, SM activo
 *   FOMO      → Euforia retail, liquidez alta, bots bajos, SM distribuyendo
 *   DECLINE   → Pérdida de interés, liquidez drenándose, SM saliendo
 *   LEGACY    → Token establecido (>6mo), liquidez alta, baja actividad bot
 *
 * Señales ponderadas:
 *   ageScore               (15%) - Tiempo desde creación del token
 *   liquidityScore         (20%) - Nivel y tendencia de liquidez
 *   botRatioScore          (15%) - Porcentaje de actividad bot
 *   smartMoneyFlowScore    (15%) - Porcentaje de flujo smart money
 *   holderVelocityScore    (10%) - Velocidad de nuevos holders
 *   volumeDistributionScore(10%) - Concentración HHI del volumen horario
 *   volatilityScore        (10%) - ATR normalizado por precio
 *   rugScore               ( 5%) - Verificación básica de seguridad
 */

import { db } from '../db';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Las 6 fases del ciclo de vida de un token */
export type TokenPhase = 'GENESIS' | 'INCIPIENT' | 'GROWTH' | 'FOMO' | 'DECLINE' | 'LEGACY';

/** Arquetipos de traders que interactúan con tokens */
export type TraderArchetype =
  | 'SMART_MONEY'
  | 'WHALE'
  | 'SNIPER'
  | 'RETAIL_FOMO'
  | 'RETAIL_HOLDER'
  | 'SCALPER'
  | 'DEGEN'
  | 'CONTRARIAN';

/** Conjunto válido de fases para validación en tiempo de ejecución */
const VALID_PHASES: TokenPhase[] = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'];

/** Resultado completo de la detección de fase */
export interface PhaseDetectionResult {
  phase: TokenPhase;
  probability: number; // 0-1 confianza en la fase detectada
  distribution: Record<TokenPhase, number>; // Probabilidad por cada fase
  signals: {
    ageScore: number;
    liquidityScore: number;
    botRatioScore: number;
    smartMoneyFlowScore: number;
    holderVelocityScore: number;
    volumeDistributionScore: number;
    volatilityScore: number;
    rugScore: number;
  };
  transitionFrom?: TokenPhase;
  transitionProbability?: number;
}

/** Resultado de una transición detectada entre fases */
export interface TransitionResult {
  from: TokenPhase;
  to: TokenPhase;
  probability: number;
  timestamp: Date;
}

/** Configuración de señales específica por fase para el big-data-engine */
export interface PhaseSignalConfig {
  primarySignal: string;
  primaryWeight: number;
  secondarySignal: string;
  secondaryWeight: number;
  tertiarySignal: string;
  tertiaryWeight: number;
  optimalHorizon: string;
  phaseSpecificWeights: Record<string, number>;
}

// ============================================================
// PHASE REFERENCE PATTERNS (Patrones de referencia por fase)
// ============================================================

/**
 * Cada fase tiene un perfil ideal de señales. La detección compara
 * las señales calculadas del token contra estos patrones de referencia
 * usando distancia euclidiana normalizada para asignar probabilidades.
 */
const PHASE_PATTERNS: Record<TokenPhase, {
  ageScore: number;
  liquidityScore: number;
  botRatioScore: number;
  smartMoneyFlowScore: number;
  holderVelocityScore: number;
  volumeDistributionScore: number;
  volatilityScore: number;
  rugScore: number;
}> = {
  GENESIS: {
    ageScore: 1.0,
    liquidityScore: 0.05,
    botRatioScore: 0.9,
    smartMoneyFlowScore: 0.1,
    holderVelocityScore: 0.9,
    volumeDistributionScore: 0.8,
    volatilityScore: 0.95,
    rugScore: 0.7,
  },
  INCIPIENT: {
    ageScore: 0.85,
    liquidityScore: 0.15,
    botRatioScore: 0.6,
    smartMoneyFlowScore: 0.2,
    holderVelocityScore: 0.7,
    volumeDistributionScore: 0.6,
    volatilityScore: 0.75,
    rugScore: 0.5,
  },
  GROWTH: {
    ageScore: 0.5,
    liquidityScore: 0.4,
    botRatioScore: 0.3,
    smartMoneyFlowScore: 0.5,
    holderVelocityScore: 0.5,
    volumeDistributionScore: 0.35,
    volatilityScore: 0.45,
    rugScore: 0.25,
  },
  FOMO: {
    ageScore: 0.3,
    liquidityScore: 0.7,
    botRatioScore: 0.15,
    smartMoneyFlowScore: 0.3,
    holderVelocityScore: 0.8,
    volumeDistributionScore: 0.5,
    volatilityScore: 0.65,
    rugScore: 0.15,
  },
  DECLINE: {
    ageScore: 0.2,
    liquidityScore: 0.2,
    botRatioScore: 0.2,
    smartMoneyFlowScore: 0.15,
    holderVelocityScore: 0.15,
    volumeDistributionScore: 0.7,
    volatilityScore: 0.3,
    rugScore: 0.4,
  },
  LEGACY: {
    ageScore: 0.0,
    liquidityScore: 0.8,
    botRatioScore: 0.1,
    smartMoneyFlowScore: 0.4,
    holderVelocityScore: 0.1,
    volumeDistributionScore: 0.15,
    volatilityScore: 0.1,
    rugScore: 0.05,
  },
};

/** Pesos de cada señal en la detección global de fase */
const SIGNAL_WEIGHTS: Record<string, number> = {
  ageScore: 0.15,
  liquidityScore: 0.20,
  botRatioScore: 0.15,
  smartMoneyFlowScore: 0.15,
  holderVelocityScore: 0.10,
  volumeDistributionScore: 0.10,
  volatilityScore: 0.10,
  rugScore: 0.05,
};

/** Configuración de señales optimizada por fase para el big-data-engine */
const PHASE_SIGNAL_CONFIGS: Record<TokenPhase, PhaseSignalConfig> = {
  GENESIS: {
    primarySignal: 'botRatioScore',
    primaryWeight: 0.30,
    secondarySignal: 'ageScore',
    secondaryWeight: 0.25,
    tertiarySignal: 'volatilityScore',
    tertiaryWeight: 0.20,
    optimalHorizon: '5m-15m',
    phaseSpecificWeights: {
      ageScore: 0.15,
      liquidityScore: 0.05,
      botRatioScore: 0.30,
      smartMoneyFlowScore: 0.10,
      holderVelocityScore: 0.15,
      volumeDistributionScore: 0.10,
      volatilityScore: 0.10,
      rugScore: 0.05,
    },
  },
  INCIPIENT: {
    primarySignal: 'botRatioScore',
    primaryWeight: 0.25,
    secondarySignal: 'smartMoneyFlowScore',
    secondaryWeight: 0.20,
    tertiarySignal: 'ageScore',
    tertiaryWeight: 0.20,
    optimalHorizon: '15m-1h',
    phaseSpecificWeights: {
      ageScore: 0.15,
      liquidityScore: 0.10,
      botRatioScore: 0.25,
      smartMoneyFlowScore: 0.20,
      holderVelocityScore: 0.10,
      volumeDistributionScore: 0.08,
      volatilityScore: 0.07,
      rugScore: 0.05,
    },
  },
  GROWTH: {
    primarySignal: 'smartMoneyFlowScore',
    primaryWeight: 0.25,
    secondarySignal: 'liquidityScore',
    secondaryWeight: 0.20,
    tertiarySignal: 'holderVelocityScore',
    tertiaryWeight: 0.15,
    optimalHorizon: '1h-4h',
    phaseSpecificWeights: {
      ageScore: 0.10,
      liquidityScore: 0.20,
      botRatioScore: 0.10,
      smartMoneyFlowScore: 0.25,
      holderVelocityScore: 0.15,
      volumeDistributionScore: 0.08,
      volatilityScore: 0.07,
      rugScore: 0.05,
    },
  },
  FOMO: {
    primarySignal: 'liquidityScore',
    primaryWeight: 0.25,
    secondarySignal: 'holderVelocityScore',
    secondaryWeight: 0.20,
    tertiarySignal: 'volatilityScore',
    tertiaryWeight: 0.15,
    optimalHorizon: '15m-1h',
    phaseSpecificWeights: {
      ageScore: 0.05,
      liquidityScore: 0.25,
      botRatioScore: 0.10,
      smartMoneyFlowScore: 0.15,
      holderVelocityScore: 0.20,
      volumeDistributionScore: 0.10,
      volatilityScore: 0.10,
      rugScore: 0.05,
    },
  },
  DECLINE: {
    primarySignal: 'liquidityScore',
    primaryWeight: 0.25,
    secondarySignal: 'smartMoneyFlowScore',
    secondaryWeight: 0.20,
    tertiarySignal: 'rugScore',
    tertiaryWeight: 0.15,
    optimalHorizon: '4h-1d',
    phaseSpecificWeights: {
      ageScore: 0.05,
      liquidityScore: 0.25,
      botRatioScore: 0.10,
      smartMoneyFlowScore: 0.20,
      holderVelocityScore: 0.10,
      volumeDistributionScore: 0.10,
      volatilityScore: 0.10,
      rugScore: 0.10,
    },
  },
  LEGACY: {
    primarySignal: 'liquidityScore',
    primaryWeight: 0.30,
    secondarySignal: 'smartMoneyFlowScore',
    secondaryWeight: 0.20,
    tertiarySignal: 'volatilityScore',
    tertiaryWeight: 0.15,
    optimalHorizon: '1d-1w',
    phaseSpecificWeights: {
      ageScore: 0.05,
      liquidityScore: 0.30,
      botRatioScore: 0.05,
      smartMoneyFlowScore: 0.20,
      holderVelocityScore: 0.10,
      volumeDistributionScore: 0.10,
      volatilityScore: 0.15,
      rugScore: 0.05,
    },
  },
};

// ============================================================
// HELPER FUNCTIONS (Funciones auxiliares)
// ============================================================

/**
 * Calcula la edad del token en minutos desde su fecha de creación.
 * Returns age in minutes; 0 if createdAt is in the future (data error).
 */
function getTokenAgeMinutes(createdAt: Date): number {
  const now = Date.now();
  const created = new Date(createdAt).getTime();
  const ageMs = Math.max(0, now - created);
  return ageMs / (1000 * 60);
}

/**
 * Convierte la edad del token en un score [0-1] donde:
 *   1.0 = recién nacido (GENESIS)
 *   0.0 = token antiguo (LEGACY)
 *
 * Escala logarítmica para capturar la diferencia crítica entre
 * los primeros minutos/horas vs. meses.
 */
function computeAgeScore(ageMinutes: number): number {
  if (ageMinutes < 60) return 1.0;               // <1h → GENESIS
  if (ageMinutes < 1440) return 0.85 - (ageMinutes / 1440) * 0.15; // <24h → INCIPIENT
  if (ageMinutes < 43200) return 0.5 - ((ageMinutes - 1440) / 41600) * 0.35; // <30d → GROWTH
  if (ageMinutes < 262800) return 0.15 - ((ageMinutes - 43200) / 219600) * 0.15; // <6mo → DECLINE zone
  return 0.0; // >6mo → LEGACY
}

/**
 * Calcula el score de liquidez [0-1] basado en:
 *   - Nivel absoluto de liquidez (normalizado)
 *   - Tendencia de liquidez (comparando candles recientes)
 *
 * Alta liquidez = score alto (FOMO/LEGACY)
 * Baja liquidez = score bajo (GENESIS/INCIPIENT)
 */
function computeLiquidityScore(
  liquidity: number,
  candles: Array<{ close: number; volume: number }>
): number {
  // Nivel absoluto: usamos escala logarítmica
  // $100K+ → alta liquidez, <$1K → muy baja
  let levelScore = 0;
  if (liquidity <= 0) {
    levelScore = 0;
  } else {
    levelScore = Math.min(1, Math.log10(liquidity + 1) / 6); // log10($1M) ≈ 6
  }

  // Tendencia: comparar volumen promedio reciente vs. más antiguo
  let trendScore = 0.5; // Neutral si no hay suficientes candles
  if (candles.length >= 10) {
    const recentCount = Math.min(5, Math.floor(candles.length / 2));
    const olderCandles = candles.slice(0, candles.length - recentCount);
    const recentCandles = candles.slice(candles.length - recentCount);

    const olderAvgVol = olderCandles.reduce((s, c) => s + c.volume, 0) / olderCandles.length;
    const recentAvgVol = recentCandles.reduce((s, c) => s + c.volume, 0) / recentCandles.length;

    if (olderAvgVol > 0) {
      const volChange = (recentAvgVol - olderAvgVol) / olderAvgVol;
      // Tendencia positiva → score más alto; negativa → más bajo
      trendScore = Math.max(0, Math.min(1, 0.5 + volChange * 0.5));
    }
  }

  // Combinar nivel (70%) y tendencia (30%)
  return levelScore * 0.7 + trendScore * 0.3;
}

/**
 * Calcula el score de ratio bot [0-1] donde:
 *   1.0 = dominio bot completo (GENESIS/INCIPIENT)
 *   0.0 = sin actividad bot (FOMO/LEGACY)
 *
 * Usa botActivityPct del Token o del TokenDNA.
 */
function computeBotRatioScore(botActivityPct: number, dnaBotScore?: number): number {
  // Combinar ambas fuentes si están disponibles, priorizando DNA
  const botPct = dnaBotScore !== undefined && dnaBotScore > 0
    ? (botActivityPct + dnaBotScore) / 2
    : botActivityPct;

  // Normalizar de 0-100 a 0-1
  return Math.max(0, Math.min(1, botPct / 100));
}

/**
 * Calcula el score de flujo smart money [0-1] donde:
 *   Valores altos (0.4-0.6) = SM activo (GROWTH)
 *   Valores bajos = SM ausente o distribuyendo
 *
 * Flujo SM en GENESIS es bajo (SM no ha entrado aún).
 * Flujo SM en GROWTH es alto (SM está acumulando).
 * Flujo SM en FOMO decrece (SM está distribuyendo).
 */
function computeSmartMoneyFlowScore(smartMoneyPct: number, dnaSmScore?: number): number {
  // Combinar fuentes con prioridad DNA
  const smPct = dnaSmScore !== undefined && dnaSmScore > 0
    ? (smartMoneyPct + dnaSmScore) / 2
    : smartMoneyPct;

  // Normalizar de 0-100 a 0-1
  return Math.max(0, Math.min(1, smPct / 100));
}

/**
 * Calcula la velocidad de holders [0-1] donde:
 *   1.0 = muchos nuevos holders rápidamente (GENESIS/FOMO)
 *   0.0 = crecimiento estancado (LEGACY/DECLINE)
 *
 * Ratio: uniqueWallets24h / holderCount
 */
function computeHolderVelocityScore(uniqueWallets24h: number, holderCount: number): number {
  if (holderCount <= 0) return uniqueWallets24h > 0 ? 1.0 : 0.0;

  const velocity = uniqueWallets24h / holderCount;
  // Escala: >50% = crecimiento explosivo, <5% = estancado
  return Math.max(0, Math.min(1, velocity * 2)); // 50% → 1.0
}

/**
 * Calcula la distribución de volumen usando HHI (Herfindahl-Hirschman Index).
 *
 * HHI = Σ(share_i²) donde share_i = volumen_hora_i / volumen_total
 *
 * HHI alto = volumen concentrado en pocas horas (GENESIS/bots)
 * HHI bajo = volumen distribuido uniformemente (LEGACY/maduro)
 */
function computeVolumeDistributionScore(
  candles: Array<{ volume: number }>
): number {
  if (candles.length < 3) return 0.5; // Sin datos suficientes, neutro

  const totalVolume = candles.reduce((s, c) => s + c.volume, 0);
  if (totalVolume <= 0) return 0.5;

  // Calcular shares y HHI
  let hhi = 0;
  for (const candle of candles) {
    const share = candle.volume / totalVolume;
    hhi += share * share;
  }

  // HHI: 1/N (uniforme) a 1.0 (monopolio)
  // Normalizar a [0-1] donde 1 = concentrado (GENESIS), 0 = distribuido (LEGACY)
  const minHHI = 1 / candles.length;
  const normalizedHHI = (hhi - minHHI) / (1 - minHHI + 0.001);

  return Math.max(0, Math.min(1, normalizedHHI));
}

/**
 * Calcula el score de volatilidad [0-1] usando ATR normalizado.
 *
 * ATR = Average True Rate (promedio de rangos verdaderos)
 * Normalizado por precio actual para comparabilidad entre tokens.
 *
 * Alta volatilidad = GENESIS/INCIPIENT/FOMO
 * Baja volatilidad = LEGACY
 */
function computeVolatilityScore(
  candles: Array<{ open: number; high: number; low: number; close: number }>,
  currentPrice: number
): number {
  if (candles.length < 2 || currentPrice <= 0) return 0.5;

  // Calcular True Ranges
  const trueRanges: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1];
    const curr = candles[i];
    const tr = Math.max(
      curr.high - curr.low,
      Math.abs(curr.high - prev.close),
      Math.abs(curr.low - prev.close)
    );
    trueRanges.push(tr);
  }

  if (trueRanges.length === 0) return 0.5;

  // ATR con suavizado Wilder (14 períodos o los disponibles)
  const period = Math.min(14, trueRanges.length);
  let atr = trueRanges.slice(0, period).reduce((s, v) => s + v, 0) / period;
  for (let i = period; i < trueRanges.length; i++) {
    atr = (atr * (period - 1) + trueRanges[i]) / period;
  }

  // Normalizar ATR por precio: ATR% = ATR / Price * 100
  const atrPct = (atr / currentPrice) * 100;

  // Escala: >20% = extremo (1.0), <2% = muy estable (0.0)
  return Math.max(0, Math.min(1, atrPct / 20));
}

/**
 * Calcula el score de riesgo rug [0-1] donde:
 *   1.0 = alta probabilidad de rug pull
 *   0.0 = token seguro
 *
 * Verificación básica: liquidez, holders, concentración bot,
 * y ausencia de smart money.
 */
function computeRugScore(
  liquidity: number,
  holderCount: number,
  botActivityPct: number,
  smartMoneyPct: number
): number {
  let risk = 0;

  // Liquidez muy baja → riesgo alto
  if (liquidity < 1000) risk += 0.3;
  else if (liquidity < 10000) risk += 0.15;
  else if (liquidity < 50000) risk += 0.05;

  // Pocos holders → riesgo alto
  if (holderCount < 10) risk += 0.25;
  else if (holderCount < 50) risk += 0.15;
  else if (holderCount < 200) risk += 0.05;

  // Alta actividad bot → riesgo alto
  if (botActivityPct > 70) risk += 0.25;
  else if (botActivityPct > 40) risk += 0.15;
  else if (botActivityPct > 20) risk += 0.05;

  // Sin smart money → riesgo alto
  if (smartMoneyPct < 5) risk += 0.2;
  else if (smartMoneyPct < 15) risk += 0.1;

  return Math.max(0, Math.min(1, risk));
}

/**
 * Calcula la distancia euclidiana normalizada entre las señales
 * del token y un patrón de referencia de fase.
 *
 * Usa los pesos de señal para ponderar la distancia.
 * Retorna un valor [0-1] donde 0 = match perfecto.
 */
function computePatternDistance(
  signals: PhaseDetectionResult['signals'],
  pattern: typeof PHASE_PATTERNS[TokenPhase]
): number {
  const signalKeys = Object.keys(SIGNAL_WEIGHTS) as Array<keyof typeof signals>;
  let weightedDistance = 0;
  let totalWeight = 0;

  for (const key of signalKeys) {
    const weight = SIGNAL_WEIGHTS[key];
    const diff = (signals[key] - pattern[key]) ** 2;
    weightedDistance += diff * weight;
    totalWeight += weight;
  }

  // Normalizar: max possible weighted distance = totalWeight (cuando diff=1 para todos)
  return Math.sqrt(weightedDistance) / Math.sqrt(totalWeight);
}

// ============================================================
// TOKEN LIFECYCLE ENGINE CLASS
// ============================================================

class TokenLifecycleEngine {
  /**
   * Detecta la fase actual del ciclo de vida de un token.
   *
   * Proceso:
   * 1. Carga el token y datos relacionados de la BD
   * 2. Calcula los 8 scores de señal
   * 3. Compara contra los patrones de referencia de cada fase
   * 4. Convierte distancias en probabilidades (softmax-like)
   * 5. Retorna la fase con mayor probabilidad y distribución completa
   *
   * @param tokenAddress - Dirección del token en blockchain
   * @param chain - Cadena (default: "SOL")
   * @returns Resultado de detección de fase con distribución y señales
   */
  async detectPhase(tokenAddress: string, chain: string = 'SOL'): Promise<PhaseDetectionResult> {
    // --- Paso 1: Cargar datos del token desde la BD ---
    const token = await db.token.findFirst({
      where: { address: tokenAddress, chain },
      include: {
        dna: true,
        candles: {
          where: { timeframe: '1h' },
          orderBy: { timestamp: 'desc' },
          take: 48, // Últimas 48 horas de candles
        },
      },
    });

    if (!token) {
      // Token no encontrado: retornar GENESIS como fallback con baja confianza
      return this.buildFallbackResult(tokenAddress);
    }

    // --- Paso 2: Calcular cada señal ---
    const ageMinutes = getTokenAgeMinutes(token.createdAt);
    const ageScore = computeAgeScore(ageMinutes);

    const liquidityScore = computeLiquidityScore(
      token.liquidity,
      token.candles
    );

    const botRatioScore = computeBotRatioScore(
      token.botActivityPct,
      token.dna?.botActivityScore
    );

    const smartMoneyFlowScore = computeSmartMoneyFlowScore(
      token.smartMoneyPct,
      token.dna?.smartMoneyScore
    );

    const holderVelocityScore = computeHolderVelocityScore(
      token.uniqueWallets24h,
      token.holderCount
    );

    const volumeDistributionScore = computeVolumeDistributionScore(
      token.candles
    );

    const volatilityScore = computeVolatilityScore(
      token.candles,
      token.priceUsd
    );

    const rugScore = computeRugScore(
      token.liquidity,
      token.holderCount,
      token.botActivityPct,
      token.smartMoneyPct
    );

    const signals: PhaseDetectionResult['signals'] = {
      ageScore,
      liquidityScore,
      botRatioScore,
      smartMoneyFlowScore,
      holderVelocityScore,
      volumeDistributionScore,
      volatilityScore,
      rugScore,
    };

    // --- Paso 3: Comparar contra patrones de referencia ---
    const distances: Record<TokenPhase, number> = {} as Record<TokenPhase, number>;
    for (const phase of VALID_PHASES) {
      distances[phase] = computePatternDistance(signals, PHASE_PATTERNS[phase]);
    }

    // --- Paso 4: Convertir distancias a probabilidades ---
    // Usar transformación tipo softmax invertida:
    // prob_i ∝ exp(-distance_i / temperature)
    // temperature controla la "sharpness" de la distribución
    const temperature = 0.3; // Valor bajo = distribución más concentrada
    const expScores: Record<TokenPhase, number> = {} as Record<TokenPhase, number>;

    for (const phase of VALID_PHASES) {
      expScores[phase] = Math.exp(-distances[phase] / temperature);
    }

    const totalExpScore = VALID_PHASES.reduce((sum, p) => sum + expScores[p], 0);
    const distribution: Record<TokenPhase, number> = {} as Record<TokenPhase, number>;

    for (const phase of VALID_PHASES) {
      distribution[phase] = totalExpScore > 0 ? expScores[phase] / totalExpScore : 1 / VALID_PHASES.length;
    }

    // --- Paso 5: Determinar la fase dominante ---
    let bestPhase: TokenPhase = 'GENESIS';
    let bestProb = 0;
    for (const phase of VALID_PHASES) {
      if (distribution[phase] > bestProb) {
        bestProb = distribution[phase];
        bestPhase = phase;
      }
    }

    // --- Paso 6: Verificar transición desde el estado previo ---
    const transitionFrom = await this.getPreviousPhase(tokenAddress);

    return {
      phase: bestPhase,
      probability: bestProb,
      distribution,
      signals,
      transitionFrom: transitionFrom ?? undefined,
      transitionProbability: transitionFrom ? distribution[transitionFrom] : undefined,
    };
  }

  /**
   * Detecta si hay una transición de fase comparando la detección actual
   * con el estado más reciente almacenado en la BD.
   *
   * Una transición se detecta si:
   * - La fase cambió respecto al estado anterior
   * - Dos fases tienen probabilidades cercanas (<0.2 diff), indicando
   *   que el token está "entre fases"
   *
   * Almacena el nuevo estado en TokenLifecycleState.
   *
   * @param tokenAddress - Dirección del token
   * @returns Información de transición o null si no hay cambio
   */
  async detectTransition(tokenAddress: string): Promise<TransitionResult | null> {
    // Obtener detección actual
    const currentResult = await this.detectPhase(tokenAddress);

    // Buscar el estado más reciente en la BD
    const lastState = await db.tokenLifecycleState.findFirst({
      where: { tokenAddress },
      orderBy: { detectedAt: 'desc' },
    });

    const now = new Date();

    // Almacenar el nuevo estado SIEMPRE para historial
    await db.tokenLifecycleState.create({
      data: {
        tokenAddress,
        phase: currentResult.phase,
        phaseProbability: currentResult.probability,
        phaseDistribution: JSON.stringify(currentResult.distribution),
        transitionFrom: currentResult.transitionFrom ?? null,
        transitionProb: currentResult.transitionProbability ?? null,
        signals: JSON.stringify(currentResult.signals),
        detectedAt: now,
      },
    });

    // Si no hay estado previo, no hay transición que detectar
    if (!lastState) return null;

    const previousPhase = lastState.phase as TokenPhase;

    // Caso 1: La fase cambió claramente
    if (previousPhase !== currentResult.phase) {
      return {
        from: previousPhase,
        to: currentResult.phase,
        probability: currentResult.probability,
        timestamp: now,
      };
    }

    // Caso 2: La fase es la misma, pero otra fase tiene probabilidad cercana
    // Esto indica una transición inminente
    const sortedPhases = VALID_PHASES
      .map(p => ({ phase: p, prob: currentResult.distribution[p] }))
      .sort((a, b) => b.prob - a.prob);

    // Si las dos fases top tienen probabilidad cercana (<0.2 diff)
    // y la segunda fase es diferente a la actual
    if (
      sortedPhases.length >= 2 &&
      sortedPhases[0].prob - sortedPhases[1].prob < 0.2 &&
      sortedPhases[1].phase !== currentResult.phase
    ) {
      return {
        from: currentResult.phase,
        to: sortedPhases[1].phase,
        probability: sortedPhases[1].prob,
        timestamp: now,
      };
    }

    return null; // No hay transición significativa
  }

  /**
   * Ejecuta detectPhase para múltiples tokens de forma eficiente.
   * Almacena todos los resultados en la BD para historial.
   *
   * Usa Promise.allSettled para que un token fallido no bloquee los demás.
   *
   * @param tokenAddresses - Array de direcciones de tokens
   * @returns Mapa de dirección → resultado de detección
   */
  async batchDetectPhases(tokenAddresses: string[]): Promise<Map<string, PhaseDetectionResult>> {
    const results = new Map<string, PhaseDetectionResult>();

    // Ejecutar detecciones en paralelo con allSettled para resiliencia
    const detections = await Promise.allSettled(
      tokenAddresses.map(async (address) => {
        const result = await this.detectPhase(address);
        return { address, result };
      })
    );

    // Procesar resultados y almacenar en BD
    const dbWrites: Promise<unknown>[] = [];

    for (const detection of detections) {
      if (detection.status === 'fulfilled') {
        const { address, result } = detection.value;
        results.set(address, result);

        // Almacenar resultado en BD
        dbWrites.push(
          db.tokenLifecycleState.create({
            data: {
              tokenAddress: address,
              phase: result.phase,
              phaseProbability: result.probability,
              phaseDistribution: JSON.stringify(result.distribution),
              transitionFrom: result.transitionFrom ?? null,
              transitionProb: result.transitionProbability ?? null,
              signals: JSON.stringify(result.signals),
              detectedAt: new Date(),
            },
          })
        );
      }
      // Si falló, simplemente lo omitimos (ya tenemos fallback en detectPhase)
    }

    // Ejecutar todas las escrituras a BD en paralelo
    await Promise.allSettled(dbWrites);

    return results;
  }

  /**
   * Retorna la configuración de señales optimizada para una fase específica.
   *
   * El big-data-engine usa esta configuración para ponderar su análisis
   * de forma diferente según la fase del token. Por ejemplo, en GENESIS
   * la señal primaria es botRatioScore, mientras que en GROWTH es
   * smartMoneyFlowScore.
   *
   * @param phase - Fase del ciclo de vida
   * @returns Configuración de señales y pesos para la fase
   */
  getPhaseSpecificSignals(phase: TokenPhase): PhaseSignalConfig {
    return PHASE_SIGNAL_CONFIGS[phase];
  }

  // ============================================================
  // MÉTODOS PRIVADOS
  // ============================================================

  /**
   * Obtiene la fase anterior de un token desde el último
   * TokenLifecycleState almacenado.
   */
  private async getPreviousPhase(tokenAddress: string): Promise<TokenPhase | null> {
    const lastState = await db.tokenLifecycleState.findFirst({
      where: { tokenAddress },
      orderBy: { detectedAt: 'desc' },
    });

    if (!lastState) return null;

    return lastState.phase as TokenPhase;
  }

  /**
   * Construye un resultado fallback cuando el token no existe en la BD.
   * Retorna GENESIS con baja confianza y distribución uniforme.
   */
  private buildFallbackResult(tokenAddress: string): PhaseDetectionResult {
    const uniformDist: Record<TokenPhase, number> = {
      GENESIS: 0.25,
      INCIPIENT: 0.20,
      GROWTH: 0.18,
      FOMO: 0.15,
      DECLINE: 0.12,
      LEGACY: 0.10,
    };

    return {
      phase: 'GENESIS',
      probability: 0.25,
      distribution: uniformDist,
      signals: {
        ageScore: 1.0,
        liquidityScore: 0,
        botRatioScore: 0,
        smartMoneyFlowScore: 0,
        holderVelocityScore: 0,
        volumeDistributionScore: 0,
        volatilityScore: 0,
        rugScore: 0.8, // Alto riesgo si no tenemos datos
      },
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

/**
 * Instancia singleton del motor de detección del ciclo de vida.
 * Usar: import { tokenLifecycleEngine } from '@/lib/services/token-lifecycle-engine'
 */
export const tokenLifecycleEngine = new TokenLifecycleEngine();
