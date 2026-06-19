"""
Auditoría FIX-A vs LEGACY — valida que confidence_for_direction resuelve la
asimetría LONG/SHORT.

Metodología:
  - Walk-forward 70k train / 30k test por token (mismo setup que long_confidence_audit).
  - Para cada señal, registrar:
      confidence_legacy    = meta.confidence            (usa win_rate mezclada)
      confidence_long      = meta.confidence_for_direction('LONG')
      confidence_short     = meta.confidence_for_direction('SHORT')
      expected_move_legacy = meta.expected_move_pct     (mezclado)
      expected_move_long   = meta.expected_move_for_direction('LONG')
      expected_move_short  = meta.expected_move_for_direction('SHORT')
      win_rate_legacy, win_rate_long, win_rate_short
      direction_chosen_legacy  (basado en signo de expected_move_pct)
      direction_chosen_fixa    (basado en argmax de expected_move_for_direction)
      actual_move_pct, pnl_legacy, pnl_fixa, won_legacy, won_fixa
  - Para cada engine (LEGACY vs FIX-A):
      - Distribución de confidence por decil × direction
      - PnL medio por decil × direction
      - WR por decil × direction
      - Correlación confidence↔PnL
  - Comparar:
      1. ¿LONG en FIX-A es rentable donde era negativo en LEGACY?
      2. ¿La correlación confidence↔PnL LONG mejora?
      3. ¿El switch de dirección (LONG→SHORT o viceversa) cambia el PnL agregado?
"""
from __future__ import annotations
import json
import sys
import statistics
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5
TRAIN_CANDLES = 70_000
TEST_CANDLES = 30_000

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/fixa_validation")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "LINKUSDT", "PEPEUSDT", "ARBUSDT",
]


def load_df(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / f"{symbol}_1m.csv")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df_train: pd.DataFrame, symbol: str) -> PPMTTrie:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime = RegimeDetector()
    syms = sax.encode(df_train)
    trie = PPMTTrie(name=f"per_asset:{symbol}")

    for i in range(len(syms) - PATTERN_LEN):
        pattern = syms[i:i + PATTERN_LEN]
        next_sym = syms[i + PATTERN_LEN] if i + PATTERN_LEN < len(syms) else None
        start_c = i * WINDOW
        end_c = (i + PATTERN_LEN) * WINDOW
        if end_c > len(df_train):
            break
        win = df_train.iloc[start_c:end_c]
        entry = win["close"].iloc[0]
        exit_ = win["close"].iloc[-1]
        move_pct = ((exit_ - entry) / entry) * 100.0
        high = win["high"].max()
        low = win["low"].min()
        dd_pct = ((low - entry) / entry) * 100.0
        fav_pct = ((high - entry) / entry) * 100.0
        won = move_pct > 0
        rg = regime.detect_simple(win)
        trie.insert_with_observations(
            symbols=pattern, move_pct=move_pct, drawdown_pct=dd_pct,
            favorable_pct=fav_pct, duration=len(win), won=won,
            next_symbol=next_sym, regime=rg,
        )
    return trie


def walk_forward(df_test: pd.DataFrame, trie: PPMTTrie, symbol: str) -> list[dict]:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    syms = sax.encode(df_test)
    regime = RegimeDetector()

    rows = []
    for i in range(len(syms) - PATTERN_LEN):
        pattern = syms[i:i + PATTERN_LEN]
        fire_c = (i + PATTERN_LEN) * WINDOW
        end_o = fire_c + PATTERN_LEN * WINDOW
        if end_o > len(df_test):
            break

        node = trie.root
        for s in pattern:
            if s not in node.children:
                node = None
                break
            node = node.children[s]
        if node is None:
            continue
        meta = node.metadata
        if meta.historical_count < 1:
            continue

        entry_price = df_test["close"].iloc[fire_c - 1]
        exit_price = df_test["close"].iloc[end_o - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        lookback = max(0, fire_c - 50)
        rg_info = regime.detect(df_test["close"].iloc[lookback:fire_c].values.astype(float))
        rg_name = rg_info.name if hasattr(rg_info, "name") else str(rg_info)

        # ===== LEGACY engine =====
        # Confidence: meta.confidence (mixed win_rate)
        # Direction: sign of expected_move_pct (mixed)
        conf_legacy = float(meta.confidence)
        em_legacy = float(meta.expected_move_pct)
        dir_legacy = "LONG" if em_legacy > 0 else "SHORT"
        pnl_legacy = actual_move_pct if dir_legacy == "LONG" else -actual_move_pct

        # ===== FIX-A engine =====
        # Decision: pick direction with the highest expected_move_for_direction.
        # This is the key change — instead of looking at the mixed expected_move_pct
        # sign, we look at which direction the pattern historically favors.
        em_long = meta.expected_move_for_direction("LONG")
        em_short = meta.expected_move_for_direction("SHORT")
        # Edge case: si ambos son 0 (no observations), fallback a legacy
        if em_long == 0.0 and em_short == 0.0:
            dir_fixa = dir_legacy
            conf_fixa = conf_legacy
        elif em_long >= em_short:
            dir_fixa = "LONG"
            conf_fixa = float(meta.confidence_for_direction("LONG"))
        else:
            dir_fixa = "SHORT"
            conf_fixa = float(meta.confidence_for_direction("SHORT"))
        pnl_fixa = actual_move_pct if dir_fixa == "LONG" else -actual_move_pct

        rows.append({
            "token": symbol,
            "regime": rg_name,
            "actual_move_pct": round(actual_move_pct, 4),
            # Legacy
            "conf_legacy": round(conf_legacy, 4),
            "em_legacy": round(em_legacy, 4),
            "wr_legacy": round(float(meta.win_rate), 4),
            "dir_legacy": dir_legacy,
            "pnl_legacy": round(pnl_legacy, 4),
            "won_legacy": bool(pnl_legacy > 0),
            # FIX-A
            "conf_fixa": round(conf_fixa, 4),
            "em_long": round(em_long, 4),
            "em_short": round(em_short, 4),
            "wr_long": round(float(meta.win_rate_long), 4),
            "wr_short": round(float(meta.win_rate_short), 4),
            "long_count": int(meta.long_stats.count),
            "short_count": int(meta.short_stats.count),
            "dir_fixa": dir_fixa,
            "pnl_fixa": round(pnl_fixa, 4),
            "won_fixa": bool(pnl_fixa > 0),
            "dir_changed": dir_legacy != dir_fixa,
        })
    return rows


def summarize(df: pd.DataFrame, engine: str) -> dict:
    """engine = 'legacy' or 'fixa'."""
    suffix = "legacy" if engine == "legacy" else "fixa"
    pnl_col = f"pnl_{suffix}"
    dir_col = f"dir_{suffix}"
    conf_col = f"conf_{suffix}"
    won_col = f"won_{suffix}"

    out = {"engine": engine, "total": len(df)}
    for d in ["LONG", "SHORT"]:
        sub = df[df[dir_col] == d]
        out[d] = {
            "n": int(len(sub)),
            "wr": round(float(sub[won_col].mean()), 4) if len(sub) else None,
            "pnl_mean": round(float(sub[pnl_col].mean()), 4) if len(sub) else None,
            "pnl_total": round(float(sub[pnl_col].sum()), 2) if len(sub) else None,
        }
    # PnL total = LONG_total + SHORT_total
    out["pnl_total"] = round(float(df[pnl_col].sum()), 2)
    out["pnl_mean"] = round(float(df[pnl_col].mean()), 4)
    out["wr_total"] = round(float(df[won_col].mean()), 4)

    # Por decil de confidence × direction
    bins = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.01]
    labels = ["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"]
    deciles = []
    for d in ["LONG", "SHORT"]:
        sub = df[df[dir_col] == d].copy()
        if sub.empty:
            continue
        sub["decile"] = pd.cut(sub[conf_col], bins=bins, labels=labels, right=False, include_lowest=True)
        for lab in labels:
            s = sub[sub["decile"] == lab]
            n = len(s)
            if n == 0:
                deciles.append({"engine": engine, "direction": d, "decile": lab, "n": 0})
                continue
            deciles.append({
                "engine": engine, "direction": d, "decile": lab,
                "n": int(n),
                "wr": round(float(s[won_col].mean()), 4),
                "pnl_mean": round(float(s[pnl_col].mean()), 4),
                "pnl_total": round(float(s[pnl_col].sum()), 2),
            })
    out["deciles"] = deciles

    # Correlación confidence vs PnL
    from scipy.stats import spearmanr
    out["correlation"] = {}
    for d in ["LONG", "SHORT"]:
        sub = df[df[dir_col] == d]
        if len(sub) < 30:
            out["correlation"][d] = None
            continue
        sp, p = spearmanr(sub[conf_col], sub[pnl_col])
        out["correlation"][d] = {
            "n": int(len(sub)),
            "spearman": round(float(sp), 4),
            "pvalue": round(float(p), 6),
        }

    # Curva por umbral
    thresholds = [0.0, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    curve = []
    for d in ["LONG", "SHORT"]:
        sub = df[df[dir_col] == d]
        for t in thresholds:
            s = sub[sub[conf_col] > t]
            n = len(s)
            if n == 0:
                curve.append({"engine": engine, "direction": d, "threshold": f">{t:.2f}", "n": 0})
                continue
            curve.append({
                "engine": engine, "direction": d, "threshold": f">{t:.2f}",
                "n": int(n),
                "wr": round(float(s[won_col].mean()), 4),
                "pnl_mean": round(float(s[pnl_col].mean()), 4),
                "pnl_total": round(float(s[pnl_col].sum()), 2),
            })
    out["threshold_curve"] = curve
    return out


def main():
    print("=" * 72)
    print("FIX-A VALIDATION — confidence_for_direction vs confidence (legacy)")
    print("=" * 72)
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}")
    print(f"Split:  Train {TRAIN_CANDLES:,}  |  Test {TEST_CANDLES:,}")
    print(f"Tokens: {len(SYMBOLS)}")
    print()

    all_rows = []
    for sym in SYMBOLS:
        try:
            df = load_df(sym)
            if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                continue
            df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
            df_test = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)
            print(f"  {sym}: building trie ...", end=" ", flush=True)
            trie = build_trie(df_train, sym)
            print(f"walk-forward ...", end=" ", flush=True)
            rows = walk_forward(df_test, trie, sym)
            print(f"{len(rows):,} signals")
            all_rows.extend(rows)
        except Exception as e:
            import traceback
            print(f"  ERROR {sym}: {e}")
            traceback.print_exc()

    df = pd.DataFrame(all_rows)
    df.to_csv(OUT_DIR / "signals_raw.csv", index=False)
    print(f"\nTotal signals: {len(df):,}")
    print(f"  Direction changes (legacy→fixa): {df['dir_changed'].sum():,} "
          f"({df['dir_changed'].mean()*100:.1f}%)")

    # Summary por engine
    sum_legacy = summarize(df, "legacy")
    sum_fixa = summarize(df, "fixa")

    print("\n" + "=" * 72)
    print("AGGREGATE COMPARISON")
    print("=" * 72)
    print(f"{'Metric':<30} {'LEGACY':>12} {'FIX-A':>12} {'Delta':>12}")
    print("-" * 72)
    def row(label, lv, fv, fmt="{:+.4f}"):
        delta = (fv - lv) if lv is not None and fv is not None else None
        d_str = fmt.format(delta) if delta is not None else "—"
        lv_str = fmt.format(lv) if lv is not None else "—"
        fv_str = fmt.format(fv) if fv is not None else "—"
        print(f"{label:<30} {lv_str:>12} {fv_str:>12} {d_str:>12}")

    row("Total PnL", sum_legacy["pnl_total"], sum_fixa["pnl_total"], "{:+.2f}")
    row("PnL mean / signal", sum_legacy["pnl_mean"], sum_fixa["pnl_mean"], "{:+.4f}")
    row("Total WR", sum_legacy["wr_total"], sum_fixa["wr_total"], "{:.4f}")
    print()
    for d in ["LONG", "SHORT"]:
        l = sum_legacy[d]
        f = sum_fixa[d]
        print(f"  [{d}]")
        print(f"    n:           {l['n']:>10,}     {f['n']:>10,}     {(f['n']-l['n']):>+10,}")
        if l["wr"] is not None and f["wr"] is not None:
            print(f"    WR:          {l['wr']:>10.4f}     {f['wr']:>10.4f}     {(f['wr']-l['wr']):>+10.4f}")
        if l["pnl_mean"] is not None and f["pnl_mean"] is not None:
            print(f"    PnL mean:    {l['pnl_mean']:>+10.4f}     {f['pnl_mean']:>+10.4f}     {(f['pnl_mean']-l['pnl_mean']):>+10.4f}")
        if l["pnl_total"] is not None and f["pnl_total"] is not None:
            print(f"    PnL total:   {l['pnl_total']:>+10.2f}     {f['pnl_total']:>+10.2f}     {(f['pnl_total']-l['pnl_total']):>+10.2f}")

    # Correlaciones
    print("\n  Correlación confidence ↔ PnL (Spearman):")
    print(f"    {'Direction':<10} {'Engine':<10} {'n':>8} {'Spearman':>10} {'p-value':>10}")
    for d in ["LONG", "SHORT"]:
        for engine, sumr in [("legacy", sum_legacy), ("fixa", sum_fixa)]:
            c = sumr["correlation"].get(d)
            if c:
                print(f"    {d:<10} {engine:<10} {c['n']:>8,} {c['spearman']:>+10.4f} {c['pvalue']:>10.4f}")

    # Deciles: solo LONG para ver si FIX-A cambia la pendiente
    print("\n  LONG PnL mean por decil de confidence:")
    print(f"    {'Decil':<8} {'LEGACY n':>10} {'LEGACY PnL':>12} {'FIX-A n':>10} {'FIX-A PnL':>12} {'Δ':>10}")
    for lab in ["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"]:
        l = next((x for x in sum_legacy["deciles"] if x["direction"]=="LONG" and x["decile"]==lab), None)
        f = next((x for x in sum_fixa["deciles"] if x["direction"]=="LONG" and x["decile"]==lab), None)
        if not l or not f:
            continue
        l_pnl = l.get("pnl_mean")
        f_pnl = f.get("pnl_mean")
        delta = (f_pnl - l_pnl) if l_pnl is not None and f_pnl is not None else None
        print(f"    {lab:<8} {l.get('n',0):>10,} "
              f"{(f'{l_pnl:+.4f}' if l_pnl is not None else '—'):>12} "
              f"{f.get('n',0):>10,} "
              f"{(f'{f_pnl:+.4f}' if f_pnl is not None else '—'):>12} "
              f"{(f'{delta:+.4f}' if delta is not None else '—'):>10}")

    # ===== Veredicto =====
    long_legacy_pnl = sum_legacy["LONG"]["pnl_mean"]
    long_fixa_pnl = sum_fixa["LONG"]["pnl_mean"]
    long_improved = (long_fixa_pnl is not None and long_legacy_pnl is not None
                     and long_fixa_pnl > long_legacy_pnl)
    long_now_positive = long_fixa_pnl is not None and long_fixa_pnl > 0

    # ¿Mejora la correlación LONG confidence↔PnL?
    sp_legacy = sum_legacy["correlation"].get("LONG", {})
    sp_fixa = sum_fixa["correlation"].get("LONG", {})
    sp_legacy_val = sp_legacy.get("spearman", 0) if sp_legacy else 0
    sp_fixa_val = sp_fixa.get("spearman", 0) if sp_fixa else 0
    corr_improved = sp_fixa_val > sp_legacy_val

    total_legacy = sum_legacy["pnl_total"]
    total_fixa = sum_fixa["pnl_total"]
    total_improved = total_fixa > total_legacy

    if long_now_positive and long_improved and total_improved:
        verdict = "POSITIVE"
        msg = (f"FIX-A mejora LONG: PnL medio LONG {long_legacy_pnl:+.4f}% → {long_fixa_pnl:+.4f}%. "
               f"PnL total {total_legacy:+.2f}% → {total_fixa:+.2f}%. "
               f"LONG ahora {'POSITIVO' if long_now_positive else 'aún negativo'}.")
    elif long_improved and total_improved:
        verdict = "PARTIAL"
        msg = (f"FIX-A mejora LONG pero no lo vuelve positivo: "
               f"PnL medio LONG {long_legacy_pnl:+.4f}% → {long_fixa_pnl:+.4f}%. "
               f"PnL total {total_legacy:+.2f}% → {total_fixa:+.2f}%.")
    elif total_improved:
        verdict = "WEAK_POSITIVE"
        msg = (f"FIX-A mejora PnL total ({total_legacy:+.2f}% → {total_fixa:+.2f}%) "
               f"pero no específicamente LONG ({long_legacy_pnl:+.4f}% → {long_fixa_pnl:+.4f}%).")
    else:
        verdict = "NEGATIVE"
        msg = (f"FIX-A NO mejora. PnL total {total_legacy:+.2f}% → {total_fixa:+.2f}%. "
               f"LONG PnL medio {long_legacy_pnl:+.4f}% → {long_fixa_pnl:+.4f}%.")

    print("\n" + "=" * 72)
    print(f"VEREDICTO: {verdict}")
    print(msg)
    print("=" * 72)

    # Artefactos
    with open(OUT_DIR / "summary.json", "w") as f:
        json.dump({
            "config": {"alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
                       "train": TRAIN_CANDLES, "test": TEST_CANDLES, "tokens": SYMBOLS},
            "totals": {"n_signals": len(df),
                       "n_dir_changes": int(df["dir_changed"].sum()),
                       "pct_dir_changes": float(df["dir_changed"].mean())},
            "legacy": sum_legacy,
            "fixa": sum_fixa,
            "verdict": {"code": verdict, "message": msg,
                        "long_legacy_pnl": long_legacy_pnl,
                        "long_fixa_pnl": long_fixa_pnl,
                        "long_improved": long_improved,
                        "long_now_positive": long_now_positive,
                        "sp_legacy_long": sp_legacy_val,
                        "sp_fixa_long": sp_fixa_val,
                        "corr_improved": corr_improved,
                        "total_legacy": total_legacy,
                        "total_fixa": total_fixa,
                        "total_improved": total_improved},
        }, f, indent=2, default=str)
    print(f"\nArtefactos en {OUT_DIR}/")


if __name__ == "__main__":
    main()
