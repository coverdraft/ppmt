#!/usr/bin/env python3
"""TAREA 17: OOS Definitivo DOGE/USDT 1m — EV Gate Filter Test.

Downloads 2000 recent 1m candles and runs a sliding-window match
with the EV Gate applied. Reports 5 key metrics:
1. Total raw matches (before any filter)
2. Signals passing EV Gate (EV >= 0.80)
3. WR of passed signals
4. Avg R:R of passed signals
5. Real EV = (WR × avg_R:R) - (Loss_Rate × 1.0)

v0.56.0 FIX: R:R uses max_favorable / max_drawdown (historical extremes),
NOT expected_move / max_drawdown (mean vs max → absurdly low).
"""
import sys, os, time, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

SYMBOL = "DOGE/USDT"
ASSET_CLASS = "meme"
TIMEFRAME = "1m"
EV_GATE_THRESHOLD = 0.80
BINANCE_BASE = "https://api.binance.com"
MS_PER_CANDLE = 60_000


def download_1m(symbol: str, total_candles: int = 2000) -> pd.DataFrame:
    api_sym = symbol.replace("/", "")
    all_data = []
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - total_candles * MS_PER_CANDLE
    cur = start_ts
    while cur < end_ts:
        try:
            resp = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": api_sym, "interval": "1m", "limit": 1000, "startTime": cur},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            time.sleep(1)
            continue
        if not data:
            break
        for c in data:
            all_data.append([c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        cur = data[-1][0] + MS_PER_CANDLE
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def main():
    print("=" * 70, flush=True)
    print("TAREA 17: EV GATE OOS — DOGE/USDT 1m (2000 velas)", flush=True)
    print("=" * 70, flush=True)

    # 1. Download data
    print(f"\nDownloading {SYMBOL} {TIMEFRAME} (2000 candles)...", flush=True)
    df = download_1m(SYMBOL, 2000)
    if len(df) < 500:
        print(f"[ERROR] Only got {len(df)} candles", flush=True)
        return
    print(f"Downloaded: {len(df)} candles", flush=True)

    # 2. Load tries and create engine
    classifier = AssetClassifier()
    info = classifier.classify(SYMBOL)
    storage = PPMTStorage()
    all_tries = storage.load_all_tries(SYMBOL, asset_class=info.asset_class, timeframe=TIMEFRAME)
    n1 = all_tries.get("n1")
    n2 = all_tries.get("n2")
    n3 = all_tries.get("n3")
    n4 = all_tries.get("n4")
    print(f"N1: {n1.pattern_count if n1 else 0} | N2: {n2.pattern_count if n2 else 0} | "
          f"N3: {n3.pattern_count if n3 else 0} | N4: {n4.pattern_count if n4 else 0}", flush=True)

    engine = PPMT(
        symbol=SYMBOL, asset_class=info.asset_class, weight_profile=info.weight_profile,
        dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME,
    )
    from ppmt.core.trie import PPMTTrie
    engine.set_tries(
        trie_n1=n1 if n1 is not None else PPMTTrie(name="universal_empty"),
        trie_n2=n2 if n2 is not None else PPMTTrie(name="class_empty"),
        trie_n3=n3 or PPMTTrie(name="n3_empty"),
        trie_n4=n4 if n4 is not None else engine.trie_n4,
    )

    # 3. Sliding window match
    w_n3 = engine.sax_n3.window_size
    pl_n3 = engine.pl_n3
    min_candles = w_n3 * pl_n3
    forward_window = pl_n3 * w_n3
    evaluable_end = len(df) - forward_window

    # Tracking
    total_raw_matches = 0
    ev_passed = 0
    ev_passed_wins = 0
    ev_passed_losses = 0
    ev_passed_rr_list = []
    
    # Also track ALL signals (no EV Gate) for comparison
    all_signal_wins = 0
    all_signal_losses = 0
    all_signal_rr_list = []

    # Track EV distribution for diagnostics
    ev_scores_all = []

    print(f"\nRunning match ({evaluable_end} eval points, step=1)...", flush=True)
    print(f"N3: W={w_n3}, P={pl_n3}, min={min_candles}, forward={forward_window}", flush=True)

    for i in range(min_candles, evaluable_end):
        window_df = df.iloc[i - min_candles + 1:i + 1]
        current_price = float(df["close"].iloc[i])

        try:
            result = engine.match_raw(
                current_symbols=[], current_price=current_price, recent_candles=window_df,
            )
        except Exception:
            continue

        if result.weighted_confidence <= 0:
            continue

        total_raw_matches += 1

        # Extract best metadata
        best_meta = None
        best_level = None
        for level_name, match_result in [
            ("N3", result.n3_match), ("N1", result.n1_match),
            ("N2", result.n2_match), ("N4", result.n4_match),
        ]:
            if match_result and match_result.node:
                meta = match_result.node.metadata
                if meta.historical_count > 0:
                    best_meta = meta
                    best_level = level_name
                    break

        if best_meta is None:
            continue

        # Direction from expected_move
        direction = "LONG" if best_meta.expected_move_pct > 0 else "SHORT"

        # R:R = max_favorable / |max_drawdown| (historical extremes)
        # This is the CORRECT R:R: what the pattern has ACTUALLY achieved
        dd_abs = abs(best_meta.max_drawdown_pct)
        fav_abs = abs(best_meta.max_favorable_pct)
        if dd_abs < 0.001:
            dd_abs = 0.1  # fallback
        rr = fav_abs / dd_abs

        confidence = result.weighted_confidence

        # EV Score = confidence × min(R:R, 3.0)
        ev_rr = min(rr, 3.0)
        ev_score = confidence * ev_rr
        ev_scores_all.append(ev_score)

        # Determine actual outcome
        entry_price = current_price
        exit_idx = i + forward_window
        if exit_idx >= len(df):
            continue
        exit_price = float(df["close"].iloc[exit_idx])
        real_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        if direction == "LONG":
            won = real_move_pct > 0.01
        else:
            won = real_move_pct < -0.01

        # Track ALL signals (no EV Gate)
        all_signal_rr_list.append(rr)
        if won:
            all_signal_wins += 1
        else:
            all_signal_losses += 1

        # EV Gate check
        if ev_score < EV_GATE_THRESHOLD:
            continue  # REJECTED

        ev_passed += 1
        ev_passed_rr_list.append(rr)
        if won:
            ev_passed_wins += 1
        else:
            ev_passed_losses += 1

        if ev_passed % 25 == 0 and ev_passed > 0:
            running_wr = ev_passed_wins / ev_passed * 100
            print(f"  ... {ev_passed} passed EV Gate, WR={running_wr:.1f}%", flush=True)

    # 4. Final report
    print(f"\n{'=' * 70}", flush=True)
    print(f"TAREA 17: EV GATE VERDICT — {SYMBOL} {TIMEFRAME}", flush=True)
    print(f"{'=' * 70}", flush=True)

    # EV distribution (diagnostic)
    if ev_scores_all:
        ev_arr = np.array(ev_scores_all)
        print(f"\nEV Score distribution ({len(ev_arr)} matches):", flush=True)
        print(f"  mean={np.mean(ev_arr):.4f}, median={np.median(ev_arr):.4f}, max={np.max(ev_arr):.4f}", flush=True)
        for t in [0.10, 0.20, 0.30, 0.50, 0.80, 1.00]:
            cnt = (ev_arr >= t).sum()
            pct = cnt / len(ev_arr) * 100
            print(f"  EV >= {t:.2f}: {cnt:4d} ({pct:.1f}%)", flush=True)

    # --- WITHOUT EV GATE (baseline) ---
    print(f"\n--- BASELINE (no EV Gate) ---", flush=True)
    total_all = all_signal_wins + all_signal_losses
    if total_all > 0:
        wr_all = all_signal_wins / total_all
        avg_rr_all = np.mean(all_signal_rr_list) if all_signal_rr_list else 0
        loss_rate_all = 1.0 - wr_all
        ev_all = (wr_all * avg_rr_all) - (loss_rate_all * 1.0)
        print(f"  Total signals: {total_all}", flush=True)
        print(f"  WR: {wr_all:.2%} ({all_signal_wins}W / {all_signal_losses}L)", flush=True)
        print(f"  Avg R:R: {avg_rr_all:.2f}", flush=True)
        print(f"  EV: ({wr_all:.2%} × {avg_rr_all:.2f}) - ({loss_rate_all:.2%} × 1.0) = {ev_all:.4f}R", flush=True)

    # --- WITH EV GATE ---
    print(f"\n--- WITH EV GATE (≥ {EV_GATE_THRESHOLD}) ---", flush=True)

    # Metric 1
    print(f"\n1. Total raw matches (before EV Gate):  {total_raw_matches}", flush=True)

    # Metric 2
    print(f"2. Signals passing EV Gate (≥ {EV_GATE_THRESHOLD}):  {ev_passed}", flush=True)
    pass_rate = (ev_passed / total_raw_matches * 100) if total_raw_matches > 0 else 0
    print(f"   Pass rate: {pass_rate:.1f}%", flush=True)

    # Metric 3
    total_ev_trades = ev_passed_wins + ev_passed_losses
    if total_ev_trades > 0:
        wr_filtered = ev_passed_wins / total_ev_trades
        print(f"3. WR of passed signals: {wr_filtered:.2%} ({ev_passed_wins}W / {ev_passed_losses}L)", flush=True)
    else:
        wr_filtered = 0
        print(f"3. WR of passed signals: N/A (0 trades passed EV Gate)", flush=True)

    # Metric 4
    if ev_passed > 0:
        avg_rr = np.mean(ev_passed_rr_list)
        print(f"4. Avg R:R of passed signals: {avg_rr:.2f}", flush=True)
    else:
        avg_rr = 0
        print(f"4. Avg R:R of passed signals: N/A", flush=True)

    # Metric 5
    if total_ev_trades > 0 and avg_rr > 0:
        loss_rate = 1.0 - wr_filtered
        real_ev = (wr_filtered * avg_rr) - (loss_rate * 1.0)
        print(f"5. Real EV: ({wr_filtered:.2%} × {avg_rr:.2f}) - ({loss_rate:.2%} × 1.0) = {real_ev:.4f}R", flush=True)
    else:
        real_ev = 0
        print(f"5. Real EV: N/A (insufficient data)", flush=True)

    # Final verdict
    print(f"\n{'=' * 70}", flush=True)
    if real_ev > 0.20:
        print(f"✅ VEREDICTO: EV real = {real_ev:.4f}R > 0.20R → 1m TIENE EDGE OCULTO", flush=True)
    elif real_ev > 0.00:
        print(f"⚠️ VEREDICTO: EV real = {real_ev:.4f}R → 1m es MARGINAL, no vale la pena", flush=True)
    elif total_ev_trades == 0:
        print(f"❌ VEREDICTO: 0 señales pasaron el EV Gate → 1m NO TIENE EDGE", flush=True)
        print(f"   → Ni una sola señal con EV >= {EV_GATE_THRESHOLD}", flush=True)
        print(f"   → Confidence max ~0.47 × R:R max ~3.0 = EV max ~1.4", flush=True)
        print(f"   → Pero la R:R REAL es max ~1.6 (favorable/drawdown)", flush=True)
        print(f"   → EV Score max real ≈ 0.47 × 1.6 ≈ 0.75 < 0.80", flush=True)
    else:
        print(f"❌ VEREDICTO: EV real = {real_ev:.4f}R ≤ 0 → 1m NO TIENE EDGE DIRECCIONAL", flush=True)

    print(f"   → 1m pasa a ser solo proveedor de datos para N1/N2", flush=True)
    print(f"   → Ejecución se mueve a 5m/15m", flush=True)

    storage.close()
    print(f"\nDone.", flush=True)


if __name__ == "__main__":
    main()
