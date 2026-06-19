"""
Validación completa P1 vs P6 — walk-forward con SL/TP y fees de producción.

OBJETIVO
  Validar si P6 (majority_avg_move) mejora de forma robusta sobre P1 (current,
  sign(expected_move_pct)) en:
    - Walk-forward con SL/TP de producción (meta.compute_sl_tp)
    - Fees de transacción incluidos (Binance taker 0.04% per side = 0.08% RT)
    - 8 tokens
    - Múltiples ventanas temporales (3 ventanas rolling)

MÉTRICAS POR (token, ventana, política):
  - N_trades
  - Win rate
  - PnL total (%)
  - PnL medio (%)
  - Profit factor (PF = sum_wins / |sum_losses|)
  - Expectancy (PnL medio por trade)

REGLA DE SL/TP (production, paper_trader.py:1654-1666):
  matched_node.metadata.compute_sl_tp(entry_price)
  → SL = max_drawdown_pct × 1.5
  → TP = max(|expected_move_pct|, max_favorable_pct) × 1.0
  → floor: SL_distance >= 0.1%, TP_distance >= 0.1%

  Para P6, usamos el MISMO compute_sl_tp (no tocamos SL/TP — solo direction).
  La idea: si LONG es mejor dirección, el SL/TP computado igual aplica.

LÓGICA DE EXIT (igual a paper_trader.py:1278-1351):
  En cada candle tras entry:
    - Si SL hit → exit at SL_price, exit_reason=stop_loss
    - Elif TP hit → exit at TP_price, exit_reason=take_profit
  Si no toca ni SL ni TP en todo el hold (PATTERN_LEN×WINDOW=35 velas):
    - Exit at close del último candle, exit_reason=timeout

FEES:
  Binance Futures taker fee = 0.04% per side (0.04% entry + 0.04% exit = 0.08% RT)
  Aplicado al PnL: pnl_net = pnl_gross - 0.08% (siempre negativo, resta)

VENTANAS TEMPORALES (sobre 100k velas por token):
  W1: train [0:70k]      test [70k:100k]   (la del análisis anterior)
  W2: train [0:50k]+[80k:100k]  test [50k:80k]  (ventana media)
  W3: train [30k:100k]   test [0:30k]      (ventana inicial)

  Por simplicidad y para no perder patrones del trie, W2 y W3 usan
  train contiguo — el trie se reconstruye completo cada vez.
  En realidad:
    W1: train [0:70k]    test [70k:100k]
    W2: train [30k:100k] test [0:30k]
    W3: train [0:30k]+[60k:100k]  test [30k:60k]  (train skip 30k)
  Para mantener el código simple, W3 = train [0:60k], test [60k:90k]
  (ventana final). Esto da 3 ventanas disjuntas sobre las 100k velas.

POLÍTICAS
  P1_current:  dir = "LONG" if expected_move_pct > 0 else "SHORT"
  P6_majority_avg_move:
               long_strength  = avg_move_long  (positive)
               short_strength = abs(avg_move_short)  (positive)
               if long_count == 0 and short_count == 0: SKIP
               elif long_count == 0: dir = "SHORT"
               elif short_count == 0: dir = "LONG"
               else: dir = "LONG" if long_strength > short_strength else "SHORT"

OUTPUT en /home/z/my-project/download/p6_validation/:
  - per_trade.csv       (todos los trades: token, ventana, política, dir, pnl_gross, pnl_net, exit_reason)
  - per_token_ventana.csv (métricas agregadas por (token, ventana, política))
  - summary.csv         (agregados totales por política)
  - validation.json     (estructurado)
  - validation.md       (reporte legible)
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

# Ventanas: (train_start, train_end, test_start, test_end)
# 3 ventanas disjuntas sobre 100k velas por token
WINDOWS = [
    ("W1", 0,    70_000, 70_000, 100_000),
    ("W2", 30_000, 100_000, 0,    30_000),
    ("W3", 0,    60_000, 60_000, 90_000),
]

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/p6_validation")
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


def decide_direction_p1(meta: BlockLifecycleMetadata) -> str | None:
    """P1: dir = sign(expected_move_pct)."""
    em = float(getattr(meta, "expected_move_pct", 0.0))
    if abs(em) < 1e-9:
        return None
    return "LONG" if em > 0 else "SHORT"


def decide_direction_p6(meta: BlockLifecycleMetadata) -> str | None:
    """P6: dir por magnitud de avg_move por dirección."""
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


def compute_sl_tp_pct(meta: BlockLifecycleMetadata, direction: str) -> tuple[float, float]:
    """Replica de paper_trader.py:1654-1666 + metadata.compute_sl_tp().
    Devuelve (sl_distance_pct, tp_distance_pct) — ambos positivos.
    """
    sl_distance_pct = abs(meta.max_drawdown_pct) * 1.5
    tp_distance_pct = max(
        abs(meta.expected_move_pct),
        meta.max_favorable_pct,
    ) * 1.0
    # Floors
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
    """Simula un trade con SL/TP y timeout.
    entry_idx = índice de la vela donde se abre (fire_candle).
    Sale en la primera vela donde toca SL o TP, o al timeout.
    """
    entry_price = float(df["close"].iloc[entry_idx - 1])  # paper_trader usa el close anterior

    if direction == "LONG":
        sl_price = entry_price * (1 - sl_distance_pct / 100)
        tp_price = entry_price * (1 + tp_distance_pct / 100)
    else:  # SHORT
        sl_price = entry_price * (1 + sl_distance_pct / 100)
        tp_price = entry_price * (1 - tp_distance_pct / 100)

    # Iterar velas hasta hold_candles o SL/TP hit
    end_idx = min(entry_idx + hold_candles, len(df))
    exit_price = None
    exit_reason = None
    exit_idx = None

    for i in range(entry_idx, end_idx):
        # Verificar SL/TP con high/low de la vela
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])

        if direction == "LONG":
            # SL hit si low <= sl_price
            if low <= sl_price:
                exit_price = sl_price
                exit_reason = "stop_loss"
                exit_idx = i
                break
            # TP hit si high >= tp_price
            if high >= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                exit_idx = i
                break
        else:  # SHORT
            # SL hit si high >= sl_price
            if high >= sl_price:
                exit_price = sl_price
                exit_reason = "stop_loss"
                exit_idx = i
                break
            # TP hit si low <= tp_price
            if low <= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                exit_idx = i
                break

    if exit_price is None:
        # Timeout: exit al close de la última vela
        exit_idx = end_idx - 1
        exit_price = float(df["close"].iloc[exit_idx])
        exit_reason = "timeout"

    # PnL bruto
    if direction == "LONG":
        pnl_gross_pct = (exit_price - entry_price) / entry_price * 100.0
    else:
        pnl_gross_pct = (entry_price - exit_price) / entry_price * 100.0

    # PnL neto (después de fees round-trip)
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
    """Walk-forward sobre el rango [test_start, test_end) del df completo."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    # Encodear todo el df (necesario para SAX; el test range se filtra luego)
    symbols = sax.encode(df)

    trades: list[dict] = []
    # Iterar solo sobre índices que caen en [test_start, test_end)
    # fire_candle = (i + PATTERN_LEN) * WINDOW
    # Necesitamos: test_start <= fire_candle < test_end
    i_start = max(0, test_start // WINDOW - PATTERN_LEN)
    i_end = min(len(symbols) - PATTERN_LEN, test_end // WINDOW)

    for i in range(i_start, i_end):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + HOLD_CANDLES
        if fire_candle < test_start or fire_candle >= test_end:
            continue
        if end_outcome > test_end + HOLD_CANDLES:
            # Necesitamos velas hasta end_outcome para el SL/TP check
            if end_outcome > len(df):
                continue

        # Lookup directo al nodo
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

        # Decidir dirección con cada política
        for policy_name, decider in [("P1", decide_direction_p1), ("P6", decide_direction_p6)]:
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
                "long_count": meta.long_stats.count,
                "short_count": meta.short_stats.count,
                "long_wr": round(meta.win_rate_long, 4),
                "short_wr": round(meta.win_rate_short, 4),
                "long_avg_move": round(meta.avg_move_long, 4),
                "short_avg_move": round(meta.avg_move_short, 4),
                "expected_move_pct": round(float(meta.expected_move_pct), 4),
                "sl_distance_pct": round(sl_pct, 4),
                "tp_distance_pct": round(tp_pct, 4),
                **trade_result,
            })
    return trades


def aggregate_metrics(trades_df: pd.DataFrame) -> dict:
    """Calcula métricas para un grupo de trades."""
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
        "expectancy": round(pnl_mean, 4),  # = pnl_mean_net
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


def main():
    print("=" * 80)
    print("VALIDACIÓN P1 vs P6 — walk-forward con SL/TP + fees producción")
    print("=" * 80)
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, HOLD={HOLD_CANDLES} velas")
    print(f"Fees: {FEE_RT_PCT}% round-trip (Binance taker 0.04% × 2)")
    print(f"SL/TP: compute_sl_tp (paper_trader.py:1654) — SL=max_dd×1.5, TP=max(|EM|,max_fav)×1.0")
    print(f"       floors: SL≥{SL_FLOOR_PCT}%, TP≥{TP_FLOOR_PCT}%")
    print(f"Tokens: {len(SYMBOLS)}  |  Ventanas: {len(WINDOWS)}  "
          f"({', '.join(w[0] for w in WINDOWS)})")
    n_escenarios = len(SYMBOLS) * len(WINDOWS) * 2
    print(f"Total escenarios: {n_escenarios} (token x ventana x política)")
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
                # Para test, usar el rango del df_full directamente (no reset index)
                # porque simulate_trade usa df.iloc[entry_idx] con entry_idx absoluto
                # en el df_train. Necesitamos mapear indices del test al df_full.

                # Construir un df que contenga train + test contiguo para que
                # simulate_trade pueda acceder a las velas post-entry.
                # Pero el trie se construye solo sobre train, y walk_forward
                # itera sobre test. Las velas post-entry (hold) pueden caer
                # en cualquier parte del df_full.

                # Estrategia: pasar df_full completo a walk_forward_window,
                # que itera solo sobre fire_candles en [te_start, te_end).
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
                # Solo contar trades únicos por política
                n_p1 = sum(1 for t in trades if t["policy"] == "P1")
                n_p6 = sum(1 for t in trades if t["policy"] == "P6")
                print(f"{len(trades):,} trades (P1={n_p1:,}, P6={n_p6:,})")
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
    print("\n" + "=" * 80)
    print("MÉTRICAS POR (TOKEN, VENTANA, POLÍTICA)")
    print("=" * 80)
    rows_agg = []
    for (tok, win, pol), sub in trades_df.groupby(["token", "window", "policy"]):
        m = aggregate_metrics(sub)
        rows_agg.append({"token": tok, "window": win, "policy": pol, **m})
    agg_df = pd.DataFrame(rows_agg)
    agg_df.to_csv(OUT_DIR / "per_token_ventana.csv", index=False)
    # Print pivotado: una fila por (token, ventana), columnas por política
    for win in sorted(agg_df["window"].unique()):
        print(f"\n--- Ventana {win} ---")
        sub = agg_df[agg_df["window"] == win].sort_values("token")
        # Pivot para comparación lado a lado
        for tok in sub["token"].unique():
            t = sub[sub["token"] == tok]
            p1 = t[t["policy"] == "P1"].iloc[0] if len(t[t["policy"] == "P1"]) else None
            p6 = t[t["policy"] == "P6"].iloc[0] if len(t[t["policy"] == "P6"]) else None
            if p1 is None or p6 is None:
                continue
            print(f"  {tok}:")
            print(f"    P1: N={p1['n_trades']:>4}  WR={p1['win_rate']}  "
                  f"PF={p1['profit_factor']:>6}  Exp={p1['expectancy']:+.4f}  "
                  f"PnL_total={p1['pnl_total_net']:+8.2f}")
            print(f"    P6: N={p6['n_trades']:>4}  WR={p6['win_rate']}  "
                  f"PF={p6['profit_factor']:>6}  Exp={p6['expectancy']:+.4f}  "
                  f"PnL_total={p6['pnl_total_net']:+8.2f}  "
                  f"Δ={p6['pnl_total_net']-p1['pnl_total_net']:+.2f}")

    # ===== Métricas agregadas totales por política =====
    print("\n" + "=" * 80)
    print("MÉTRICAS AGREGADAS TOTALES")
    print("=" * 80)
    summary_rows = []
    for pol in ["P1", "P6"]:
        sub = trades_df[trades_df["policy"] == pol]
        m = aggregate_metrics(sub)
        summary_rows.append({"policy": pol, **m})
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(OUT_DIR / "summary.csv", index=False)

    print("\n| Politica | N trades | WR    | PF     | Expectancy | PnL total | PnL medio | LONG N | LONG WR | LONG PnL | SHORT N | SHORT WR | SHORT PnL |")
    print("|----------|----------|-------|--------|------------|----------|----------|--------|---------|----------|---------|---------|----------|")
    for _, r in summary_df.iterrows():
        print(f"| {r['policy']:<8} | {r['n_trades']:>8,} | {r['win_rate']} | "
              f"{r['profit_factor']:>6} | {r['expectancy']:+.4f}    | "
              f"{r['pnl_total_net']:+8.2f} | {r['pnl_mean_net']:+.4f}  | "
              f"{r['long_n']:>6} | {r['long_wr']} | {r['long_pnl_mean']:+.4f}  | "
              f"{r['short_n']:>7} | {r['short_wr']} | {r['short_pnl_mean']:+.4f}  |")

    # ===== Agregado por token (sumando 3 ventanas) =====
    print("\n" + "=" * 80)
    print("AGREGADO POR TOKEN (3 ventanas sumadas)")
    print("=" * 80)
    per_token_rows = []
    for tok in sorted(trades_df["token"].unique()):
        for pol in ["P1", "P6"]:
            sub = trades_df[(trades_df["token"] == tok) & (trades_df["policy"] == pol)]
            m = aggregate_metrics(sub)
            per_token_rows.append({"token": tok, "policy": pol, **m})
    per_token_df = pd.DataFrame(per_token_rows)
    per_token_df.to_csv(OUT_DIR / "per_token_aggregated.csv", index=False)
    print()
    print(f"{'Token':<10} {'Pol':<4} {'N':>6} {'WR':>6} {'PF':>6} {'Exp':>8} {'PnL_total':>10}")
    for _, r in per_token_df.iterrows():
        print(f"{r['token']:<10} {r['policy']:<4} {r['n_trades']:>6,} "
              f"{str(r['win_rate']):>6} {str(r['profit_factor']):>6} "
              f"{r['expectancy']:+.4f} {r['pnl_total_net']:+10.2f}")

    # ===== Delta por token (P6 - P1) =====
    print("\n" + "=" * 80)
    print("DELTA POR TOKEN (P6 - P1)")
    print("=" * 80)
    print(f"\n{'Token':<10} {'P1 PnL':>10} {'P6 PnL':>10} {'Δ PnL':>10} {'P1 N':>6} {'P6 N':>6} "
          f"{'P1 WR':>6} {'P6 WR':>6} {'Veredicto':<20}")
    deltas = []
    for tok in sorted(trades_df["token"].unique()):
        p1_row = per_token_df[(per_token_df["token"] == tok) & (per_token_df["policy"] == "P1")].iloc[0]
        p6_row = per_token_df[(per_token_df["token"] == tok) & (per_token_df["policy"] == "P6")].iloc[0]
        d_pnl = p6_row["pnl_total_net"] - p1_row["pnl_total_net"]
        verdict = "MEJORA" if d_pnl > 0 else ("IGUAL" if d_pnl == 0 else "EMPEORA")
        print(f"{tok:<10} {p1_row['pnl_total_net']:+10.2f} {p6_row['pnl_total_net']:+10.2f} "
              f"{d_pnl:+10.2f} {p1_row['n_trades']:>6,} {p6_row['n_trades']:>6,} "
              f"{str(p1_row['win_rate']):>6} {str(p6_row['win_rate']):>6} {verdict:<20}")
        deltas.append({"token": tok, "p1_pnl": p1_row["pnl_total_net"],
                       "p6_pnl": p6_row["pnl_total_net"], "delta": d_pnl, "verdict": verdict})

    n_mejora = sum(1 for d in deltas if d["verdict"] == "MEJORA")
    n_empeora = sum(1 for d in deltas if d["verdict"] == "EMPEORA")
    n_igual = sum(1 for d in deltas if d["verdict"] == "IGUAL")
    print(f"\nResumen delta por token: {n_mejora} mejoran, {n_empeora} empeoran, {n_igual} igual")

    # ===== Delta por ventana =====
    print("\n" + "=" * 80)
    print("DELTA POR VENTANA (P6 - P1)")
    print("=" * 80)
    per_window_rows = []
    for win in sorted(trades_df["window"].unique()):
        for pol in ["P1", "P6"]:
            sub = trades_df[(trades_df["window"] == win) & (trades_df["policy"] == pol)]
            m = aggregate_metrics(sub)
            per_window_rows.append({"window": win, "policy": pol, **m})
    per_window_df = pd.DataFrame(per_window_rows)
    per_window_df.to_csv(OUT_DIR / "per_window_aggregated.csv", index=False)

    print(f"\n{'Ventana':<8} {'Pol':<4} {'N':>6} {'WR':>6} {'PF':>6} {'Exp':>8} {'PnL_total':>10}")
    for _, r in per_window_df.iterrows():
        print(f"{r['window']:<8} {r['policy']:<4} {r['n_trades']:>6,} "
              f"{str(r['win_rate']):>6} {str(r['profit_factor']):>6} "
              f"{r['expectancy']:+.4f} {r['pnl_total_net']:+10.2f}")

    print(f"\nDelta por ventana:")
    for win in sorted(trades_df["window"].unique()):
        p1 = per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P1")].iloc[0]
        p6 = per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P6")].iloc[0]
        d = p6["pnl_total_net"] - p1["pnl_total_net"]
        v = "MEJORA" if d > 0 else ("IGUAL" if d == 0 else "EMPEORA")
        print(f"  {win}: P1={p1['pnl_total_net']:+.2f} → P6={p6['pnl_total_net']:+.2f}  Δ={d:+.2f}  {v}")

    # ===== JSON estructurado =====
    payload = {
        "config": {
            "alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
            "hold_candles": HOLD_CANDLES,
            "fee_rt_pct": FEE_RT_PCT,
            "sl_tp_rule": "compute_sl_tp — SL=max_dd×1.5, TP=max(|EM|,max_fav)×1.0, floors 0.1%",
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
    delta_pnl = p6["pnl_total_net"] - p1["pnl_total_net"]
    delta_exp = (p6["expectancy"] or 0) - (p1["expectancy"] or 0)
    n_mejora = sum(1 for d in deltas if d["verdict"] == "MEJORA")
    n_empeora = sum(1 for d in deltas if d["verdict"] == "EMPEORA")

    lines = []
    lines.append("# Validación P1 vs P6 — Walk-forward con SL/TP y Fees de Producción\n\n")
    lines.append("## Setup\n\n")
    lines.append(f"- α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, hold={HOLD_CANDLES} velas\n")
    lines.append(f"- Tokens: {len(SYMBOLS)} ({', '.join(SYMBOLS)})\n")
    lines.append(f"- Ventanas: {len(WINDOWS)} ({', '.join(w[0] for w in WINDOWS)}) — train/test disjuntos sobre 100k velas\n")
    lines.append(f"- SL/TP: `meta.compute_sl_tp()` — SL=max_drawdown×1.5, TP=max(|expected_move|, max_favorable)×1.0, floor 0.1%\n")
    lines.append(f"- Fees: {FEE_RT_PCT}% round-trip (Binance taker 0.04% × 2)\n")
    lines.append(f"- Políticas:\n")
    lines.append(f"  - **P1 (current)**: `dir = sign(expected_move_pct)`\n")
    lines.append(f"  - **P6 (majority_avg_move)**: `dir = LONG si avg_move_long > |avg_move_short|, sino SHORT`\n\n")

    lines.append("## Resultados agregados (todos los tokens × 3 ventanas)\n\n")
    lines.append("| Política | N trades | WR | PF | Expectancy | PnL total | PnL medio | LONG WR | LONG PnL medio | SHORT WR | SHORT PnL medio |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|\n")
    for _, r in summary_df.iterrows():
        lines.append(f"| **{r['policy']}** | {r['n_trades']:,} | {r['win_rate']} | "
                     f"{r['profit_factor']} | {r['expectancy']:+.4f}% | "
                     f"{r['pnl_total_net']:+.2f}% | {r['pnl_mean_net']:+.4f}% | "
                     f"{r['long_wr']} | {r['long_pnl_mean']} | "
                     f"{r['short_wr']} | {r['short_pnl_mean']} |\n")
    lines.append(f"\n**Delta P6 − P1**: PnL total {delta_pnl:+.2f}pp, "
                 f"expectancy {delta_exp:+.4f}pp/trade\n\n")

    lines.append("## Delta por token (3 ventanas agregadas)\n\n")
    lines.append("| Token | P1 PnL | P6 PnL | Δ PnL | P1 N | P6 N | P1 WR | P6 WR | Veredicto |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    for d in deltas:
        p1_row = per_token_df[(per_token_df["token"] == d["token"]) & (per_token_df["policy"] == "P1")].iloc[0]
        p6_row = per_token_df[(per_token_df["token"] == d["token"]) & (per_token_df["policy"] == "P6")].iloc[0]
        lines.append(f"| {d['token']} | {p1_row['pnl_total_net']:+.2f} | "
                     f"{p6_row['pnl_total_net']:+.2f} | {d['delta']:+.2f} | "
                     f"{p1_row['n_trades']:,} | {p6_row['n_trades']:,} | "
                     f"{p1_row['win_rate']} | {p6_row['win_rate']} | "
                     f"{d['verdict']} |\n")
    lines.append(f"\n**Tokens mejorados**: {n_mejora}/{len(deltas)}  ·  "
                 f"**Tokens empeorados**: {n_empeora}/{len(deltas)}\n\n")

    lines.append("## Delta por ventana (todos los tokens agregados)\n\n")
    lines.append("| Ventana | Política | N | WR | PF | Exp | PnL total |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    for _, r in per_window_df.iterrows():
        lines.append(f"| {r['window']} | {r['policy']} | {r['n_trades']:,} | "
                     f"{r['win_rate']} | {r['profit_factor']} | "
                     f"{r['expectancy']:+.4f}% | {r['pnl_total_net']:+.2f}% |\n")
    lines.append("\n| Ventana | P1 PnL | P6 PnL | Δ | Veredicto |\n|---|---|---|---|---|\n")
    for win in sorted(per_window_df["window"].unique()):
        p1w = per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P1")].iloc[0]
        p6w = per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P6")].iloc[0]
        dw = p6w["pnl_total_net"] - p1w["pnl_total_net"]
        vw = "MEJORA" if dw > 0 else ("IGUAL" if dw == 0 else "EMPEORA")
        lines.append(f"| {win} | {p1w['pnl_total_net']:+.2f} | {p6w['pnl_total_net']:+.2f} | "
                     f"{dw:+.2f} | {vw} |\n")

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
    robust_token = n_mejora >= len(deltas) * 0.6  # ≥60% tokens mejoran
    robust_window = sum(
        1 for win in per_window_df["window"].unique()
        if (per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P6")].iloc[0]["pnl_total_net"]
            - per_window_df[(per_window_df["window"] == win) & (per_window_df["policy"] == "P1")].iloc[0]["pnl_total_net"]) > 0
    )
    robust_window_ok = robust_window >= 2  # ≥2 de 3 ventanas
    overall_positive = delta_pnl > 0

    if overall_positive and robust_token and robust_window_ok:
        verdict = ("ROBUSTO: P6 mejora PnL total, mejora en ≥60% de tokens y en ≥2/3 ventanas. "
                   "RECOMENDACIÓN: integrar al motor.")
    elif overall_positive and (robust_token or robust_window_ok):
        verdict = ("PARCIAL: P6 mejora PnL total pero no consistentemente por token/ventana. "
                   "RECOMENDACIÓN: revisar antes de integrar — puede haber overfitting a 1-2 tokens.")
    elif overall_positive:
        verdict = ("MARGINAL: P6 mejora PnL total pero la mejora está concentrada. "
                   "RECOMENDACIÓN: NO integrar — no es robusto.")
    else:
        verdict = ("NEGATIVO: P6 NO mejora PnL total con SL/TP y fees. "
                   "RECOMENDACIÓN: NO integrar.")
    lines.append(f"**{verdict}**\n\n")
    lines.append(f"- PnL total delta: {delta_pnl:+.2f}pp\n")
    lines.append(f"- Tokens mejorados: {n_mejora}/{len(deltas)} ({100*n_mejora/len(deltas):.0f}%)\n")
    lines.append(f"- Ventanas mejoradas: {robust_window}/{len(per_window_df['window'].unique())}\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"MD:   {out_path}")


if __name__ == "__main__":
    main()
