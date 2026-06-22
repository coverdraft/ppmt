#!/usr/bin/env python3
"""TAREA 18: Portfolio Velocity OOS — 5m and 15m.

Measures multi-token execution volume to prove 5m/15m can generate
sufficient trade frequency for a professional portfolio.

Tokens: SOL/USDT (large_cap), DOGE/USDT (meme), LINK/USDT (alt/large_cap)
Timeframes: 5m, 15m
Candles: 1000 per token per timeframe

Metrics per token:
  1. Signals passing EV Gate (>= 0.80)
  2. WR of passed signals
  3. Avg R:R of passed signals

Portfolio aggregate:
  4. Total operations across 3 tokens
  5. WR average of portfolio
  6. EV estimated of portfolio

Projection:
  - 1000 candles in 5m = ~3.4 days
  - Monthly ops = (total_ops / 3.4) * 30
  - 10-token projection = monthly_ops * 3.3
"""
import sys
import os
import time
import requests
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie

logging.basicConfig(level=logging.WARNING)

# ── Configuration ──
TOKENS = [
    ("SOL/USDT", "large_cap"),
    ("DOGE/USDT", "meme"),
    ("LINK/USDT", "large_cap"),
]
TIMEFRAMES = ["5m", "15m"]
TOTAL_CANDLES = 1000
EV_GATE_THRESHOLD = 0.80
BINANCE_BASE = "https://api.binance.com"

# MS per candle by timeframe
TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
}


def download_candles(symbol: str, timeframe: str, total_candles: int) -> pd.DataFrame:
    """Download candles from Binance."""
    api_sym = symbol.replace("/", "")
    ms_per = TF_MS[timeframe]
    all_data = []
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - total_candles * ms_per
    cur = start_ts
    while cur < end_ts:
        try:
            resp = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={
                    "symbol": api_sym,
                    "interval": timeframe,
                    "limit": 1000,
                    "startTime": cur,
                },
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
            all_data.append([
                c[0], float(c[1]), float(c[2]), float(c[3]),
                float(c[4]), float(c[5]),
            ])
        cur = data[-1][0] + ms_per
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def run_oos_single(symbol: str, asset_class: str, timeframe: str, df: pd.DataFrame) -> dict:
    """Run EV Gate OOS for a single token+timeframe. Returns metrics dict."""
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    storage = PPMTStorage()

    all_tries = storage.load_all_tries(symbol, asset_class=info.asset_class, timeframe=timeframe)
    n1 = all_tries.get("n1")
    n2 = all_tries.get("n2")
    n3 = all_tries.get("n3")
    n4 = all_tries.get("n4")

    print(f"    Tries: N1={n1.pattern_count if n1 else 0}, N2={n2.pattern_count if n2 else 0}, "
          f"N3={n3.pattern_count if n3 else 0}, N4={n4.pattern_count if n4 else 0}", flush=True)

    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        weight_profile=info.weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )
    engine.set_tries(
        trie_n1=n1 if n1 is not None else PPMTTrie(name="n1_empty"),
        trie_n2=n2 if n2 is not None else PPMTTrie(name="n2_empty"),
        trie_n3=n3 or PPMTTrie(name="n3_empty"),
        trie_n4=n4 if n4 is not None else engine.trie_n4,
    )

    # Determine window parameters
    w_n3 = engine.sax_n3.window_size
    pl_n3 = engine.pl_n3
    min_candles = w_n3 * pl_n3
    forward_window = pl_n3 * w_n3
    evaluable_end = len(df) - forward_window

    # Tracking
    total_raw = 0
    ev_passed = 0
    ev_passed_wins = 0
    ev_passed_losses = 0
    ev_passed_rr_list = []

    print(f"    W_n3={w_n3}, P_n3={pl_n3}, min={min_candles}, fwd={forward_window}, "
          f"eval_end={evaluable_end}", flush=True)

    for i in range(min_candles, evaluable_end):
        window_df = df.iloc[i - min_candles + 1: i + 1]
        current_price = float(df["close"].iloc[i])

        try:
            result = engine.match_raw(
                current_symbols=[], current_price=current_price,
                recent_candles=window_df,
            )
        except Exception:
            continue

        if result.weighted_confidence <= 0:
            continue

        total_raw += 1

        # Extract best metadata
        best_meta = None
        for level_name, match_result in [
            ("N3", result.n3_match), ("N1", result.n1_match),
            ("N2", result.n2_match), ("N4", result.n4_match),
        ]:
            if match_result and match_result.node:
                meta = match_result.node.metadata
                if meta.historical_count > 0:
                    best_meta = meta
                    break

        if best_meta is None:
            continue

        direction = "LONG" if best_meta.expected_move_pct > 0 else "SHORT"

        # R:R = max_favorable / |max_drawdown| (historical extremes)
        dd_abs = abs(best_meta.max_drawdown_pct)
        fav_abs = abs(best_meta.max_favorable_pct)
        if dd_abs < 0.001:
            dd_abs = 0.1
        rr = fav_abs / dd_abs

        confidence = result.weighted_confidence

        # EV Score
        ev_rr = min(rr, 3.0)
        ev_score = confidence * ev_rr

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

        # EV Gate check
        if ev_score < EV_GATE_THRESHOLD:
            continue

        ev_passed += 1
        ev_passed_rr_list.append(rr)
        if won:
            ev_passed_wins += 1
        else:
            ev_passed_losses += 1

    storage.close()

    total_ev = ev_passed_wins + ev_passed_losses
    wr = ev_passed_wins / total_ev if total_ev > 0 else 0.0
    avg_rr = float(np.mean(ev_passed_rr_list)) if ev_passed_rr_list else 0.0
    loss_rate = 1.0 - wr if total_ev > 0 else 1.0
    real_ev = (wr * avg_rr) - (loss_rate * 1.0) if total_ev > 0 else 0.0

    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "total_raw": total_raw,
        "ev_passed": ev_passed,
        "ev_wins": ev_passed_wins,
        "ev_losses": ev_passed_losses,
        "wr": wr,
        "avg_rr": avg_rr,
        "real_ev": real_ev,
        "candles_used": len(df),
    }


def main():
    print("=" * 78, flush=True)
    print("TAREA 18: PORTFOLIO VELOCITY OOS — 5m & 15m", flush=True)
    print("=" * 78, flush=True)

    all_results = []

    for tf in TIMEFRAMES:
        print(f"\n{'─' * 78}", flush=True)
        print(f"  TIMEFRAME: {tf}", flush=True)
        print(f"{'─' * 78}", flush=True)

        for symbol, asset_class in TOKENS:
            print(f"\n  ▸ {symbol} ({asset_class}) @ {tf}", flush=True)

            # Download data
            print(f"    Downloading {TOTAL_CANDLES} {tf} candles...", flush=True)
            df = download_candles(symbol, tf, TOTAL_CANDLES)
            if len(df) < 200:
                print(f"    [ERROR] Only got {len(df)} candles — skipping", flush=True)
                continue
            print(f"    Got {len(df)} candles", flush=True)

            # Run OOS
            result = run_oos_single(symbol, asset_class, tf, df)
            all_results.append(result)

            print(f"    Raw matches: {result['total_raw']}", flush=True)
            print(f"    EV Gate passed: {result['ev_passed']}", flush=True)
            if result['ev_passed'] > 0:
                print(f"    WR: {result['wr']:.2%} ({result['ev_wins']}W / {result['ev_losses']}L)",
                      flush=True)
                print(f"    Avg R:R: {result['avg_rr']:.2f}", flush=True)
                print(f"    Real EV: {result['real_ev']:.4f}R", flush=True)
            else:
                print(f"    WR: N/A | R:R: N/A | EV: N/A", flush=True)

    # ── Final Report ──
    print(f"\n{'=' * 78}", flush=True)
    print("TAREA 18: PORTFOLIO VELOCITY — FINAL REPORT", flush=True)
    print(f"{'=' * 78}", flush=True)

    # ── Table 5m ──
    for tf in TIMEFRAMES:
        tf_results = [r for r in all_results if r["timeframe"] == tf]
        if not tf_results:
            continue

        # Days covered by 1000 candles
        if tf == "5m":
            days_covered = (TOTAL_CANDLES * 5) / (60 * 24)  # ~3.47 days
        elif tf == "15m":
            days_covered = (TOTAL_CANDLES * 15) / (60 * 24)  # ~10.42 days
        else:
            days_covered = 1.0

        print(f"\n{'─' * 78}", flush=True)
        print(f"  TABLA {tf.upper()} — {TOTAL_CANDLES} velas (~{days_covered:.1f} días)", flush=True)
        print(f"{'─' * 78}", flush=True)
        print(f"  {'Token':<14} {'Señales EV':>10} {'WR':>8} {'R:R':>7} {'EV':>9}", flush=True)
        print(f"  {'─'*14} {'─'*10} {'─'*8} {'─'*7} {'─'*9}", flush=True)

        # Per-token rows
        for r in tf_results:
            ev_str = f"{r['real_ev']:.4f}R" if r['ev_passed'] > 0 else "N/A"
            wr_str = f"{r['wr']:.1%}" if r['ev_passed'] > 0 else "N/A"
            rr_str = f"{r['avg_rr']:.2f}" if r['ev_passed'] > 0 else "N/A"
            print(f"  {r['symbol']:<14} {r['ev_passed']:>10} {wr_str:>8} {rr_str:>7} {ev_str:>9}",
                  flush=True)

        # Portfolio aggregate
        total_ev_passed = sum(r["ev_passed"] for r in tf_results)
        total_ev_wins = sum(r["ev_wins"] for r in tf_results)
        total_ev_losses = sum(r["ev_losses"] for r in tf_results)
        total_ev_trades = total_ev_wins + total_ev_losses
        portfolio_wr = total_ev_wins / total_ev_trades if total_ev_trades > 0 else 0.0
        # Weighted average R:R across all passed signals
        all_rr = []
        for r in tf_results:
            if r["ev_passed"] > 0 and r["avg_rr"] > 0:
                # Approximate: extend avg_rr by number of trades
                all_rr.extend([r["avg_rr"]] * r["ev_passed"])
        portfolio_avg_rr = float(np.mean(all_rr)) if all_rr else 0.0
        portfolio_loss_rate = 1.0 - portfolio_wr if total_ev_trades > 0 else 1.0
        portfolio_ev = (portfolio_wr * portfolio_avg_rr) - (portfolio_loss_rate * 1.0) if total_ev_trades > 0 else 0.0

        print(f"  {'─'*14} {'─'*10} {'─'*8} {'─'*7} {'─'*9}", flush=True)
        ev_str = f"{portfolio_ev:.4f}R" if total_ev_trades > 0 else "N/A"
        wr_str = f"{portfolio_wr:.1%}" if total_ev_trades > 0 else "N/A"
        rr_str = f"{portfolio_avg_rr:.2f}" if all_rr else "N/A"
        print(f"  {'PORTAFOLIO':<14} {total_ev_passed:>10} {wr_str:>8} {rr_str:>7} {ev_str:>9}",
              flush=True)

        # ── PASO 3: Volume Projection ──
        print(f"\n  PROYECCIÓN DE VOLUMEN ({tf.upper()}):", flush=True)
        ops_per_period = total_ev_passed
        monthly_ops_3tok = (ops_per_period / days_covered) * 30
        monthly_ops_10tok = monthly_ops_3tok * (10 / 3)

        print(f"    Operaciones en {days_covered:.1f} días (3 tokens): {ops_per_period}", flush=True)
        print(f"    Ops/mes estimadas (3 tokens):  {monthly_ops_3tok:.0f}", flush=True)
        print(f"    Ops/mes estimadas (10 tokens): {monthly_ops_10tok:.0f}", flush=True)

        if portfolio_ev > 0.20:
            verdict = f"✅ EV={portfolio_ev:.4f}R > 0.20R → {tf} TIENE EDGE como executor"
        elif portfolio_ev > 0.00:
            verdict = f"⚠️ EV={portfolio_ev:.4f}R → {tf} es MARGINAL"
        elif total_ev_trades == 0:
            verdict = f"❌ 0 señales pasaron EV Gate → {tf} NO TIENE EDGE"
        else:
            verdict = f"❌ EV={portfolio_ev:.4f}R ≤ 0 → {tf} NO TIENE EDGE"
        print(f"    VEREDICTO: {verdict}", flush=True)

    # ── Summary comparison ──
    print(f"\n{'=' * 78}", flush=True)
    print("RESUMEN COMPARATIVO: 5m vs 15m", flush=True)
    print(f"{'=' * 78}", flush=True)

    for tf in TIMEFRAMES:
        tf_results = [r for r in all_results if r["timeframe"] == tf]
        total_ev = sum(r["ev_passed"] for r in tf_results)
        total_wins = sum(r["ev_wins"] for r in tf_results)
        total_trades = sum(r["ev_wins"] + r["ev_losses"] for r in tf_results)
        wr = total_wins / total_trades * 100 if total_trades > 0 else 0
        print(f"  {tf}: {total_ev} señales EV | {total_trades} trades | WR={wr:.1f}%", flush=True)

    print(f"\nDone.", flush=True)


if __name__ == "__main__":
    main()
