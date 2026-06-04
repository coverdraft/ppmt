/**
 * Trader Behavioral Model Engine - CryptoQuant Terminal
 * Motor de Predicción Conductual de Traders
 *
 * Modela el comportamiento de los distintos arquetipos de traders a lo largo
 * del ciclo de vida de un token y genera predicciones conductuales agregadas.
 * Componente central del cerebro Big Data que alimenta al motor predictivo
 * y a los sistemas de trading con inteligencia conductual.
 *
 * Arquitectura:
 *   - Matrices de transición conductual 3D: arquetipo → fase → acción → probabilidad
 *   - Inicialización con defaults basados en investigación de behavioral finance
 *   - Actualización Bayesiana con suavizado de Laplace a partir de observaciones on-chain
 *   - Detección de anomalías conductuales (desviación > 2σ del modelo)
 *
 * Arquetipos modelados:
 *   SMART_MONEY    → Acumula temprano, distribuye en FOMO, sale en DECLINE
 *   WHALE          → Grandes posiciones, timing pausado, distribución gradual
 *   SNIPER         → Entra en GENESIS, sell rápido, alta rotación
 *   RETAIL_FOMO    → Compra en GROWTH/FOMO, hold en DECLINE, baja sofisticación
 *   RETAIL_HOLDER  → Buy & hold, baja actividad, entra después de confirmación
 *   SCALPER        → Alta frecuencia, buy+sell equilibrado, timing corto
 *   DEGEN          → Compra impulsiva, sin análisis, alto riesgo
 *   CONTRARIAN     → Vende cuando otros compran, compra en DECLINE
 */

import { db } from '../db';
import { TokenPhase, TraderArchetype } from './token-lifecycle-engine';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Acciones posibles de un trader en cualquier fase del ciclo de vida */
export type TraderAction = 'BUY' | 'SELL' | 'HOLD' | 'ACCUMULATE' | 'DISTRIBUTE' | 'WATCH';

/** Conjunto válido de arquetipos para validación en runtime */
const VALID_ARCHETYPES: TraderArchetype[] = [
  'SMART_MONEY', 'WHALE', 'SNIPER', 'RETAIL_FOMO',
  'RETAIL_HOLDER', 'SCALPER', 'DEGEN', 'CONTRARIAN',
];

/** Conjunto válido de fases para validación en runtime */
const VALID_PHASES: TokenPhase[] = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'];

/** Conjunto válido de acciones para validación en runtime */
const VALID_ACTIONS: TraderAction[] = ['BUY', 'SELL', 'HOLD', 'ACCUMULATE', 'DISTRIBUTE', 'WATCH'];

/**
 * Resultado de la predicción conductual agregada para un token.
 *
 * Combina las matrices de transición de cada arquetipo con la composición
 * de traders del token (volumen por arquetipo) para producir una predicción
 * neta de flujo y un desglose por arquetipo.
 */
export interface BehavioralPrediction {
  tokenAddress: string;
  phase: TokenPhase;
  netFlowDirection: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  netFlowScore: number; // -1 to 1 (negative = selling pressure, positive = buying pressure)
  confidence: number; // 0-1, statistical confidence based on observations & phase certainty
  archetypeBreakdown: {
    archetype: TraderArchetype;
    dominantAction: TraderAction;
    probability: number; // probability of the dominant action
    volumeShare: number; // % of total volume attributed to this archetype
  }[];
  predictedActions: Record<TraderAction, number>; // weighted aggregate probabilities
}

/**
 * Anomalía conductual detectada cuando el comportamiento observado
 * difiere significativamente (> 2σ) del modelo predicho.
 */
export interface BehavioralAnomaly {
  tokenAddress: string;
  phase: TokenPhase;
  predictedDirection: string;
  observedDirection: string;
  deviationScore: number; // z-score of the deviation
  affectedArchetypes: {
    archetype: TraderArchetype;
    predicted: Record<TraderAction, number>;
    observed: Record<TraderAction, number>;
    deviation: number; // magnitude of difference
  }[];
  timestamp: Date;
}

/**
 * Tipo interno para la matriz de transición conductual.
 * Mapa de 3 niveles: arquetipo → fase → acción → probabilidad
 */
type BehavioralMatrix = Record<TraderArchetype, Record<TokenPhase, Record<TraderAction, number>>>;

// ============================================================
// DEFAULT BEHAVIORAL MATRICES
// ============================================================

/**
 * Matrices de transición conductual por defecto, basadas en investigación
 * de behavioral finance y observaciones empíricas de mercado crypto.
 *
 * Cada valor representa la probabilidad de que un arquetipo dado tome
 * una acción específica durante una fase del ciclo de vida del token.
 *
 * Los valores suman ~1.0 dentro de cada combinación arquetipo+fase
 * (pueden no sumar exactamente 1.0 por redondeo; se normalizan en runtime).
 *
 * Fuentes:
 *   - Behavioral finance: prospect theory, herding, disposition effect
 *   - On-chain analytics: patrones observados en DEXes de Solana/ETH
 *   - Market microstructure: order flow, toxic flow, informed trading
 */
const DEFAULT_MATRICES: BehavioralMatrix = {
  SMART_MONEY: {
    GENESIS:   { ACCUMULATE: 0.5, BUY: 0.1, WATCH: 0.3, HOLD: 0.1, SELL: 0, DISTRIBUTE: 0 },
    INCIPIENT: { ACCUMULATE: 0.4, BUY: 0.2, WATCH: 0.3, HOLD: 0.1, SELL: 0, DISTRIBUTE: 0 },
    GROWTH:    { ACCUMULATE: 0.3, BUY: 0.2, HOLD: 0.3, WATCH: 0.2, SELL: 0, DISTRIBUTE: 0 },
    FOMO:      { DISTRIBUTE: 0.4, SELL: 0.2, HOLD: 0.3, BUY: 0.1, ACCUMULATE: 0, WATCH: 0 },
    DECLINE:   { SELL: 0.4, DISTRIBUTE: 0.3, HOLD: 0.2, WATCH: 0.1, BUY: 0, ACCUMULATE: 0 },
    LEGACY:    { ACCUMULATE: 0.2, HOLD: 0.3, BUY: 0.2, WATCH: 0.3, SELL: 0, DISTRIBUTE: 0 },
  },
  WHALE: {
    GENESIS:   { ACCUMULATE: 0.4, BUY: 0.2, WATCH: 0.4, SELL: 0, HOLD: 0, DISTRIBUTE: 0 },
    INCIPIENT: { ACCUMULATE: 0.5, BUY: 0.2, WATCH: 0.3, SELL: 0, HOLD: 0, DISTRIBUTE: 0 },
    GROWTH:    { HOLD: 0.4, ACCUMULATE: 0.3, BUY: 0.2, WATCH: 0.1, SELL: 0, DISTRIBUTE: 0 },
    FOMO:      { DISTRIBUTE: 0.5, HOLD: 0.3, SELL: 0.2, BUY: 0, ACCUMULATE: 0, WATCH: 0 },
    DECLINE:   { DISTRIBUTE: 0.4, SELL: 0.3, HOLD: 0.3, BUY: 0, ACCUMULATE: 0, WATCH: 0 },
    LEGACY:    { HOLD: 0.4, ACCUMULATE: 0.3, DISTRIBUTE: 0.2, WATCH: 0.1, BUY: 0, SELL: 0 },
  },
  SNIPER: {
    GENESIS:   { BUY: 0.8, SELL: 0.2, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0 },
    INCIPIENT: { BUY: 0.6, SELL: 0.3, HOLD: 0.1, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0 },
    GROWTH:    { SELL: 0.5, BUY: 0.3, HOLD: 0.2, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0 },
    FOMO:      { SELL: 0.7, BUY: 0.2, HOLD: 0.1, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0 },
    DECLINE:   { SELL: 0.8, WATCH: 0.2, BUY: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    LEGACY:    { WATCH: 0.5, BUY: 0.3, SELL: 0.2, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
  },
  RETAIL_FOMO: {
    GENESIS:   { WATCH: 0.7, BUY: 0.3, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    INCIPIENT: { BUY: 0.4, WATCH: 0.4, HOLD: 0.2, SELL: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    GROWTH:    { BUY: 0.5, HOLD: 0.3, WATCH: 0.2, SELL: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    FOMO:      { BUY: 0.7, HOLD: 0.2, SELL: 0.1, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    DECLINE:   { HOLD: 0.4, SELL: 0.3, BUY: 0.2, WATCH: 0.1, ACCUMULATE: 0, DISTRIBUTE: 0 },
    LEGACY:    { HOLD: 0.3, WATCH: 0.3, BUY: 0.2, SELL: 0.2, ACCUMULATE: 0, DISTRIBUTE: 0 },
  },
  RETAIL_HOLDER: {
    GENESIS:   { WATCH: 0.8, BUY: 0.2, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    INCIPIENT: { BUY: 0.3, WATCH: 0.4, HOLD: 0.3, SELL: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    GROWTH:    { BUY: 0.3, HOLD: 0.5, WATCH: 0.2, SELL: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    FOMO:      { HOLD: 0.5, BUY: 0.3, SELL: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    DECLINE:   { HOLD: 0.6, WATCH: 0.2, SELL: 0.2, BUY: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    LEGACY:    { HOLD: 0.5, WATCH: 0.3, BUY: 0.2, SELL: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
  },
  SCALPER: {
    GENESIS:   { BUY: 0.5, SELL: 0.5, HOLD: 0, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    INCIPIENT: { BUY: 0.4, SELL: 0.4, WATCH: 0.2, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    GROWTH:    { BUY: 0.4, SELL: 0.4, HOLD: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    FOMO:      { BUY: 0.3, SELL: 0.5, HOLD: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    DECLINE:   { SELL: 0.5, BUY: 0.3, WATCH: 0.2, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    LEGACY:    { BUY: 0.3, SELL: 0.3, HOLD: 0.2, WATCH: 0.2, ACCUMULATE: 0, DISTRIBUTE: 0 },
  },
  DEGEN: {
    GENESIS:   { BUY: 0.7, WATCH: 0.3, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    INCIPIENT: { BUY: 0.6, HOLD: 0.2, SELL: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    GROWTH:    { BUY: 0.5, HOLD: 0.3, SELL: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    FOMO:      { BUY: 0.6, HOLD: 0.2, SELL: 0.2, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    DECLINE:   { SELL: 0.4, BUY: 0.3, HOLD: 0.3, WATCH: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    LEGACY:    { BUY: 0.3, SELL: 0.3, HOLD: 0.2, WATCH: 0.2, ACCUMULATE: 0, DISTRIBUTE: 0 },
  },
  CONTRARIAN: {
    GENESIS:   { WATCH: 0.5, BUY: 0.3, SELL: 0.2, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    INCIPIENT: { SELL: 0.3, BUY: 0.3, WATCH: 0.4, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    GROWTH:    { SELL: 0.4, WATCH: 0.3, HOLD: 0.3, BUY: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    FOMO:      { SELL: 0.7, WATCH: 0.2, HOLD: 0.1, BUY: 0, ACCUMULATE: 0, DISTRIBUTE: 0 },
    DECLINE:   { BUY: 0.5, ACCUMULATE: 0.3, WATCH: 0.2, SELL: 0, HOLD: 0, DISTRIBUTE: 0 },
    LEGACY:    { BUY: 0.4, ACCUMULATE: 0.3, WATCH: 0.3, SELL: 0, HOLD: 0, DISTRIBUTE: 0 },
  },
};

/**
 * Pesos de flujo neto para cada acción.
 * Acciones alcistas tienen peso positivo; bajistas negativo; neutras cero.
 * Usados para calcular netFlowScore a partir de las probabilidades agregadas.
 */
const ACTION_FLOW_WEIGHTS: Record<TraderAction, number> = {
  ACCUMULATE: 1.0,  // Compra fuerte y sostenida
  BUY: 0.6,         // Compra normal
  HOLD: 0.0,        // Neutro (no genera flujo)
  WATCH: -0.1,      // Ligeramente bajista (indica indecisión/retirada)
  SELL: -0.6,       // Venta normal
  DISTRIBUTE: -1.0, // Venta fuerte y sostenida
};

/** Parámetro alpha para suavizado de Laplace en la actualización Bayesiana */
const LAPLACE_ALPHA = 1;

// ============================================================
// HELPER FUNCTIONS
// ============================================================

/**
 * Normaliza un mapa de probabilidades para que sumen exactamente 1.0.
 * Elimina entradas con valor 0 antes de normalizar para mantener la matriz limpia.
 */
function normalizeProbabilities(probs: Record<TraderAction, number>): Record<TraderAction, number> {
  const total = Object.values(probs).reduce((s, v) => s + v, 0);
  if (total <= 0) return probs;

  const normalized: Record<TraderAction, number> = { ...probs };
  for (const action of VALID_ACTIONS) {
    normalized[action] = probs[action] / total;
  }
  return normalized;
}

/**
 * Encuentra la acción dominante (mayor probabilidad) en un mapa de acciones.
 */
function findDominantAction(probs: Record<TraderAction, number>): {
  action: TraderAction;
  probability: number;
} {
  let best: TraderAction = 'HOLD';
  let bestProb = -1;

  for (const action of VALID_ACTIONS) {
    if ((probs[action] ?? 0) > bestProb) {
      bestProb = probs[action] ?? 0;
      best = action;
    }
  }

  return { action: best, probability: bestProb };
}

/**
 * Calcula la divergencia Jensen-Shannon entre dos distribuciones de probabilidad.
 * Versión simplificada usando divergencia KL simétrica para detección de anomalías.
 * Retorna un valor >= 0 donde 0 = distribuciones idénticas.
 */
function computeDistributionDeviation(
  predicted: Record<TraderAction, number>,
  observed: Record<TraderAction, number>
): number {
  let deviation = 0;

  for (const action of VALID_ACTIONS) {
    const p = predicted[action] ?? 0;
    const q = observed[action] ?? 0;
    // Distancia euclidiana entre las distribuciones
    deviation += (p - q) ** 2;
  }

  return Math.sqrt(deviation);
}

/**
 * Parsea el campo traderComposition de TokenDNA desde JSON.
 * Retorna un mapa de arquetipo → porcentaje de volumen.
 * Si el JSON es inválido o vacío, retorna un fallback con distribución uniforme.
 */
function parseTraderComposition(compositionJson: string): Record<TraderArchetype, number> {
  try {
    const parsed = JSON.parse(compositionJson);
    const result: Record<TraderArchetype, number> = {} as Record<TraderArchetype, number>;

    // Mapear claves del JSON a arquetipos (acepta múltiples formatos)
    const keyMapping: Record<string, TraderArchetype> = {
      smartMoney: 'SMART_MONEY',
      SMART_MONEY: 'SMART_MONEY',
      smart_money: 'SMART_MONEY',
      whale: 'WHALE',
      WHALE: 'WHALE',
      sniper: 'SNIPER',
      SNIPER: 'SNIPER',
      retailFomo: 'RETAIL_FOMO',
      RETAIL_FOMO: 'RETAIL_FOMO',
      retail_fomo: 'RETAIL_FOMO',
      retailHolder: 'RETAIL_HOLDER',
      RETAIL_HOLDER: 'RETAIL_HOLDER',
      retail_holder: 'RETAIL_HOLDER',
      retail: 'RETAIL_FOMO', // 'retail' mapea a RETAIL_FOMO como fallback
      scalper: 'SCALPER',
      SCALPER: 'SCALPER',
      degen: 'DEGEN',
      DEGEN: 'DEGEN',
      contrarian: 'CONTRARIAN',
      CONTRARIAN: 'CONTRARIAN',
    };

    for (const [key, value] of Object.entries(parsed)) {
      const archetype = keyMapping[key];
      if (archetype && typeof value === 'number') {
        result[archetype] = (result[archetype] ?? 0) + value;
      }
    }

    // Normalizar a porcentajes (0-100)
    const total = Object.values(result).reduce((s, v) => s + v, 0);
    if (total > 0) {
      for (const key of Object.keys(result) as TraderArchetype[]) {
        result[key] = (result[key] / total) * 100;
      }
    }

    return result;
  } catch {
    // Fallback: distribución uniforme si no hay datos
    const uniform: Record<TraderArchetype, number> = {} as Record<TraderArchetype, number>;
    const equalShare = 100 / VALID_ARCHETYPES.length;
    for (const archetype of VALID_ARCHETYPES) {
      uniform[archetype] = equalShare;
    }
    return uniform;
  }
}

// ============================================================
// BEHAVIORAL MODEL ENGINE CLASS
// ============================================================

class BehavioralModelEngine {
  /**
   * Predice el comportamiento agregado de los traders para un token.
   *
   * Proceso:
   * 1. Cargar la fase del ciclo de vida del token (último TokenLifecycleState)
   * 2. Cargar la composición de traders del token (TokenDNA.traderComposition)
   * 3. Combinar las matrices conductuales con la composición para producir
   *    una predicción ponderada de flujo neto
   * 4. Retornar dirección de flujo, confianza y desglose por arquetipo
   *
   * @param tokenAddress - Dirección del token en blockchain
   * @param chain - Cadena (default: "SOL")
   * @returns Predicción conductual con flujo neto y desglose por arquetipo
   */
  async predictBehavior(
    tokenAddress: string,
    chain: string = 'SOL'
  ): Promise<BehavioralPrediction> {
    // --- Paso 1: Cargar la fase del ciclo de vida ---
    const lifecycleState = await db.tokenLifecycleState.findFirst({
      where: { tokenAddress },
      orderBy: { detectedAt: 'desc' },
    });

    // Fase detectada o fallback a GENESIS con baja confianza
    const phase: TokenPhase = (lifecycleState?.phase as TokenPhase) ?? 'GENESIS';
    const phaseConfidence = lifecycleState?.phaseProbability ?? 0.25;

    // --- Paso 2: Cargar la composición de traders ---
    const token = await db.token.findFirst({
      where: { address: tokenAddress, chain },
      include: { dna: true },
    });

    // Parsear composición de traders desde TokenDNA
    let composition: Record<TraderArchetype, number>;
    if (token?.dna?.traderComposition) {
      composition = parseTraderComposition(token.dna.traderComposition);
    } else {
      // Fallback: usar métricas del token para estimar composición básica
      composition = this.estimateFallbackComposition(token);
    }

    // --- Paso 3: Cargar matrices conductuales y combinar con composición ---
    const archetypeBreakdown: BehavioralPrediction['archetypeBreakdown'] = [];
    const weightedActions: Record<TraderAction, number> = {
      BUY: 0, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0,
    };
    let totalVolumeShare = 0;

    for (const archetype of VALID_ARCHETYPES) {
      // Obtener la matriz para este arquetipo+fase (desde BD o defaults)
      const actionProbs = await this.getMatrix(archetype, phase);
      const volumeShare = composition[archetype] ?? 0;

      // Solo incluir arquetipos con presencia significativa (>0.5% de volumen)
      if (volumeShare < 0.5) continue;

      totalVolumeShare += volumeShare;

      // Encontrar la acción dominante para este arquetipo
      const dominant = findDominantAction(actionProbs);

      archetypeBreakdown.push({
        archetype,
        dominantAction: dominant.action,
        probability: dominant.probability,
        volumeShare,
      });

      // Acumular acciones ponderadas por volumen
      for (const action of VALID_ACTIONS) {
        weightedActions[action] += (actionProbs[action] ?? 0) * volumeShare;
      }
    }

    // Normalizar las acciones ponderadas por el volumen total participante
    if (totalVolumeShare > 0) {
      for (const action of VALID_ACTIONS) {
        weightedActions[action] /= totalVolumeShare;
      }
    }

    // --- Paso 4: Calcular flujo neto ---
    let netFlowScore = 0;
    for (const action of VALID_ACTIONS) {
      netFlowScore += weightedActions[action] * ACTION_FLOW_WEIGHTS[action];
    }
    // Clamp to [-1, 1]
    netFlowScore = Math.max(-1, Math.min(1, netFlowScore));

    // Determinar dirección del flujo
    let netFlowDirection: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
    if (netFlowScore > 0.15) {
      netFlowDirection = 'BULLISH';
    } else if (netFlowScore < -0.15) {
      netFlowDirection = 'BEARISH';
    } else {
      netFlowDirection = 'NEUTRAL';
    }

    // --- Paso 5: Calcular confianza ---
    // La confianza depende de:
    //   - Confianza en la detección de fase (phaseConfidence)
    //   - Disponibilidad de datos de composición de traders
    //   - Número de observaciones en el modelo conductual
    const hasDnaData = !!token?.dna?.traderComposition;
    const compositionConfidence = hasDnaData ? 0.8 : 0.3;

    // Consultar observaciones totales para esta fase
    const modelRows = await db.traderBehaviorModel.findMany({
      where: { tokenPhase: phase },
    });
    const totalObservations = modelRows.reduce((s, r) => s + r.observations, 0);
    // Más observaciones → mayor confianza (log scale, max 1.0)
    const observationConfidence = Math.min(1.0, Math.log10(totalObservations + 1) / 3);

    const confidence = phaseConfidence * 0.4 + compositionConfidence * 0.35 + observationConfidence * 0.25;

    // Ordenar breakdown por volumen descendente
    archetypeBreakdown.sort((a, b) => b.volumeShare - a.volumeShare);

    return {
      tokenAddress,
      phase,
      netFlowDirection,
      netFlowScore,
      confidence: Math.min(1, confidence),
      archetypeBreakdown,
      predictedActions: weightedActions,
    };
  }

  /**
   * Actualiza el modelo conductual usando inferencia Bayesiana.
   *
   * Cuando se observa una acción real de un trader en una fase específica,
   * se actualiza la probabilidad de esa acción usando suavizado de Laplace:
   *
   *   P(action | observations) = (count(action) + α) / (N + α * K)
   *
   * donde:
   *   - count(action) = observaciones previas de esta acción
   *   - N = total de observaciones para este arquetipo+fase
   *   - α = Laplace smoothing parameter (1)
   *   - K = número de acciones posibles (6)
   *
   * Si observed=true, se incrementa count para esta acción.
   * Si observed=false, se incrementa count para las demás acciones (implícito).
   *
   * @param archetype - Arquetipo del trader observado
   * @param phase - Fase del ciclo de vida del token
   * @param action - Acción observada
   * @param observed - true si la acción fue observada, false si no
   */
  async updateModel(
    archetype: TraderArchetype,
    phase: TokenPhase,
    action: TraderAction,
    observed: boolean
  ): Promise<void> {
    // Cargar todas las filas existentes para este arquetipo+fase
    const existingRows = await db.traderBehaviorModel.findMany({
      where: { archetype, tokenPhase: phase },
    });

    const existingMap = new Map<string, (typeof existingRows)[0]>();
    for (const row of existingRows) {
      existingMap.set(row.action, row);
    }

    // Recolectar counts actuales
    const currentCounts: Record<TraderAction, number> = {
      BUY: 0, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0,
    };

    for (const act of VALID_ACTIONS) {
      const row = existingMap.get(act);
      currentCounts[act] = row ? Math.round(row.probability * row.observations) : 0;
    }

    // Incrementar el count de la acción observada
    if (observed) {
      currentCounts[action] += 1;
    }

    // Calcular el total de observaciones
    const totalObservations = Object.values(currentCounts).reduce((s, v) => s + v, 0);

    // Recalcular probabilidades con suavizado de Laplace
    // P(action) = (count(action) + α) / (N + α * K)
    const K = VALID_ACTIONS.length;
    const updatedProbs: Record<TraderAction, number> = {
      BUY: 0, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0,
    };

    for (const act of VALID_ACTIONS) {
      updatedProbs[act] = (currentCounts[act] + LAPLACE_ALPHA) / (totalObservations + LAPLACE_ALPHA * K);
    }

    // Escribir/actualizar cada fila en la BD
    const writePromises: Promise<unknown>[] = [];

    for (const act of VALID_ACTIONS) {
      const existing = existingMap.get(act);
      const newObservations = existing ? existing.observations + (observed && act === action ? 1 : 0) : (observed && act === action ? 1 : 0);
      const newConfidence = Math.min(1.0, Math.log10(totalObservations + 1) / 3);

      if (existing) {
        writePromises.push(
          db.traderBehaviorModel.update({
            where: { id: existing.id },
            data: {
              probability: updatedProbs[act],
              observations: newObservations,
              confidence: newConfidence,
            },
          })
        );
      } else {
        writePromises.push(
          db.traderBehaviorModel.create({
            data: {
              archetype,
              tokenPhase: phase,
              action: act,
              probability: updatedProbs[act],
              observations: newObservations,
              confidence: totalObservations > 0 ? Math.min(1.0, Math.log10(totalObservations + 1) / 3) : 0,
              intensity: 0,
              duration: 0,
            },
          })
        );
      }
    }

    await Promise.allSettled(writePromises);
  }

  /**
   * Detecta anomalías conductuales comparando el comportamiento predicho
   * con la actividad on-chain observada recientemente.
   *
   * Una anomalía se detecta cuando el flujo neto observado difiere del
   * predicho por más de 2 desviaciones estándar (z-score > 2).
   *
   * Esto puede indicar:
   *   - Manipulación de mercado (wash trading, spoofing)
   *   - Cambio de régimen no detectado por el lifecycle engine
   *   - Evento catalítico (news, listing, hack)
   *   - Coordinación de bots (swarm behavior)
   *
   * @param tokenAddress - Dirección del token
   * @param chain - Cadena (default: "SOL")
   * @returns Anomalía detectada o null si el comportamiento está dentro de lo esperado
   */
  async detectBehaviorAnomaly(
    tokenAddress: string,
    chain: string = 'SOL'
  ): Promise<BehavioralAnomaly | null> {
    // --- Paso 1: Obtener predicción actual ---
    const prediction = await this.predictBehavior(tokenAddress, chain);

    // --- Paso 2: Obtener comportamiento observado recientemente ---
    // Analizar las últimas transacciones para construir distribución observada
    const recentTransactions = await db.traderTransaction.findMany({
      where: {
        tokenAddress,
        chain,
        action: { in: ['BUY', 'SELL'] },
        blockTime: {
          gte: new Date(Date.now() - 24 * 60 * 60 * 1000), // Últimas 24h
        },
      },
      include: { trader: true },
      orderBy: { blockTime: 'desc' },
      take: 500, // Limitar para rendimiento
    });

    // Si no hay transacciones recientes, no se puede detectar anomalía
    if (recentTransactions.length < 10) return null;

    // --- Paso 3: Construir distribución observada por arquetipo ---
    const observedByArchetype: Record<TraderArchetype, Record<TraderAction, number>> =
      {} as Record<TraderArchetype, Record<TraderAction, number>>;

    for (const archetype of VALID_ARCHETYPES) {
      observedByArchetype[archetype] = { BUY: 0, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0 };
    }

    // Mapear traders a arquetipos y contar acciones
    let totalTxCount = 0;
    for (const tx of recentTransactions) {
      const archetype = this.mapTraderToArchetype(tx.trader);
      const action = this.mapTxActionToTraderAction(tx.action);

      observedByArchetype[archetype][action] += tx.valueUsd > 0 ? 1 : 1;
      totalTxCount++;
    }

    // Normalizar observaciones por arquetipo
    for (const archetype of VALID_ARCHETYPES) {
      const total = Object.values(observedByArchetype[archetype]).reduce((s, v) => s + v, 0);
      if (total > 0) {
        for (const action of VALID_ACTIONS) {
          observedByArchetype[archetype][action] /= total;
        }
      }
    }

    // --- Paso 4: Comparar predicho vs observado por arquetipo ---
    const affectedArchetypes: BehavioralAnomaly['affectedArchetypes'] = [];
    let totalDeviation = 0;

    for (const archetype of VALID_ARCHETYPES) {
      const predicted = await this.getMatrix(archetype, prediction.phase);
      const observed = observedByArchetype[archetype];
      const deviation = computeDistributionDeviation(predicted, observed);

      // Solo incluir arquetipos con desviación significativa y presencia suficiente
      if (deviation > 0.3) {
        affectedArchetypes.push({
          archetype,
          predicted,
          observed,
          deviation,
        });
      }

      totalDeviation += deviation;
    }

    // --- Paso 5: Calcular z-score de la desviación total ---
    // Estimamos la desviación estándar usando la varianza de las desviaciones por arquetipo
    const meanDeviation = totalDeviation / VALID_ARCHETYPES.length;
    let variance = 0;
    for (const archetype of VALID_ARCHETYPES) {
      const predicted = await this.getMatrix(archetype, prediction.phase);
      const observed = observedByArchetype[archetype];
      const dev = computeDistributionDeviation(predicted, observed);
      variance += (dev - meanDeviation) ** 2;
    }
    const stdDeviation = Math.sqrt(variance / VALID_ARCHETYPES.length);

    // Calcular z-score: si std es 0, no hay variación entre arquetipos → no hay anomalía
    const zScore = stdDeviation > 0.001
      ? (totalDeviation - meanDeviation * VALID_ARCHETYPES.length) / (stdDeviation * VALID_ARCHETYPES.length)
      : 0;

    // Umbral de anomalía: z-score > 2 (2 desviaciones estándar)
    if (Math.abs(zScore) < 2 || affectedArchetypes.length === 0) {
      return null;
    }

    // --- Paso 6: Determinar direcciones predicha vs observada ---
    const observedNetFlow = this.computeObservedNetFlow(observedByArchetype);
    const observedDirection = observedNetFlow > 0.15 ? 'BULLISH' : observedNetFlow < -0.15 ? 'BEARISH' : 'NEUTRAL';

    return {
      tokenAddress,
      phase: prediction.phase,
      predictedDirection: prediction.netFlowDirection,
      observedDirection,
      deviationScore: Math.abs(zScore),
      affectedArchetypes,
      timestamp: new Date(),
    };
  }

  /**
   * Inicializa las matrices de transición conductual con los valores por defecto.
   *
   * Solo inserta filas si no existen ya en la tabla TraderBehaviorModel.
   * Esto permite que el motor funcione inmediatamente después del deployment
   * sin necesidad de observaciones previas.
   *
   * Total de filas: 8 arquetipos × 6 fases × 6 acciones = 288 filas
   * (pero solo se insertan las acciones con probabilidad > 0 en los defaults)
   */
  async initializeDefaultMatrices(): Promise<void> {
    // Verificar si ya existen filas
    const existingCount = await db.traderBehaviorModel.count();
    if (existingCount > 0) {
      return; // Ya inicializado, no sobreescribir datos existentes
    }

    const createPromises: Promise<unknown>[] = [];

    for (const archetype of VALID_ARCHETYPES) {
      for (const phase of VALID_PHASES) {
        const actionProbs = DEFAULT_MATRICES[archetype][phase];

        for (const action of VALID_ACTIONS) {
          const probability = actionProbs[action] ?? 0;
          // Solo crear filas para acciones con probabilidad > 0 en los defaults
          if (probability > 0) {
            createPromises.push(
              db.traderBehaviorModel.create({
                data: {
                  archetype,
                  tokenPhase: phase,
                  action,
                  probability,
                  intensity: 0,
                  duration: 0,
                  observations: 0,
                  confidence: 0,
                },
              })
            );
          }
        }
      }
    }

    // Ejecutar todas las inserciones en paralelo
    await Promise.allSettled(createPromises);
  }

  /**
   * Obtiene la matriz de probabilidades para un arquetipo y fase específicos.
   *
   * Primero intenta cargar desde la BD (valores actualizados por observaciones).
   * Si no hay suficientes datos en la BD (menos de 2 acciones con datos),
   * usa los valores por defecto de la matriz de referencia.
   *
   * La matriz retornada siempre está normalizada (suma = 1.0).
   *
   * @param archetype - Arquetipo del trader
   * @param phase - Fase del ciclo de vida del token
   * @returns Mapa de acción → probabilidad normalizada
   */
  async getMatrix(
    archetype: TraderArchetype,
    phase: TokenPhase
  ): Promise<Record<TraderAction, number>> {
    // Intentar cargar desde BD
    const rows = await db.traderBehaviorModel.findMany({
      where: { archetype, tokenPhase: phase },
    });

    // Si hay suficientes datos en BD (al menos 2 acciones con observaciones),
    // usar los valores actualizados
    if (rows.length >= 2) {
      const result: Record<TraderAction, number> = {
        BUY: 0, SELL: 0, HOLD: 0, ACCUMULATE: 0, DISTRIBUTE: 0, WATCH: 0,
      };

      for (const row of rows) {
        result[row.action as TraderAction] = row.probability;
      }

      // Llenar acciones faltantes con los defaults correspondientes
      for (const action of VALID_ACTIONS) {
        if (result[action] === 0 && !rows.some(r => r.action === action)) {
          result[action] = DEFAULT_MATRICES[archetype]?.[phase]?.[action] ?? 0;
        }
      }

      return normalizeProbabilities(result);
    }

    // Fallback a los valores por defecto
    const defaults = DEFAULT_MATRICES[archetype]?.[phase];
    if (defaults) {
      return normalizeProbabilities({ ...defaults });
    }

    // Último fallback: distribución uniforme
    const uniform: Record<TraderAction, number> = {
      BUY: 1, SELL: 1, HOLD: 1, ACCUMULATE: 1, DISTRIBUTE: 1, WATCH: 1,
    };
    return normalizeProbabilities(uniform);
  }

  // ============================================================
  // MÉTODOS PRIVADOS
  // ============================================================

  /**
   * Estima la composición de traders cuando no hay datos de TokenDNA.
   * Usa las métricas básicas del token para inferir la composición.
   *
   * Heurísticas:
   *   - Alta actividad bot → más SNIPER, DEGEN
   *   - Alta presencia smart money → más SMART_MONEY
   *   - Alta liquidez → más WHALE, RETAIL
   */
  private estimateFallbackComposition(
    token: {
      botActivityPct?: number;
      smartMoneyPct?: number;
      holderCount?: number;
      liquidity?: number;
    } | null
  ): Record<TraderArchetype, number> {
    const result: Record<TraderArchetype, number> = {
      SMART_MONEY: 5,
      WHALE: 5,
      SNIPER: 5,
      RETAIL_FOMO: 20,
      RETAIL_HOLDER: 25,
      SCALPER: 10,
      DEGEN: 15,
      CONTRARIAN: 5,
    };

    if (!token) return result;

    // Ajustar basándose en métricas disponibles
    if ((token.botActivityPct ?? 0) > 50) {
      result.SNIPER += 15;
      result.SCALPER += 10;
      result.DEGEN += 5;
      result.RETAIL_FOMO -= 15;
      result.RETAIL_HOLDER -= 15;
    }

    if ((token.smartMoneyPct ?? 0) > 20) {
      result.SMART_MONEY += 15;
      result.RETAIL_FOMO -= 5;
      result.RETAIL_HOLDER -= 5;
    }

    if ((token.liquidity ?? 0) > 100000) {
      result.WHALE += 10;
      result.RETAIL_HOLDER += 5;
    }

    if ((token.holderCount ?? 0) > 1000) {
      result.RETAIL_FOMO += 10;
      result.RETAIL_HOLDER += 10;
    }

    return result;
  }

  /**
   * Mapea un Trader a su arquetipo conductual basándose en sus propiedades.
   *
   * Prioridad de clasificación:
   *   1. Smart money (isSmartMoney o label)
   *   2. Whale (isWhale o holdings)
   *   3. Sniper (isSniper o label)
   *   4. Bot → SCALPER o SNIPER según tipo
   *   5. Label directa
   *   6. Default: RETAIL_FOMO (retail es el tipo más común)
   */
  private mapTraderToArchetype(trader: {
    isSmartMoney?: boolean;
    isWhale?: boolean;
    isSniper?: boolean;
    isBot?: boolean;
    botType?: string | null;
    primaryLabel?: string;
    avgHoldTimeMin?: number;
    totalTrades?: number;
  }): TraderArchetype {
    // Prioridad 1: Smart Money
    if (trader.isSmartMoney) return 'SMART_MONEY';
    if (trader.primaryLabel === 'SMART_MONEY') return 'SMART_MONEY';

    // Prioridad 2: Whale
    if (trader.isWhale) return 'WHALE';
    if (trader.primaryLabel === 'WHALE') return 'WHALE';

    // Prioridad 3: Sniper
    if (trader.isSniper) return 'SNIPER';
    if (trader.primaryLabel === 'SNIPER' || trader.primaryLabel === 'BOT_SNIPER') return 'SNIPER';

    // Prioridad 4: Bot types
    if (trader.isBot) {
      if (trader.botType === 'SCALPER_BOT') return 'SCALPER';
      if (trader.botType === 'SNIPER_BOT' || trader.botType === 'FRONT_RUN_BOT') return 'SNIPER';
      return 'SCALPER'; // Default bot → scalper
    }

    // Prioridad 5: Labels directos
    const labelMapping: Record<string, TraderArchetype> = {
      DEGEN: 'DEGEN',
      SCALPER: 'SCALPER',
      SCALPER_BOT: 'SCALPER',
      RETAIL: 'RETAIL_FOMO',
      FUND: 'WHALE',
      INFLUENCER: 'RETAIL_FOMO',
    };

    if (trader.primaryLabel && labelMapping[trader.primaryLabel]) {
      return labelMapping[trader.primaryLabel];
    }

    // Prioridad 6: Heurística por comportamiento
    if (trader.avgHoldTimeMin !== undefined) {
      if (trader.avgHoldTimeMin < 30) return 'SCALPER';
      if (trader.avgHoldTimeMin < 240) return 'DEGEN';
      if (trader.avgHoldTimeMin > 10080) return 'RETAIL_HOLDER'; // > 1 semana
    }

    // Default: RETAIL_FOMO (el arquetipo más común entre traders no identificados)
    return 'RETAIL_FOMO';
  }

  /**
   * Mapea acciones de transacción a acciones de trader conductuales.
   *
   * BUY → BUY, SELL → SELL, otras acciones neutrales → HOLD
   */
  private mapTxActionToTraderAction(txAction: string): TraderAction {
    switch (txAction) {
      case 'BUY':
      case 'SWAP': // Compra vía swap
        return 'BUY';
      case 'SELL':
        return 'SELL';
      case 'ADD_LIQUIDITY':
        return 'ACCUMULATE';
      case 'REMOVE_LIQUIDITY':
        return 'DISTRIBUTE';
      default:
        return 'HOLD';
    }
  }

  /**
   * Calcula el flujo neto observado a partir de las distribuciones
   * de acciones por arquetipo.
   *
   * Usa los mismos pesos de flujo que la predicción para consistencia.
   */
  private computeObservedNetFlow(
    observedByArchetype: Record<TraderArchetype, Record<TraderAction, number>>
  ): number {
    let totalWeight = 0;
    let totalFlow = 0;

    for (const archetype of VALID_ARCHETYPES) {
      const dist = observedByArchetype[archetype];
      const totalArchetypeActions = Object.values(dist).reduce((s, v) => s + v, 0);

      if (totalArchetypeActions <= 0) continue;

      // Ponderar por la participación del arquetipo
      const weight = totalArchetypeActions;
      totalWeight += weight;

      let archetypeFlow = 0;
      for (const action of VALID_ACTIONS) {
        archetypeFlow += (dist[action] ?? 0) * ACTION_FLOW_WEIGHTS[action];
      }

      totalFlow += archetypeFlow * weight;
    }

    return totalWeight > 0 ? totalFlow / totalWeight : 0;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

/**
 * Instancia singleton del motor de predicción conductual.
 * Usar: import { behavioralModelEngine } from '@/lib/services/behavioral-model-engine'
 */
export const behavioralModelEngine = new BehavioralModelEngine();
