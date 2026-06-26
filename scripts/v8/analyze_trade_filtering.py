"""
analyze_trade_filtering.py — What happens if we remove big losses and tiny wins?

Goal: Find the "core" trades where the trader's ENTRY was good,
removing the noise from bad exits (big losses from not cutting,
tiny wins from cutting too early).
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

# Load the analysis
data_path = Path("/home/z/my-project/download/trader_analysis.json")
if not data_path.exists():
    print("ERROR: trader_analysis.json not found. Run the MEXC analysis first.")
    exit(1)

with open(data_path) as f:
    data = json.load(f)

# Load raw Excel to re-analyze with filters
xlsx_path = Path("/home/z/my-project/ppmt/scripts/v8/pattern_analysis/MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx")
if not xlsx_path.exists():
    print("ERROR: XLSX not found")
    exit(1)

from collections import defaultdict
from datetime import timedelta

df = pd.read_excel(xlsx_path)
df["_ts"] = pd.to_datetime(df["Tiempo(UTC+02:00)"].astype(str)) - pd.Timedelta(hours=2)
df["_ts"] = df["_ts"].dt.tz_localize("UTC")
df["_symbol"] = df["Par de Trading de Futuros"].str.strip().str.upper().str.replace("USDT", "", regex=False).str.strip()
df["_action"] = df["Dirección"].str.strip().str.lower().map({
    "buy long": "open_long", "sell long": "close_long",
    "buy short": "open_short", "sell short": "close_short",
})
df["_price"] = pd.to_numeric(df["Precio promedio completo"], errors="coerce")
df["_pnl"] = pd.to_numeric(df["PNL de Cierre"], errors="coerce").fillna(0)
df["_fee"] = pd.to_numeric(df["Comisión de Trading"], errors="coerce").fillna(0).abs()
df["_qty"] = pd.to_numeric(df["Cant. Completada (Cripto)"], errors="coerce").fillna(0)
df["_leverage"] = pd.to_numeric(df["Apalancamiento"], errors="coerce").fillna(1)

df = df.sort_values("_ts").reset_index(drop=True)

# Match trades
trades = []
opens = defaultdict(lambda: defaultdict(list))
for idx, row in df.iterrows():
    action = row.get("_action")
    if not action or pd.isna(action):
        continue
    symbol = row["_symbol"]
    ts = row["_ts"]
    price = row["_price"]
    pnl = row["_pnl"]
    fee = row["_fee"]
    qty = row["_qty"]
    leverage = row["_leverage"]

    if "open" in action:
        direction = "long" if "long" in action else "short"
        opens[symbol][direction].append({
            "ts": ts, "price": price, "fee": fee, "qty": qty,
            "leverage": leverage, "idx": idx
        })
    elif "close" in action:
        direction = "long" if "long" in action else "short"
        open_list = opens[symbol][direction]
        if not open_list:
            continue
        matched = []
        remaining = qty
        while open_list and remaining > 0:
            o = open_list.pop(0)
            matched.append(o)
            remaining -= o["qty"]
        if remaining < 0 and matched:
            partial = matched.pop()
            partial["qty"] = -remaining
            open_list.insert(0, partial)
        if matched:
            first = matched[0]
            total_fee = sum(o["fee"] for o in matched) + fee
            avg_entry = np.average([o["price"] for o in matched],
                                   weights=[o["qty"] for o in matched])
            duration_min = (ts - first["ts"]).total_seconds() / 60
            n_entries = len(matched)
            entry_leverage = first.get("leverage", leverage)
            trades.append({
                "symbol": symbol, "direction": direction,
                "entry_time": first["ts"], "entry_price": avg_entry,
                "exit_time": ts, "exit_price": price,
                "pnl": pnl, "fee": total_fee,
                "pnl_net": pnl - total_fee,
                "n_entries": n_entries, "leverage": entry_leverage,
                "duration_min": duration_min,
                "is_win": pnl > 0,
            })

tdf = pd.DataFrame(trades)
closed = tdf[tdf["pnl"] != 0].copy()
print(f"Total closed trades: {len(closed)}")
print(f"Overall: WR={closed['is_win'].mean()*100:.1f}%  PnL=${closed['pnl'].sum():+.2f}  PF={closed[closed['pnl']>0]['pnl'].sum()/abs(closed[closed['pnl']<0]['pnl'].sum()):.2f}")

# ── Distribution analysis ──
wins = closed[closed["pnl"] > 0]["pnl"]
losses = closed[closed["pnl"] < 0]["pnl"]

print(f"\n── WIN distribution ──")
print(f"  Count: {len(wins)}")
for p in [5, 10, 25, 50, 75, 90, 95]:
    print(f"  P{p}: ${np.percentile(wins, p):.2f}")
print(f"  Mean: ${wins.mean():.2f}  Median: ${wins.median():.2f}")

print(f"\n── LOSS distribution ──")
print(f"  Count: {len(losses)}")
for p in [5, 10, 25, 50, 75, 90, 95]:
    print(f"  P{p}: ${np.percentile(losses, p):.2f}")
print(f"  Mean: ${losses.mean():.2f}  Median: ${losses.median():.2f}")

# ── Test different filter thresholds ──
print("\n" + "=" * 100)
print("FILTERING ANALYSIS — Remove big losses + tiny wins")
print("=" * 100)

# Define thresholds to test
tiny_win_thresholds = [0, 0.5, 1.0, 1.5, 2.0]
big_loss_thresholds = [5, 10, 15, 20, 30, 50]

print(f"\n{'TinyWin<':>8} {'BigLoss>':>8} {'N':>6} {'WR%':>6} {'PnL':>10} {'PF':>6} {'AvgW':>7} {'AvgL':>7} {'W/L':>6} {'MedDur':>7}")
print("-" * 85)

best_combo = None
best_pnl = -9999

for tw in tiny_win_thresholds:
    for bl in big_loss_thresholds:
        mask = ~((closed["pnl"] > 0) & (closed["pnl"] <= tw)) & ~((closed["pnl"] < 0) & (closed["pnl"] <= -bl))
        filtered = closed[mask]
        if len(filtered) < 50:
            continue

        n = len(filtered)
        wr = filtered["is_win"].mean() * 100
        total_pnl = filtered["pnl"].sum()
        w = filtered[filtered["pnl"] > 0]["pnl"]
        l = filtered[filtered["pnl"] < 0]["pnl"]
        avg_w = w.mean() if len(w) > 0 else 0
        avg_l = l.mean() if len(l) > 0 else 0
        wl_ratio = abs(avg_w / avg_l) if avg_l != 0 else 0
        gains = w.sum()
        losses_sum = abs(l.sum())
        pf = gains / losses_sum if losses_sum > 0 else 0
        med_dur = filtered["duration_min"].median()

        print(f"  ${tw:>5.1f}  ${bl:>5.0f}  {n:>6} {wr:>5.1f} {total_pnl:>+10.1f} {pf:>5.2f} {avg_w:>+6.2f} {avg_l:>+6.2f} {wl_ratio:>5.2f} {med_dur:>6.1f}m")

        if total_pnl > best_pnl:
            best_pnl = total_pnl
            best_combo = (tw, bl, n, wr, total_pnl, pf, avg_w, avg_l, wl_ratio, med_dur)

print(f"\n  🏆 BEST FILTER: TinyWin<${best_combo[0]:.1f} + BigLoss>${best_combo[1]:.0f}")
print(f"     N={best_combo[2]}  WR={best_combo[3]:.1f}%  PnL=${best_combo[4]:+.1f}  PF={best_combo[5]:.2f}  W/L={best_combo[8]:.2f}")

# ── Detailed analysis of the best filter ──
tw, bl = best_combo[0], best_combo[1]
mask = ~((closed["pnl"] > 0) & (closed["pnl"] <= tw)) & ~((closed["pnl"] < 0) & (closed["pnl"] <= -bl))
filtered = closed[mask]

print(f"\n{'='*80}")
print(f"DETAILED ANALYSIS: TinyWin<${tw:.1f} + BigLoss>${bl:.0f}")
print(f"{'='*80}")

# Per direction
for direction in ["long", "short"]:
    d = filtered[filtered["direction"] == direction]
    if len(d) == 0:
        continue
    w = d[d["pnl"] > 0]["pnl"]
    l = d[d["pnl"] < 0]["pnl"]
    wr = d["is_win"].mean() * 100
    pnl = d["pnl"].sum()
    pf = w.sum() / abs(l.sum()) if len(l) > 0 and l.sum() != 0 else 0
    print(f"\n  {direction.upper()}: N={len(d)}  WR={wr:.1f}%  PnL=${pnl:+.1f}  PF={pf:.2f}")
    print(f"    Avg win=${w.mean():.2f}  Avg loss=${l.mean():.2f}  W/L={abs(w.mean()/l.mean()) if len(l)>0 and l.mean()!=0 else 0:.2f}")
    print(f"    Median dur: {d['duration_min'].median():.1f}m  Avg entries: {d['n_entries'].mean():.1f}")

# Per n_entries
print(f"\n  Per DCA entries:")
for ne in sorted(filtered["n_entries"].unique()):
    sub = filtered[filtered["n_entries"] == ne]
    wr = sub["is_win"].mean() * 100
    pnl = sub["pnl"].sum()
    med_dur = sub["duration_min"].median()
    print(f"    {ne} entries: N={len(sub):>4}  WR={wr:.1f}%  PnL=${pnl:+.1f}  MedDur={med_dur:.0f}m")

# What got removed?
removed_big_losses = closed[(closed["pnl"] < 0) & (closed["pnl"] <= -bl)]
removed_tiny_wins = closed[(closed["pnl"] > 0) & (closed["pnl"] <= tw)]
kept_trades = filtered

print(f"\n  REMOVED: {len(removed_big_losses)} big losses (>${bl}), total ${removed_big_losses['pnl'].sum():+.1f}")
print(f"  REMOVED: {len(removed_tiny_wins)} tiny wins (<${tw}), total ${removed_tiny_wins['pnl'].sum():+.1f}")
print(f"  KEPT: {len(kept_trades)} trades, total ${kept_trades['pnl'].sum():+.1f}")
print(f"  Removed {len(removed_big_losses) + len(removed_tiny_wins)} / {len(closed)} trades ({(len(removed_big_losses) + len(removed_tiny_wins))/len(closed)*100:.1f}%)")

# Top symbols in filtered set
print(f"\n  Top symbols (filtered):")
sym_stats = filtered.groupby("symbol").agg(
    n=("pnl", "count"),
    wr=("is_win", "mean"),
    pnl=("pnl", "sum"),
    avg_dur=("duration_min", "mean"),
).sort_values("n", ascending=False)

for sym, row in sym_stats.head(15).iterrows():
    print(f"    {sym:<12} N={int(row['n']):>4}  WR={row['wr']*100:.1f}%  PnL=${row['pnl']:+.1f}  AvgDur={row['avg_dur']:.0f}m")

# ── Duration analysis ──
print(f"\n  Duration vs Outcome (filtered):")
for label, sub in [("Winners", filtered[filtered["pnl"] > 0]), ("Losers", filtered[filtered["pnl"] < 0])]:
    d = sub["duration_min"]
    print(f"    {label}: mean={d.mean():.1f}m  median={d.median():.1f}m  P25={d.quantile(0.25):.1f}m  P75={d.quantile(0.75):.1f}m")

# ── KEY QUESTION: Is the filtered set profitable? ──
total_pnl = filtered["pnl"].sum()
total_fee = filtered["fee"].sum()
net_pnl = total_pnl - total_fee

print(f"\n  ══ BOTTOM LINE ══")
print(f"  Gross PnL: ${total_pnl:+.1f}")
print(f"  Total fees: ${total_fee:.1f}")
print(f"  Net PnL: ${net_pnl:+.1f}")

if net_pnl > 0:
    print(f"\n  ✅ FILTERED SET IS PROFITABLE — This is a valid training target!")
    print(f"  The trader has edge in ENTRY SELECTION, loses money in EXIT MANAGEMENT.")
else:
    print(f"\n  ❌ Filtered set still negative — need more aggressive filtering or different approach")
