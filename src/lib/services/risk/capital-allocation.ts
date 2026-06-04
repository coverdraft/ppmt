/**
 * Capital Allocation Engine — CryptoQuant Terminal
 *
 * Provides 16 position-sizing and portfolio-allocation methodologies,
 * from simple fixed-fractional sizing to advanced meta-allocation and
 * reinforcement-learning-inspired approaches.
 *
 * Every calculation uses real, well-known formulas — no random values.
 */

// ---------------------------------------------------------------------------
// 1. Types
// ---------------------------------------------------------------------------

export type AllocationMethod =
  | 'FIXED_FRACTIONAL'
  | 'FIXED_RATIO'
  | 'VOLATILITY_TARGETING'
  | 'MAX_DRAWDOWN_CONTROL'
  | 'EQUAL_WEIGHT'
  | 'MEAN_VARIANCE'
  | 'MIN_VARIANCE'
  | 'RISK_PARITY'
  | 'SCORE_BASED'
  | 'KELLY_MODIFIED'
  | 'REGIME_BASED'
  | 'RL_ALLOCATION'
  | 'META_ALLOCATION'
  | 'ADAPTIVE'
  | 'CUSTOM_COMPOSITE'
  | 'FIXED_AMOUNT';

export type AllocationCategory =
  | 'BASIC'
  | 'ADVANCED'
  | 'PORTFOLIO_OPTIMIZATION'
  | 'ADAPTIVE'
  | 'COMBINED';

export interface Signal {
  tokenAddress: string;
  confidence: number; // 0..1
  direction: 'LONG' | 'SHORT';
}

export interface HistoricalTrade {
  winRate: number;       // 0..1
  avgWin: number;        // e.g. 0.15 = 15 %
  avgLoss: number;       // e.g. 0.05 = 5 %
  totalTrades: number;
}

export interface AllocationInput {
  capital: number;
  currentPositions: Array<{
    tokenAddress: string;
    sizeUsd: number;
    sizePct: number;
  }>;
  signals: Signal[];
  historicalTrades: HistoricalTrade;
  volatility: number;          // annualised vol 0..1
  currentDrawdown: number;     // 0..1
  maxDrawdown: number;         // 0..1
  marketRegime: 'BULL' | 'BEAR' | 'SIDEWAYS' | 'VOLATILE';
  targetVolatility?: number;   // annualised target
  riskPerTrade?: number;       // fraction at risk per trade, e.g. 0.01
  stopLossPct?: number;        // e.g. 0.02
  delta?: number;              // fixed-ratio delta
  currentUnits?: number;       // fixed-ratio current units
  baseSizePct?: number;        // base position size as % of capital
  signalScore?: number;        // 0..100
  fraction?: number;           // Kelly fraction (0..1), default 0.5
  amountPerTrade?: number;     // fixed dollar amount per trade
  streakType?: 'WIN' | 'LOSS';
  streakLength?: number;
  strategies?: Record<string, number>;     // regime → weight
  systems?: Array<{ id: string; weight: number; performance: number }>;
  performanceHistory?: Record<string, number[]>; // systemId → returns
  compositeMethods?: AllocationMethod[];
  compositeWeights?: number[];
  /** Matrix data for portfolio optimisation */
  returns?: number[][];              // [asset][period]
  covMatrix?: number[][];            // [asset][asset]
  volatilities?: number[];           // per-asset annualised vol
  correlations?: number[][];         // [asset][asset]
  qTable?: Record<string, number>;   // state → action-value for RL
  rlState?: string;                  // current state key

  // === FEE / SLIPPAGE AWARENESS (NEW) ===
  /** Estimated total round-trip fee as fraction (e.g. 0.006 = 0.6%). Deducted from position size. */
  estimatedFeePct?: number;
  /** Estimated slippage as fraction (e.g. 0.01 = 1%). Deducted from position size. */
  estimatedSlippagePct?: number;
  /** Minimum net gain after fees required to trade (e.g. 0.03 = 3%). Skip if expected < this. */
  minimumNetGainPct?: number;
  /** Expected gain from the signal (e.g. 0.05 = 5%). Used for fee-vs-gain check. */
  expectedGainPct?: number;
}

export interface AllocationPosition {
  tokenAddress: string;
  sizeUsd: number;
  sizePct: number;
  method: AllocationMethod;
  confidence: number;
}

export interface AllocationOutput {
  positions: AllocationPosition[];
  cashReserve: number;
  totalAllocated: number;
  method: AllocationMethod;
}

export interface AllocationMethodInfo {
  name: string;
  icon: string;
  description: string;
  category: AllocationCategory;
}

// ---------------------------------------------------------------------------
// 2. Method Info Registry (Spanish descriptions)
// ---------------------------------------------------------------------------

export const ALLOCATION_METHODS: Record<AllocationMethod, AllocationMethodInfo> = {
  FIXED_FRACTIONAL: {
    name: 'Fracción Fija',
    icon: '📐',
    description:
      'Arriesga un porcentaje fijo del capital en cada operación. Por ejemplo, si el capital es $10 000 y el riesgo por operación es 1 %, se arriesgan $100. El tamaño de posición se calcula como: riesgo $ / stop-loss %. Es el método más sencillo y recomendado para principiantes porque limita las pérdidas de forma proporcional al capital disponible.',
    category: 'BASIC',
  },
  FIXED_RATIO: {
    name: 'Ratio Fijo (Ryan Jones)',
    icon: '📏',
    description:
      'Método de Ryan Jones: aumenta el tamaño de posición solo cuando el capital crece en una cantidad fija (delta). Si delta = $2 000 y el capital base es $10 000, se añade una unidad de tamaño por cada $2 000 de beneficio. Esto permite un crecimiento acelerado cuando se gana, pero protege el capital durante rachas negativas, ya que no se aumenta el tamaño hasta superar el umbral.',
    category: 'BASIC',
  },
  VOLATILITY_TARGETING: {
    name: 'Objetivo de Volatilidad',
    icon: '🌊',
    description:
      'Ajusta el tamaño de posición de forma inversamente proporcional a la volatilidad del activo. Si un activo tiene el doble de volatilidad que otro, recibe la mitad del tamaño. Fórmula: tamaño = (volatilidad objetivo / volatilidad del activo) × capital. Ideal para mantener un perfil de riesgo constante en carteras con activos de distinta volatilidad.',
    category: 'ADVANCED',
  },
  MAX_DRAWDOWN_CONTROL: {
    name: 'Control de Drawdown Máximo',
    icon: '🛡️',
    description:
      'Reduce progresivamente el tamaño de la posición conforme el drawdown se aproxima al límite máximo tolerado. Fórmula: tamaño = tamañoBase × (1 - drawdownActual / drawdownMáximo). Cuando el drawdown es 0 %, se opera con el tamaño completo; al acercarse al límite, el tamaño tiende a cero. Fundamental para proteger el capital en rachas adversas prolongadas.',
    category: 'ADVANCED',
  },
  EQUAL_WEIGHT: {
    name: 'Peso Igualitario',
    icon: '⚖️',
    description:
      'Distribuye el capital equitativamente entre todos los activos de la cartera. Si hay N activos, cada uno recibe capital / N. Es el enfoque más simple de diversificación y, según estudios académicos, frecuentemente supera a asignaciones más complejas porque evita errores de estimación en parámetros estadísticos.',
    category: 'BASIC',
  },
  MEAN_VARIANCE: {
    name: 'Media-Varianza (Markowitz)',
    icon: '📊',
    description:
      'Optimización de Media-Varianza de Markowitz (MPT). Busca la cartera con máxima rentabilidad esperada para un nivel de riesgo dado. Requiere la matriz de covarianza y el vector de rendimientos esperados. Resuelve: max w′μ − λ w′Σ w, sujeto a Σwᵢ = 1. Es el pilar de la teoría moderna de carteras, aunque sensible a errores en las estimaciones de entrada.',
    category: 'PORTFOLIO_OPTIMIZATION',
  },
  MIN_VARIANCE: {
    name: 'Mínima Varianza',
    icon: '📉',
    description:
      'Minimiza la varianza total de la cartera sin considerar el rendimiento esperado. Resuelve: min w′Σ w sujeto a Σwᵢ = 1. La solución analítica es w = Σ⁻¹1 / (1′Σ⁻¹1). Útil cuando las estimaciones de rendimiento son poco fiables y se prefiere la cartera de menor riesgo absoluto.',
    category: 'PORTFOLIO_OPTIMIZATION',
  },
  RISK_PARITY: {
    name: 'Paridad de Riesgo',
    icon: '🎯',
    description:
      'Cada activo contribuye igualmente al riesgo total de la cartera. La contribución de riesgo del activo i es wᵢ × (Σw)ᵢ. Se igualan todas las contribuciones mediante un algoritmo iterativo. A diferencia de peso igual, los activos menos volátiles reciben mayor asignación. Popularizado por Ray Dalio y Bridgewater en su cartera "All Weather".',
    category: 'PORTFOLIO_OPTIMIZATION',
  },
  SCORE_BASED: {
    name: 'Asignación por Puntuación',
    icon: '🏆',
    description:
      'Dimensiona la posición proporcionalmente a la puntuación de la señal (0-100). Fórmula: tamaño = tamañoBase × (puntuación / 100). Una señal con puntuación 80 recibe el 80 % del tamaño base, mientras que una de 40 solo el 40 %. Permite integrar señales cuantitativas y cualitativas en el dimensionamiento.',
    category: 'ADVANCED',
  },
  KELLY_MODIFIED: {
    name: 'Kelly Modificado (Fraccional)',
    icon: '🧮',
    description:
      'Criterio de Kelly con fracción ajustable: f = (p×b − q) / b, donde p = winRate, q = 1−p, b = avgWin/avgLoss. El Kelly completo maximiza el crecimiento a largo plazo pero produce gran volatilidad; por ello se usa Kelly fraccional (típicamente f/2 o "half-Kelly"). Con fraction = 0.5 se obtiene ~75 % del crecimiento óptimo con ~50 % menos de varianza.',
    category: 'ADVANCED',
  },
  REGIME_BASED: {
    name: 'Asignación por Régimen',
    icon: '🌤️',
    description:
      'Ajusta la asignación según el régimen de mercado detectado (BULL, BEAR, SIDEWAYS, VOLATILE). En mercado alcista se aumenta la exposición; en bajista se reduce. Se define un mapa de pesos por régimen que escala el tamaño base. Permite adaptar la estrategia a las condiciones macro del mercado cripto.',
    category: 'ADAPTIVE',
  },
  RL_ALLOCATION: {
    name: 'Asignación por Aprendizaje por Refuerzo',
    icon: '🤖',
    description:
      'Utiliza una Q-table simplificada donde cada estado del mercado mapea a un valor de asignación. El estado se codifica combinando régimen, volatilidad y drawdown. La acción elegida es argmax Q(s, a). Representa una versión simplificada de RL para entornos sin simulación completa. Ideal cuando se dispone de datos históricos de estados y resultados.',
    category: 'ADAPTIVE',
  },
  META_ALLOCATION: {
    name: 'Meta-Asignación',
    icon: '🔀',
    description:
      'Distribuye capital entre múltiples sistemas de trading basándose en su rendimiento histórico. A cada sistema se le asigna un peso proporcional a su performance normalizada. Si el Sistema A rinde 2× y el B 1×, A recibe el doble. Permite diversificar no solo entre activos sino entre estrategias, reduciendo la dependencia de un único enfoque.',
    category: 'COMBINED',
  },
  ADAPTIVE: {
    name: 'Dimensionamiento Adaptativo',
    icon: '📈',
    description:
      'Aumenta el tamaño tras rachas de victorias y lo reduce tras rachas de derrotas. Tras k victorias consecutivas: tamaño = base × (1 + 0.05 × k). Tras k pérdidas: tamaño = base × (1 − 0.05 × k), con un mínimo del 25 % del base. Captura la idea de "apostar con las ganancias" mientras protege el capital en rachas negativas.',
    category: 'ADAPTIVE',
  },
  CUSTOM_COMPOSITE: {
    name: 'Compuesto Personalizado',
    icon: '🧩',
    description:
      'Combina múltiples métodos de asignación con pesos personalizados. El resultado es un promedio ponderado de las salidas de cada método: posición = Σ(wᵢ × posᵢ) / Σwᵢ. Permite, por ejemplo, combinar Kelly (50 %) + Paridad de Riesgo (30 %) + Volatilidad Objetivo (20 %) para aprovechar las fortalezas de cada enfoque y mitigar sus debilidades individuales.',
    category: 'COMBINED',
  },
  FIXED_AMOUNT: {
    name: 'Cantidad Fija',
    icon: '💲',
    description:
      'Asigna una cantidad fija de dólares a cada operación, independientemente del capital total. Si el monto por operación es $500, cada posición será de $500. Es el método más simple pero no escala con el capital: no aprovecha el crecimiento ni protege contra pérdidas proporcionales. Útil para cuentas pequeñas o trading experimental con riesgo limitado.',
    category: 'BASIC',
  },
};

// ---------------------------------------------------------------------------
// 3. Individual Calculation Functions
// ---------------------------------------------------------------------------

/**
 * a. Fixed Fractional — risk X% of capital per trade.
 * positionSize = (capital × riskPct) / stopLossPct
 */
export function fixedFractional(
  capital: number,
  riskPct: number,   // e.g. 0.01 = 1 %
  stopLossPct: number, // e.g. 0.02 = 2 %
): number {
  if (stopLossPct <= 0) return 0;
  return (capital * riskPct) / stopLossPct;
}

/**
 * b. Fixed Ratio (Ryan Jones) — increase units when capital grows by delta.
 * nextLevel = baseCapital + delta × units
 * positionSize = (capital - (baseCapital + delta × currentUnits)) / delta + 1
 * Simplified: capital at risk per unit.
 */
export function fixedRatio(
  capital: number,
  delta: number,       // dollar increase per unit step
  currentUnits: number, // current number of position units
): number {
  if (delta <= 0) return 0;
  // How many full units can be supported at this capital level?
  // Using the Ryan Jones formula: the capital required for n units is base + n*(n+1)/2 * delta
  // We approximate by checking the incremental unit size.
  // capitalRequiredForNextUnit = baseCapital + delta * currentUnits
  // If capital > capitalRequiredForNextUnit, we can increase.
  // Position size for current level:
  const baseCapital = capital - delta * currentUnits; // working capital at current unit level
  if (baseCapital <= 0) return 0;
  const unitsAffordable = Math.floor(
    (-delta + Math.sqrt(delta * delta + 4 * delta * capital)) / (2 * delta),
  );
  return Math.max(unitsAffordable, 1);
}

/**
 * c. Volatility Targeting — size inversely proportional to asset volatility.
 * positionPct = targetVol / assetVol
 */
export function volatilityTargeting(
  capital: number,
  targetVol: number, // e.g. 0.15 = 15 % annualised
  assetVol: number,  // e.g. 0.60 = 60 % annualised
): number {
  if (assetVol <= 0) return 0;
  const pct = Math.min(targetVol / assetVol, 1); // cap at 100 %
  return capital * pct;
}

/**
 * d. Max Drawdown Control — reduce size as drawdown approaches limit.
 * positionPct = baseSize × (1 - currentDD / maxDD)
 */
export function maxDrawdownControl(
  capital: number,
  maxDD: number,      // e.g. 0.20 = 20 %
  currentDD: number,  // e.g. 0.08 = 8 %
  baseSize: number,   // base fraction of capital, e.g. 0.10
): number {
  if (maxDD <= 0) return 0;
  const scaleFactor = Math.max(1 - currentDD / maxDD, 0);
  return capital * baseSize * scaleFactor;
}

/**
 * e. Equal Weight — simple equal distribution across N assets.
 */
export function equalWeight(
  capital: number,
  numAssets: number,
): number {
  if (numAssets <= 0) return 0;
  return capital / numAssets;
}

/**
 * f. Mean-Variance Optimization (Markowitz MPT).
 * Solves: max w′μ − λ w′Σ w  s.t. Σwᵢ = 1
 * Using the analytical solution with a risk-aversion parameter λ.
 * w* = (Σ⁻¹ μ) / (1′ Σ⁻¹ μ)  (when sum-to-one constraint is applied)
 */
export function meanVarianceOptimization(
  capital: number,
  returns: number[][],  // [asset][period]
  covMatrix: number[][], // [asset][asset]
): number[] {
  const n = returns.length;
  if (n === 0) return [];

  // Compute expected returns vector (mean per asset)
  const mu: number[] = returns.map((r) => {
    if (r.length === 0) return 0;
    return r.reduce((a, b) => a + b, 0) / r.length;
  });

  // Invert covariance matrix (simple Gauss-Jordan for small matrices)
  const invCov = invertMatrix(covMatrix);
  if (invCov.length === 0) {
    // Fallback to equal weight
    const w = 1 / n;
    return Array(n).fill(capital * w);
  }

  // w = Σ⁻¹μ
  const rawWeights = multiplyMatrixVector(invCov, mu);

  // Normalise so weights sum to 1
  const sum = rawWeights.reduce((a, b) => a + b, 0);
  if (sum === 0) return Array(n).fill(capital / n);

  const weights = rawWeights.map((w) => w / sum);

  return weights.map((w) => capital * Math.max(w, 0));
}

/**
 * g. Minimum Variance Portfolio.
 * w = Σ⁻¹1 / (1′Σ⁻¹1)
 */
export function minimumVariance(
  capital: number,
  covMatrix: number[][],
): number[] {
  const n = covMatrix.length;
  if (n === 0) return [];

  const invCov = invertMatrix(covMatrix);
  if (invCov.length === 0) {
    return Array(n).fill(capital / n);
  }

  const ones = Array(n).fill(1);
  const invCovOnes = multiplyMatrixVector(invCov, ones);
  const denom = ones.reduce((sum, _val, i) => sum + invCovOnes[i], 0);

  if (denom === 0) return Array(n).fill(capital / n);

  const weights = invCovOnes.map((v) => v / denom);
  return weights.map((w) => capital * Math.max(w, 0));
}

/**
 * h. Risk Parity — equal risk contribution.
 * Iterative algorithm: adjust weights until each asset's risk contribution
 * equals 1/n of total portfolio risk.
 */
export function riskParity(
  capital: number,
  volatilities: number[],
  correlations: number[][],
): number[] {
  const n = volatilities.length;
  if (n === 0) return [];

  // Build covariance matrix from volatilities and correlations
  // Σᵢⱼ = σᵢ × σⱼ × ρᵢⱼ
  const covMatrix: number[][] = [];
  for (let i = 0; i < n; i++) {
    covMatrix[i] = [];
    for (let j = 0; j < n; j++) {
      const rho = correlations[i]?.[j] ?? (i === j ? 1 : 0);
      covMatrix[i][j] = volatilities[i] * volatilities[j] * rho;
    }
  }

  // Start with inverse-volatility weights
  const invVols = volatilities.map((v) => (v > 0 ? 1 / v : 0));
  const invVolSum = invVols.reduce((a, b) => a + b, 0);
  let weights: number[] =
    invVolSum > 0
      ? invVols.map((iv) => iv / invVolSum)
      : Array(n).fill(1 / n);

  // Iterative risk parity (Spinu 2013 formulation)
  const maxIter = 100;
  const tol = 1e-8;

  for (let iter = 0; iter < maxIter; iter++) {
    // Portfolio variance: σ² = w′Σw
    const sigma2 = portfolioVariance(weights, covMatrix);
    if (sigma2 <= 0) break;

    // Marginal risk contribution: (Σw)ᵢ
    const marginalRisk = multiplyMatrixVector(covMatrix, weights);

    // Risk contribution: RCᵢ = wᵢ × (Σw)ᵢ / σ
    const riskContributions = weights.map((w, i) => (w * marginalRisk[i]) / Math.sqrt(sigma2));
    const totalRC = riskContributions.reduce((a, b) => a + b, 0);
    if (totalRC === 0) break;

    const targetRC = totalRC / n;

    // Check convergence
    const maxDeviation = Math.max(
      ...riskContributions.map((rc) => Math.abs(rc - targetRC)),
    );
    if (maxDeviation < tol) break;

    // Update weights: wᵢ *= (targetRC / RCᵢ)^0.5 (square-root update for stability)
    weights = weights.map((w, i) => {
      if (riskContributions[i] <= 0) return w;
      return w * Math.sqrt(targetRC / riskContributions[i]);
    });

    // Normalise
    const wSum = weights.reduce((a, b) => a + b, 0);
    if (wSum > 0) weights = weights.map((w) => w / wSum);
  }

  return weights.map((w) => capital * Math.max(w, 0));
}

/**
 * i. Score-Based Sizing — size proportional to signal confidence.
 * positionSize = baseSize × (score / 100)
 */
export function scoreBasedSizing(
  capital: number,
  baseSize: number,   // fraction of capital, e.g. 0.10
  signalScore: number, // 0..100
): number {
  const scoreNorm = Math.max(0, Math.min(signalScore, 100)) / 100;
  return capital * baseSize * scoreNorm;
}

/**
 * j. Kelly Modified — Kelly criterion with fractional Kelly.
 * f* = (p × b − q) / b,  where p = winRate, q = 1−p, b = avgWin / avgLoss
 * applied fraction = f* × fraction
 */
export function kellyModified(
  capital: number,
  winRate: number,  // p
  avgWin: number,   // e.g. 0.15
  avgLoss: number,  // e.g. 0.05
  fraction: number = 0.5,
): number {
  const p = winRate;
  const q = 1 - p;
  const b = avgLoss > 0 ? avgWin / avgLoss : 0;

  if (b <= 0) return 0;

  // Full Kelly
  const kellyF = (p * b - q) / b;

  // Fractional Kelly (never allocate more than capital)
  const adjustedF = Math.max(kellyF * fraction, 0);
  return capital * Math.min(adjustedF, 1);
}

/**
 * k. Regime-Based Allocation — adjust by market regime.
 * Returns the allocation percentage based on the regime and its mapped weight.
 */
export function regimeBasedAllocation(
  capital: number,
  regime: 'BULL' | 'BEAR' | 'SIDEWAYS' | 'VOLATILE',
  strategies: Record<string, number>, // regime → exposure weight
): number {
  const defaultWeights: Record<string, number> = {
    BULL: 0.80,
    BEAR: 0.20,
    SIDEWAYS: 0.40,
    VOLATILE: 0.30,
  };

  const weight = strategies[regime] ?? defaultWeights[regime] ?? 0.40;
  return capital * Math.max(0, Math.min(weight, 1));
}

/**
 * l. RL Allocation — simplified Q-table lookup.
 * Returns allocation based on the Q-value for the current state.
 */
export function rlAllocation(
  capital: number,
  state: string,
  qTable: Record<string, number>,
): number {
  const qValue = qTable[state] ?? 0;

  // Normalise Q-value to [0, 1] using sigmoid-like mapping
  // If all Q-values are around the same range, this gives proportional allocation
  const sigmoid = 1 / (1 + Math.exp(-qValue));

  // Scale to a reasonable allocation range [0.10, 0.90]
  const allocationPct = 0.1 + 0.8 * sigmoid;
  return capital * allocationPct;
}

/**
 * m. Meta-Allocation — allocate between trading systems based on performance.
 * Each system gets capital proportional to its normalised performance.
 */
export function metaAllocation(
  capital: number,
  systems: Array<{ id: string; weight: number; performance: number }>,
  _performanceHistory?: Record<string, number[]>,
): number[] {
  if (systems.length === 0) return [];

  // Combined score = weight × normalised performance
  const performances = systems.map((s) => s.performance);
  const maxPerf = Math.max(...performances, 1);

  const scores = systems.map((s) => {
    const normPerf = s.performance / maxPerf;
    return s.weight * normPerf;
  });

  const totalScore = scores.reduce((a, b) => a + b, 0);
  if (totalScore === 0) return systems.map(() => capital / systems.length);

  return scores.map((s) => capital * (s / totalScore));
}

/**
 * n. Adaptive Position Sizing — increase on wins, decrease on losses.
 * On win streak:  size = base × (1 + 0.05 × streakLength)
 * On loss streak: size = base × (1 − 0.05 × streakLength), min 0.25 × base
 */
export function adaptivePositionSizing(
  capital: number,
  streakType: 'WIN' | 'LOSS',
  streakLength: number,
  baseSize: number, // fraction of capital
): number {
  const base = capital * baseSize;

  if (streakType === 'WIN') {
    const factor = 1 + 0.05 * streakLength;
    return capital * Math.min(baseSize * factor, 1);
  }

  // LOSS
  const factor = Math.max(1 - 0.05 * streakLength, 0.25);
  return capital * baseSize * factor;
}

/**
 * o. Fixed Amount — fixed dollar amount per trade.
 */
export function fixedAmount(
  capital: number,
  amountPerTrade: number,
): number {
  return Math.min(amountPerTrade, capital);
}

/**
 * p. Custom Composite — combine multiple methods with weights.
 * position = Σ(wᵢ × positionᵢ) / Σwᵢ
 */
export function customComposite(
  capital: number,
  methods: AllocationMethod[],
  weights: number[],
  engine: CapitalAllocationEngine,
  input: AllocationInput,
): number {
  if (methods.length === 0 || weights.length === 0) return 0;

  const positions: number[] = [];
  const validWeights: number[] = [];

  for (let i = 0; i < methods.length; i++) {
    try {
      const output = engine.calculate(methods[i], input);
      const totalPos = output.positions.reduce((s, p) => s + p.sizeUsd, 0);
      positions.push(totalPos);
      validWeights.push(weights[i] ?? 1);
    } catch {
      positions.push(0);
      validWeights.push(0);
    }
  }

  const totalWeight = validWeights.reduce((a, b) => a + b, 0);
  if (totalWeight === 0) return 0;

  const combined = positions.reduce(
    (sum, pos, i) => sum + pos * validWeights[i],
    0,
  );

  return Math.min(combined / totalWeight, capital);
}

// ---------------------------------------------------------------------------
// Matrix Helpers (for portfolio optimisation)
// ---------------------------------------------------------------------------

/**
 * Inverts a square matrix using Gauss-Jordan elimination.
 * Returns empty array if the matrix is singular.
 */
function invertMatrix(m: number[][]): number[][] {
  const n = m.length;
  if (n === 0) return [];

  // Augment with identity
  const aug: number[][] = m.map((row, i) => {
    const identityRow = Array(n).fill(0);
    identityRow[i] = 1;
    return [...row, ...identityRow];
  });

  for (let col = 0; col < n; col++) {
    // Find pivot
    let maxRow = col;
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(aug[row][col]) > Math.abs(aug[maxRow][col])) {
        maxRow = row;
      }
    }

    // Swap rows
    [aug[col], aug[maxRow]] = [aug[maxRow], aug[col]];

    // Singular check
    if (Math.abs(aug[col][col]) < 1e-12) return [];

    // Pivot
    const pivot = aug[col][col];
    for (let j = 0; j < 2 * n; j++) {
      aug[col][j] /= pivot;
    }

    // Eliminate other rows
    for (let row = 0; row < n; row++) {
      if (row === col) continue;
      const factor = aug[row][col];
      for (let j = 0; j < 2 * n; j++) {
        aug[row][j] -= factor * aug[col][j];
      }
    }
  }

  // Extract inverse
  return aug.map((row) => row.slice(n));
}

/** Multiply matrix × vector */
function multiplyMatrixVector(m: number[][], v: number[]): number[] {
  return m.map((row) => row.reduce((sum, val, j) => sum + val * (v[j] ?? 0), 0));
}

/** Compute portfolio variance: w′Σw */
function portfolioVariance(weights: number[], covMatrix: number[][]): number {
  let variance = 0;
  const n = weights.length;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      variance += weights[i] * weights[j] * (covMatrix[i]?.[j] ?? 0);
    }
  }
  return variance;
}

// ---------------------------------------------------------------------------
// 4. Main Engine Class
// ---------------------------------------------------------------------------

export class CapitalAllocationEngine {
  /**
   * Calculate position allocation for a single method.
   */
  calculate(method: AllocationMethod, input: AllocationInput): AllocationOutput {
    let positions: AllocationPosition[] = [];
    const capital = input.capital;
    const signals = input.signals;

    switch (method) {
      case 'FIXED_FRACTIONAL': {
        const riskPct = input.riskPerTrade ?? 0.01;
        const stopLoss = input.stopLossPct ?? 0.02;
        const size = fixedFractional(capital, riskPct, stopLoss);
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: size,
          sizePct: size / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'FIXED_RATIO': {
        const delta = input.delta ?? 2000;
        const currentUnits = input.currentUnits ?? 0;
        const units = fixedRatio(capital, delta, currentUnits);
        const perUnit = capital / Math.max(units, 1);
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perUnit,
          sizePct: perUnit / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'VOLATILITY_TARGETING': {
        const targetVol = input.targetVolatility ?? 0.15;
        const assetVol = input.volatility > 0 ? input.volatility : 0.5;
        const size = volatilityTargeting(capital, targetVol, assetVol);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => {
          // Adjust by individual signal's inverse-vol (use confidence as proxy)
          const adjustedSize = perSignal * s.confidence;
          return {
            tokenAddress: s.tokenAddress,
            sizeUsd: adjustedSize,
            sizePct: adjustedSize / capital,
            method,
            confidence: s.confidence,
          };
        });
        break;
      }

      case 'MAX_DRAWDOWN_CONTROL': {
        const maxDD = input.maxDrawdown ?? 0.20;
        const currentDD = input.currentDrawdown ?? 0;
        const baseSize = input.baseSizePct ?? 0.10;
        const size = maxDrawdownControl(capital, maxDD, currentDD, baseSize);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'EQUAL_WEIGHT': {
        const numAssets = Math.max(signals.length, 1);
        const size = equalWeight(capital, numAssets);
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: size,
          sizePct: size / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'MEAN_VARIANCE': {
        const returns = input.returns ?? signals.map(() => [0.01]);
        const covMatrix = input.covMatrix ?? signals.map(() => signals.map(() => (input.volatility ?? 0.5) ** 2));
        const allocations = meanVarianceOptimization(capital, returns, covMatrix);
        positions = signals.map((s, i) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: allocations[i] ?? 0,
          sizePct: (allocations[i] ?? 0) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'MIN_VARIANCE': {
        const covMatrix = input.covMatrix ?? signals.map(() => signals.map(() => (input.volatility ?? 0.5) ** 2));
        const allocations = minimumVariance(capital, covMatrix);
        positions = signals.map((s, i) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: allocations[i] ?? 0,
          sizePct: (allocations[i] ?? 0) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'RISK_PARITY': {
        const vols = input.volatilities ?? signals.map(() => input.volatility ?? 0.5);
        const corrs = input.correlations ?? signals.map((_, i) => signals.map((__, j) => (i === j ? 1 : 0.3)));
        const allocations = riskParity(capital, vols, corrs);
        positions = signals.map((s, i) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: allocations[i] ?? 0,
          sizePct: (allocations[i] ?? 0) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'SCORE_BASED': {
        const baseSize = input.baseSizePct ?? 0.10;
        const score = input.signalScore ?? 50;
        const size = scoreBasedSizing(capital, baseSize, score);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'KELLY_MODIFIED': {
        const winRate = input.historicalTrades.winRate;
        const avgWin = input.historicalTrades.avgWin;
        const avgLoss = input.historicalTrades.avgLoss;
        const fraction = input.fraction ?? 0.5;
        const size = kellyModified(capital, winRate, avgWin, avgLoss, fraction);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'REGIME_BASED': {
        const strategies = input.strategies ?? {};
        const size = regimeBasedAllocation(capital, input.marketRegime, strategies);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'RL_ALLOCATION': {
        const state = input.rlState ?? input.marketRegime;
        const qTable = input.qTable ?? {
          BULL: 2.0,
          BEAR: -1.5,
          SIDEWAYS: 0.3,
          VOLATILE: -0.5,
        };
        const size = rlAllocation(capital, state, qTable);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'META_ALLOCATION': {
        const systems =
          input.systems ?? signals.map((s) => ({
            id: s.tokenAddress,
            weight: s.confidence,
            performance: s.confidence,
          }));
        const allocations = metaAllocation(capital, systems, input.performanceHistory);
        positions = signals.map((s, i) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: allocations[i] ?? 0,
          sizePct: (allocations[i] ?? 0) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'ADAPTIVE': {
        const streakType = input.streakType ?? 'WIN';
        const streakLength = input.streakLength ?? 0;
        const baseSize = input.baseSizePct ?? 0.10;
        const size = adaptivePositionSizing(capital, streakType, streakLength, baseSize);
        const perSignal = signals.length > 0 ? size / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'CUSTOM_COMPOSITE': {
        const methods = input.compositeMethods ?? ['EQUAL_WEIGHT', 'KELLY_MODIFIED'];
        const weights = input.compositeWeights ?? [0.5, 0.5];
        const totalSize = customComposite(capital, methods, weights, this, input);
        const perSignal = signals.length > 0 ? totalSize / signals.length : 0;
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: perSignal * s.confidence,
          sizePct: (perSignal * s.confidence) / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      case 'FIXED_AMOUNT': {
        const amount = input.amountPerTrade ?? 500;
        const size = fixedAmount(capital, amount);
        positions = signals.map((s) => ({
          tokenAddress: s.tokenAddress,
          sizeUsd: size,
          sizePct: size / capital,
          method,
          confidence: s.confidence,
        }));
        break;
      }

      default: {
        // Exhaustive check — should never reach here
        const _exhaustive: never = method;
        throw new Error(`Unknown allocation method: ${String(_exhaustive)}`);
      }
    }

    // === FEE / SLIPPAGE POST-PROCESSING ===
    // If fee data is provided, adjust positions to account for trading costs.
    // This ensures the brain's fee-awareness flows into position sizing.
    if (input.estimatedFeePct || input.estimatedSlippagePct) {
      const totalCostPct = (input.estimatedFeePct || 0) + (input.estimatedSlippagePct || 0);
      const minimumNetGain = input.minimumNetGainPct ?? totalCostPct * 3; // 3x safety margin

      positions = positions.map(pos => {
        // Deduct fees from position: effective size = size * (1 - costPct)
        const effectiveSize = pos.sizeUsd * (1 - totalCostPct);

        // Check if expected gain justifies the trade
        const expectedGain = input.expectedGainPct ?? 0;
        const netGain = expectedGain - totalCostPct;
        const shouldTrade = netGain >= minimumNetGain;

        // If the trade doesn't justify fees, reduce position to minimum or zero
        if (!shouldTrade && minimumNetGain > 0) {
          // Scale down proportionally to how far we are from minimum
          const scaleFactor = netGain > 0 ? netGain / minimumNetGain : 0;
          return {
            ...pos,
            sizeUsd: effectiveSize * scaleFactor,
            sizePct: (effectiveSize * scaleFactor) / capital,
            confidence: pos.confidence * scaleFactor,
          };
        }

        return {
          ...pos,
          sizeUsd: effectiveSize,
          sizePct: effectiveSize / capital,
        };
      });
    }

    const totalAllocated = positions.reduce((s, p) => s + p.sizeUsd, 0);
    const cashReserve = Math.max(capital - totalAllocated, 0);

    return {
      positions,
      cashReserve,
      totalAllocated,
      method,
    };
  }

  /**
   * Get all methods belonging to a category.
   */
  getMethodByCategory(category: AllocationCategory): AllocationMethodInfo[] {
    return (Object.entries(ALLOCATION_METHODS) as [AllocationMethod, AllocationMethodInfo][])
      .filter(([, info]) => info.category === category)
      .map(([method, info]) => ({ ...info, method }));
  }

  /**
   * Get metadata for a specific method.
   */
  getMethodInfo(method: AllocationMethod): AllocationMethodInfo {
    return ALLOCATION_METHODS[method];
  }

  /**
   * Test multiple allocation methods on the same input and return the one
   * that maximises total allocated capital while maintaining a cash reserve
   * of at least 10 %.
   */
  optimizeAllocation(
    methods: AllocationMethod[],
    input: AllocationInput,
  ): AllocationOutput {
    if (methods.length === 0) {
      return this.calculate('EQUAL_WEIGHT', input);
    }

    const minCashReservePct = 0.10;
    let best: AllocationOutput | null = null;
    let bestScore = -Infinity;

    for (const method of methods) {
      try {
        const output = this.calculate(method, input);

        // Scoring: maximise allocated capital, but penalise if cash reserve < 10 %
        const cashReservePct = output.cashReserve / input.capital;
        let score = output.totalAllocated;

        if (cashReservePct < minCashReservePct) {
          // Heavy penalty for insufficient reserve
          score *= cashReservePct / minCashReservePct;
        }

        // Small bonus for higher average confidence across positions
        const avgConfidence =
          output.positions.length > 0
            ? output.positions.reduce((s, p) => s + p.confidence, 0) /
              output.positions.length
            : 0;
        score *= 1 + 0.1 * avgConfidence;

        if (score > bestScore) {
          bestScore = score;
          best = output;
        }
      } catch {
        // Skip failing methods
        continue;
      }
    }

    return best ?? this.calculate('EQUAL_WEIGHT', input);
  }
}

// ---------------------------------------------------------------------------
// Singleton export for convenience
// ---------------------------------------------------------------------------

export const capitalAllocationEngine = new CapitalAllocationEngine();
