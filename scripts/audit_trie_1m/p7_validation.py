"""
Validación P1 vs P6 vs P7 — walk-forward con SL/TP y fees de producción.

OBJETIVO
  Comparar tres políticas de selección de dirección en el motor PPMT:
    P1 (current, sign(expected_move_pct))
    P6 (majority_avg_move — magnitud per dirección, sin gate)
    P7 (directional_edge — bayesian smoothing + gate de calidad)

  Hipótesis P7: P6 ganó +304pp agregados pero falló en 4/8 tokens (BTC, ETH,
  LINK, BNB, XRP) por ruido direccional en patrones de baja muestra. P7
  aplica:
    (a) Bayesian shrinkage: bayesian_wr = (wins + 1) / (count + 2) [Laplace α=β=1]
    (b) Edge ponderado por WR: long_edge = bayesian_long_wr × avg_move_long
                                  short_edge = bayesian_short_wr × |avg_move_short|
    (c) Gate de calidad: solo tradear si max(long_edge, short_edge) >= MIN_EDGE_PCT
  Predicción: si la hipótesis es correcta, P7 mejora en ≥6/8 tokens (vs 4/8 de P6).

MÉTRICAS POR (token, ventana, política):
  N_trades, Win rate, PnL total, PnL medio, Profit factor, Expectancy.

CONFIGURACIÓN
  - α=4, W=7, PL=5, HOLD=35 velas (igual que p6_validation.py)
  - SL/TP: meta.compute_sl_tp — SL=max_dd×1.5, TP=max(|EM|,max_fav)×1.0, floor 0.1%
  - Fees: Binance taker 0.04% × 2 = 0.08% RT
  - 8 tokens × 3 ventanas disjuntas (W1/W2/W3) sobre 100k velas 1m
  - MIN_EDGE_PCT = 0.10 (gate P7)
"""
from __future__ import annotations
import json
import math
import sys
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
HOLD_CANDLES = PATTERN_LEN * WINDOW  # 35

# SL/TP floors (paper_trader.py:1665-1666)
SL_FLOOR_PCT = 0.1
TP_FLOOR_PCT = 0.1

# Binance Futures taker fee: 0.04% per side → 0.08% round-trip
FEE_RT_PCT = 0.08

# P7 gate: solo tradear si max(long_edge, short_edge) >= MIN_EDGE_PCT
# 0.10% = 10 bps — por debajo de esto el expected edge es ruido.
MIN_EDGE_PCT = 0.10

# Bayesian prior strength (Laplace α=β=1)
BAYES_ALPHA = 1.0
BAYES_BETA = 1.0

# Ventanas: (train_start, train_end, test_start, test_end)
# 3 ventanas disjuntas sobre 100k velas por token
WINDOWS = [
    ("W1", 0,    70_000, 70_000, 100_000),
    ("W2", 30_000, 100_000, 0,    30_000),
    ("W3", 0,    60_000, 60_000, 90_000),
]

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/p7_validation")
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


def build_trie(df_train: pd.DataFrame, symbol: str, window_name: str) -> PPMTTrie:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime = RegimeDetector()
    symbols = sax.encode(df_train)
    trie = PPMTTrie(name=f"per_asset:{symbol}:{window_name}")
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
    trie.propagate_metadata()
    return trie


# ---------------------------------------------------------------------------
# POLICIES
# ---------------------------------------------------------------------------

def decide_p1(meta: BlockLifecycleMetadata) -> str | None:
    """P1 (current): dir = sign(expected_move_pct)."""
    em = float(getattr(meta, "expected_move_pct", 0.0))
    if abs(em) < 1e-9:
        return None
    return "LONG" if em > 0 else "SHORT"


def decide_p6(meta: BlockLifecycleMetadata) -> str | None:
    """P6 (majority_avg_move): dir por magnitud de avg_move por dirección."""
    lc = meta.long_stats.count
    sc = meta.short_stats.count
    if lc == 0 and sc == 0:
        return None
    if lc == 0:
        return "SHORT"
    if sc == 0:
        return "LONG"
    long_strength = meta.avg_move_long       # positivo
    short_strength = abs(meta.avg_move_short)  # también positivo
    return "LONG" if long_strength > short_strength else "SHORT"


def _bayesian_wr(wins: int, count: int) -> float:
    """Bayesian win rate with Laplace prior (α=β=1).
    bayesian_wr = (wins + α) / (count + α + β)
    Penaliza patrones con N bajo — un 80% sobre 5 casos se shrinks a 67%.
    """
    if count == 0:
        return 0.0
    return (wins + BAYES_ALPHA) / (count + BAYES_ALPHA + BAYES_BETA)


def decide_p7(meta: BlockLifecycleMetadata) -> str | None:
    """P7 (directional_edge): bayesian smoothing + edge ponderado por WR + gate.

    long_edge  = bayesian_wr_long  × avg_move_long
    short_edge = bayesian_wr_short × |avg_move_short|

    Donde bayesian_wr_long = (long_wins + 1) / (long_count + 2).
    IMPORTANTE: como long_wins ≡ long_count en la implementación actual,
    bayesian_wr_long = (lc+1)/(lc+2) — shrinkage hacia 0.5 para N bajo.
    Esto PENALIZA patrones con pocos casos en esa dirección.

    Gate: solo tradear si max(long_edge, short_edge) >= MIN_EDGE_PCT.
    """
    lc = meta.long_stats.count
    sc = meta.short_stats.count
    if lc == 0 and sc == 0:
        return None

    # long_wins ≡ long_count por definición actual, pero dejamos el código
    # genérico por si la definición se redefine en el futuro (ver Trazabilidad).
    bayes_long_wr = _bayesian_wr(lc, lc)  # wins=lc
    bayes_short_wr = _bayesian_wr(sc, sc)

    long_edge = bayes_long_wr * meta.avg_move_long        # positivo
    short_edge = bayes_short_wr * abs(meta.avg_move_short)  # positivo

    # Gate de calidad: si el mejor edge es < MIN_EDGE_PCT, no tradear
    best_edge = max(long_edge, short_edge)
    if best_edge < MIN_EDGE_PCT:
        return None

    if lc == 0:
        return "SHORT" if sc > 0 else None
    if sc == 0:
        return "LONG"
    return "LONG" if long_edge >= short_edge else "SHORT"


POLICIES: list[tuple[str, Any]] = [
    ("P1", decide_p1),
    ("P6", decide_p6),
    ("P7", decide_p7),
]


# ---------------------------------------------------------------------------
# SIMULATION (igual a p6_validation.py)
# ---------------------------------------------------------------------------

def compute_sl_tp_pct(meta: BlockLifecycleMetadata, direction: str) -> tuple[float, float]:
    """Replica de metadata.compute_sl_tp + paper_trader floors."""
    sl_distance_pct = abs(meta.max_drawdown_pct) * 1.5
    tp_distance_pct = max(
        abs(meta.expected_move_pct),
        meta.max_favorable_pct,
    ) * 1.0
    sl_distance_pct = max(sl_distance_pct, SL_FLOOR_PCT)
    tp_distance_pct = max(tp_distance_pct, TP_FLOOR_PCT)
    return sl_distance_pct, tp_distance_pct


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    sl_distance_pct: float,
    tp_distance_pct: float,
    hold_candles: int,
) -> dict:
    entry_price = float(df["close"].iloc[entry_idx - 1])

    if direction == "LONG":
        sl_price = entry_price * (1 - sl_distance_pct / 100)
        tp_price = entry_price * (1 + tp_distance_pct / 100)
    else:
        sl_price = entry_price * (1 + sl_distance_pct / 100)
        tp_price = entry_price * (1 - tp_distance_pct / 100)

    end_idx = min(entry_idx + hold_candles, len(df))
    exit_price = None
    exit_reason = None
    exit_idx = None

    for i in range(entry_idx, end_idx):
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])

        if direction == "LONG":
            if low <= sl_price:
                exit_price, exit_reason, exit_idx = sl_price, "stop_loss", i
                break
            if high >= tp_price:
                exit_price, exit_reason, exit_idx = tp_price, "take_profit", i
                break
        else:
            if high >= sl_price:
                exit_price, exit_reason, exit_idx = sl_price, "stop_loss", i
                break
            if low <= tp_price:
                exit_price, exit_reason, exit_idx = tp_price, "take_profit", i
                break

    if exit_price is None:
        exit_idx = end_idx - 1
        exit_price = float(df["close"].iloc[exit_idx])
        exit_reason = "timeout"

    if direction == "LONG":
        pnl_gross_pct = (exit_price - entry_price) / entry_price * 100.0
    else:
        pnl_gross_pct = (entry_price - exit_price) / entry_price * 100.0

    pnl_net_pct = pnl_gross_pct - FEE_RT_PCT
    hold_actual = exit_idx - entry_idx + 1

    return {
        "entry_price": round(entry_price, 6),
        "exit_price": round(exit_price, 6),
        "pnl_gross_pct": round(pnl_gross_pct, 4),
        "pnl_net_pct": round(pnl_net_pct, 4),
        "exit_reason": exit_reason,
        "hold_candles": hold_actual,
    }


def walk_forward_window(
    df: pd.DataFrame,
    trie: PPMTTrie,
    symbol: str,
    window_name: str,
    test_start: int,
    test_end: int,
) -> list[dict]:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    symbols = sax.encode(df)

    trades: list[dict] = []
    i_start = max(0, test_start // WINDOW - PATTERN_LEN)
    i_end = min(len(symbols) - PATTERN_LEN, test_end // WINDOW)

    for i in range(i_start, i_end):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + HOLD_CANDLES
        if fire_candle < test_start or fire_candle >= test_end:
            continue
        if end_outcome > test_end + HOLD_CANDLES:
            if end_outcome > len(df):
                continue

        node = trie.root
        for sym in pattern:
            if sym not in node.children:
                node = None
                break
            node = node.children[sym]
        if node is None:
            continue
        meta = node.metadata
        if meta.historical_count < 1:
            continue

        # Pre-compute P7 diagnostic info (for transparency)
        lc = meta.long_stats.count
        sc = meta.short_stats.count
        bayes_long_wr = _bayesian_wr(lc, lc) if lc > 0 else 0.0
        bayes_short_wr = _bayesian_wr(sc, sc) if sc > 0 else 0.0
        long_edge = bayes_long_wr * meta.avg_move_long
        short_edge = bayes_short_wr * abs(meta.avg_move_short)

        for policy_name, decider in POLICIES:
            direction = decider(meta)
            if direction is None:
                continue
            sl_pct, tp_pct = compute_sl_tp_pct(meta, direction)
            trade_result = simulate_trade(
                df=df,
                entry_idx=fire_candle,
                direction=direction,
                sl_distance_pct=sl_pct,
                tp_distance_pct=tp_pct,
                hold_candles=HOLD_CANDLES,
            )
            trades.append({
                "token": symbol,
                "window": window_name,
                "policy": policy_name,
                "pattern": "".join(pattern),
                "fire_candle": fire_candle,
                "direction": direction,
                "historical_count": meta.historical_count,
                "long_count": lc,
                "short_count": sc,
                "long_wr": round(meta.win_rate_long, 4),
                "short_wr": round(meta.win_rate_short, 4),
                "long_avg_move": round(meta.avg_move_long, 4),
                "short_avg_move": round(meta.avg_move_short, 4),
                "bayesian_long_wr": round(bayes_long_wr, 4),
                "bayesian_short_wr": round(bayes_short_wr, 4),
                "long_edge": round(long_edge, 4),
                "short_edge": round(short_edge, 4),
                "expected_move_pct": round(float(meta.expected_move_pct), 4),
                "sl_distance_pct": round(sl_pct, 4),
                "tp_distance_pct": round(tp_pct, 4),
                **trade_result,
            })
    return trades


def aggregate_metrics(trades_df: pd.DataFrame) -> dict:
    if len(trades_df) == 0:
        return {
            "n_trades": 0, "win_rate": None, "pnl_total_net": 0.0,
            "pnl_mean_net": None, "profit_factor": None, "expectancy": None,
            "long_n": 0, "short_n": 0, "long_wr": None, "short_wr": None,
            "long_pnl_mean": None, "short_pnl_mean": None,
            "n_sl": 0, "n_tp": 0, "n_timeout": 0,
        }
    n = len(trades_df)
    wins = trades_df[trades_df["pnl_net_pct"] > 0]
    losses = trades_df[trades_df["pnl_net_pct"] <= 0]
    pnl_total = float(trades_df["pnl_net_pct"].sum())
    pnl_mean = float(trades_df["pnl_net_pct"].mean())
    sum_wins = float(wins["pnl_net_pct"].sum()) if len(wins) else 0.0
    sum_losses = float(losses["pnl_net_pct"].sum()) if len(losses) else 0.0
    pf = (sum_wins / abs(sum_losses)) if sum_losses != 0 else float("inf") if sum_wins > 0 else 0.0

    longs = trades_df[trades_df["direction"] == "LONG"]
    shorts = trades_df[trades_df["direction"] == "SHORT"]

    return {
        "n_trades": int(n),
        "win_rate": round(float(len(wins) / n), 4),
        "pnl_total_net": round(pnl_total, 2),
        "pnl_mean_net": round(pnl_mean, 4),
        "profit_factor": round(pf, 4) if pf != float("inf") else "inf",
        "expectancy": round(pnl_mean, 4),
        "long_n": int(len(longs)),
        "short_n": int(len(shorts)),
        "long_wr": round(float((longs["pnl_net_pct"] > 0).mean()), 4) if len(longs) else None,
        "short_wr": round(float((shorts["pnl_net_pct"] > 0).mean()), 4) if len(shorts) else None,
        "long_pnl_mean": round(float(longs["pnl_net_pct"].mean()), 4) if len(longs) else None,
        "short_pnl_mean": round(float(shorts["pnl_net_pct"].mean()), 4) if len(shorts) else None,
        "n_sl": int((trades_df["exit_reason"] == "stop_loss").sum()),
        "n_tp": int((trades_df["exit_reason"] == "take_profit").sum()),
        "n_timeout": int((trades_df["exit_reason"] == "timeout").sum()),
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print("=" * 88)
    print("VALIDACIÓN P1 vs P6 vs P7 — walk-forward con SL/TP + fees producción")
    print("=" * 88)
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, HOLD={HOLD_CANDLES} velas")
    print(f"Fees:   {FEE_RT_PCT}% round-trip (Binance taker 0.04% × 2)")
    print(f"SL/TP:   compute_sl_tp — SL=max_dd×1.5, TP=max(|EM|,max_fav)×1.0, floors 0.1%")
    print(f"P7 gate: MIN_EDGE_PCT = {MIN_EDGE_PCT}%   |   Bayesian prior: Laplace α=β={BAYES_ALPHA}")
    print(f"Tokens:  {len(SYMBOLS)}   |   Ventanas: {len(WINDOWS)} ({', '.join(w[0] for w in WINDOWS)})")
    print()

    all_trades: list[dict] = []

    for sym in SYMBOLS:
        try:
            df_full = load_df(sym)
            if len(df_full) < 100_000:
                print(f"  SKIP {sym}: solo {len(df_full)} velas (necesita 100k)")
                continue

            for window_name, tr_start, tr_end, te_start, te_end in WINDOWS:
                df_train = df_full.iloc[tr_start:tr_end].reset_index(drop=True)
                print(f"  {sym} {window_name}: build trie "
                      f"(train [{tr_start}:{tr_end}] = {tr_end-tr_start:,} velas) ...",
                      end=" ", flush=True)
                trie = build_trie(df_train, sym, window_name)
                print(f"walk-forward (test [{te_start}:{te_end}]) ...", end=" ", flush=True)
                trades = walk_forward_window(
                    df=df_full,
                    trie=trie,
                    symbol=sym,
                    window_name=window_name,
                    test_start=te_start,
                    test_end=te_end,
                )
                counts = {p: sum(1 for t in trades if t["policy"] == p) for p, _ in POLICIES}
                print(f"{len(trades):,} trades (P1={counts['P1']:,}, "
                      f"P6={counts['P6']:,}, P7={counts['P7']:,})")
                all_trades.extend(trades)
        except Exception as e:
            import traceback
            print(f"  ERROR {sym}: {e}")
            traceback.print_exc()

    if not all_trades:
        print("\nNO HAY TRADES — abortando")
        return

    trades_df = pd.DataFrame(all_trades)
    trades_df.to_csv(OUT_DIR / "per_trade.csv", index=False)
    print(f"\nTotal trades: {len(trades_df):,} guardados en {OUT_DIR / 'per_trade.csv'}")

    # ===== Métricas por (token, ventana, política) =====
    print("\n" + "=" * 88)
    print("MÉTRICAS POR (TOKEN, VENTANA, POLÍTICA)")
    print("=" * 88)
    rows_agg = []
    for (tok, win, pol), sub in trades_df.groupby(["token", "window", "policy"]):
        m = aggregate_metrics(sub)
        rows_agg.append({"token": tok, "window": win, "policy": pol, **m})
    agg_df = pd.DataFrame(rows_agg)
    agg_df.to_csv(OUT_DIR / "per_token_ventana.csv", index=False)
    for win in sorted(agg_df["window"].unique()):
        print(f"\n--- Ventana {win} ---")
        sub = agg_df[agg_df["window"] == win].sort_values("token")
        for tok in sub["token"].unique():
            t = sub[sub["token"] == tok]
            rows = {p: t[t["policy"] == p].iloc[0] if len(t[t["policy"] == p]) else None
                    for p, _ in POLICIES}
            print(f"  {tok}:")
            for p, _ in POLICIES:
                r = rows[p]
                if r is None:
                    print(f"    {p}:  N/A")
                    continue
                print(f"    {p}: N={r['n_trades']:>4}  WR={r['win_rate']}  "
                      f"PF={r['profit_factor']:>6}  Exp={r['expectancy']:+.4f}  "
                      f"PnL_total={r['pnl_total_net']:+8.2f}")
            if rows["P1"] is not None and rows["P7"] is not None:
                d = rows["P7"]["pnl_total_net"] - rows["P1"]["pnl_total_net"]
                print(f"    Δ P7-P1 = {d:+.2f}")

    # ===== Métricas agregadas totales por política =====
    print("\n" + "=" * 88)
    print("MÉTRICAS AGREGADAS TOTALES")
    print("=" * 88)
    summary_rows = []
    for pol, _ in POLICIES:
        sub = trades_df[trades_df["policy"] == pol]
        m = aggregate_metrics(sub)
        summary_rows.append({"policy": pol, **m})
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(OUT_DIR / "summary.csv", index=False)

    print("\n| Politica | N trades | WR    | PF     | Exp       | PnL total  | LONG N | LONG PnL | SHORT N | SHORT PnL |")
    print("|----------|----------|-------|--------|-----------|------------|--------|----------|---------|-----------|")
    for _, r in summary_df.iterrows():
        print(f"| {r['policy']:<8} | {r['n_trades']:>8,} | {r['win_rate']} | "
              f"{str(r['profit_factor']):>6} | {r['expectancy']:+.4f}%   | "
              f"{r['pnl_total_net']:+10.2f} | {r['long_n']:>6} | {str(r['long_pnl_mean']):>8} | "
              f"{r['short_n']:>7} | {str(r['short_pnl_mean']):>9} |")

    # ===== Agregado por token (sumando 3 ventanas) =====
    print("\n" + "=" * 88)
    print("AGREGADO POR TOKEN (3 ventanas sumadas)")
    print("=" * 88)
    per_token_rows = []
    for tok in sorted(trades_df["token"].unique()):
        for pol, _ in POLICIES:
            sub = trades_df[(trades_df["token"] == tok) & (trades_df["policy"] == pol)]
            m = aggregate_metrics(sub)
            per_token_rows.append({"token": tok, "policy": pol, **m})
    per_token_df = pd.DataFrame(per_token_rows)
    per_token_df.to_csv(OUT_DIR / "per_token_aggregated.csv", index=False)
    print()
    print(f"{'Token':<10} {'Pol':<4} {'N':>6} {'WR':>6} {'PF':>6} {'Exp':>9} {'PnL_total':>11}")
    for _, r in per_token_df.iterrows():
        print(f"{r['token']:<10} {r['policy']:<4} {r['n_trades']:>6,} "
              f"{str(r['win_rate']):>6} {str(r['profit_factor']):>6} "
              f"{r['expectancy']:+.4f} {r['pnl_total_net']:+11.2f}")

    # ===== Delta por token (P6-P1) y (P7-P1) y (P7-P6) =====
    print("\n" + "=" * 88)
    print("DELTA POR TOKEN")
    print("=" * 88)
    print(f"\n{'Token':<10} {'P1 PnL':>10} {'P6 PnL':>10} {'P7 PnL':>10} "
          f"{'Δ P6-P1':>9} {'Δ P7-P1':>9} {'Δ P7-P6':>9} {'V P7 vs P1':<14}")
    deltas = []
    for tok in sorted(trades_df["token"].unique()):
        rows = {p: per_token_df[(per_token_df["token"] == tok) & (per_token_df["policy"] == p)].iloc[0]
                for p, _ in POLICIES}
        d_p6_p1 = rows["P6"]["pnl_total_net"] - rows["P1"]["pnl_total_net"]
        d_p7_p1 = rows["P7"]["pnl_total_net"] - rows["P1"]["pnl_total_net"]
        d_p7_p6 = rows["P7"]["pnl_total_net"] - rows["P6"]["pnl_total_net"]
        verdict = "MEJORA" if d_p7_p1 > 0 else ("IGUAL" if d_p7_p1 == 0 else "EMPEORA")
        print(f"{tok:<10} {rows['P1']['pnl_total_net']:+10.2f} "
              f"{rows['P6']['pnl_total_net']:+10.2f} "
              f"{rows['P7']['pnl_total_net']:+10.2f} "
              f"{d_p6_p1:+9.2f} {d_p7_p1:+9.2f} {d_p7_p6:+9.2f} {verdict:<14}")
        deltas.append({
            "token": tok,
            "p1_pnl": rows["P1"]["pnl_total_net"],
            "p6_pnl": rows["P6"]["pnl_total_net"],
            "p7_pnl": rows["P7"]["pnl_total_net"],
            "delta_p6_p1": d_p6_p1,
            "delta_p7_p1": d_p7_p1,
            "delta_p7_p6": d_p7_p6,
            "verdict_p7_vs_p1": verdict,
        })

    n_mejora_p7 = sum(1 for d in deltas if d["verdict_p7_vs_p1"] == "MEJORA")
    n_empeora_p7 = sum(1 for d in deltas if d["verdict_p7_vs_p1"] == "EMPEORA")
    n_igual_p7 = sum(1 for d in deltas if d["verdict_p7_vs_p1"] == "IGUAL")
    print(f"\nResumen P7 vs P1: {n_mejora_p7} mejoran, {n_empeora_p7} empeoran, {n_igual_p7} igual")
    print(f"  (P6 vs P1 había 4/8 mejoran, 4/8 empeoran)")

    # ===== Delta por ventana =====
    print("\n" + "=" * 88)
    print("DELTA POR VENTANA (todos tokens agregados)")
    print("=" * 88)
    per_window_rows = []
    for win in sorted(trades_df["window"].unique()):
        for pol, _ in POLICIES:
            sub = trades_df[(trades_df["window"] == win) & (trades_df["policy"] == pol)]
            m = aggregate_metrics(sub)
            per_window_rows.append({"window": win, "policy": pol, **m})
    per_window_df = pd.DataFrame(per_window_rows)
    per_window_df.to_csv(OUT_DIR / "per_window_aggregated.csv", index=False)

    print(f"\n{'Ventana':<8} {'Pol':<4} {'N':>6} {'WR':>6} {'PF':>6} {'Exp':>9} {'PnL_total':>11}")
    for _, r in per_window_df.iterrows():
        print(f"{r['window']:<8} {r['policy']:<4} {r['n_trades']:>6,} "
              f"{str(r['win_rate']):>6} {str(r['profit_factor']):>6} "
              f"{r['expectancy']:+.4f} {r['pnl_total_net']:+11.2f}")

    print(f"\nDelta por ventana:")
    for win in sorted(trades_df["window"].unique()):
        rows = {p: per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == p)].iloc[0]
                for p, _ in POLICIES}
        d_p7_p1 = rows["P7"]["pnl_total_net"] - rows["P1"]["pnl_total_net"]
        d_p7_p6 = rows["P7"]["pnl_total_net"] - rows["P6"]["pnl_total_net"]
        v = "MEJORA" if d_p7_p1 > 0 else ("IGUAL" if d_p7_p1 == 0 else "EMPEORA")
        print(f"  {win}: P1={rows['P1']['pnl_total_net']:+.2f} → P6={rows['P6']['pnl_total_net']:+.2f} "
              f"→ P7={rows['P7']['pnl_total_net']:+.2f}  "
              f"Δ P7-P1={d_p7_p1:+.2f}  Δ P7-P6={d_p7_p6:+.2f}  {v}")

    # ===== JSON estructurado =====
    payload = {
        "config": {
            "alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
            "hold_candles": HOLD_CANDLES,
            "fee_rt_pct": FEE_RT_PCT,
            "sl_tp_rule": "compute_sl_tp — SL=max_dd×1.5, TP=max(|EM|,max_fav)×1.0, floors 0.1%",
            "min_edge_pct": MIN_EDGE_PCT,
            "bayesian_prior": {"alpha": BAYES_ALPHA, "beta": BAYES_BETA},
            "symbols": SYMBOLS,
            "windows": [{"name": w[0], "train_start": w[1], "train_end": w[2],
                         "test_start": w[3], "test_end": w[4]} for w in WINDOWS],
        },
        "summary": summary_df.to_dict(orient="records"),
        "per_token": per_token_df.to_dict(orient="records"),
        "per_window": per_window_df.to_dict(orient="records"),
        "per_token_ventana": agg_df.to_dict(orient="records"),
        "delta_per_token": deltas,
    }
    (OUT_DIR / "validation.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nJSON: {OUT_DIR / 'validation.json'}")

    # ===== Reporte MD =====
    write_md_report(summary_df, per_token_df, per_window_df, agg_df, deltas, OUT_DIR / "validation.md")


def write_md_report(summary_df, per_token_df, per_window_df, agg_df, deltas, out_path):
    p1 = summary_df[summary_df["policy"] == "P1"].iloc[0]
    p6 = summary_df[summary_df["policy"] == "P6"].iloc[0]
    p7 = summary_df[summary_df["policy"] == "P7"].iloc[0]
    delta_p7_p1 = p7["pnl_total_net"] - p1["pnl_total_net"]
    delta_p7_p6 = p7["pnl_total_net"] - p6["pnl_total_net"]
    delta_p6_p1 = p6["pnl_total_net"] - p1["pnl_total_net"]
    n_mejora_p7 = sum(1 for d in deltas if d["verdict_p7_vs_p1"] == "MEJORA")
    n_empeora_p7 = sum(1 for d in deltas if d["verdict_p7_vs_p1"] == "EMPEORA")

    lines = []
    lines.append("# Validación P1 vs P6 vs P7 — Walk-forward con SL/TP y Fees de Producción\n\n")
    lines.append("## Setup\n\n")
    lines.append(f"- α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, hold={HOLD_CANDLES} velas\n")
    lines.append(f"- Tokens: {len(SYMBOLS)} ({', '.join(SYMBOLS)})\n")
    lines.append(f"- Ventanas: {len(WINDOWS)} ({', '.join(w[0] for w in WINDOWS)}) — train/test disjuntos sobre 100k velas\n")
    lines.append(f"- SL/TP: `meta.compute_sl_tp()` — SL=max_drawdown×1.5, TP=max(|expected_move|, max_favorable)×1.0, floor 0.1%\n")
    lines.append(f"- Fees: {FEE_RT_PCT}% round-trip (Binance taker 0.04% × 2)\n")
    lines.append(f"- P7 gate: MIN_EDGE_PCT = {MIN_EDGE_PCT}%\n")
    lines.append(f"- P7 Bayesian: Laplace prior α=β={BAYES_ALPHA} → bayesian_wr = (wins+α)/(count+α+β)\n")
    lines.append(f"- Políticas:\n")
    lines.append(f"  - **P1 (current)**: `dir = sign(expected_move_pct)`\n")
    lines.append(f"  - **P6 (majority_avg_move)**: `dir = LONG si avg_move_long > |avg_move_short|, sino SHORT`\n")
    lines.append(f"  - **P7 (directional_edge)**: \n")
    lines.append(f"    - `bayesian_long_wr = (long_count + 1) / (long_count + 2)`\n")
    lines.append(f"    - `long_edge = bayesian_long_wr × avg_move_long`\n")
    lines.append(f"    - `short_edge = bayesian_short_wr × |avg_move_short|`\n")
    lines.append(f"    - `dir = LONG if long_edge >= short_edge else SHORT`\n")
    lines.append(f"    - **Gate**: skip if `max(long_edge, short_edge) < {MIN_EDGE_PCT}%`\n\n")

    lines.append("## Resultados agregados (todos los tokens × 3 ventanas)\n\n")
    lines.append("| Política | N trades | WR | PF | Expectancy | PnL total | PnL medio | LONG N | LONG WR | LONG PnL medio | SHORT N | SHORT WR | SHORT PnL medio |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for _, r in summary_df.iterrows():
        lines.append(f"| **{r['policy']}** | {r['n_trades']:,} | {r['win_rate']} | "
                     f"{r['profit_factor']} | {r['expectancy']:+.4f}% | "
                     f"{r['pnl_total_net']:+.2f}% | {r['pnl_mean_net']:+.4f}% | "
                     f"{r['long_n']:,} | {r['long_wr']} | {r['long_pnl_mean']} | "
                     f"{r['short_n']:,} | {r['short_wr']} | {r['short_pnl_mean']} |\n")
    lines.append(f"\n**Deltas**:\n")
    lines.append(f"- P6 − P1: PnL total {delta_p6_p1:+.2f}pp\n")
    lines.append(f"- P7 − P1: PnL total {delta_p7_p1:+.2f}pp\n")
    lines.append(f"- P7 − P6: PnL total {delta_p7_p6:+.2f}pp\n\n")

    lines.append("## Delta por token (3 ventanas agregadas)\n\n")
    lines.append("| Token | P1 PnL | P6 PnL | P7 PnL | Δ P6−P1 | Δ P7−P1 | Δ P7−P6 | P7 N | P1 N | Veredicto P7 vs P1 |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for d in deltas:
        p1_row = per_token_df[(per_token_df["token"] == d["token"]) & (per_token_df["policy"] == "P1")].iloc[0]
        p7_row = per_token_df[(per_token_df["token"] == d["token"]) & (per_token_df["policy"] == "P7")].iloc[0]
        lines.append(f"| {d['token']} | {d['p1_pnl']:+.2f} | {d['p6_pnl']:+.2f} | "
                     f"{d['p7_pnl']:+.2f} | {d['delta_p6_p1']:+.2f} | "
                     f"{d['delta_p7_p1']:+.2f} | {d['delta_p7_p6']:+.2f} | "
                     f"{p7_row['n_trades']:,} | {p1_row['n_trades']:,} | "
                     f"{d['verdict_p7_vs_p1']} |\n")
    lines.append(f"\n**Tokens mejorados P7 vs P1**: {n_mejora_p7}/{len(deltas)}  ·  "
                 f"**empeorados**: {n_empeora_p7}/{len(deltas)}\n\n")
    lines.append(f"*(Para referencia, P6 vs P1 mejoró en 4/8 tokens — ver commit f5bec08)*\n\n")

    lines.append("## Delta por ventana (todos los tokens agregados)\n\n")
    lines.append("| Ventana | Política | N | WR | PF | Exp | PnL total |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    for _, r in per_window_df.iterrows():
        lines.append(f"| {r['window']} | {r['policy']} | {r['n_trades']:,} | "
                     f"{r['win_rate']} | {r['profit_factor']} | "
                     f"{r['expectancy']:+.4f}% | {r['pnl_total_net']:+.2f}% |\n")
    lines.append("\n| Ventana | P1 PnL | P6 PnL | P7 PnL | Δ P7−P1 | Δ P7−P6 | Veredicto |\n|---|---|---|---|---|---|---|\n")
    for win in sorted(per_window_df["window"].unique()):
        rows = {p: per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == p)].iloc[0]
                for p in ["P1", "P6", "P7"]}
        d_p7_p1 = rows["P7"]["pnl_total_net"] - rows["P1"]["pnl_total_net"]
        d_p7_p6 = rows["P7"]["pnl_total_net"] - rows["P6"]["pnl_total_net"]
        vw = "MEJORA" if d_p7_p1 > 0 else ("IGUAL" if d_p7_p1 == 0 else "EMPEORA")
        lines.append(f"| {win} | {rows['P1']['pnl_total_net']:+.2f} | "
                     f"{rows['P6']['pnl_total_net']:+.2f} | "
                     f"{rows['P7']['pnl_total_net']:+.2f} | "
                     f"{d_p7_p1:+.2f} | {d_p7_p6:+.2f} | {vw} |\n")

    lines.append("\n## Detalle por (token × ventana × política)\n\n")
    for win in sorted(agg_df["window"].unique()):
        lines.append(f"\n### Ventana {win}\n\n")
        lines.append("| Token | Pol | N | WR | PF | Exp | PnL total | LONG N | LONG WR | LONG PnL | SHORT N | SHORT WR | SHORT PnL |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        sub = agg_df[agg_df["window"] == win].sort_values(["token", "policy"])
        for _, r in sub.iterrows():
            lines.append(f"| {r['token']} | {r['policy']} | {r['n_trades']} | "
                         f"{r['win_rate']} | {r['profit_factor']} | "
                         f"{r['expectancy']:+.4f}% | {r['pnl_total_net']:+.2f}% | "
                         f"{r['long_n']} | {r['long_wr']} | {r['long_pnl_mean']} | "
                         f"{r['short_n']} | {r['short_wr']} | {r['short_pnl_mean']} |\n")

    # Veredicto
    lines.append("\n## Veredicto\n\n")
    robust_token = n_mejora_p7 >= len(deltas) * 0.625  # ≥5/8 tokens mejoran (62.5%)
    robust_window = sum(
        1 for win in per_window_df["window"].unique()
        if (per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P7")].iloc[0]["pnl_total_net"]
            - per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P1")].iloc[0]["pnl_total_net"]) > 0
    )
    robust_window_ok = robust_window >= 2  # ≥2 de 3 ventanas
    overall_positive = delta_p7_p1 > 0
    beats_p6 = delta_p7_p6 > 0

    if overall_positive and robust_token and robust_window_ok and beats_p6:
        verdict = ("ROBUSTO Y SUPERIOR A P6: P7 mejora PnL total, mejora en ≥5/8 tokens, "
                   "mejora en ≥2/3 ventanas, y supera a P6. "
                   "RECOMENDACIÓN: integrar P7 al motor (reemplazando P1 y P6).")
    elif overall_positive and robust_token and robust_window_ok:
        verdict = ("ROBUSTO: P7 mejora PnL total, mejora en ≥5/8 tokens y en ≥2/3 ventanas. "
                   "Pero NO supera consistentemente a P6 — revisar antes de integrar.")
    elif overall_positive and (robust_token or robust_window_ok):
        verdict = ("PARCIAL: P7 mejora PnL total pero no consistentemente por token/ventana. "
                   "RECOMENDACIÓN: revisar antes de integrar — puede haber overfitting a 1-2 tokens.")
    elif overall_positive:
        verdict = ("MARGINAL: P7 mejora PnL total pero la mejora está concentrada. "
                   "RECOMENDACIÓN: NO integrar — no es robusto.")
    else:
        verdict = ("NEGATIVO: P7 NO mejora PnL total con SL/TP y fees. "
                   "RECOMENDACIÓN: NO integrar.")
    lines.append(f"**{verdict}**\n\n")
    lines.append(f"- PnL total delta P7−P1: {delta_p7_p1:+.2f}pp\n")
    lines.append(f"- PnL total delta P7−P6: {delta_p7_p6:+.2f}pp\n")
    lines.append(f"- Tokens mejorados P7 vs P1: {n_mejora_p7}/{len(deltas)} ({100*n_mejora_p7/len(deltas):.0f}%)\n")
    lines.append(f"- Ventanas mejoradas P7 vs P1: {robust_window}/{len(per_window_df['window'].unique())}\n")
    lines.append(f"- P7 supera a P6 en PnL total: {'SÍ' if beats_p6 else 'NO'}\n")
    lines.append("\n## Contexto de hipótesis\n\n")
    lines.append("P6 (commit f5bec08) mejoró +304pp agregado pero solo en 4/8 tokens — "
                 "fallaba en BTC, ETH, LINK, BNB, XRP por ruido direccional en patrones de baja muestra.\n\n")
    lines.append("P7 hipótesis: el gate de MIN_EDGE_PCT y el bayesian shrinkage filtran "
                 "esos patrones ruidosos, mejorando consistencia cross-token.\n\n")
    lines.append("Si P7 mejora en ≥5/8 tokens (vs 4/8 de P6), la hipótesis se confirma y "
                 "el siguiente paso es la Fase C: redefinir `long_wins` con outcome SL/TP "
                 "(no `move_pct>0`) para romper la equivalencia `long_wr ≡ legacy_wr`.\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"MD:   {out_path}")


if __name__ == "__main__":
    main()
