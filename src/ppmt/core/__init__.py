"""Core PPMT components: SAX, Trie, Delta Encoder, Fuzzy Matcher, Metadata, Regime."""

from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.sax import SAXEncoder, get_alpha_for_level, LEVEL_ALPHA_CONFIG
from ppmt.core.trie import PPMTTrie, TrieNode
from ppmt.core.encoder import DeltaEncoder
from ppmt.core.matcher import FuzzyMatcher
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.core.thresholds import SignalThresholds, RegimeThresholds

__all__ = [
    "BlockLifecycleMetadata",
    "SAXEncoder",
    "get_alpha_for_level",
    "LEVEL_ALPHA_CONFIG",
    "PPMTTrie",
    "TrieNode",
    "DeltaEncoder",
    "FuzzyMatcher",
    "RegimeDetector",
    "RegimeInfo",
    "SignalThresholds",
    "RegimeThresholds",
]
