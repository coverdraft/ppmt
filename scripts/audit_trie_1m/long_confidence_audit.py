"""
Auditoría LONG vs Confidence — responde a la hipótesis crítica:

¿confidence predice edge?

Si YES  → FIX-15 (filtrar LONG por confidence) tiene justificación.
Si NO   → FIX-15 es un parche inútil; pivotar a otra hipótesis.

Metodología:
  - Walk-forward 70k train / 30k test por token (mismo setup que layer1_fix14).
  - Registrar TODAS las señales que el trie podría disparar, SIN filtro de
    MIN_CONFIDENCE — para que el análisis por decil sea representativo.
  - Para cada señal registrar:
      token, direction, confidence, expected_move_pct, historical_count,
      historical_win_rate, regime, actual_move_pct, pnl_pct, won
  - PnL = actual_move_pct (LONG) o -actual_move_pct (SHORT). Sin fees.
  - Análisis por decil de confidence (0-10, 10-20, …, 90-100) separado
    LONG y SHORT. Métricas: n, WR, PnL medio, PnL total, expectancy.

Output en /home/z/my-project/download/long_confidence_audit/:
  - signals_raw.csv       (todas las señales, para análisis ad hoc)
  - decile_summary.csv    (tabla por decil × direction)
  - audit.json            (estructurado)
  - audit.md              (veredicto legible)
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

# ----- config -----
ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5
# SIN filtro MIN_CONFIDENCE — queremos ver TODAS las señales.
TRAIN_CANDLES = 70_000
TEST_CANDLES = 30_000

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/long_confidence_audit")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "LINKUSDT", "PEPEUSDT", "ARBUSDT",
]


def load_df(symbol: str) -> pd.DataFrame:
    csv = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df_train: pd.DataFrame, symbol: str) -> tuple[PPMTTrie, int]:
    """Construye N3 (per-asset, regime-agnostic) sobre train."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime = RegimeDetector()
    symbols = sax.encode(df_train)

    trie = PPMTTrie(name=f"per_asset:{symbol}")

    count = 0
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None
        start_candle = i * WINDOW
        end_candle = (i + PATTERN_LEN) * WINDOW
        if end_candle > len(df_train):
            break
        win = df_train.iloc[start_candle:end_candle]
        entry = win["close"].iloc[0]
        exit_ = win["close"].iloc[-1]
        move_pct = ((exit_ - entry) / entry) * 100.0
        high = win["high"].max()
        low = win["low"].min()
        dd_pct = ((low - entry) / entry) * 100.0
        fav_pct = ((high - entry) / entry) * 100.0
        duration = len(win)
        won = move_pct > 0
        rg = regime.detect_simple(win)

        trie.insert_with_observations(
            symbols=pattern, move_pct=move_pct, drawdown_pct=dd_pct,
            favorable_pct=fav_pct, duration=duration, won=won,
            next_symbol=next_sym, regime=rg,
        )
        count += 1
    return trie, count


def walk_forward(df_test: pd.DataFrame, trie: PPMTTrie, symbol: str) -> list[dict]:
    """Walk-forward sobre test — registra TODA señal que el trie pueda producir."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime = RegimeDetector()
    symbols = sax.encode(df_test)

    rows: list[dict] = []
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(df_test):
            break

        # Lookup directo al nodo (sin importar confidence)
        node = trie.root
        for sym in pattern:
            if sym not in node.children:
                node = None
                break
            node = node.children[sym]
        if node is None:
            continue  # patrón no visto en train → no produce señal

        meta = node.metadata
        if meta.historical_count < 1:
            continue

        # Outcome real
        entry_price = df_test["close"].iloc[fire_candle - 1]
        exit_price = df_test["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        # Régimen al fuego
        lookback_start = max(0, fire_candle - 50)
        rg_info = regime.detect(df_test["close"].iloc[lookback_start:fire_candle].values.astype(float))
        rg_name = rg_info.name if hasattr(rg_info, "name") else str(rg_info)

        # Predicción del trie
        conf = float(meta.confidence)
        expected_move = float(getattr(meta, "expected_move_pct", 0.0))
        direction = "LONG" if expected_move > 0 else "SHORT"
        # PnL sin fees: LONG gana si sube, SHORT gana si baja
        pnl_pct = actual_move_pct if direction == "LONG" else -actual_move_pct
        won = pnl_pct > 0

        rows.append({
            "token": symbol,
            "direction": direction,
            "confidence": round(conf, 4),
            "expected_move_pct": round(expected_move, 4),
            "historical_count": meta.historical_count,
            "historical_win_rate": round(float(meta.win_rate), 4),
            "regime": rg_name,
            "actual_move_pct": round(actual_move_pct, 4),
            "pnl_pct": round(pnl_pct, 4),
            "won": bool(won),
        })
    return rows


def decile_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Para cada direction, computa métricas por decil de confidence."""
    out_rows = []
    # deciles 0-10, 10-20, ..., 90-100. Pongo edge inclusivo a la izquierda.
    bins = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.01]
    labels = ["0-10", "10-20", "20-30", "30-40", "40-50",
              "50-60", "60-70", "70-80", "80-90", "90-100"]
    for direction in ["LONG", "SHORT"]:
        sub = df[df["direction"] == direction].copy()
        if sub.empty:
            continue
        sub["decile"] = pd.cut(sub["confidence"], bins=bins, labels=labels, right=False, include_lowest=True)
        for lab in labels:
            d = sub[sub["decile"] == lab]
            n = len(d)
            if n == 0:
                out_rows.append({
                    "direction": direction, "decile": lab, "n": 0,
                    "wr": None, "pnl_mean": None, "pnl_total": None,
                    "expectancy": None, "avg_win": None, "avg_loss": None,
                })
                continue
            wins = d[d["won"]]
            losses = d[~d["won"]]
            wr = len(wins) / n
            avg_win = wins["pnl_pct"].mean() if len(wins) else 0.0
            avg_loss = losses["pnl_pct"].mean() if len(losses) else 0.0
            # expectancy = WR * avg_win + (1-WR) * avg_loss   (avg_loss es negativo)
            exp = wr * avg_win + (1 - wr) * avg_loss
            out_rows.append({
                "direction": direction,
                "decile": lab,
                "n": int(n),
                "wr": round(wr, 4),
                "pnl_mean": round(d["pnl_pct"].mean(), 4),
                "pnl_total": round(d["pnl_pct"].sum(), 2),
                "expectancy": round(exp, 4),
                "avg_win": round(avg_win, 4),
                "avg_loss": round(avg_loss, 4),
            })
    return pd.DataFrame(out_rows)


def threshold_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Análisis por umbrales concretos que el usuario mencionó:
       >0.15, >0.20, >0.25, >0.30, >0.35, >0.40, >0.50
       Para cada direction: n, WR, PnL medio, PnL total, expectancy."""
    out_rows = []
    thresholds = [0.0, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    for direction in ["LONG", "SHORT"]:
        sub = df[df["direction"] == direction].copy()
        if sub.empty:
            continue
        for t in thresholds:
            d = sub[sub["confidence"] > t]
            n = len(d)
            if n == 0:
                out_rows.append({
                    "direction": direction, "threshold": f">{t:.2f}", "n": 0,
                    "wr": None, "pnl_mean": None, "pnl_total": None, "expectancy": None,
                })
                continue
            wins = d[d["won"]]
            losses = d[~d["won"]]
            wr = len(wins) / n
            avg_win = wins["pnl_pct"].mean() if len(wins) else 0.0
            avg_loss = losses["pnl_pct"].mean() if len(losses) else 0.0
            exp = wr * avg_win + (1 - wr) * avg_loss
            out_rows.append({
                "direction": direction,
                "threshold": f">{t:.2f}",
                "n": int(n),
                "wr": round(wr, 4),
                "pnl_mean": round(d["pnl_pct"].mean(), 4),
                "pnl_total": round(d["pnl_pct"].sum(), 2),
                "expectancy": round(exp, 4),
            })
    return pd.DataFrame(out_rows)


def correlation_analysis(df: pd.DataFrame) -> dict:
    """¿Confidence correlaciona con PnL? ¿Con won?"""
    out = {}
    for direction in ["LONG", "SHORT"]:
        sub = df[df["direction"] == direction]
        if len(sub) < 30:
            out[direction] = None
            continue
        conf = sub["confidence"].values
        pnl = sub["pnl_pct"].values
        won = sub["won"].astype(int).values
        # Spearman es más robusto para relaciones no lineales
        from scipy.stats import spearmanr, pearsonr
        sp_pnl, p_pnl = spearmanr(conf, pnl)
        sp_won, p_won = spearmanr(conf, won)
        pe_pnl, _ = pearsonr(conf, pnl)
        out[direction] = {
            "n": int(len(sub)),
            "spearman_conf_pnl": round(float(sp_pnl), 4),
            "spearman_conf_pnl_pvalue": round(float(p_pnl), 6),
            "pearson_conf_pnl": round(float(pe_pnl), 4),
            "spearman_conf_won": round(float(sp_won), 4),
            "spearman_conf_won_pvalue": round(float(p_won), 6),
        }
    return out


def per_token_long_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Para cada token, breakdown LONG por bandas de confidence."""
    bands = [(0.0, 0.20, "low"), (0.20, 0.30, "mid"), (0.30, 1.01, "high")]
    rows = []
    for tok in df["token"].unique():
        sub_tok = df[df["token"] == tok]
        for lo, hi, lab in bands:
            d = sub_tok[(sub_tok["direction"] == "LONG") &
                        (sub_tok["confidence"] >= lo) &
                        (sub_tok["confidence"] < hi)]
            n = len(d)
            if n == 0:
                rows.append({"token": tok, "band": lab, "n": 0, "wr": None,
                             "pnl_mean": None, "pnl_total": None})
                continue
            wr = d["won"].mean()
            rows.append({
                "token": tok, "band": lab, "n": int(n),
                "wr": round(float(wr), 4),
                "pnl_mean": round(d["pnl_pct"].mean(), 4),
                "pnl_total": round(d["pnl_pct"].sum(), 2),
            })
    return pd.DataFrame(rows)


def main():
    print("=" * 72)
    print("AUDITORIA LONG vs CONFIDENCE — ¿confidence predice edge?")
    print("=" * 72)
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}")
    print(f"Split:  Train {TRAIN_CANDLES:,}  |  Test {TEST_CANDLES:,}")
    print(f"Tokens: {len(SYMBOLS)}  ({', '.join(SYMBOLS)})")
    print(f"Sin filtro MIN_CONFIDENCE — capturamos TODAS las señales")
    print()

    all_rows: list[dict] = []
    for sym in SYMBOLS:
        try:
            df = load_df(sym)
            if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                print(f"  SKIP {sym}: solo {len(df)} velas")
                continue
            df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
            df_test = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)
            print(f"  {sym}: build trie ({TRAIN_CANDLES} train) ...", end=" ", flush=True)
            trie, n_ins = build_trie(df_train, sym)
            print(f"{n_ins:,} patrones. Walk-forward ...", end=" ", flush=True)
            rows = walk_forward(df_test, trie, sym)
            n_long = sum(1 for r in rows if r["direction"] == "LONG")
            n_short = sum(1 for r in rows if r["direction"] == "SHORT")
            print(f"{len(rows):,} señales (L={n_long:,} S={n_short:,})")
            all_rows.extend(rows)
        except Exception as e:
            import traceback
            print(f"  ERROR {sym}: {e}")
            traceback.print_exc()

    if not all_rows:
        print("\nNO HAY SEÑALES — abortando")
        return

    df = pd.DataFrame(all_rows)
    df.to_csv(OUT_DIR / "signals_raw.csv", index=False)
    print(f"\nTotal señales: {len(df):,} "
          f"(LONG={len(df[df.direction=='LONG']):,}  "
          f"SHORT={len(df[df.direction=='SHORT']):,})")

    # 1. Distribución de confidence
    print("\n--- Distribución de confidence ---")
    print(df.groupby("direction")["confidence"].describe().round(4).to_string())

    # 2. Análisis por decil
    dec = decile_analysis(df)
    dec.to_csv(OUT_DIR / "decile_summary.csv", index=False)
    print("\n--- PnL medio por decil de confidence ---")
    print(dec.to_string(index=False))

    # 3. Análisis por umbrales concretos
    thr = threshold_analysis(df)
    thr.to_csv(OUT_DIR / "threshold_summary.csv", index=False)
    print("\n--- Análisis por umbrales concretos ---")
    print(thr.to_string(index=False))

    # 4. Correlación
    corr = correlation_analysis(df)
    print("\n--- Correlación confidence vs PnL/won ---")
    for d, c in corr.items():
        print(f"  {d}: {c}")

    # 5. Per-token LONG summary
    pts = per_token_long_summary(df)
    pts.to_csv(OUT_DIR / "per_token_long_bands.csv", index=False)
    print("\n--- LONG por banda de confidence, por token ---")
    print(pts.to_string(index=False))

    # 6. Preguntas concretas del usuario
    long_df = df[df["direction"] == "LONG"]
    short_df = df[df["direction"] == "SHORT"]
    answers = {}

    # P1: ¿LONG >0.25 rentables?
    long_25 = long_df[long_df["confidence"] > 0.25]
    long_25_pnl = long_25["pnl_pct"].mean() if len(long_25) else None
    long_25_wr = long_25["won"].mean() if len(long_25) else None
    answers["long_above_025"] = {
        "n": int(len(long_25)),
        "pnl_mean_pct": round(float(long_25_pnl), 4) if long_25_pnl is not None else None,
        "win_rate": round(float(long_25_wr), 4) if long_25_wr is not None else None,
        "profitable": bool(long_25_pnl is not None and long_25_pnl > 0),
    }

    # P2: ¿umbral claro donde LONG pasa de - a +?
    # Buscamos el primer threshold donde pnl_mean > 0
    threshold_pnl = {}
    for t in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70]:
        d = long_df[long_df["confidence"] > t]
        if len(d) >= 30:
            threshold_pnl[f">{t:.2f}"] = round(float(d["pnl_pct"].mean()), 4)
    answers["long_threshold_curve"] = threshold_pnl
    first_positive = None
    for k, v in threshold_pnl.items():
        if v > 0:
            first_positive = k
            break
    answers["first_positive_threshold"] = first_positive

    # P3: ¿LONG pierden dinero incluso con confidence alta?
    long_50 = long_df[long_df["confidence"] > 0.50]
    long_70 = long_df[long_df["confidence"] > 0.70]
    answers["long_high_confidence"] = {
        "above_050": {
            "n": int(len(long_50)),
            "pnl_mean_pct": round(float(long_50["pnl_pct"].mean()), 4) if len(long_50) else None,
            "win_rate": round(float(long_50["won"].mean()), 4) if len(long_50) else None,
        },
        "above_070": {
            "n": int(len(long_70)),
            "pnl_mean_pct": round(float(long_70["pnl_pct"].mean()), 4) if len(long_70) else None,
            "win_rate": round(float(long_70["won"].mean()), 4) if len(long_70) else None,
        },
    }

    # Comparativa LONG vs SHORT por decil
    ls_compare = []
    for lab in dec["decile"].unique():
        l = dec[(dec["direction"] == "LONG") & (dec["decile"] == lab)]
        s = dec[(dec["direction"] == "SHORT") & (dec["decile"] == lab)]
        if len(l) and len(s):
            ls_compare.append({
                "decile": lab,
                "long_n": int(l["n"].iloc[0]),
                "long_pnl_mean": float(l["pnl_mean"].iloc[0]) if pd.notna(l["pnl_mean"].iloc[0]) else None,
                "long_wr": float(l["wr"].iloc[0]) if pd.notna(l["wr"].iloc[0]) else None,
                "short_n": int(s["n"].iloc[0]),
                "short_pnl_mean": float(s["pnl_mean"].iloc[0]) if pd.notna(s["pnl_mean"].iloc[0]) else None,
                "short_wr": float(s["wr"].iloc[0]) if pd.notna(s["wr"].iloc[0]) else None,
            })
    answers["long_vs_short_by_decile"] = ls_compare

    # --- Veredicto ---
    long_curve = threshold_pnl
    n_pos = sum(1 for v in long_curve.values() if v > 0)
    n_total = len(long_curve)
    long_corr = corr.get("LONG", {}) or {}
    spearman_long = long_corr.get("spearman_conf_pnl", 0)

    if n_pos == 0:
        verdict = "CRITICAL_NEGATIVE"
        verdict_msg = ("LONG pierde dinero en TODOS los umbrales de confidence. "
                       "Confidence NO predice edge. FIX-15 (filtrar por confidence) "
                       "es un parche inútil — descartar.")
    elif n_pos <= n_total // 3:
        verdict = "WEAK_NEGATIVE"
        verdict_msg = ("LONG solo es rentable en pocos umbrales altos. "
                       "Confidence tiene poder predictivo débil. "
                       "FIX-15 podría funcionar pero con filtrado agresivo "
                       "(perderá la mayoría de señales).")
    elif spearman_long > 0.05:
        verdict = "POSITIVE"
        verdict_msg = (f"Confidence SÍ predice edge para LONG "
                       f"(Spearman={spearman_long:+.3f}). "
                       f"FIX-15 está justificado. "
                       f"Primer umbral positivo: {first_positive}.")
    elif spearman_long > 0:
        verdict = "WEAK_POSITIVE"
        verdict_msg = (f"Confidence muestra correlación marginal con PnL LONG "
                       f"(Spearman={spearman_long:+.3f}). "
                       f"FIX-15 podría aportar valor marginal pero no resolverá "
                       f"el problema estructural.")
    else:
        verdict = "NEGATIVE"
        verdict_msg = (f"Correlación confidence-PnL LONG es negativa o cero "
                       f"(Spearman={spearman_long:+.3f}). "
                       f"Confidence NO predice edge. FIX-15 NO recomendado.")

    answers["verdict"] = {
        "code": verdict,
        "message": verdict_msg,
        "long_curve": long_curve,
        "spearman_long_pnl": spearman_long,
        "n_positive_thresholds": n_pos,
        "n_total_thresholds": n_total,
        "first_positive_threshold": first_positive,
    }

    print("\n" + "=" * 72)
    print(f"VEREDICTO: {verdict}")
    print(verdict_msg)
    print("=" * 72)

    # JSON
    with open(OUT_DIR / "audit.json", "w") as f:
        # json-safe
        def _safe(o):
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return float(o)
            if isinstance(o, (np.bool_,)):
                return bool(o)
            if isinstance(o, pd.DataFrame):
                return o.to_dict(orient="records")
            return str(o)
        json.dump({
            "config": {
                "alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
                "train_candles": TRAIN_CANDLES, "test_candles": TEST_CANDLES,
                "tokens": SYMBOLS,
            },
            "totals": {
                "n_signals": int(len(df)),
                "n_long": int(len(long_df)),
                "n_short": int(len(short_df)),
            },
            "decile_summary": dec.to_dict(orient="records"),
            "threshold_summary": thr.to_dict(orient="records"),
            "correlation": corr,
            "per_token_long_bands": pts.to_dict(orient="records"),
            "answers": answers,
        }, f, indent=2, default=_safe)

    # Markdown
    md = []
    md.append("# Auditoría LONG vs Confidence — ¿confidence predice edge?\n")
    md.append(f"**Setup**: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN} | "
              f"Train {TRAIN_CANDLES:,} / Test {TEST_CANDLES:,} | "
              f"{len(SYMBOLS)} tokens | Sin filtro MIN_CONFIDENCE\n")
    md.append(f"**Total señales**: {len(df):,} "
              f"(LONG={len(long_df):,}, SHORT={len(short_df):,})\n")
    md.append(f"## VEREDICTO: {verdict}\n")
    md.append(f"**{verdict_msg}**\n")

    md.append("## PnL medio por decil de confidence\n")
    md.append("| Decil | LONG n | LONG WR | LONG PnL medio | LONG expectancy | "
              "SHORT n | SHORT WR | SHORT PnL medio | SHORT expectancy |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lab in ["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"]:
        l = dec[(dec["direction"]=="LONG") & (dec["decile"]==lab)]
        s = dec[(dec["direction"]=="SHORT") & (dec["decile"]==lab)]
        def fmt(x):
            return f"{x:.4f}" if pd.notna(x) and x is not None else "—"
        if len(l) and len(s):
            md.append(f"| {lab} | {int(l['n'].iloc[0])} | {fmt(l['wr'].iloc[0])} | "
                      f"{fmt(l['pnl_mean'].iloc[0])} | {fmt(l['expectancy'].iloc[0])} | "
                      f"{int(s['n'].iloc[0])} | {fmt(s['wr'].iloc[0])} | "
                      f"{fmt(s['pnl_mean'].iloc[0])} | {fmt(s['expectancy'].iloc[0])} |")
        else:
            md.append(f"| {lab} | — | — | — | — | — | — | — | — |")

    md.append("\n## Curva LONG: PnL medio por umbral de confidence\n")
    md.append("| Umbral | n | WR | PnL medio | PnL total | Expectancy |")
    md.append("|---|---:|---:|---:|---:|---:|")
    long_thr = thr[thr["direction"]=="LONG"]
    for _, r in long_thr.iterrows():
        md.append(f"| `{r['threshold']}` | {int(r['n'])} | "
                  f"{r['wr'] if pd.notna(r['wr']) else '—'} | "
                  f"{r['pnl_mean'] if pd.notna(r['pnl_mean']) else '—'} | "
                  f"{r['pnl_total'] if pd.notna(r['pnl_total']) else '—'} | "
                  f"{r['expectancy'] if pd.notna(r['expectancy']) else '—'} |")

    md.append("\n## Curva SHORT: PnL medio por umbral de confidence\n")
    md.append("| Umbral | n | WR | PnL medio | PnL total | Expectancy |")
    md.append("|---|---:|---:|---:|---:|---:|")
    short_thr = thr[thr["direction"]=="SHORT"]
    for _, r in short_thr.iterrows():
        md.append(f"| `{r['threshold']}` | {int(r['n'])} | "
                  f"{r['wr'] if pd.notna(r['wr']) else '—'} | "
                  f"{r['pnl_mean'] if pd.notna(r['pnl_mean']) else '—'} | "
                  f"{r['pnl_total'] if pd.notna(r['pnl_total']) else '—'} | "
                  f"{r['expectancy'] if pd.notna(r['expectancy']) else '—'} |")

    md.append("\n## Correlación confidence vs outcome\n")
    md.append("| Direction | n | Spearman conf↔PnL | p-value | Spearman conf↔won | p-value |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for d in ["LONG", "SHORT"]:
        c = corr.get(d)
        if c:
            md.append(f"| {d} | {c['n']} | {c['spearman_conf_pnl']:+.4f} | "
                      f"{c['spearman_conf_pnl_pvalue']:.4f} | "
                      f"{c['spearman_conf_won']:+.4f} | "
                      f"{c['spearman_conf_won_pvalue']:.4f} |")

    md.append("\n## LONG por banda de confidence, por token\n")
    md.append("| Token | Banda | n | WR | PnL medio | PnL total |")
    md.append("|---|---|---:|---:|---:|---:|")
    for _, r in pts.iterrows():
        md.append(f"| {r['token']} | {r['band']} | {int(r['n'])} | "
                  f"{r['wr'] if pd.notna(r['wr']) else '—'} | "
                  f"{r['pnl_mean'] if pd.notna(r['pnl_mean']) else '—'} | "
                  f"{r['pnl_total'] if pd.notna(r['pnl_total']) else '—'} |")

    md.append("\n## Respuestas a las 3 preguntas concretas\n")
    a = answers
    md.append(f"### 1. ¿LONG >0.25 son rentables?\n")
    md.append(f"- n = {a['long_above_025']['n']}")
    md.append(f"- PnL medio = {a['long_above_025']['pnl_mean_pct']}%")
    md.append(f"- WR = {a['long_above_025']['win_rate']}")
    md.append(f"- **Rentable: {'SÍ' if a['long_above_025']['profitable'] else 'NO'}**\n")

    md.append(f"### 2. ¿Umbral claro donde LONG pasa de − a +?\n")
    md.append(f"- Curva: {a['long_threshold_curve']}")
    md.append(f"- Primer umbral positivo: `{a['first_positive_threshold']}`\n")

    md.append(f"### 3. ¿LONG pierde dinero incluso con confidence alta?\n")
    h = a["long_high_confidence"]
    md.append(f"- conf >0.50: n={h['above_050']['n']}, "
              f"PnL medio={h['above_050']['pnl_mean_pct']}%, "
              f"WR={h['above_050']['win_rate']}")
    md.append(f"- conf >0.70: n={h['above_070']['n']}, "
              f"PnL medio={h['above_070']['pnl_mean_pct']}%, "
              f"WR={h['above_070']['win_rate']}\n")

    md.append("## Recomendación\n")
    if verdict in ("POSITIVE", "WEAK_POSITIVE"):
        md.append("Proceder con FIX-15 (filtrar LONG por confidence). "
                  f"Umbral sugerido: `{first_positive}`.\n")
    else:
        md.append("**Descartar FIX-15.** Pivotar a hipótesis alternativas:\n")
        md.append("- SL/TP dinámico basado en ATR o en drawdown/favorable histórico del patrón")
        md.append("- Filtros horarios (liquidity sessions: Asia/Europa/US)")
        md.append("- Filtros por volumen relativo (excluir señales en volumen bajo)")
        md.append("- Revisar el cálculo de `expected_move_pct` — puede estar sesgado a LONG")
        md.append("- Revisar el PnL de LONG vs SHORT en train (no solo test) — "
                  "¿el sesgo viene del trie o del walk-forward?\n")

    md_path = OUT_DIR / "audit.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"\nMarkdown → {md_path}")
    print(f"JSON     → {OUT_DIR / 'audit.json'}")
    print(f"CSV raw  → {OUT_DIR / 'signals_raw.csv'}")
    print(f"CSV dec  → {OUT_DIR / 'decile_summary.csv'}")
    print(f"CSV thr  → {OUT_DIR / 'threshold_summary.csv'}")
    print(f"CSV pts  → {OUT_DIR / 'per_token_long_bands.csv'}")


if __name__ == "__main__":
    main()
