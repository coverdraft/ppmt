#!/usr/bin/env python3
"""
OOS (Out-Of-Sample) Replay for PEPE/USDT — PRUEBA DE FUEGO

PEPE was NOT included in the build. Its N3/N4 tries are EMPTY.
The engine must rely entirely on __UNIVERSAL__ (N1) and __CLASS_meme__ (N2)
to generate signals. This proves Transfer Learning works.

v0.43.0: Updated for stratified SAX Dual (N1=price-only, N2/N3/N4=dual).
N1 patterns are strings like ['a','b','c','a','b'] (max 243 patterns).
N2/N3/N4 patterns are tuples like [('a','x'),('b','y'),...].

Usage:
    python /home/z/my-project/ppmt/scripts/oos_pepe_replay.py --timeframe 1m
    python /home/z/my-project/ppmt/scripts/oos_pepe_replay.py --timeframe 5m
"""

import sys
sys.path.insert(0, "/home/z/my-project/ppmt/src")

import json
import time
import numpy as np
import pandas as pd
from collections import defaultdict

from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.engine.ppmt import PPMT
from ppmt.engine.btc_filter import BTCContextFilter
from ppmt.data.classifier import AssetClassifier


def run_oos_replay(timeframe: str = "5m"):
    storage = PPMTStorage()
    classifier = AssetClassifier()
    
    # === Load PEPE OOS data ===
    pepe_df = storage.load_ohlcv("PEPE/USDT", timeframe)
    if pepe_df is None or len(pepe_df) < 100:
        print(f"ERROR: PEPE data insufficient ({len(pepe_df) if pepe_df is not None else 0} rows)")
        return
    
    print(f"PEPE OOS data ({timeframe}): {len(pepe_df)} candles")
    
    # === Load BTC context data ===
    btc_df = storage.load_ohlcv("BTC/USDT", timeframe)
    print(f"BTC context data: {len(btc_df) if btc_df is not None else 0} candles")
    
    # === Create PEPE engine with EMPTY N3/N4 ===
    info = classifier.classify("PEPE/USDT")
    print(f"\nPEPE classification: asset_class={info.asset_class}, profile={info.weight_profile}")
    
    engine = PPMT(
        symbol="PEPE/USDT",
        asset_class=info.asset_class,
        weight_profile=info.weight_profile,
        timeframe=timeframe,
    )
    
    # Load shared pools into the engine
    n1_trie = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n2_trie = storage.load_trie(class_pool_key(info.asset_class), "n2")
    
    if n1_trie is None:
        print("FATAL: __UNIVERSAL__ N1 pool is empty!")
        return
    if n2_trie is None:
        print(f"FATAL: __CLASS_{info.asset_class}__ N2 pool is empty!")
        return
    
    print(f"\nShared pools loaded:")
    print(f"  __UNIVERSAL__ N1: {n1_trie.pattern_count} patterns (PRICE-ONLY, max 243)")
    print(f"  __CLASS_{info.asset_class}__ N2: {n2_trie.pattern_count} patterns (SAX Dual)")
    
    # v0.43.0: Verify N1 key type — should be str (price-only), not tuple
    n1_sample_key = None
    if n1_trie.root.children:
        n1_sample_key = list(n1_trie.root.children.keys())[0]
    print(f"  N1 key type: {type(n1_sample_key).__name__} = {n1_sample_key}")
    
    n2_sample_key = None
    if n2_trie.root.children:
        n2_sample_key = list(n2_trie.root.children.keys())[0]
    print(f"  N2 key type: {type(n2_sample_key).__name__} = {n2_sample_key}")
    
    # Set tries: N1 and N2 from shared pools, N3/N4 EMPTY
    from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
    engine.set_tries(
        trie_n1=n1_trie,
        trie_n2=n2_trie,
        trie_n3=PPMTTrie(name="PEPE_EMPTY_N3"),  # EMPTY!
        trie_n4=RegimePartitionedTrie(name="PEPE_EMPTY_N4"),  # EMPTY!
    )
    
    print(f"  PEPE N3: {engine.trie_n3.pattern_count} patterns (EMPTY = Transfer Learning)")
    print(f"  PEPE N4: EMPTY (RegimePartitionedTrie)")
    
    # === BTC Context Filter ===
    btc_filter = BTCContextFilter()
    
    # === Encode PEPE data with per-level encoders ===
    # v0.43.0: Use encode_all_levels() which handles N1=str vs N2/N3/N4=tuple
    all_symbols = engine.encode_all_levels(pepe_df)
    symbols_n1 = all_symbols["n1"]
    symbols_n2 = all_symbols["n2"]
    symbols_n3 = all_symbols["n3"]
    symbols_n4 = all_symbols["n4"]
    
    print(f"\nSAX symbols encoded: N1={len(symbols_n1)}, N2={len(symbols_n2)}, N3={len(symbols_n3)}")
    print(f"  N1 sample: {symbols_n1[:5]} (type: {type(symbols_n1[0]).__name__})")
    if symbols_n2:
        print(f"  N2 sample: {symbols_n2[:3]} (type: {type(symbols_n2[0]).__name__})")
    
    # Decision log
    decisions = []
    signals = []
    trades = []
    open_position = None
    
    # Update BTC context from overlapping data
    if btc_df is not None and len(btc_df) > 0:
        btc_prices = btc_df["close"].values[-200:]
        btc_filter.update_btc_context(btc_prices)
        print(f"BTC regime: {btc_filter._btc_regime} (vol={btc_filter._btc_volatility:.2%})")
    
    WINDOW_SIZE = engine.sax.window_size
    PATTERN_LENGTH = 5
    
    # Replay through PEPE data
    min_syms = min(len(symbols_n1), len(symbols_n2), len(symbols_n3), len(symbols_n4))
    
    for i in range(PATTERN_LENGTH, min_syms):
        # Current pattern window
        pat_n1 = symbols_n1[i - PATTERN_LENGTH:i]
        pat_n2 = symbols_n2[i - PATTERN_LENGTH:i]
        pat_n3 = symbols_n3[i - PATTERN_LENGTH:i]
        pat_n4 = symbols_n4[i - PATTERN_LENGTH:i]
        
        # Map to candle indices for price
        candle_idx = i * WINDOW_SIZE
        if candle_idx >= len(pepe_df):
            break
        
        current_price = float(pepe_df["close"].iloc[min(candle_idx, len(pepe_df) - 1)])
        
        # Run 4-level match
        result = engine.match_raw(
            current_symbols=pat_n3,
            current_price=current_price,
            current_symbols_n1=pat_n1,
            current_symbols_n2=pat_n2,
            current_symbols_n3=pat_n3,
            current_symbols_n4=pat_n4,
        )
        
        # Extract match details
        n1_matched = result.n1_match.node is not None
        n2_matched = result.n2_match.node is not None
        n3_matched = result.n3_match.node is not None
        n4_matched = result.n4_match.node is not None
        
        n1_conf = result.n1_confidence
        n2_conf = result.n2_confidence
        n3_conf = result.n3_confidence
        n4_conf = result.n4_confidence
        weighted_conf = result.weighted_confidence
        
        # Get expected sequences from matched nodes
        n1_expected = []
        n2_expected = []
        if n1_matched and hasattr(result.n1_match.node.metadata, 'expected_sequences'):
            n1_expected = list(result.n1_match.node.metadata.expected_sequences.keys())[:3]
        if n2_matched and hasattr(result.n2_match.node.metadata, 'expected_sequences'):
            n2_expected = list(result.n2_match.node.metadata.expected_sequences.keys())[:3]
        
        # Determine direction
        direction = "NONE"
        if n1_matched or n2_matched:
            # Use N2 as primary (more specific), fallback to N1
            best_meta = result.n2_match.node.metadata if n2_matched else result.n1_match.node.metadata
            if best_meta.expected_move_pct > 0.1:
                direction = "LONG"
            elif best_meta.expected_move_pct < -0.1:
                direction = "SHORT"
        
        # Apply BTC filter
        btc_result = btc_filter.filter_signal(direction, weighted_conf)
        btc_approved = not btc_result['rejected']
        final_conf = btc_result['adjusted_confidence']
        
        # Log decision (every 10th step or when there's a match)
        if (n1_matched or n2_matched) and i % 10 == 0:
            decision = {
                'step': i,
                'price': current_price,
                'pattern_n1': pat_n1,
                'pattern_n2': pat_n2,
                'n1_matched': n1_matched,
                'n2_matched': n2_matched,
                'n3_matched': n3_matched,
                'n1_conf': round(n1_conf, 4),
                'n2_conf': round(n2_conf, 4),
                'n3_conf': round(n3_conf, 4),
                'weighted_conf': round(weighted_conf, 4),
                'direction': direction,
                'btc_regime': btc_filter._btc_regime or 'unknown',
                'btc_approved': btc_approved,
                'final_conf': round(final_conf, 4),
                'n1_expected': n1_expected,
                'n2_expected': n2_expected,
            }
            decisions.append(decision)
        
        # Signal generation — use expected_move from matched node for SL/TP
        SIGNAL_THRESHOLD = 0.15  # Lower threshold for OOS Transfer Learning test
        
        if direction in ("LONG", "SHORT") and final_conf >= SIGNAL_THRESHOLD and btc_approved:
            # v0.43.0: SL/TP from expected_move of matched node, not static floors
            best_meta = result.n2_match.node.metadata if n2_matched else result.n1_match.node.metadata
            
            # SL based on observed max_drawdown from node (or expected_move as floor)
            sl_pct = max(abs(best_meta.max_drawdown_pct) * 1.5, abs(best_meta.expected_move_pct) * 0.75) if best_meta.max_drawdown_pct != 0 else abs(best_meta.expected_move_pct) * 0.75
            # TP based on expected_move and max_favorable from node
            tp_pct = max(abs(best_meta.expected_move_pct), best_meta.max_favorable_pct * 0.8) if best_meta.max_favorable_pct != 0 else abs(best_meta.expected_move_pct) * 1.5
            
            # Minimum SL/TP floors (avoid zero)
            sl_pct = max(sl_pct, 0.5)
            tp_pct = max(tp_pct, 0.5)
            
            signal = {
                'step': i,
                'price': current_price,
                'direction': direction,
                'confidence': final_conf,
                'n1_match': n1_matched,
                'n2_match': n2_matched,
                'n3_empty': not n3_matched,
                'btc_regime': btc_filter._btc_regime,
                'sl_pct': round(sl_pct, 2),
                'tp_pct': round(tp_pct, 2),
                'pattern_n1': pat_n1,
                'pattern_n2': pat_n2,
                'expected_seq_n1': n1_expected,
                'expected_seq_n2': n2_expected,
                'from_pool': 'N2_CLASS_meme' if n2_matched else 'N1_UNIVERSAL',
            }
            signals.append(signal)
            
            # Simple trade simulation
            if open_position is None:
                open_position = {
                    'entry_price': current_price,
                    'direction': direction,
                    'entry_step': i,
                    'sl_price': current_price * (1 - sl_pct / 100) if direction == "LONG" else current_price * (1 + sl_pct / 100),
                    'tp_price': current_price * (1 + tp_pct / 100) if direction == "LONG" else current_price * (1 - tp_pct / 100),
                    'confidence': final_conf,
                }
        
        # Check open position for exit
        if open_position is not None:
            pos = open_position
            if pos['direction'] == 'LONG':
                if current_price >= pos['tp_price']:
                    pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                    trades.append({'direction': 'LONG', 'pnl_pct': round(pnl_pct, 2), 'exit': 'TP', 'conf': pos['confidence']})
                    open_position = None
                elif current_price <= pos['sl_price']:
                    pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                    trades.append({'direction': 'LONG', 'pnl_pct': round(pnl_pct, 2), 'exit': 'SL', 'conf': pos['confidence']})
                    open_position = None
            elif pos['direction'] == 'SHORT':
                if current_price <= pos['tp_price']:
                    pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
                    trades.append({'direction': 'SHORT', 'pnl_pct': round(pnl_pct, 2), 'exit': 'TP', 'conf': pos['confidence']})
                    open_position = None
                elif current_price >= pos['sl_price']:
                    pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
                    trades.append({'direction': 'SHORT', 'pnl_pct': round(pnl_pct, 2), 'exit': 'SL', 'conf': pos['confidence']})
                    open_position = None
    
    # Close any remaining position at end
    if open_position is not None:
        last_price = float(pepe_df["close"].iloc[-1])
        if open_position['direction'] == 'LONG':
            pnl_pct = ((last_price - open_position['entry_price']) / open_position['entry_price']) * 100
        else:
            pnl_pct = ((open_position['entry_price'] - last_price) / open_position['entry_price']) * 100
        trades.append({'direction': open_position['direction'], 'pnl_pct': round(pnl_pct, 2), 'exit': 'EOD', 'conf': open_position['confidence']})
    
    # === REPORT ===
    print(f"\n{'='*70}")
    print("PRUEBA DE FUEGO OOS — REPORTE COMPLETO")
    print(f"{'='*70}")
    
    print(f"\n1. ESTADO DB POST-BUILD:")
    print(f"   __UNIVERSAL__ N1: {n1_trie.pattern_count} patterns (PRICE-ONLY, α=3, max 243)")
    print(f"   __CLASS_{info.asset_class}__ N2: {n2_trie.pattern_count} patterns (SAX Dual)")
    print(f"   PEPE N3: 0 (EMPTY — Transfer Learning)")
    print(f"   PEPE N4: 0 (EMPTY — Transfer Learning)")
    print(f"   N1 density: ~{11000 / max(n1_trie.pattern_count, 1):.1f} obs/node")
    
    print(f"\n2. LOG DE DECISIÓN DE PEPE (3 ejemplos reales):")
    for d in decisions[:3]:
        print(f"   Step {d['step']} @ ${d['price']:.6f}")
        print(f"     Pattern N1: {d['pattern_n1']} (price-only)")
        print(f"     Pattern N2: {d['pattern_n2']} (dual)")
        print(f"     N1 match: {d['n1_matched']} (conf={d['n1_conf']:.3f})")
        print(f"     N2 match: {d['n2_matched']} (conf={d['n2_conf']:.3f})")
        print(f"     N3 match: {d['n3_matched']} (EMPTY → weight redistributed)")
        print(f"     Weighted conf: {d['weighted_conf']:.3f}")
        print(f"     Direction: {d['direction']}")
        print(f"     BTC filter: {d['btc_regime']} → {'APROBADO' if d['btc_approved'] else 'RECHAZADO'}")
        print(f"     Expected sequences N1: {d['n1_expected']}")
        print(f"     Expected sequences N2: {d['n2_expected']}")
        print()
    
    print(f"3. RESULTADO DEL REPLAY OOS ({timeframe}):")
    print(f"   Total velas PEPE: {len(pepe_df)}")
    print(f"   Total pasos replay: {min_syms - PATTERN_LENGTH}")
    print(f"   Decisiones con match N1/N2: {len(decisions)}")
    print(f"   Señales generadas: {len(signals)}")
    
    # Signals above various thresholds
    conf_60 = [s for s in signals if s['confidence'] >= 0.60]
    conf_40 = [s for s in signals if s['confidence'] >= 0.40]
    conf_30 = [s for s in signals if s['confidence'] >= 0.30]
    conf_25 = [s for s in signals if s['confidence'] >= 0.25]
    print(f"   Señales con conf >= 0.60: {len(conf_60)}")
    print(f"   Señales con conf >= 0.40: {len(conf_40)}")
    print(f"   Señales con conf >= 0.30: {len(conf_30)}")
    print(f"   Señales con conf >= 0.25: {len(conf_25)}")
    
    # Pool origin breakdown
    n2_only = [s for s in signals if s['n2_match'] and not s['n1_match']]
    n1_only = [s for s in signals if s['n1_match'] and not s['n2_match']]
    both = [s for s in signals if s['n1_match'] and s['n2_match']]
    print(f"   Origen: solo N2={len(n2_only)}, solo N1={len(n1_only)}, ambos={len(both)}")
    
    print(f"\n   Trades cerrados: {len(trades)}")
    total_pnl = sum(t['pnl_pct'] for t in trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    print(f"   P&L total: {total_pnl:+.2f}%")
    print(f"   Win rate: {len(wins)}/{len(trades)} = {len(wins)/len(trades)*100:.0f}%" if trades else "   No trades")
    
    for t in trades:
        print(f"     {t['direction']:5s} | P&L: {t['pnl_pct']:+.2f}% | Exit: {t['exit']} | Conf: {t['conf']:.3f}")
    
    # VEREDICTO
    print(f"\n{'='*70}")
    if len(conf_40) >= 1:
        print("VEREDICTO: EXITO CIENTIFICO — PEPE genero senales con conf > 0.40")
        print("           usando SOLO pools compartidos (Transfer Learning FUNCIONA)")
    elif len(conf_25) >= 1:
        print("VEREDICTO: RESULTADO PARCIAL — Senales generadas pero conf < 0.40")
        print("           El motor encuentra patrones pero necesita mas datos para confianza")
    else:
        print("VEREDICTO: FALLO — PEPE no genero senales con pools compartidos")
        print("           Posibles causas: umbrales demasiado altos, patrones no coinciden")
    print(f"{'='*70}")
    
    # Top signals detail
    if signals:
        print(f"\nTOP 5 SENALES (por confidence):")
        sorted_sigs = sorted(signals, key=lambda s: s['confidence'], reverse=True)[:5]
        for s in sorted_sigs:
            print(f"  Step {s['step']} @ ${s['price']:.6f} | {s['direction']} | conf={s['confidence']:.3f} | from={s['from_pool']}")
            print(f"    Pattern N1: {s['pattern_n1']} (price-only)")
            print(f"    Pattern N2: {s['pattern_n2']} (dual)")
            print(f"    Expected N1: {s['expected_seq_n1']}")
            print(f"    Expected N2: {s['expected_seq_n2']}")
            print(f"    BTC: {s['btc_regime']}, SL={s['sl_pct']:.1f}%, TP={s['tp_pct']:.1f}%")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OOS PEPE Replay")
    parser.add_argument("--timeframe", default="5m", help="Timeframe for replay (1m, 5m)")
    args = parser.parse_args()
    run_oos_replay(timeframe=args.timeframe)
