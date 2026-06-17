/**
 * Statistical Validation Layer - CryptoQuant Terminal
 * Capa de Validación Estadística Profesional
 *
 * Provee rigor estadístico a todos los motores analíticos:
 * - Intervalos de confianza (95% CI)
 * - Significancia estadística (p-values, t-tests, chi-square)
 * - Mínimo muestral obligatorio
 * - Power analysis para determinación de sample size
 * - Validación de correlaciones
 * - Decay temporal para observaciones antiguas
 *
 * NINGÚN motor debe hacer predicciones sin pasar por esta capa.
 */

// ============================================================
// TYPES & INTERFACES
// ============================================================

export interface ConfidenceInterval {
  estimate: number;
  lower: number;
  upper: number;
  confidenceLevel: number; // e.g. 0.95
  sampleSize: number;
  isReliable: boolean; // true if sampleSize >= minimum required
}

export interface SignificanceTest {
  testType: 't-test' | 'chi-square' | 'z-test' | 'fisher-exact' | 'mann-whitney';
  statistic: number;
  pValue: number;
  isSignificant: boolean;
  alpha: number; // significance level (typically 0.05)
  sampleSize: number;
  effectSize?: number; // Cohen's d or equivalent
  interpretation: string;
}

export interface ValidationResult {
  isValid: boolean;
  confidence: number; // 0-1 overall confidence in the result
  sampleSize: number;
  minSampleSize: number;
  confidenceInterval: ConfidenceInterval;
  warnings: string[];
  recommendation: 'USE' | 'USE_WITH_CAUTION' | 'DO_NOT_USE' | 'INSUFFICIENT_DATA';
}

export interface PowerAnalysisResult {
  achievedPower: number;
  requiredSampleSize: number;
  currentSampleSize: number;
  effectSize: number; // Cohen's d
  alpha: number;
  isAdequatelyPowered: boolean; // power >= 0.8
  recommendation: string;
}

export interface CorrelationTest {
  coefficient: number; // Pearson r or Spearman rho
  pValue: number;
  isSignificant: boolean;
  confidenceInterval: ConfidenceInterval;
  interpretation: string;
  sampleSize: number;
}

export interface SampleSufficiency {
  category: string; // e.g., "SMART_MONEY+GROWTH+BUY"
  currentSamples: number;
  minRequired: number;
  optimalSamples: number;
  sufficiencyLevel: 'INSUFFICIENT' | 'MINIMAL' | 'ADEQUATE' | 'OPTIMAL';
  confidenceAchieved: number; // 0-1
  additionalSamplesNeeded: number;
}

export interface TemporalDecayConfig {
  halfLifeDays: number; // Observaciones pierden 50% peso en este tiempo
  maxAgeDays: number;   // Observaciones más viejas se descartan
  decayFunction: 'exponential' | 'linear' | 'step';
}

// ============================================================
// CONSTANTS
// ============================================================

/** Mínimo muestral absoluto - por debajo de esto NO se hacen predicciones */
const ABSOLUTE_MINIMUM_SAMPLES = 10;

/** Mínimo muestral para confianza moderada */
const MODERATE_CONFIDENCE_SAMPLES = 30;

/** Mínimo muestral para alta confianza (teorema del límite central) */
const HIGH_CONFIDENCE_SAMPLES = 100;

/** Mínimo muestral para conclusión robusta */
const ROBUST_SAMPLES = 500;

/** Nivel de significancia estándar */
const DEFAULT_ALPHA = 0.05;

/** Nivel de confianza estándar */
const DEFAULT_CONFIDENCE_LEVEL = 0.95;

/** Power estándar para análisis */
const DEFAULT_POWER = 0.80;

/** Valores críticos de t-distribución para alpha=0.05 (two-tailed) */
const T_CRITICAL_VALUES: Record<number, number> = {
  1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
  6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
  11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
  16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
  25: 2.060, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000,
  80: 1.990, 100: 1.984, 120: 1.980, 1000: 1.962,
};

/** Z-value para 95% CI */
const Z_95 = 1.96;

/** Z-value para 99% CI */
const Z_99 = 2.576;

/** Valores críticos de chi-square para df=1 */
const CHI_SQUARE_CRITICAL: Record<number, number> = {
  1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070,
  6: 12.592, 7: 14.067, 8: 15.507, 9: 16.919, 10: 18.307,
};

// ============================================================
// CORE STATISTICAL FUNCTIONS
// ============================================================

/**
 * Calcula intervalo de confianza para una proporción
 * Usa método Wilson score (más preciso que normal approximation para muestras pequeñas)
 */
export function proportionConfidenceInterval(
  successes: number,
  total: number,
  confidenceLevel: number = DEFAULT_CONFIDENCE_LEVEL
): ConfidenceInterval {
  if (total === 0) {
    return {
      estimate: 0, lower: 0, upper: 0,
      confidenceLevel, sampleSize: 0, isReliable: false,
    };
  }

  const z = confidenceLevel >= 0.99 ? Z_99 : Z_95;
  const p = successes / total;
  const n = total;

  // Wilson score interval
  const denominator = 1 + z * z / n;
  const center = (p + z * z / (2 * n)) / denominator;
  const margin = z * Math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator;

  const lower = Math.max(0, center - margin);
  const upper = Math.min(1, center + margin);

  return {
    estimate: p,
    lower,
    upper,
    confidenceLevel,
    sampleSize: n,
    isReliable: n >= MODERATE_CONFIDENCE_SAMPLES,
  };
}

/**
 * Calcula intervalo de confianza para una media
 * Usa t-distribución para muestras pequeñas, z para grandes
 */
export function meanConfidenceInterval(
  values: number[],
  confidenceLevel: number = DEFAULT_CONFIDENCE_LEVEL
): ConfidenceInterval {
  const n = values.length;
  if (n < 2) {
    return {
      estimate: n === 1 ? values[0] : 0,
      lower: 0, upper: 0,
      confidenceLevel, sampleSize: n, isReliable: false,
    };
  }

  const mean = values.reduce((s, v) => s + v, 0) / n;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
  const stdDev = Math.sqrt(variance);
  const stdError = stdDev / Math.sqrt(n);

  // Use t-distribution for small samples
  const df = n - 1;
  const tCritical = getTCritical(df);
  const margin = tCritical * stdError;

  return {
    estimate: mean,
    lower: mean - margin,
    upper: mean + margin,
    confidenceLevel,
    sampleSize: n,
    isReliable: n >= MODERATE_CONFIDENCE_SAMPLES,
  };
}

/**
 * T-test para comparar dos muestras independientes
 * H0: mean1 = mean2
 */
export function independentTTest(
  sample1: number[],
  sample2: number[],
  alpha: number = DEFAULT_ALPHA
): SignificanceTest {
  const n1 = sample1.length;
  const n2 = sample2.length;

  if (n1 < 2 || n2 < 2) {
    return {
      testType: 't-test',
      statistic: 0, pValue: 1, isSignificant: false,
      alpha, sampleSize: n1 + n2,
      interpretation: 'Insufficient data for t-test (need >= 2 per group)',
    };
  }

  const mean1 = sample1.reduce((s, v) => s + v, 0) / n1;
  const mean2 = sample2.reduce((s, v) => s + v, 0) / n2;
  const var1 = sample1.reduce((s, v) => s + (v - mean1) ** 2, 0) / (n1 - 1);
  const var2 = sample2.reduce((s, v) => s + (v - mean2) ** 2, 0) / (n2 - 1);

  // Welch's t-test (does not assume equal variances)
  const se = Math.sqrt(var1 / n1 + var2 / n2);
  if (se === 0) {
    return {
      testType: 't-test',
      statistic: 0, pValue: 1, isSignificant: false,
      alpha, sampleSize: n1 + n2,
      interpretation: 'Zero standard error - samples are identical',
    };
  }

  const tStatistic = (mean1 - mean2) / se;

  // Welch-Satterthwaite degrees of freedom
  const dfNumerator = (var1 / n1 + var2 / n2) ** 2;
  const dfDenominator = (var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1);
  const df = dfDenominator > 0 ? dfNumerator / dfDenominator : n1 + n2 - 2;

  // Approximate p-value using t-distribution approximation
  const pValue = approximatePValue(tStatistic, df);
  const isSignificant = pValue < alpha;

  // Cohen's d effect size
  const pooledStdDev = Math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2));
  const cohensD = pooledStdDev > 0 ? (mean1 - mean2) / pooledStdDev : 0;

  let interpretation = '';
  if (!isSignificant) {
    interpretation = `No significant difference (p=${pValue.toFixed(4)} > α=${alpha}). Cannot reject H0.`;
  } else {
    const effectDesc = Math.abs(cohensD) < 0.2 ? 'negligible' :
      Math.abs(cohensD) < 0.5 ? 'small' :
      Math.abs(cohensD) < 0.8 ? 'medium' : 'large';
    interpretation = `Significant difference (p=${pValue.toFixed(4)} < α=${alpha}). Effect size: ${effectDesc} (d=${cohensD.toFixed(3)}).`;
  }

  return {
    testType: 't-test',
    statistic: tStatistic,
    pValue,
    isSignificant,
    alpha,
    sampleSize: n1 + n2,
    effectSize: cohensD,
    interpretation,
  };
}

/**
 * Chi-square test de independencia
 * Para tablas de contingencia: P(outcome | conditions)
 */
export function chiSquareTest(
  observed: number[][],
  alpha: number = DEFAULT_ALPHA
): SignificanceTest {
  const rows = observed.length;
  const cols = observed[0]?.length ?? 0;

  if (rows < 2 || cols < 2) {
    return {
      testType: 'chi-square',
      statistic: 0, pValue: 1, isSignificant: false,
      alpha, sampleSize: 0,
      interpretation: 'Need at least 2x2 contingency table',
    };
  }

  // Calculate expected frequencies
  const rowTotals = observed.map(row => row.reduce((s, v) => s + v, 0));
  const colTotals = Array(cols).fill(0);
  let total = 0;
  for (const row of observed) {
    for (let j = 0; j < cols; j++) {
      colTotals[j] += row[j];
      total += row[j];
    }
  }

  if (total === 0) {
    return {
      testType: 'chi-square',
      statistic: 0, pValue: 1, isSignificant: false,
      alpha, sampleSize: 0,
      interpretation: 'No observations in contingency table',
    };
  }

  // Chi-square statistic
  let chiSquare = 0;
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const expected = (rowTotals[i] * colTotals[j]) / total;
      if (expected > 0) {
        chiSquare += (observed[i][j] - expected) ** 2 / expected;
      }
    }
  }

  const df = (rows - 1) * (cols - 1);
  const pValue = chiSquarePValue(chiSquare, df);
  const isSignificant = pValue < alpha;

  // Cramér's V effect size
  const cramersV = total > 0 ? Math.sqrt(chiSquare / (total * Math.min(rows - 1, cols - 1))) : 0;

  let interpretation = '';
  if (!isSignificant) {
    interpretation = `No significant association (p=${pValue.toFixed(4)}). Variables are likely independent.`;
  } else {
    const effectDesc = cramersV < 0.1 ? 'negligible' :
      cramersV < 0.3 ? 'small' :
      cramersV < 0.5 ? 'medium' : 'large';
    interpretation = `Significant association (p=${pValue.toFixed(4)}). Effect: ${effectDesc} (Cramér's V=${cramersV.toFixed(3)}).`;
  }

  return {
    testType: 'chi-square',
    statistic: chiSquare,
    pValue,
    isSignificant,
    alpha,
    sampleSize: total,
    effectSize: cramersV,
    interpretation,
  };
}

/**
 * Test de significancia para correlación Pearson
 */
export function correlationSignificanceTest(
  r: number,
  n: number,
  alpha: number = DEFAULT_ALPHA
): CorrelationTest {
  if (n < 3) {
    return {
      coefficient: r, pValue: 1, isSignificant: false,
      confidenceInterval: { estimate: r, lower: -1, upper: 1, confidenceLevel: 0.95, sampleSize: n, isReliable: false },
      interpretation: 'Need at least 3 observations for correlation test',
      sampleSize: n,
    };
  }

  // t-statistic for correlation
  const tStat = r * Math.sqrt((n - 2) / (1 - r * r + 1e-10));
  const df = n - 2;
  const pValue = approximatePValue(tStat, df);
  const isSignificant = pValue < alpha;

  // Fisher z-transformation for CI
  const z = 0.5 * Math.log((1 + r) / (1 - r + 1e-10));
  const seZ = 1 / Math.sqrt(n - 3);
  const zCritical = Z_95;
  const zLower = z - zCritical * seZ;
  const zUpper = z + zCritical * seZ;

  // Transform back
  const rLower = (Math.exp(2 * zLower) - 1) / (Math.exp(2 * zLower) + 1);
  const rUpper = (Math.exp(2 * zUpper) - 1) / (Math.exp(2 * zUpper) + 1);

  const absR = Math.abs(r);
  const strengthDesc = absR < 0.1 ? 'negligible' :
    absR < 0.3 ? 'small' :
    absR < 0.5 ? 'moderate' :
    absR < 0.7 ? 'strong' : 'very strong';
  const directionDesc = r > 0 ? 'positive' : r < 0 ? 'negative' : 'no';

  let interpretation = '';
  if (!isSignificant) {
    interpretation = `${directionDesc} correlation is not statistically significant (r=${r.toFixed(3)}, p=${pValue.toFixed(4)}).`;
  } else {
    interpretation = `Significant ${directionDesc} ${strengthDesc} correlation (r=${r.toFixed(3)}, p=${pValue.toFixed(4)}, 95% CI: [${rLower.toFixed(3)}, ${rUpper.toFixed(3)}]).`;
  }

  return {
    coefficient: r,
    pValue,
    isSignificant,
    confidenceInterval: {
      estimate: r, lower: rLower, upper: rUpper,
      confidenceLevel: 0.95, sampleSize: n,
      isReliable: n >= MODERATE_CONFIDENCE_SAMPLES,
    },
    interpretation,
    sampleSize: n,
  };
}

// ============================================================
// SAMPLE SIZE & POWER ANALYSIS
// ============================================================

/**
 * Calcula el tamaño muestral necesario para detectar un efecto dado
 * Basado en power analysis para test de proporciones
 */
export function calculateRequiredSampleSize(
  baselineProportion: number,
  minimumDetectableEffect: number,
  alpha: number = DEFAULT_ALPHA,
  power: number = DEFAULT_POWER
): PowerAnalysisResult {
  // Z-values for alpha and power
  const zAlpha = alpha <= 0.05 ? Z_95 : Z_99;
  const zBeta = power >= 0.9 ? Z_99 : power >= 0.8 ? Z_84 : Z_75;
  const zBetaApprox = power >= 0.9 ? 1.282 : power >= 0.8 ? 0.842 : 0.674;

  const p1 = baselineProportion;
  const p2 = baselineProportion + minimumDetectableEffect;
  const pAvg = (p1 + p2) / 2;

  // Sample size formula for two-proportion z-test
  const n = Math.ceil(
    ((zAlpha * Math.sqrt(2 * pAvg * (1 - pAvg)) +
      zBetaApprox * Math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) /
    ((p2 - p1) ** 2 + 1e-10)
  );

  // Cohen's h effect size for proportions
  const h = 2 * Math.asin(Math.sqrt(p2)) - 2 * Math.asin(Math.sqrt(p1));

  return {
    achievedPower: power,
    requiredSampleSize: n,
    currentSampleSize: 0,
    effectSize: h,
    alpha,
    isAdequatelyPowered: false,
    recommendation: `Need ${n} samples per group to detect ${minimumDetectableEffect > 0 ? '+' : ''}${(minimumDetectableEffect * 100).toFixed(1)}% effect with ${(power * 100).toFixed(0)}% power at α=${alpha}`,
  };
}

/**
 * Evalúa si el número de muestras es suficiente para una categoría específica
 */
export function assessSampleSufficiency(
  category: string,
  currentSamples: number,
  targetProportion?: number
): SampleSufficiency {
  let minRequired = ABSOLUTE_MINIMUM_SAMPLES;
  let optimalSamples = ROBUST_SAMPLES;

  // Adjust minimums based on target proportion
  if (targetProportion !== undefined && targetProportion > 0) {
    const powerResult = calculateRequiredSampleSize(
      0.5, // baseline
      targetProportion - 0.5, // effect size
      DEFAULT_ALPHA,
      DEFAULT_POWER
    );
    minRequired = Math.max(ABSOLUTE_MINIMUM_SAMPLES, powerResult.requiredSampleSize);
    optimalSamples = Math.max(ROBUST_SAMPLES, minRequired * 3);
  }

  const sufficiencyLevel: SampleSufficiency['sufficiencyLevel'] =
    currentSamples < ABSOLUTE_MINIMUM_SAMPLES ? 'INSUFFICIENT' :
    currentSamples < minRequired ? 'MINIMAL' :
    currentSamples < optimalSamples ? 'ADEQUATE' : 'OPTIMAL';

  // Confidence based on sample size (logarithmic scaling)
  const confidenceAchieved = Math.min(1, currentSamples >= optimalSamples ? 0.99 :
    currentSamples >= minRequired ? 0.7 + 0.29 * Math.log10(currentSamples / minRequired) / Math.log10(optimalSamples / minRequired) :
    currentSamples >= ABSOLUTE_MINIMUM_SAMPLES ? 0.3 + 0.4 * (currentSamples - ABSOLUTE_MINIMUM_SAMPLES) / (minRequired - ABSOLUTE_MINIMUM_SAMPLES) :
    currentSamples / ABSOLUTE_MINIMUM_SAMPLES * 0.3
  );

  return {
    category,
    currentSamples,
    minRequired,
    optimalSamples,
    sufficiencyLevel,
    confidenceAchieved,
    additionalSamplesNeeded: Math.max(0, optimalSamples - currentSamples),
  };
}

// ============================================================
// VALIDATION GATE
// ============================================================

/**
 * VALIDATION GATE - Todo motor debe pasar por aquí antes de emitir predicciones
 *
 * Retorna un ValidationResult que indica si la predicción es usable,
 * con qué confianza, y qué precauciones tomar.
 */
export function validatePrediction(
  estimate: number,
  sampleSize: number,
  successes?: number,
  category?: string
): ValidationResult {
  const warnings: string[] = [];
  let recommendation: ValidationResult['recommendation'] = 'USE';
  let confidence = 0;

  // Gate 1: Absolute minimum
  if (sampleSize < ABSOLUTE_MINIMUM_SAMPLES) {
    warnings.push(`Only ${sampleSize} samples - absolute minimum is ${ABSOLUTE_MINIMUM_SAMPLES}`);
    recommendation = 'DO_NOT_USE';
    confidence = sampleSize / ABSOLUTE_MINIMUM_SAMPLES * 0.2;
    return {
      isValid: false,
      confidence,
      sampleSize,
      minSampleSize: ABSOLUTE_MINIMUM_SAMPLES,
      confidenceInterval: {
        estimate, lower: 0, upper: 1,
        confidenceLevel: 0.95, sampleSize, isReliable: false,
      },
      warnings,
      recommendation,
    };
  }

  // Gate 2: Minimum for statistical validity
  if (sampleSize < MODERATE_CONFIDENCE_SAMPLES) {
    warnings.push(`Only ${sampleSize} samples - moderate confidence requires ${MODERATE_CONFIDENCE_SAMPLES}`);
    recommendation = 'DO_NOT_USE';
    confidence = 0.2 + 0.3 * (sampleSize - ABSOLUTE_MINIMUM_SAMPLES) / (MODERATE_CONFIDENCE_SAMPLES - ABSOLUTE_MINIMUM_SAMPLES);
  }

  // Gate 3: Adequate for most analysis
  if (sampleSize >= MODERATE_CONFIDENCE_SAMPLES && sampleSize < HIGH_CONFIDENCE_SAMPLES) {
    recommendation = 'USE_WITH_CAUTION';
    confidence = 0.5 + 0.3 * (sampleSize - MODERATE_CONFIDENCE_SAMPLES) / (HIGH_CONFIDENCE_SAMPLES - MODERATE_CONFIDENCE_SAMPLES);
    warnings.push(`Adequate samples but high confidence requires ${HIGH_CONFIDENCE_SAMPLES}`);
  }

  // Gate 4: High confidence
  if (sampleSize >= HIGH_CONFIDENCE_SAMPLES && sampleSize < ROBUST_SAMPLES) {
    recommendation = 'USE';
    confidence = 0.8 + 0.1 * (sampleSize - HIGH_CONFIDENCE_SAMPLES) / (ROBUST_SAMPLES - HIGH_CONFIDENCE_SAMPLES);
  }

  // Gate 5: Robust
  if (sampleSize >= ROBUST_SAMPLES) {
    recommendation = 'USE';
    confidence = 0.9 + 0.1 * Math.min(1, Math.log10(sampleSize / ROBUST_SAMPLES));
  }

  // Calculate CI
  const ci = successes !== undefined
    ? proportionConfidenceInterval(successes, sampleSize)
    : meanConfidenceInterval([estimate], 0.95); // Single point, not ideal

  // If CI is very wide, reduce confidence
  const ciWidth = ci.upper - ci.lower;
  if (ciWidth > 0.5) {
    confidence *= 0.7;
    warnings.push(`Very wide confidence interval (${(ciWidth * 100).toFixed(0)}%) - result is imprecise`);
  }

  return {
    isValid: recommendation !== 'DO_NOT_USE',
    confidence: Math.min(0.99, confidence),
    sampleSize,
    minSampleSize: ABSOLUTE_MINIMUM_SAMPLES,
    confidenceInterval: ci,
    warnings,
    recommendation,
  };
}

// ============================================================
// TEMPORAL DECAY
// ============================================================

/**
 * Calcula el peso de una observación basado en su antigüedad
 * Las observaciones más recientes pesan más que las antiguas
 */
export function calculateTemporalWeight(
  observationDate: Date,
  config: TemporalDecayConfig = DEFAULT_DECAY_CONFIG
): number {
  const ageMs = Date.now() - new Date(observationDate).getTime();
  const ageDays = ageMs / (1000 * 60 * 60 * 24);

  if (ageDays > config.maxAgeDays) return 0;

  switch (config.decayFunction) {
    case 'exponential':
      return Math.exp(-Math.log(2) * ageDays / config.halfLifeDays);

    case 'linear':
      return Math.max(0, 1 - ageDays / config.maxAgeDays);

    case 'step':
      // Full weight for recent, half for older, zero for ancient
      if (ageDays < config.halfLifeDays) return 1.0;
      if (ageDays < config.maxAgeDays) return 0.5;
      return 0;

    default:
      return Math.exp(-Math.log(2) * ageDays / config.halfLifeDays);
  }
}

/**
 * Calcula el "effective sample size" considering temporal decay
 * N_eff = sum(w_i)^2 / sum(w_i^2) where w_i = temporal weight
 */
export function effectiveSampleSize(
  observationDates: Date[],
  config: TemporalDecayConfig = DEFAULT_DECAY_CONFIG
): number {
  const weights = observationDates
    .map(d => calculateTemporalWeight(d, config))
    .filter(w => w > 0);

  if (weights.length === 0) return 0;

  const sumW = weights.reduce((s, w) => s + w, 0);
  const sumW2 = weights.reduce((s, w) => s + w * w, 0);

  return sumW2 > 0 ? (sumW * sumW) / sumW2 : 0;
}

/** Default temporal decay configuration */
const DEFAULT_DECAY_CONFIG: TemporalDecayConfig = {
  halfLifeDays: 30, // En crypto, 30 días es mucho tiempo
  maxAgeDays: 180,  // Más de 6 meses se descarta
  decayFunction: 'exponential',
};

const Z_84 = 1.036;
const Z_75 = 0.674;

// ============================================================
// HELPER FUNCTIONS
// ============================================================

/**
 * Get critical t-value for given degrees of freedom
 */
function getTCritical(df: number): number {
  // For large df, t approaches z
  if (df >= 1000) return Z_95;
  if (df >= 120) return T_CRITICAL_VALUES[120] ?? 1.98;

  // Find closest available df
  const availableDfs = Object.keys(T_CRITICAL_VALUES).map(Number).sort((a, b) => a - b);
  let closest = availableDfs[0];
  for (const d of availableDfs) {
    if (d <= df) closest = d;
    else break;
  }

  // Interpolate if df doesn't match exactly
  const exactValue = T_CRITICAL_VALUES[df];
  if (exactValue) return exactValue;

  // Use closest value (slightly conservative)
  return T_CRITICAL_VALUES[closest] ?? Z_95;
}

/**
 * Approximate p-value from t-statistic and degrees of freedom
 * Uses a numerical approximation of the t-distribution CDF
 */
function approximatePValue(t: number, df: number): number {
  const absT = Math.abs(t);

  // For very large |t|, p is essentially 0
  if (absT > 10) return 0.0001;

  // For large df, use normal approximation
  if (df > 30) {
    // Standard normal CDF approximation
    const z = absT;
    const p = 1 - normalCDF(z);
    return Math.max(0.0001, 2 * p); // two-tailed
  }

  // For small df, use approximation based on t-distribution
  // Using the relationship: t^2 = F(1, df)
  // p-value ≈ 2 * (1 - t_cdf(abs_t, df))
  // Approximation using beta function relationship
  const x = df / (df + absT * absT);
  const p = incompleteBeta(df / 2, 0.5, x);
  return Math.max(0.0001, p); // one-tailed * 2 for two-tailed, but incompleteBeta already gives the right tail
}

/**
 * Standard normal CDF approximation (Abramowitz and Stegun)
 */
function normalCDF(z: number): number {
  if (z < -8) return 0;
  if (z > 8) return 1;

  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const sign = z < 0 ? -1 : 1;
  const x = Math.abs(z) / Math.SQRT2;

  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);

  return 0.5 * (1 + sign * y);
}

/**
 * Regularized incomplete beta function approximation
 * Used for p-value calculation of t and F distributions
 */
function incompleteBeta(a: number, b: number, x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;

  // Use continued fraction expansion
  const lnBeta = lgamma(a) + lgamma(b) - lgamma(a + b);
  const front = Math.exp(Math.log(x) * a + Math.log(1 - x) * b - lnBeta);

  if (x < (a + 1) / (a + b + 2)) {
    return front * betaCF(a, b, x) / a;
  } else {
    return 1 - front * betaCF(b, a, 1 - x) / b;
  }
}

/**
 * Continued fraction for incomplete beta function
 */
function betaCF(a: number, b: number, x: number): number {
  const maxIter = 200;
  const eps = 1e-10;

  let qab = a + b;
  let qap = a + 1;
  let qam = a - 1;
  let c = 1;
  let d = 1 - qab * x / qap;

  if (Math.abs(d) < 1e-30) d = 1e-30;
  d = 1 / d;
  let h = d;

  for (let m = 1; m <= maxIter; m++) {
    const m2 = 2 * m;

    // Even step
    let aa = m * (b - m) * x / ((qam + m2) * (a + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < 1e-30) d = 1e-30;
    c = 1 + aa / c;
    if (Math.abs(c) < 1e-30) c = 1e-30;
    d = 1 / d;
    h *= d * c;

    // Odd step
    aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < 1e-30) d = 1e-30;
    c = 1 + aa / c;
    if (Math.abs(c) < 1e-30) c = 1e-30;
    d = 1 / d;
    const del = d * c;
    h *= del;

    if (Math.abs(del - 1) < eps) break;
  }

  return h;
}

/**
 * Log-gamma function (Stirling's approximation + Lanczos)
 */
function lgamma(x: number): number {
  const g = 7;
  const coef = [
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
  ];

  if (x < 0.5) {
    return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
  }

  x -= 1;
  let a = coef[0];
  for (let i = 1; i < g + 2; i++) {
    a += coef[i] / (x + i);
  }

  const t = x + g + 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}

/**
 * Approximate chi-square p-value using Wilson-Hilferty transformation
 */
function chiSquarePValue(chiSquare: number, df: number): number {
  if (df <= 0) return 1;
  if (chiSquare <= 0) return 1;

  // Wilson-Hilferty approximation: chi^2/df ~ normal for large df
  if (df > 30) {
    const z = (Math.pow(chiSquare / df, 1/3) - (1 - 2/(9*df))) / Math.sqrt(2/(9*df));
    return 1 - normalCDF(z);
  }

  // For small df, use lookup or approximation
  // Critical values approach
  const criticalValue = CHI_SQUARE_CRITICAL[df] ?? df * 2; // rough approximation
  if (chiSquare > criticalValue * 3) return 0.001;
  if (chiSquare > criticalValue * 2) return 0.01;
  if (chiSquare > criticalValue) return 0.04;
  if (chiSquare > criticalValue * 0.5) return 0.2;
  return 0.5;
}

/**
 * Combined validation for conditional probability tables
 * Validates P(outcome | conditions) with statistical rigor
 */
export function validateConditionalProbability(
  conditionCategory: string,
  totalObservations: number,
  outcomeCount: number,
  recentObservationDates: Date[]
): {
  probability: number;
  validation: ValidationResult;
  effectiveN: number;
  temporalDecayApplied: boolean;
} {
  const probability = totalObservations > 0 ? outcomeCount / totalObservations : 0;

  // Calculate effective sample size with temporal decay
  const effN = effectiveSampleSize(recentObservationDates);

  // Validate with effective N (more honest than raw N)
  const validation = validatePrediction(
    probability,
    Math.floor(effN),
    outcomeCount,
    conditionCategory
  );

  return {
    probability,
    validation,
    effectiveN: Math.floor(effN),
    temporalDecayApplied: effN < totalObservations,
  };
}

// ============================================================
// EXPORTS
// ============================================================

export const statisticalValidation = {
  proportionConfidenceInterval,
  meanConfidenceInterval,
  independentTTest,
  chiSquareTest,
  correlationSignificanceTest,
  calculateRequiredSampleSize,
  assessSampleSufficiency,
  validatePrediction,
  validateConditionalProbability,
  calculateTemporalWeight,
  effectiveSampleSize,
  DEFAULT_DECAY_CONFIG,
};

export default statisticalValidation;
