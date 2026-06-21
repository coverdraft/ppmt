#!/usr/bin/env python3
"""TAREA 19: Net EV Gate signal counting (no position management).

Like TAREA 18 OOS but with Net EV Gate applied.
No positions held — counts ALL signals that pass the gate.
"""
import sys, os, time, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie
from ppmt.core.profiles import SPREAD_ESTIMATES

STREAMS = [
    ("BTC/USDT",  "5m",  "blue_chip"),
    ("ETH/USDT",  "5m",  "blue_chip"),
    ("SOL/USDT",  "5m",  "large_cap"),
    ("SOL/USDT",  "15m", "large_cap"),
    ("AVAX/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "15m", "large_cap"),
]
CANDLES = 1000
NET_EV_THRESHOLD = 0.80
BINANCE_BASE = "https://api.binance.com"
TF_MS = {"5m": 300_000, "15m": 900_000}

def download(symbol, tf, n):
    api_sym = symbol.replace("/", "")
    ms = TF_MS[tf]
    data = []
    end = int(time.time() * 1000)
    cur = end - n * ms
    while cur < end:
        try:
            r = requests.get(f"{BINANCE_BASE}/api/v3/klines",
                           params={"symbol": api_sym, "interval": tf, "limit": 1000, "startTime": cur}, timeout=30)
            r.raise_for_status()
            d = r.json()
        except Exception:
            time.sleep(1); continue
        if not d: break
        for c in d:
            data.append([c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        cur = d[-1][0] + ms
        time.sleep(0.1)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

def run(symbol, tf, asset_class):
    display = f"{symbol}/{tf}"
    df = download(symbol, tf, CANDLES)
    if len(df) < 200:
        print(f"  {display:18s} SKIP (only {len(df)} candles)", flush=True)
        return None

    info = AssetClassifier().classify(symbol)
    storage = PPMTStorage()
    engine = PPMT(symbol=symbol, asset_class=info.asset_class, weight_profile=info.weight_profile,
                  dual_sax=True, min_confidence=0.08, timeframe=tf)
    tries = storage.load_all_tries(symbol, asset_class=info.asset_class, timeframe=tf)
    n1, n2, n3, n4 = tries.get("n1"), tries.get("n2"), tries.get("n3"), tries.get("n4")
    if not (n1 or n2 or n3):
        storage.close()
        return None
    engine.set_tries(trie_n1=n1 if n1 else PPMTTrie(name="e"), trie_n2=n2 if n2 else PPMTTrie(name="e"),
                     trie_n3=n3 or PPMTTrie(name="e"), trie_n4=n4 if n4 else engine.trie_n4)

    w = engine.sax_n3.window_size
    pl = engine.pl_n3
    fwd = w * pl
    end_i = len(df) - fwd
    spread = SPREAD_ESTIMATES.get(info.asset_class, 0.05)

    raw = 0; passed = 0; spread_rej = 0; ev_rej = 0
    wins = 0; losses = 0; rr_list = []; ev_list = []

    for i in range(w * pl, end_i):
        price = float(df["close"].iloc[i])
        window = df.iloc[i - w * pl + 1:i + 1]
        try:
            result = engine.match_raw(current_symbols=[], current_price=price, recent_candles=window)
        except Exception:
            continue
        if result.weighted_confidence <= 0:
            continue

        best = None
        for _, mr in [("N3", result.n3_match), ("N1", result.n1_match), ("N2", result.n2_match), ("N4", result.n4_match)]:
            if mr and mr.node and mr.node.metadata.historical_count > 0:
                best = mr.node; break
        if not best:
            continue

        raw += 1
        conf = result.weighted_confidence
        fav = abs(best.metadata.max_favorable_pct)
        dd = abs(best.metadata.max_drawdown_pct)
        if dd < 0.001: dd = 0.1
        if fav < 0.001: fav = 0.1

        net_fav = fav - spread
        if net_fav <= 0:
            spread_rej += 1
            continue
        net_rr = min(net_fav / dd, 3.0)
        net_ev = conf * net_rr
        if net_ev < NET_EV_THRESHOLD:
            ev_rej += 1
            continue

        passed += 1
        rr_list.append(fav / dd if dd > 0 else 0)
        ev_list.append(net_ev)

        # Outcome
        direction = "LONG" if best.metadata.expected_move_pct > 0 else "SHORT"
        exit_i = min(i + fwd, len(df) - 1)
        exit_price = float(df["close"].iloc[exit_i])
        move = (exit_price - price) / price * 100
        won = (move > 0.01) if direction == "LONG" else (move < -0.01)
        if won: wins += 1
        else: losses += 1

    storage.close()
    total = wins + losses
    wr = wins / total if total > 0 else 0
    avg_rr = float(np.mean(rr_list)) if rr_list else 0
    avg_ev = float(np.mean(ev_list)) if ev_list else 0
    real_ev = (wr * avg_rr) - ((1 - wr) * 1.0) if total > 0 else 0

    return {
        "display": display, "candles": len(df), "raw": raw, "passed": passed,
        "spread_rej": spread_rej, "ev_rej": ev_rej,
        "wins": wins, "losses": losses, "wr": wr, "avg_rr": avg_rr,
        "avg_net_ev": avg_ev, "real_ev": real_ev,
    }

def main():
    print("=" * 78, flush=True)
    print("TAREA 19: Net EV Gate — Signal Count (No Position Management)", flush=True)
    print("=" * 78, flush=True)
    print(f"SPREAD_ESTIMATES: {SPREAD_ESTIMATES}", flush=True)
    print(f"Net EV threshold: {NET_EV_THRESHOLD}", flush=True)

    results = []
    for sym, tf, ac in STREAMS:
        print(f"\n  Processing {sym}/{tf}...", flush=True)
        r = run(sym, tf, ac)
        if r:
            results.append(r)
            print(f"    Raw={r['raw']} Passed={r['passed']} SpreadRj={r['spread_rej']} "
                  f"EVRj={r['ev_rej']} WR={r['wr']:.1%} R:R={r['avg_rr']:.2f} "
                  f"Net_EV={r['avg_net_ev']:.3f} Real_EV={r['real_ev']:.4f}R", flush=True)

    # Table
    print(f"\n{'=' * 78}", flush=True)
    print("NET EV GATE RESULTS — 7 Streams, 1000 Candles Each", flush=True)
    print(f"{'=' * 78}", flush=True)
    print(f"  {'Stream':18s} {'Raw':>5} {'Pass':>5} {'SprdRj':>6} {'EVRj':>5} {'WR':>7} {'R:R':>5} {'NetEV':>6} {'RealEV':>8}", flush=True)
    print(f"  {'─'*18} {'─'*5} {'─'*5} {'─'*6} {'─'*5} {'─'*7} {'─'*5} {'─'*6} {'─'*8}", flush=True)

    total_raw = 0; total_passed = 0; total_spread = 0; total_ev_rej = 0
    total_wins = 0; total_losses = 0; all_rr = []; all_ev = []

    for r in results:
        total_raw += r['raw']; total_passed += r['passed']
        total_spread += r['spread_rej']; total_ev_rej += r['ev_rej']
        total_wins += r['wins']; total_losses += r['losses']
        all_rr.extend([r['avg_rr']] * r['passed'] if r['passed'] > 0 else [])
        all_ev.extend([r['avg_net_ev']] * r['passed'] if r['passed'] > 0 else [])
        re_str = f"{r['real_ev']:.4f}R" if r['passed'] > 0 else "N/A"
        wr_str = f"{r['wr']:.1%}" if r['passed'] > 0 else "N/A"
        print(f"  {r['display']:18s} {r['raw']:>5d} {r['passed']:>5d} {r['spread_rej']:>6d} "
              f"{r['ev_rej']:>5d} {wr_str:>7s} {r['avg_rr']:>5.2f} {r['avg_net_ev']:>6.3f} {re_str:>8s}", flush=True)

    # Portfolio aggregate
    total_trades = total_wins + total_losses
    port_wr = total_wins / total_trades if total_trades > 0 else 0
    port_rr = float(np.mean(all_rr)) if all_rr else 0
    port_ev = float(np.mean(all_ev)) if all_ev else 0
    port_real_ev = (port_wr * port_rr) - ((1 - port_wr) * 1.0) if total_trades > 0 else 0

    print(f"  {'─'*18} {'─'*5} {'─'*5} {'─'*6} {'─'*5} {'─'*7} {'─'*5} {'─'*6} {'─'*8}", flush=True)
    re_str = f"{port_real_ev:.4f}R" if total_trades > 0 else "N/A"
    print(f"  {'PORTAFOLIO':18s} {total_raw:>5d} {total_passed:>5d} {total_spread:>6d} "
          f"{total_ev_rej:>5d} {port_wr:>6.1%} {port_rr:>5.2f} {port_ev:>6.3f} {re_str:>8s}", flush=True)

    print(f"\n  KEY FINDINGS:", flush=True)
    print(f"    Raw signals:       {total_raw}", flush=True)
    print(f"    Passed Net EV:     {total_passed} ({total_passed/total_raw*100:.1f}%)" if total_raw > 0 else "", flush=True)
    print(f"    Rejected spread:   {total_spread}", flush=True)
    print(f"    Rejected EV score: {total_ev_rej}", flush=True)
    print(f"    Portfolio WR:      {port_wr:.1%}", flush=True)
    print(f"    Portfolio R:R:     {port_rr:.2f}", flush=True)
    print(f"    Portfolio Real EV: {port_real_ev:.4f}R", flush=True)

if __name__ == "__main__":
    main()
