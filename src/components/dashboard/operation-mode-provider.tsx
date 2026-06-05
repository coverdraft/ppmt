'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

// ============================================================
// TYPES
// ============================================================

export type OperationMode = 'research' | 'operation';

export interface WorkflowStep {
  id: string;
  label: string;
  shortLabel: string;
  description: string;
  mode: OperationMode;
  targetTab: string;
  icon: string; // emoji
  requiredActions: string[];
  validationCriteria: string[];
}

// MODO INVESTIGACIÓN — Research & Backtesting Pipeline
const RESEARCH_STEPS: WorkflowStep[] = [
  {
    id: 'scan',
    label: 'Escanear',
    shortLabel: 'ESCANEAR',
    description: 'Descubrir tokens y entender el mercado actual',
    mode: 'research',
    targetTab: 'dashboard',
    icon: '📡',
    requiredActions: [
      'Revisar el feed de tokens en Dashboard',
      'Verificar precios BTC/ETH en la barra superior',
      'Explorar Multi-Chain para comparar cadenas',
      'Consultar Market Regime para entender el contexto',
    ],
    validationCriteria: [
      'Al menos 10 tokens detectados en el feed',
      'Regimen de mercado identificado (BULL/BEAR/SIDEWAYS/CRISIS)',
    ],
  },
  {
    id: 'analyze',
    label: 'Analizar',
    shortLabel: 'ANALIZAR',
    description: 'Brain analiza tokens, extrae features y genera señales',
    mode: 'research',
    targetTab: 'brain',
    icon: '🧠',
    requiredActions: [
      'Iniciar el Brain desde la pestaña Brain',
      'Ejecutar un ciclo de análisis completo',
      'Revisar señales generadas en Signals',
      'Verificar Alpha Ranking para oportunidades',
      'Usar Deep Analysis en tokens de interés',
    ],
    validationCriteria: [
      'Brain completó al menos 1 ciclo',
      'Señales generadas con confidence > 0.5',
      'Tokens analizados con DNA score',
    ],
  },
  {
    id: 'filter',
    label: 'Filtrar',
    shortLabel: 'FILTRAR',
    description: 'Solo pasan señales que superan el risk pre-filter',
    mode: 'research',
    targetTab: 'risk-pre-filter',
    icon: '🛡️',
    requiredActions: [
      'Activar Risk Pre-Filter en señales',
      'Revisar que señales pasen los checks de riesgo',
      'Ajustar umbrales de riesgo si es necesario',
      'Verificar operability score de tokens',
    ],
    validationCriteria: [
      'Señales filtradas por risk pre-filter',
      'Risk score aceptable para las señales aprobadas',
    ],
  },
  {
    id: 'design',
    label: 'Diseñar',
    shortLabel: 'DISEÑAR',
    description: 'Crear trading systems para los tokens seleccionados',
    mode: 'research',
    targetTab: 'strategy-lab',
    icon: '⚙️',
    requiredActions: [
      'Crear un Trading System en Strategy Lab',
      'Configurar reglas de entrada/salida',
      'Usar AI Manager para optimización automática',
      'Definir patrones en Pattern Builder si aplica',
    ],
    validationCriteria: [
      'Trading System creado con configuración completa',
      'Reglas de entrada y salida definidas',
    ],
  },
  {
    id: 'backtest',
    label: 'Backtest',
    shortLabel: 'BACKTEST',
    description: 'Validar estrategias con datos históricos reales',
    mode: 'research',
    targetTab: 'backtesting',
    icon: '🧪',
    requiredActions: [
      'Ejecutar backtest con el Trading System',
      'Revisar métricas: Sharpe, Sortino, Max Drawdown',
      'Ejecutar Walk-Forward Analysis',
      'Correr Monte Carlo Simulation',
    ],
    validationCriteria: [
      'Backtest completado con métricas positivas',
      'Sharpe Ratio > 0.5',
      'Max Drawdown < 30%',
      'Walk-Forward validado (no overfitting)',
    ],
  },
  {
    id: 'optimize',
    label: 'Optimizar',
    shortLabel: 'OPTIMIZAR',
    description: 'Evolucionar estrategias y ajustar parámetros',
    mode: 'research',
    targetTab: 'meta-model',
    icon: '🧬',
    requiredActions: [
      'Revisar Meta-Model para ver rendimiento de engines',
      'Activar auto-evolution en Strategy Lab',
      'Revisar Evolution Tree para ajustes',
      'Verificar Strategy States para cambios',
    ],
    validationCriteria: [
      'Estrategia evolucionada con parámetros mejorados',
      'Meta-model muestra mejora en weights',
      'Backtest post-optimización confirma mejora',
    ],
  },
];

// MODO OPERACIÓN — Live Trading Pipeline
const OPERATION_STEPS: WorkflowStep[] = [
  {
    id: 'select',
    label: 'Seleccionar',
    shortLabel: 'SELECCIONAR',
    description: 'Elegir estrategias validadas del Modo Investigación',
    mode: 'operation',
    targetTab: 'strategy-lab',
    icon: '✅',
    requiredActions: [
      'Ir a Strategy Lab y buscar estrategias con backtest positivo',
      'Seleccionar estrategias con Sharpe > 0.5',
      'Verificar que Walk-Forward pasó validación',
      'Confirmar que el trading system está ACTIVO',
    ],
    validationCriteria: [
      'Al menos 1 estrategia validada seleccionada',
      'Métricas de backtest verificadas',
    ],
  },
  {
    id: 'configure',
    label: 'Configurar',
    shortLabel: 'CONFIGURAR',
    description: 'Definir risk controls, capital allocation y kill switches',
    mode: 'operation',
    targetTab: 'kill-switches',
    icon: '🛑',
    requiredActions: [
      'Configurar Kill Switches (global y por estrategia)',
      'Definir Capital Allocation (cuánto capital por estrategia)',
      'Configurar Risk Controls (max drawdown, max positions, etc.)',
      'Establecer Risk Budget',
    ],
    validationCriteria: [
      'Kill switches configurados y activos',
      'Capital allocation definido',
      'Risk controls verificados',
    ],
  },
  {
    id: 'paper-trade',
    label: 'Paper Trade',
    shortLabel: 'PAPER TRADE',
    description: 'Simular trading con precios reales en tiempo real',
    mode: 'operation',
    targetTab: 'paper-trading',
    icon: '📝',
    requiredActions: [
      'Iniciar sesión de Paper Trading',
      'Activar estrategia validada en paper mode',
      'Monitorear posiciones abiertas',
      'Verificar que las entradas/salidas coinciden con la estrategia',
    ],
    validationCriteria: [
      'Paper trading ejecutando con precios reales',
      'Al menos 5 trades simulados',
      'Win rate en paper trading > 40%',
    ],
  },
  {
    id: 'monitor',
    label: 'Monitorear',
    shortLabel: 'MONITOREAR',
    description: 'Vigilar posiciones, riesgo y portfolio en tiempo real',
    mode: 'operation',
    targetTab: 'portfolio',
    icon: '📊',
    requiredActions: [
      'Revisar Portfolio para ver posiciones y P&L',
      'Verificar Portfolio AI para análisis de impacto',
      'Consultar Risk Dashboard para exposición',
      'Revisar Capital Allocation periódicamente',
    ],
    validationCriteria: [
      'Portfolio visible con posiciones activas',
      'Risk dentro de los límites configurados',
      'P&L tracking funcionando',
    ],
  },
  {
    id: 'execute',
    label: 'Ejecutar',
    shortLabel: 'EJECUTAR',
    description: 'Ejecutar trades reales (cuando esté conectado a exchange)',
    mode: 'operation',
    targetTab: 'execution-cost',
    icon: '⚡',
    requiredActions: [
      'Estimar Execution Cost antes de cada trade',
      'Verificar que el costo de ejecución es aceptable',
      'Ejecutar trade con el tamaño adecuado',
      'Registrar trade en Execution History',
    ],
    validationCriteria: [
      'Execution cost estimado y verificado',
      'Trade ejecutado (o simulado)',
      'Slippage dentro de lo esperado',
    ],
  },
  {
    id: 'control',
    label: 'Controlar',
    shortLabel: 'CONTROLAR',
    description: 'Controles de emergencia y auditoría',
    mode: 'operation',
    targetTab: 'event-bus',
    icon: '🔴',
    requiredActions: [
      'Monitorear Event Bus para eventos del sistema',
      'Revisar SDE Decisions para auditoría',
      'Verificar Kill Switches periódicamente',
      'Exportar datos para registro',
    ],
    validationCriteria: [
      'Event Bus mostrando actividad del sistema',
      'Auditoría de decisiones disponible',
      'Sistema bajo control en todo momento',
    ],
  },
];

interface OperationModeContextValue {
  mode: OperationMode;
  setMode: (mode: OperationMode) => void;
  currentStepIndex: number;
  setCurrentStepIndex: (index: number) => void;
  completedSteps: string[];
  markStepCompleted: (stepId: string) => void;
  unmarkStepCompleted: (stepId: string) => void;
  steps: WorkflowStep[];
  currentStep: WorkflowStep | null;
  goToNextStep: () => void;
  goToPrevStep: () => void;
  goToStep: (index: number) => void;
  isStepCompleted: (stepId: string) => boolean;
  progressPct: number;
}

// ============================================================
// CONTEXT
// ============================================================

const OperationModeContext = createContext<OperationModeContextValue | null>(null);

const STORAGE_KEY_MODE = 'cryptoquant-operation-mode';
const STORAGE_KEY_STEP_PREFIX = 'cryptoquant-step-';
const STORAGE_KEY_COMPLETED = 'cryptoquant-completed-steps';

// ============================================================
// HELPERS
// ============================================================

function readStoredMode(): OperationMode | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = localStorage.getItem(STORAGE_KEY_MODE);
    if (stored === 'research' || stored === 'operation') return stored;
  } catch { /* noop */ }
  return null;
}

function readStoredStepIndex(mode: OperationMode): number {
  if (typeof window === 'undefined') return 0;
  try {
    const stored = localStorage.getItem(`${STORAGE_KEY_STEP_PREFIX}${mode}`);
    if (stored) {
      const idx = parseInt(stored, 10);
      if (!isNaN(idx) && idx >= 0) return idx;
    }
  } catch { /* noop */ }
  return 0;
}

function readCompletedSteps(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const stored = localStorage.getItem(STORAGE_KEY_COMPLETED);
    if (stored) return JSON.parse(stored);
  } catch { /* noop */ }
  return [];
}

// ============================================================
// PROVIDER
// ============================================================

export function OperationModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<OperationMode>('research');
  const [currentStepIndex, setCurrentStepIndexState] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);

  // Sync from localStorage on mount
  useEffect(() => {
    const storedMode = readStoredMode();
    if (storedMode) setModeState(storedMode);
    const storedStep = readStoredStepIndex(storedMode ?? 'research');
    setCurrentStepIndexState(storedStep);
    const storedCompleted = readCompletedSteps();
    if (storedCompleted.length > 0) setCompletedSteps(storedCompleted);
  }, []);

  const steps = mode === 'research' ? RESEARCH_STEPS : OPERATION_STEPS;
  const currentStep = steps[currentStepIndex] ?? null;
  const progressPct = steps.length > 0 ? (completedSteps.filter(id => steps.some(s => s.id === id)).length / steps.length) * 100 : 0;

  const setMode = useCallback((newMode: OperationMode) => {
    setModeState(newMode);
    try {
      localStorage.setItem(STORAGE_KEY_MODE, newMode);
    } catch { /* noop */ }
    // Reset step index for new mode
    const storedStep = readStoredStepIndex(newMode);
    setCurrentStepIndexState(storedStep);
  }, []);

  const setCurrentStepIndex = useCallback((index: number) => {
    setCurrentStepIndexState(index);
    try {
      localStorage.setItem(`${STORAGE_KEY_STEP_PREFIX}${mode}`, String(index));
    } catch { /* noop */ }
  }, [mode]);

  const markStepCompleted = useCallback((stepId: string) => {
    setCompletedSteps(prev => {
      if (prev.includes(stepId)) return prev;
      const next = [...prev, stepId];
      try { localStorage.setItem(STORAGE_KEY_COMPLETED, JSON.stringify(next)); } catch { /* noop */ }
      return next;
    });
  }, []);

  const unmarkStepCompleted = useCallback((stepId: string) => {
    setCompletedSteps(prev => {
      const next = prev.filter(id => id !== stepId);
      try { localStorage.setItem(STORAGE_KEY_COMPLETED, JSON.stringify(next)); } catch { /* noop */ }
      return next;
    });
  }, []);

  const goToNextStep = useCallback(() => {
    if (currentStepIndex < steps.length - 1) {
      if (currentStep) markStepCompleted(currentStep.id);
      setCurrentStepIndex(currentStepIndex + 1);
    }
  }, [currentStepIndex, steps.length, currentStep, markStepCompleted, setCurrentStepIndex]);

  const goToPrevStep = useCallback(() => {
    if (currentStepIndex > 0) {
      setCurrentStepIndex(currentStepIndex - 1);
    }
  }, [currentStepIndex, setCurrentStepIndex]);

  const goToStep = useCallback((index: number) => {
    if (index >= 0 && index < steps.length) {
      setCurrentStepIndex(index);
    }
  }, [steps.length, setCurrentStepIndex]);

  const isStepCompleted = useCallback((stepId: string) => {
    return completedSteps.includes(stepId);
  }, [completedSteps]);

  return (
    <OperationModeContext.Provider value={{
      mode,
      setMode,
      currentStepIndex,
      setCurrentStepIndex,
      completedSteps,
      markStepCompleted,
      unmarkStepCompleted,
      steps,
      currentStep,
      goToNextStep,
      goToPrevStep,
      goToStep,
      isStepCompleted,
      progressPct,
    }}>
      {children}
    </OperationModeContext.Provider>
  );
}

// ============================================================
// HOOK
// ============================================================

export function useOperationMode() {
  const context = useContext(OperationModeContext);
  if (!context) {
    throw new Error('useOperationMode must be used within an OperationModeProvider');
  }
  return context;
}
