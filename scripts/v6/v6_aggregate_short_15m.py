"""
v6_aggregate_short_15m.py — Aggregate SHORT-expert v2 15m walk-forward results.
Compare against 5m SHORT-expert v2 to answer: does 15m TF unlock SHORT side?
"""
import json
from pathlib import Path

OUT_15M = Path('/home/z/my-project/data/v6_models/short_expert_v2_15m')
OUT_5M  = Path('/home/z/my-project/data/v6_models/short_expert_v2')
WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']

print("=" * 110)
print("SHORT-EXPERT v2: 15m TF vs 5m TF — walk-forward comparison")
print("=" * 110)
print(f"{'window':<8} | {'5m SHORT':>10} {'5m WR':>7} {'5m PF':>7} | {'15m SHORT@0.30':>16} {'15m WR':>7} {'15m PF':>7} | {'15m SHORT@0.50':>16} {'15m WR':>7} {'15m PF':>7}")
print("-" * 110)

tot_5m = 0
tot_15m_030 = 0
tot_15m_050 = 0
tot_15m_combined = 0  # SHORT@0.50 + LONG@0.30 (sanity check)

for w in WINDOWS:
    with open(OUT_5M / f'v6_short_expert_v2_{w}_results.json') as f:
        r5 = json.load(f)
    with open(OUT_15M / f'v6_short_expert_v2_15m_{w}_results.json') as f:
        r15 = json.load(f)
    s5 = r5['short_thr_030']
    s15_030 = r15['short']['thr_0.30']
    s15_050 = r15['short']['thr_0.50']
    l15_030 = r15['short']['long_thr_0.30']
    tot_5m += s5['tot_dollars']
    tot_15m_030 += s15_030['tot_dollars']
    tot_15m_050 += s15_050['tot_dollars']
    tot_15m_combined += s15_050['tot_dollars'] + l15_030['tot_dollars']
    print(f"{w:<8} | {s5['tot_dollars']:>+10.2f} {s5['wr']:>7.3f} {s5['pf']:>7.2f} | "
          f"{s15_030['tot_dollars']:>+16.2f} {s15_030['wr']:>7.3f} {s15_030['pf']:>7.2f} | "
          f"{s15_050['tot_dollars']:>+16.2f} {s15_050['wr']:>7.3f} {s15_050['pf']:>7.2f}")

print("-" * 110)
print(f"{'TOTAL':<8} | {tot_5m:>+10.2f} {'':>7} {'':>7} | {tot_15m_030:>+16.2f} {'':>7} {'':>7} | {tot_15m_050:>+16.2f} {'':>7} {'':>7}")

print()
print("=" * 80)
print("VERDICT")
print("=" * 80)
delta_030 = tot_15m_030 - tot_5m
delta_050 = tot_15m_050 - tot_5m
print(f"5m SHORT (baseline):                  ${tot_5m:>+10.2f}")
print(f"15m SHORT @ thr=0.30%:                ${tot_15m_030:>+10.2f}  (delta vs 5m: {delta_030:+.2f})")
print(f"15m SHORT @ thr=0.50% (tighter):      ${tot_15m_050:>+10.2f}  (delta vs 5m: {delta_050:+.2f})")
print(f"15m SHORT@0.50 + LONG@0.30 (combined):${tot_15m_combined:>+10.2f}")
print()
if tot_15m_050 > 0:
    print(f"🎉 15m SHORT @ thr=0.50% CROSSES ZERO — first profitable SHORT result!")
elif tot_15m_050 > tot_5m:
    print(f"⚠️  15m SHORT @ thr=0.50% is LESS BAD than 5m (delta {delta_050:+.2f}), but still net negative.")
    print(f"   Consider: tighter threshold, different label (fwd_mae_1), or funding rate feature (Fase 4).")
else:
    print(f"❌ 15m TF doesn't help SHORT — both 5m and 15m are negative.")

# Per-window detail for transparency
print()
print("=" * 80)
print("PER-WINDOW DETAIL (15m @ thr=0.50%)")
print("=" * 80)
print(f"{'window':<10} {'signals':>8} {'WR':>7} {'PF':>7} {'avg_pnl%':>10} {'tot_$':>10} {'rmse':>8} {'corr':>8} {'top_feat':<20}")
for w in WINDOWS:
    with open(OUT_15M / f'v6_short_expert_v2_15m_{w}_results.json') as f:
        r = json.load(f)
    s = r['short']['thr_0.50']
    print(f"{w:<10} {s['n_signals']:>8} {s['wr']:>7.3f} {s['pf']:>7.2f} {s['avg_pnl_pct']:>+10.4f} {s['tot_dollars']:>+10.2f} {r['rmse_test']:>8.4f} {r['corr_test']:>+8.4f} {r['top_feat_name']:<20}")
