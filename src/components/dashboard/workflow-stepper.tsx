'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useOperationMode, type OperationMode } from './operation-mode-provider';
import {
  ChevronRight,
  ChevronLeft,
  Check,
  AlertTriangle,
  Info,
  Play,
  FlaskConical,
  Rocket,
  X,
} from 'lucide-react';

// ============================================================
// MODE SWITCHER
// ============================================================

export function ModeSwitcher() {
  const { mode, setMode } = useOperationMode();

  const modes: Array<{ id: OperationMode; label: string; icon: React.ComponentType<{ className?: string }>; color: string }> = [
    { id: 'research', label: 'Investigación', icon: FlaskConical, color: 'cyan' },
    { id: 'operation', label: 'Operación', icon: Rocket, color: 'amber' },
  ];

  return (
    <div className="flex items-center gap-0.5 bg-[#0a0e17] rounded border border-[#1e293b] px-0.5 py-0.5">
      {modes.map((m) => {
        const Icon = m.icon;
        const isActive = mode === m.id;
        const borderColor = isActive
          ? m.id === 'research' ? 'border-cyan-500/50' : 'border-amber-500/50'
          : 'border-transparent';
        const textColor = isActive
          ? m.id === 'research' ? 'text-cyan-400' : 'text-amber-400'
          : 'text-[#64748b]';
        const bgColor = isActive
          ? m.id === 'research' ? 'bg-cyan-500/10' : 'bg-amber-500/10'
          : '';

        return (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-mono font-bold transition-all border ${borderColor} ${textColor} ${bgColor} hover:bg-[#1e293b]/80`}
            title={m.id === 'research' ? 'Modo Investigación: Backtesting & Validación' : 'Modo Operación: Paper Trading & Ejecución'}
          >
            <Icon className="h-3 w-3" />
            <span className="hidden sm:inline">{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// WORKFLOW STEPPER — Compact horizontal stepper for the top bar
// ============================================================

export function WorkflowStepperCompact() {
  const { mode, steps, currentStepIndex, goToStep, isStepCompleted, progressPct, currentStep } = useOperationMode();
  const modeColor = mode === 'research' ? 'cyan' : 'amber';
  const modeLabel = mode === 'research' ? 'INVESTIGACIÓN' : 'OPERACIÓN';

  return (
    <div className="flex items-center gap-1.5">
      {/* Mode label */}
      <span className={`font-mono text-[8px] font-bold ${mode === 'research' ? 'text-cyan-500' : 'text-amber-500'} tracking-wider hidden lg:inline`}>
        {modeLabel}
      </span>

      {/* Step dots */}
      <div className="flex items-center gap-0.5">
        {steps.map((step, i) => {
          const isCurrent = i === currentStepIndex;
          const isDone = isStepCompleted(step.id);
          const isPast = i < currentStepIndex;

          return (
            <button
              key={step.id}
              onClick={() => goToStep(i)}
              className="group relative"
              title={`${step.shortLabel}: ${step.description}`}
            >
              <div
                className={`w-2 h-2 rounded-full transition-all ${
                  isCurrent
                    ? mode === 'research'
                      ? 'bg-cyan-400 ring-2 ring-cyan-400/30 scale-125'
                      : 'bg-amber-400 ring-2 ring-amber-400/30 scale-125'
                    : isDone
                    ? 'bg-emerald-500'
                    : isPast
                    ? 'bg-[#475569]'
                    : 'bg-[#1e293b] border border-[#334155]'
                }`}
              />
              {/* Tooltip */}
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 rounded bg-[#111827] border border-[#1e293b] shadow-lg whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                <span className="font-mono text-[9px] text-[#e2e8f0]">{step.icon} {step.shortLabel}</span>
                {isDone && <Check className="inline h-2.5 w-2.5 text-emerald-400 ml-1" />}
              </div>
            </button>
          );
        })}
      </div>

      {/* Progress percentage */}
      <span className={`font-mono text-[8px] ${mode === 'research' ? 'text-cyan-600' : 'text-amber-600'}`}>
        {Math.round(progressPct)}%
      </span>

      {/* Current step label */}
      {currentStep && (
        <span className={`font-mono text-[8px] hidden xl:inline ${mode === 'research' ? 'text-cyan-400/70' : 'text-amber-400/70'}`}>
          {currentStep.icon} {currentStep.shortLabel}
        </span>
      )}
    </div>
  );
}

// ============================================================
// WORKFLOW GUIDE PANEL — Expandable side panel with full step details
// ============================================================

export function WorkflowGuidePanel() {
  const {
    mode,
    steps,
    currentStepIndex,
    currentStep,
    goToStep,
    goToNextStep,
    goToPrevStep,
    isStepCompleted,
    markStepCompleted,
    unmarkStepCompleted,
    progressPct,
  } = useOperationMode();

  const [expanded, setExpanded] = useState(false);
  const modeColor = mode === 'research' ? 'cyan' : 'amber';
  const modeLabel = mode === 'research' ? 'MODO INVESTIGACIÓN' : 'MODO OPERACIÓN';
  const modeDesc = mode === 'research'
    ? 'Pipeline de backtesting y validación de estrategias'
    : 'Pipeline de ejecución y trading en vivo';

  if (!expanded) {
    // Collapsed: just a floating button
    return (
      <button
        onClick={() => setExpanded(true)}
        className={`fixed right-2 top-1/2 -translate-y-1/2 z-40 flex items-center gap-1 px-2 py-1.5 rounded-l-lg border transition-all ${
          mode === 'research'
            ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/20'
            : 'bg-amber-500/10 border-amber-500/30 text-amber-400 hover:bg-amber-500/20'
        }`}
        title="Abrir guía de workflow"
      >
        <Play className="h-3 w-3" />
        <span className="font-mono text-[9px] font-bold writing-vertical hidden sm:inline" style={{ writingMode: 'vertical-rl' }}>
          PASO {currentStepIndex + 1}
        </span>
      </button>
    );
  }

  return (
    <motion.div
      initial={{ x: 300, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 300, opacity: 0 }}
      className={`fixed right-0 top-0 bottom-0 z-40 w-[320px] bg-[#0d1117] border-l flex flex-col ${
        mode === 'research' ? 'border-cyan-500/20' : 'border-amber-500/20'
      }`}
    >
      {/* Header */}
      <div className={`px-4 py-3 border-b ${
        mode === 'research' ? 'border-cyan-500/20 bg-cyan-500/5' : 'border-amber-500/20 bg-amber-500/5'
      }`}>
        <div className="flex items-center justify-between">
          <div>
            <span className={`font-mono text-[10px] font-bold tracking-wider ${
              mode === 'research' ? 'text-cyan-400' : 'text-amber-400'
            }`}>
              {modeLabel}
            </span>
            <p className="text-[9px] text-[#64748b] mt-0.5">{modeDesc}</p>
          </div>
          <button
            onClick={() => setExpanded(false)}
            className="text-[#64748b] hover:text-[#94a3b8] transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Progress bar */}
        <div className="mt-2 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              mode === 'research' ? 'bg-cyan-500' : 'bg-amber-500'
            }`}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <span className="font-mono text-[8px] text-[#64748b] mt-1 block">
          Progreso: {Math.round(progressPct)}% — Paso {currentStepIndex + 1} de {steps.length}
        </span>
      </div>

      {/* Steps list */}
      <div className="flex-1 overflow-y-auto py-2">
        {steps.map((step, i) => {
          const isCurrent = i === currentStepIndex;
          const isDone = isStepCompleted(step.id);
          const isPast = i < currentStepIndex;
          const isFuture = i > currentStepIndex;

          return (
            <button
              key={step.id}
              onClick={() => goToStep(i)}
              className={`w-full text-left px-4 py-2.5 transition-all ${
                isCurrent
                  ? mode === 'research'
                    ? 'bg-cyan-500/10 border-l-2 border-l-cyan-400'
                    : 'bg-amber-500/10 border-l-2 border-l-amber-400'
                  : 'border-l-2 border-l-transparent hover:bg-[#1e293b]/50'
              }`}
            >
              <div className="flex items-center gap-2">
                {/* Step indicator */}
                <div className={`flex items-center justify-center w-6 h-6 rounded-full text-[10px] font-mono font-bold shrink-0 ${
                  isDone
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                    : isCurrent
                    ? mode === 'research'
                      ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                      : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                    : 'bg-[#1e293b] text-[#64748b] border border-[#334155]'
                }`}>
                  {isDone ? <Check className="h-3 w-3" /> : i + 1}
                </div>

                {/* Step info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px]">{step.icon}</span>
                    <span className={`font-mono text-[11px] font-bold ${
                      isCurrent
                        ? 'text-[#f1f5f9]'
                        : isDone
                        ? 'text-emerald-400'
                        : 'text-[#64748b]'
                    }`}>
                      {step.shortLabel}
                    </span>
                  </div>
                  {isCurrent && (
                    <p className="text-[9px] text-[#94a3b8] mt-0.5 leading-relaxed">
                      {step.description}
                    </p>
                  )}
                </div>
              </div>

              {/* Expanded content for current step */}
              {isCurrent && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  className="mt-2 ml-8 space-y-2"
                >
                  {/* Required actions */}
                  <div>
                    <span className="font-mono text-[8px] text-[#64748b] uppercase tracking-wider">
                      Acciones requeridas
                    </span>
                    <ul className="mt-1 space-y-0.5">
                      {step.requiredActions.map((action, j) => (
                        <li key={j} className="flex items-start gap-1.5">
                          <span className="text-[9px] text-[#475569] mt-0.5">•</span>
                          <span className="text-[9px] text-[#94a3b8] leading-relaxed">{action}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Validation criteria */}
                  <div>
                    <span className="font-mono text-[8px] text-[#64748b] uppercase tracking-wider">
                      Criterios de validación
                    </span>
                    <ul className="mt-1 space-y-0.5">
                      {step.validationCriteria.map((criteria, j) => (
                        <li key={j} className="flex items-start gap-1.5">
                          <AlertTriangle className="h-2.5 w-2.5 text-amber-500/60 mt-0.5 shrink-0" />
                          <span className="text-[9px] text-[#94a3b8] leading-relaxed">{criteria}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Step completion toggle */}
                  <div className="pt-2 border-t border-[#1e293b]">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (isDone) {
                          unmarkStepCompleted(step.id);
                        } else {
                          markStepCompleted(step.id);
                        }
                      }}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded text-[9px] font-mono transition-all ${
                        isDone
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20'
                          : mode === 'research'
                          ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/20'
                          : 'bg-amber-500/10 text-amber-400 border border-amber-500/30 hover:bg-amber-500/20'
                      }`}
                    >
                      {isDone ? (
                        <>
                          <Check className="h-3 w-3" />
                          <span>Paso completado — Click para desmarcar</span>
                        </>
                      ) : (
                        <>
                          <Check className="h-3 w-3" />
                          <span>Marcar paso como completado</span>
                        </>
                      )}
                    </button>
                  </div>
                </motion.div>
              )}
            </button>
          );
        })}
      </div>

      {/* Navigation buttons */}
      <div className={`px-4 py-3 border-t flex items-center justify-between ${
        mode === 'research' ? 'border-cyan-500/20' : 'border-amber-500/20'
      }`}>
        <button
          onClick={goToPrevStep}
          disabled={currentStepIndex === 0}
          className={`flex items-center gap-1 px-2 py-1 rounded text-[9px] font-mono transition-all ${
            currentStepIndex === 0
              ? 'text-[#334155] cursor-not-allowed'
              : 'text-[#94a3b8] hover:text-[#f1f5f9] hover:bg-[#1e293b]'
          }`}
        >
          <ChevronLeft className="h-3 w-3" />
          <span>Anterior</span>
        </button>

        <button
          onClick={goToNextStep}
          disabled={currentStepIndex === steps.length - 1}
          className={`flex items-center gap-1 px-2 py-1 rounded text-[9px] font-mono font-bold transition-all ${
            currentStepIndex === steps.length - 1
              ? 'text-[#334155] cursor-not-allowed'
              : mode === 'research'
              ? 'text-cyan-400 hover:bg-cyan-500/10'
              : 'text-amber-400 hover:bg-amber-500/10'
          }`}
        >
          <span>Siguiente</span>
          <ChevronRight className="h-3 w-3" />
        </button>
      </div>
    </motion.div>
  );
}
