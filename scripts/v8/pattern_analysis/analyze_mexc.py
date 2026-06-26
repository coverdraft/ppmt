#!/usr/bin/env python3
"""
Analyze MEXC futures order history: FIFO trade matching + comprehensive stats.
"""

import pandas as pd
import numpy as np
import json
from collections import defaultdict, deque
from datetime import timedelta
import warnings
warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────
# 1. LOAD & PARSE
# ──────────────────────────────────────────────
FILE = "/home/z/my-project/ppmt/scripts/v8/pattern_analysis/MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx"
OUT  = "/home/z/my-project/download/trader_analysis.json"

df = pd.read_excel(FILE)

# Parse timestamps (UTC+2 → UTC)
df["timestamp"] = pd.to_datetime(df["Tiempo(UTC+02:00)"]) - timedelta(hours=2)

# Parse symbol: remove "USDT" suffix
df["symbol"] = df["Par de Trading de Futuros"].str.replace("USDT$", "", regex=True)

# Map direction
dir_map = {
    "buy long":  "open_long",
    "sell long": "close_long",
    "buy short": "open_short",
    "sell short":"close_short",
}
df["action"] = df["Dirección"].map(dir_map)

# Determine side (long / short) and action type (open / close)
df["side"]   = df["action"].str.split("_").str[1]   # long or short
df["otype"]  = df["action"].str.split("_").str[0]   # open or close

# Key columns
df["leverage"] = df["Apalancamiento"].astype(int)
df["pnl"]      = df["PNL de Cierre"].astype(float)
df["qty"]      = df["Cant. Completada (Cont.)"].astype(int)
df["price"]    = df["Precio promedio completo"].astype(float)
df["amount"]   = df["Cant. Completada (Monto)"].astype(float)  # USDT notional

# Sort by timestamp (earliest first) so FIFO works
df = df.sort_values("timestamp").reset_index(drop=True)

print(f"Loaded {len(df):,} orders  |  {df['symbol'].nunique()} symbols  |  {df['timestamp'].min()} → {df['timestamp'].max()}")

# ──────────────────────────────────────────────
# 2. FIFO TRADE MATCHING
# ──────────────────────────────────────────────
# Group open orders by (UID, symbol, side) and match closes against them FIFO.
# A "trade" is the full lifecycle: open(s) → close(s).
# We aggregate all entries and all exits, then compute trade-level metrics.

# Build queues: key = (symbol, side), value = deque of open order dicts
open_queues: dict[tuple, deque] = defaultdict(deque)

trades = []
trade_id = 0

# Process rows in chronological order
for _, row in df.iterrows():
    sym   = row["symbol"]
    side  = row["side"]
    otype = row["otype"]
    qty   = row["qty"]
    key   = (sym, side)

    if otype == "open":
        open_queues[key].append({
            "timestamp": row["timestamp"],
            "price":     row["price"],
            "leverage":  row["leverage"],
            "amount":    row["amount"],
            "qty_left":  qty,
            "qty_orig":  qty,
        })
    else:  # close
        remaining = qty
        entry_prices = []
        entry_times  = []
        leverages    = []
        entry_amounts= []
        n_entries    = 0

        while remaining > 0 and open_queues[key]:
            front = open_queues[key][0]
            matched = min(remaining, front["qty_left"])

            # Weighted price / amount for this partial match
            frac = matched / front["qty_orig"]
            entry_prices.append(front["price"])
            entry_times.append(front["timestamp"])
            leverages.append(front["leverage"])
            entry_amounts.append(front["amount"] * frac)

            n_entries += 1
            front["qty_left"] -= matched
            remaining         -= matched

            if front["qty_left"] <= 0:
                open_queues[key].popleft()

        if n_entries > 0:
            # Weighted average entry price & time
            total_entry_amount = sum(entry_amounts)
            avg_entry_price = (
                sum(p * a for p, a in zip(entry_prices, entry_amounts)) / total_entry_amount
                if total_entry_amount > 0 else np.mean(entry_prices)
            )
            # Entry time = time of FIRST entry (trade start)
            first_entry_time = min(entry_times)
            # Leverage = max leverage used (conservative)
            max_leverage = max(leverages)

            trades.append({
                "trade_id":      trade_id,
                "symbol":        sym,
                "side":          side,
                "entry_time":    first_entry_time,
                "exit_time":     row["timestamp"],
                "avg_entry_price": round(avg_entry_price, 8),
                "exit_price":    row["price"],
                "pnl":           row["pnl"],
                "leverage":      max_leverage,
                "n_entries":     n_entries,
                "close_qty":     qty,
                "entry_amount":  round(total_entry_amount, 6),
                "exit_amount":   row["amount"],
            })
            trade_id += 1

# Any remaining unmatched opens → still open positions (no trade yet)
unmatched_opens = sum(q[0]["qty_left"] if q else 0 for q in open_queues.values())
# Actually count all
unmatched_total = sum(item["qty_left"] for q in open_queues.values() for item in q)

print(f"Matched {len(trades):,} closed trades  |  Unmatched open contracts remaining: {unmatched_total}")

tdf = pd.DataFrame(trades)
if not tdf.empty:
    tdf["duration"] = tdf["exit_time"] - tdf["entry_time"]
    tdf["duration_sec"] = tdf["duration"].dt.total_seconds()
    tdf["duration_min"] = tdf["duration_sec"] / 60
    tdf["duration_hours"]= tdf["duration_sec"] / 3600
    tdf["win"] = tdf["pnl"] > 0
    tdf["loss"]= tdf["pnl"] < 0
    tdf["be"] = tdf["pnl"] == 0  # break-even

# ──────────────────────────────────────────────
# 3. COMPREHENSIVE STATISTICS
# ──────────────────────────────────────────────
def pct(val, total):
    return round(val / total * 100, 2) if total > 0 else 0.0

def safe_round(val, ndigits=2):
    return round(float(val), ndigits) if not pd.isna(val) else None

results = {}

# --- Overall ---
total_trades = len(tdf)
total_wins   = int(tdf["win"].sum())
total_losses = int(tdf["loss"].sum())
total_be     = int(tdf["be"].sum())
total_pnl    = float(tdf["pnl"].sum())
avg_pnl      = float(tdf["pnl"].mean())
median_pnl   = float(tdf["pnl"].median())
wr           = pct(total_wins, total_trades)

results["overall"] = {
    "total_trades":     total_trades,
    "wins":             total_wins,
    "losses":           total_losses,
    "break_even":       total_be,
    "win_rate_pct":     wr,
    "total_pnl":        safe_round(total_pnl, 4),
    "avg_pnl":          safe_round(avg_pnl, 4),
    "median_pnl":       safe_round(median_pnl, 4),
    "largest_win":      safe_round(tdf["pnl"].max(), 4),
    "largest_loss":     safe_round(tdf["pnl"].min(), 4),
    "profit_factor":    safe_round(tdf.loc[tdf["pnl"]>0, "pnl"].sum() / abs(tdf.loc[tdf["pnl"]<0, "pnl"].sum()), 4) if total_losses > 0 else None,
    "avg_win":          safe_round(tdf.loc[tdf["win"], "pnl"].mean(), 4),
    "avg_loss":         safe_round(tdf.loc[tdf["loss"], "pnl"].mean(), 4),
    "avg_win_loss_ratio": safe_round(abs(tdf.loc[tdf["win"], "pnl"].mean() / tdf.loc[tdf["loss"], "pnl"].mean()), 4) if total_losses > 0 else None,
}

# --- Per direction (long vs short) ---
dir_stats = {}
for side in ["long", "short"]:
    sub = tdf[tdf["side"] == side]
    if len(sub) == 0:
        continue
    wins_s = int(sub["win"].sum())
    dir_stats[side] = {
        "trades":       len(sub),
        "wins":         wins_s,
        "win_rate_pct": pct(wins_s, len(sub)),
        "total_pnl":    safe_round(sub["pnl"].sum(), 4),
        "avg_pnl":      safe_round(sub["pnl"].mean(), 4),
        "largest_win":  safe_round(sub["pnl"].max(), 4),
        "largest_loss": safe_round(sub["pnl"].min(), 4),
    }
results["per_direction"] = dir_stats

# --- Per symbol (top 20 most traded) ---
sym_stats = {}
for sym in tdf["symbol"].value_counts().head(20).index:
    sub = tdf[tdf["symbol"] == sym]
    wins_sym = int(sub["win"].sum())
    sym_stats[sym] = {
        "trades":       len(sub),
        "wins":         wins_sym,
        "win_rate_pct": pct(wins_sym, len(sub)),
        "total_pnl":    safe_round(sub["pnl"].sum(), 4),
        "avg_pnl":      safe_round(sub["pnl"].mean(), 4),
    }
results["per_symbol_top20"] = sym_stats

# --- Duration distribution ---
dur = tdf["duration_min"]
results["duration"] = {
    "mean_minutes":      safe_round(dur.mean(), 2),
    "median_minutes":    safe_round(dur.median(), 2),
    "p25_minutes":       safe_round(dur.quantile(0.25), 2),
    "p75_minutes":       safe_round(dur.quantile(0.75), 2),
    "p90_minutes":       safe_round(dur.quantile(0.90), 2),
    "p99_minutes":       safe_round(dur.quantile(0.99), 2),
    "max_hours":         safe_round(dur.max()/60, 2),
    "min_minutes":       safe_round(dur.min(), 2),
}

# Duration for winners vs losers
results["duration_win_vs_loss"] = {
    "avg_winner_minutes":  safe_round(tdf.loc[tdf["win"], "duration_min"].mean(), 2),
    "avg_loser_minutes":   safe_round(tdf.loc[tdf["loss"], "duration_min"].mean(), 2),
    "median_winner_minutes": safe_round(tdf.loc[tdf["win"], "duration_min"].median(), 2),
    "median_loser_minutes":  safe_round(tdf.loc[tdf["loss"], "duration_min"].median(), 2),
}

# --- Leverage distribution ---
lev = tdf["leverage"]
results["leverage"] = {
    "mean":       safe_round(lev.mean(), 2),
    "median":     safe_round(lev.median(), 2),
    "mode":       int(lev.mode().iloc[0]),
    "min":        int(lev.min()),
    "max":        int(lev.max()),
    "distribution": {str(k): int(v) for k, v in lev.value_counts().sort_index().items()},
}

# --- DCA frequency ---
dca = tdf["n_entries"]
results["dca"] = {
    "trades_with_single_entry":  int((dca == 1).sum()),
    "trades_with_dca":           int((dca > 1).sum()),
    "dca_frequency_pct":         pct((dca > 1).sum(), len(dca)),
    "max_entries":               int(dca.max()),
    "mean_entries":              safe_round(dca.mean(), 2),
    "median_entries":            safe_round(dca.median(), 2),
    "distribution":              {str(k): int(v) for k, v in dca.value_counts().sort_index().items()},
}

# --- Time-of-day distribution (UTC hour) ---
tdf["hour_utc"] = tdf["entry_time"].dt.hour
hour_dist = tdf.groupby("hour_utc").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    win_rate=("win", "mean"),
).to_dict(orient="index")
# Convert keys to str for JSON
hour_dist = {str(k): {
    "trades": int(v["trades"]),
    "total_pnl": safe_round(v["total_pnl"], 4),
    "win_rate_pct": safe_round(v["win_rate"] * 100, 2),
} for k, v in hour_dist.items()}
results["hour_of_day_utc"] = hour_dist

# --- Day-of-week distribution ---
tdf["dow"] = tdf["entry_time"].dt.day_name()
dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
dow_stats = {}
for d in dow_order:
    sub = tdf[tdf["dow"] == d]
    if len(sub) == 0:
        continue
    dow_stats[d] = {
        "trades": len(sub),
        "total_pnl": safe_round(sub["pnl"].sum(), 4),
        "win_rate_pct": pct(sub["win"].sum(), len(sub)),
    }
results["day_of_week"] = dow_stats

# --- Consecutive win/loss streaks ---
# Sort trades by entry_time
streak_trades = tdf.sort_values("entry_time").reset_index(drop=True)
max_win_streak  = 0
max_loss_streak = 0
cur_win  = 0
cur_loss = 0
for w in streak_trades["win"]:
    if w:
        cur_win += 1
        cur_loss = 0
        max_win_streak = max(max_win_streak, cur_win)
    else:
        cur_loss += 1
        cur_win = 0
        max_loss_streak = max(max_loss_streak, cur_loss)

results["streaks"] = {
    "max_consecutive_wins":  max_win_streak,
    "max_consecutive_losses": max_loss_streak,
}

# --- Monthly PnL ---
tdf["month"] = tdf["entry_time"].dt.to_period("M").astype(str)
monthly = tdf.groupby("month").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    win_rate=("win", "mean"),
).to_dict(orient="index")
monthly = {k: {
    "trades": int(v["trades"]),
    "total_pnl": safe_round(v["total_pnl"], 4),
    "win_rate_pct": safe_round(v["win_rate"] * 100, 2),
} for k, v in monthly.items()}
results["monthly"] = monthly

# ──────────────────────────────────────────────
# 4. SAVE JSON
# ──────────────────────────────────────────────
with open(OUT, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\n✅ Results saved to {OUT}")

# ──────────────────────────────────────────────
# 5. PRINT FORMATTED SUMMARY
# ──────────────────────────────────────────────
o = results["overall"]
print("\n" + "="*70)
print("  MEXC FUTURES TRADER ANALYSIS REPORT")
print("="*70)
print(f"  Period: {tdf['entry_time'].min().strftime('%Y-%m-%d')} → {tdf['entry_time'].max().strftime('%Y-%m-%d')}")
print(f"  Total Closed Trades:  {o['total_trades']:,}")
print(f"  Wins / Losses / BE:   {o['wins']:,} / {o['losses']:,} / {o['break_even']:,}")
print(f"  Win Rate:             {o['win_rate_pct']}%")
print(f"  Total PnL:            ${o['total_pnl']:,.4f}")
print(f"  Avg PnL per Trade:    ${o['avg_pnl']:,.4f}")
print(f"  Median PnL:           ${o['median_pnl']:,.4f}")
print(f"  Largest Win:          ${o['largest_win']:,.4f}")
print(f"  Largest Loss:         ${o['largest_loss']:,.4f}")
print(f"  Profit Factor:        {o['profit_factor']}")
print(f"  Avg Win:              ${o['avg_win']:,.4f}")
print(f"  Avg Loss:             ${o['avg_loss']:,.4f}")
print(f"  Avg Win/Loss Ratio:   {o['avg_win_loss_ratio']}")

print("\n" + "-"*70)
print("  DIRECTION BREAKDOWN")
print("-"*70)
for side, s in results["per_direction"].items():
    print(f"  {side.upper():6s} | Trades: {s['trades']:,} | WR: {s['win_rate_pct']}% | PnL: ${s['total_pnl']:,.4f} | Avg: ${s['avg_pnl']:,.4f}")

print("\n" + "-"*70)
print("  TOP 10 MOST TRADED SYMBOLS")
print("-"*70)
for i, (sym, s) in enumerate(list(results["per_symbol_top20"].items())[:10]):
    print(f"  {i+1:2d}. {sym:12s} | Trades: {s['trades']:3,} | WR: {s['win_rate_pct']}% | PnL: ${s['total_pnl']:>10,.4f}")

print("\n" + "-"*70)
print("  DURATION DISTRIBUTION")
print("-"*70)
d = results["duration"]
print(f"  Mean:   {d['mean_minutes']:.1f} min  |  Median: {d['median_minutes']:.1f} min")
print(f"  P25:    {d['p25_minutes']:.1f} min  |  P75: {d['p75_minutes']:.1f} min")
print(f"  P90:    {d['p90_minutes']:.1f} min  |  P99: {d['p99_minutes']:.1f} min")
print(f"  Min:    {d['min_minutes']:.1f} min  |  Max: {d['max_hours']:.1f} hrs")

dw = results["duration_win_vs_loss"]
print(f"  Avg Winner Hold: {dw['avg_winner_minutes']:.1f} min  |  Avg Loser Hold: {dw['avg_loser_minutes']:.1f} min")
print(f"  Med Winner Hold: {dw['median_winner_minutes']:.1f} min  |  Med Loser Hold: {dw['median_loser_minutes']:.1f} min")

print("\n" + "-"*70)
print("  LEVERAGE")
print("-"*70)
l = results["leverage"]
print(f"  Mean: {l['mean']}x  |  Median: {l['median']}x  |  Mode: {l['mode']}x  |  Range: {l['min']}x–{l['max']}x")
top_lev = sorted(l["distribution"].items(), key=lambda x: -int(x[1]))[:5]
for k, v in top_lev:
    print(f"    {k}x: {v} trades ({pct(v, total_trades)}%)")

print("\n" + "-"*70)
print("  DCA (MULTI-ENTRY) ANALYSIS")
print("-"*70)
dc = results["dca"]
print(f"  Single-entry trades: {dc['trades_with_single_entry']:,} ({pct(dc['trades_with_single_entry'], total_trades)}%)")
print(f"  DCA trades (n>1):    {dc['trades_with_dca']:,} ({dc['dca_frequency_pct']}%)")
print(f"  Max entries:         {dc['max_entries']}")
print(f"  Mean entries:        {dc['mean_entries']}")
top_dca = sorted(dc["distribution"].items(), key=lambda x: -int(x[1]))[:5]
for k, v in top_dca:
    print(f"    {k} entries: {v} trades")

print("\n" + "-"*70)
print("  TIME-OF-DAY (UTC) — Top 5 by PnL")
print("-"*70)
hod = results["hour_of_day_utc"]
top_hours = sorted(hod.items(), key=lambda x: -float(x[1]["total_pnl"]))[:5]
for h, v in top_hours:
    print(f"  {h:2s}:00 | Trades: {v['trades']:4,} | WR: {v['win_rate_pct']}% | PnL: ${v['total_pnl']:>10,.4f}")

print("\n" + "-"*70)
print("  DAY OF WEEK")
print("-"*70)
for d, v in results["day_of_week"].items():
    print(f"  {d:9s} | Trades: {v['trades']:4,} | WR: {v['win_rate_pct']}% | PnL: ${v['total_pnl']:>10,.4f}")

print("\n" + "-"*70)
print("  STREAKS")
print("-"*70)
print(f"  Max Consecutive Wins:   {results['streaks']['max_consecutive_wins']}")
print(f"  Max Consecutive Losses:  {results['streaks']['max_consecutive_losses']}")

print("\n" + "-"*70)
print("  MONTHLY PnL")
print("-"*70)
for m, v in sorted(results["monthly"].items()):
    bar = "█" * max(1, int(abs(v["total_pnl"]) / 5))
    sign = "+" if v["total_pnl"] >= 0 else ""
    print(f"  {m} | {v['trades']:4,} trades | WR: {v['win_rate_pct']:5.1f}% | PnL: {sign}{v['total_pnl']:>10,.4f} {bar}")

print("\n" + "="*70)
