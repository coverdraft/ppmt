/**
 * PPMT - Progressive Pattern Matching Trie
 * Main Entry Point
 *
 * Exports the complete PPMT V3 engine with:
 *   - SAX symbolization
 *   - Multi-level Trie (4 levels with adaptive weights)
 *   - Block Lifecycle Metadata (autonomous trading)
 *   - Regime detection
 *   - Asset classification
 *   - Risk management
 */

// Core Engine
export { PPMTEngine } from './core/index';

// Individual components (for advanced usage)
export { SAXEngine } from './core/sax';
export { PatternTrie, ProgressiveCursor } from './core/trie';
export { MultiLevelTrie } from './core/multiLevelTrie';
export { RegimeDetector } from './core/regime';
export { AssetClassifier } from './core/assetClassifier';
export { RiskManager } from './core/riskManager';

// Types
export * from './core/types';
