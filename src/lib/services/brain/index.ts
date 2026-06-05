// Barrel export for brain/ module
// Note: brain-pipeline and brain-analysis-pipeline share some type names,
// so brain-pipeline is excluded from barrel to avoid conflicts.
// Import brain-pipeline directly when needed.

export * from './brain-capacity-engine';
export * from './brain-cycle-engine';
export * from './brain-orchestrator';
export * from './pattern-compression-pipeline';
export * from './phase-strategy-engine';
export * from './scheduler-persistence';
export * from './token-lifecycle-engine';

// meta-model-engine exports TokenPhase which conflicts with token-lifecycle-engine
// Import directly: import { MetaModelEngine } from './meta-model-engine'
export { metaModelEngine, type EngineReport, type EngineMetrics, type PredictionContext, type PredictionOutcome } from './meta-model-engine';
