"""
Verify the alpha=4 hypothesis: does lowering SAX alphabet from 5 to 4 actually
improve pattern repetition enough to push confidence above 0.15?

Tests 3 configs on BTCUSDT 50k 1m candles:
  - α=5, W=7, PL=5  (current production for 1m)
  - α=4, W=7, PL=5  (proposed — fewer unique patterns → more reps)
  - α=4, W=7, PL=4  (proposed + shorter patterns → even more reps)

Usage:
    python /home/z/my-project/scripts/verify_alpha4_hypothesis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.regime import RegimeDetector

DATA = Path("/home/z/my-project/download/real_data_1m")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
           "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
N_CANDLES = 50_000
MIN_CONF = 0.15

CONFIGS = [
    {"name": "alpha5_w7_pl5 (prod)", "alpha": 5, "window": 7, "pl": 5},
    {"name": "alpha4_w7_pl5",        "alpha": 4, "window": 7, "pl": 5},
    {"name": "alpha4_w7_pl4",        "alpha": 4, "window": 7, "pl": 4},
    {"name": "alpha3_w7_pl5",        "alpha": 3, "window": 7, "pl": 5},
]


def build(df: pd.DataFrame, alpha: int, window: int, pl: int) -> PPMTTrie:
    sax = SAXEncoder(alphabet_size=alpha, window_size=window)
    rd = RegimeDetector()
    syms = sax.encode(df)
    trie = PPMTTrie(name="test")
    for i in range(len(syms) - pl):
        pattern = syms[i:i+pl]
        next_sym = syms[i+pl] if i+pl < len(syms) else None
        start = i * window
        end = (i + pl) * window
        if end > len(df):
            break
        wdf = df.iloc[start:end]
        ep = wdf["close"].iloc[0]
        xp = wdf["close"].iloc[-1]
        move = ((xp - ep) / ep) * 100.0
        dd = ((wdf["low"].min() - ep) / ep) * 100.0
        fav = ((wdf["high"].max() - ep) / ep) * 100.0
        won = move > 0
        regime = rd.detect_simple(wdf)
        trie.insert_with_observations(
            symbols=pattern, move_pct=move, drawdown_pct=dd,
            favorable_pct=fav, duration=len(wdf), won=won,
            next_symbol=next_sym, regime=regime,
        )
    return trie


def stats(trie: PPMTTrie, pl: int) -> dict:
    counts, confs = [], []
    stack = [(trie.root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth == pl:
            m = node.metadata
            if m.historical_count > 0:
                counts.append(m.historical_count)
                confs.append(float(m.confidence))
            continue
        for c in node.children.values():
            stack.append((c, depth + 1))
    if not counts:
        return {"n": 0}
    c = np.array(counts)
    cf = np.array(confs)
    return {
        "n_unique": len(c),
        "count_mean": float(c.mean()),
        "count_median": float(np.median(c)),
        "count_p90": float(np.percentile(c, 90)),
        "count_max": int(c.max()),
        "pct_eq_1": round(float((c == 1).sum()) / len(c) * 100, 1),
        "pct_5_9": round(float(((c >= 5) & (c <= 9)).sum()) / len(c) * 100, 1),
        "pct_10p": round(float((c >= 10).sum()) / len(c) * 100, 1),
        "conf_mean": float(cf.mean()),
        "conf_max": float(cf.max()),
        "n_above_conf": int((cf >= MIN_CONF).sum()),
        "pct_above_conf": round(float((cf >= MIN_CONF).sum()) / len(c) * 100, 1),
    }


def main():
    print(f"Testing {len(CONFIGS)} SAX configs on {len(SYMBOLS)} tokens @ {N_CANDLES} 1m candles")
    print()
    results = {cfg["name"]: [] for cfg in CONFIGS}

    for sym in SYMBOLS:
        df = pd.read_csv(DATA / f"{sym}_1m.csv").tail(N_CANDLES).reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)

        for cfg in CONFIGS:
            trie = build(df, cfg["alpha"], cfg["window"], cfg["pl"])
            s = stats(trie, cfg["pl"])
            results[cfg["name"]].append(s)
        print(f"  {sym} done")

    # Aggregate
    print()
    print("=" * 130)
    print(f"{'Config':<30} {'#Patrones':>10} {'MeanCount':>10} {'MedCount':>10} "
          f"{'MaxCount':>10} {'%cnt=1':>8} {'%cnt5-9':>9} {'%cnt10+':>9} "
          f"{'ConfMed':>9} {'ConfMax':>9} {'%conf>=0.15':>13}")
    print("-" * 130)
    for cfg in CONFIGS:
        arr = results[cfg["name"]]
        n_avg = np.mean([s["n_unique"] for s in arr])
        cm_avg = np.mean([s["count_mean"] for s in arr])
        cmd_avg = np.mean([s["count_median"] for s in arr])
        cmx_avg = np.mean([s["count_max"] for s in arr])
        p1_avg = np.mean([s["pct_eq_1"] for s in arr])
        p59_avg = np.mean([s["pct_5_9"] for s in arr])
        p10_avg = np.mean([s["pct_10p"] for s in arr])
        cfm_avg = np.mean([s["conf_mean"] for s in arr])
        cfmx_avg = np.mean([s["conf_max"] for s in arr])
        pac_avg = np.mean([s["pct_above_conf"] for s in arr])
        print(f"{cfg['name']:<30} {n_avg:>10.0f} {cm_avg:>10.2f} {cmd_avg:>10.1f} "
              f"{cmx_avg:>10.0f} {p1_avg:>7.1f}% {p59_avg:>8.1f}% {p10_avg:>8.1f}% "
              f"{cfm_avg:>9.4f} {cfmx_avg:>9.4f} {pac_avg:>12.1f}%")

    print()
    print("Interpretation:")
    print("  - Lower alpha = fewer unique patterns = more reps per pattern = higher conf")
    print("  - But too low alpha = loses discrimination (all candles look the same)")
    print("  - Sweet spot is where conf_mean approaches or exceeds 0.15 without losing too much detail")


if __name__ == "__main__":
    main()
