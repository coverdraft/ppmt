#!/usr/bin/env python3
"""
Precise diagnosis: which gate in generate_entry_signal kills SHORT signals?
We patch the method to log the rejection reason.
"""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import pandas as pd
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.engine.signal import SignalGenerator, Signal, SignalType, SignalThresholds
from ppmt.core.trie import PPMTTrie
from typing import Optional

storage = PPMTStorage()
classifier = AssetClassifier()

# Patch SignalGenerator to log rejections
class DiagnosticSignalGenerator(SignalGenerator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rejection_reasons = {}
        self.short_rejections = []
    
    def generate_entry_signal(self, match_result, symbol, current_price, confidence,
                              trie_level="", regime_name="UNKNOWN"):
        if match_result.node is None:
            self._log_rejection("no_node", match_result)
            return None

        meta = match_result.node.metadata
        
        # Get direction FIRST to know if this is a SHORT
        direction_str = meta.best_direction_p7(
            min_edge_pct=self.thresholds.p7_min_edge_pct,
            alpha=self.thresholds.p7_bayesian_alpha,
            beta=self.thresholds.p7_bayesian_beta,
        )
        
        is_short = (direction_str == "SHORT")
        
        if direction_str is None:
            self._log_rejection("p7_no_direction", match_result, is_short)
            return None

        # Check all gates and log which one fails
        adaptive_min_conf, adaptive_min_rr = self.get_adaptive_thresholds(regime_name)
        absolute_floor = self.thresholds.per_trade_min_confidence
        adaptive_min_conf = min(adaptive_min_conf, absolute_floor)
        
        if confidence < adaptive_min_conf:
            self._log_rejection(f"confidence_too_low({confidence:.3f}<{adaptive_min_conf:.3f})", match_result, is_short)
            return None

        if meta.historical_count < 1:
            self._log_rejection("count_too_low", match_result, is_short)
            return None

        # P7 direction already computed above
        
        signal_type = (SignalType.ENTRY_LONG if direction_str == "LONG" else SignalType.ENTRY_SHORT)

        effective_move = (meta.avg_move_long if signal_type == SignalType.ENTRY_LONG 
                         else abs(meta.avg_move_short))
        if effective_move < self.thresholds.hard_move_floor:
            self._log_rejection(f"move_too_small({effective_move:.3f}<{self.thresholds.hard_move_floor})", match_result, is_short)
            return None

        meta.compute_sl_tp(current_price)

        min_rr_effective = min(adaptive_min_rr, 0.5)
        if meta.risk_reward_ratio < min_rr_effective:
            self._log_rejection(f"rr_too_low({meta.risk_reward_ratio:.3f}<{min_rr_effective:.3f})", match_result, is_short)
            return None

        # If we get here, the signal passes all gates
        self._log_rejection("PASSED", match_result, is_short)
        
        # Return the actual signal (call super with same logic)
        return super().generate_entry_signal(match_result, symbol, current_price, confidence,
                                              trie_level, regime_name)
    
    def _log_rejection(self, reason, match_result, is_short=False):
        key = f"{'SHORT' if is_short else 'LONG'}_{reason}"
        self.rejection_reasons[key] = self.rejection_reasons.get(key, 0) + 1
        if is_short and reason != "PASSED" and len(self.short_rejections) < 5:
            meta = match_result.node.metadata if match_result.node else None
            self.short_rejections.append({
                'reason': reason,
                'confidence': meta.confidence if meta else 0,
                'hist_count': meta.historical_count if meta else 0,
                'long_edge': meta.long_edge() if meta else 0,
                'short_edge': meta.short_edge() if meta else 0,
                'avg_move_short': meta.avg_move_short if meta else 0,
                'best_dir': meta.best_direction_p7(min_edge_pct=0.10) if meta and meta.historical_count > 0 else None,
            })


# Run replay with diagnostic signal generator
symbol = "BTC/USDT"
info = classifier.classify(symbol)
df = storage.load_ohlcv(symbol, "5m")
oos_start = df.index[-1] - pd.Timedelta(days=7)
oos_df = df[df.index >= oos_start]

tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")
engine = PPMT(symbol=symbol, asset_class=info.asset_class,
    weight_profile=info.weight_profile, dual_sax=True,
    min_confidence=0.08, timeframe="5m")
engine.weights = AdaptiveWeights.from_profile(info.weight_profile, timeframe="5m")
engine.set_tries(
    trie_n1=tries["n1"] or PPMTTrie(name="empty_n1"),
    trie_n2=tries["n2"] or PPMTTrie(name="empty_n2"),
    trie_n3=tries["n3"] or PPMTTrie(name="empty_n3"),
    trie_n4=tries["n4"] or engine.trie_n4,
)

# Replace the signal generator with our diagnostic version
diag_sg = DiagnosticSignalGenerator()
engine.signal_generator = diag_sg

_last_engine_ts = 0
for idx in range(len(oos_df)):
    row = oos_df.iloc[[idx]]
    current_price = float(row["close"].iloc[0])
    ts = oos_df.index[idx]
    ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)
    if ts_sec <= _last_engine_ts: continue
    _last_engine_ts = ts_sec
    engine.process_new_candle(candle_df=row, current_price=current_price,
                               is_in_position=False, entry_price=None)

print(f"\n{'='*70}")
print(f"  {symbol} — Signal Gate Rejection Analysis")
print(f"{'='*70}")
print(f"\n  Rejection reasons (sorted by frequency):")
sorted_rej = sorted(diag_sg.rejection_reasons.items(), key=lambda x: -x[1])
for key, count in sorted_rej:
    print(f"    {key:<60} → {count}")

print(f"\n  SHORT rejection samples (first 5):")
for sr in diag_sg.short_rejections:
    print(f"    reason={sr['reason']}")
    print(f"      confidence={sr['confidence']:.3f} hist={sr['hist_count']} "
          f"long_edge={sr['long_edge']:.4f} short_edge={sr['short_edge']:.4f} "
          f"avg_move_short={sr['avg_move_short']:.4f} best_dir={sr['best_dir']}")
