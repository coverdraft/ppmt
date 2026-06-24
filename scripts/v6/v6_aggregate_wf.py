"""Aggregate v6 walk-forward results into a single summary."""
import json
import os
from pathlib import Path

import numpy as np

OUT_DIR = Path('/home/z/my-project/data/v6_models')

windows = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']
results = []
for w in windows:
    p = OUT_DIR / f'v6_{w}_results.json'
    if p.exists():
        results.append(json.loads(p.read_text()))

if not results:
    print("No results found")
    exit(1)

print("=" * 90)
print("v6 WALK-FORWARD SUMMARY (5 windows, 2025-04 to 2025-10)")
print("=" * 90)
print(f"{'window':<10} {'n_train':>9} {'n_test':>7} {'rmse_t':>7} {'mae_t':>7} {'corr_t':>7} {'dir_t':>7} {'top_feat%':>10} {'top_feat':<20}")
print("-" * 90)
corrs = []
dir_accs = []
rmse_better_than_baseline = []
for r in results:
    baseline = r['rmse_mean_baseline']
    diff = r['rmse_test'] - baseline
    print(f"{r['window']:<10} {r['n_train']:>9,} {r['n_test']:>7,} "
          f"{r['rmse_test']:>7.4f} {r['mae_test']:>7.4f} {r['corr_test']:>+7.4f} "
          f"{r['dir_acc_test']:>7.4f} {r['top_feat_pct']*100:>9.1f}% {r['top_feat_name']:<20}")
    corrs.append(r['corr_test'])
    dir_accs.append(r['dir_acc_test'])
    rmse_better_than_baseline.append(diff)

print("-" * 90)
print(f"{'MEAN':<10} {'':>9} {'':>7} {'':>7} {'':>7} {np.mean(corrs):>+7.4f} {np.mean(dir_accs):>7.4f}")
print(f"{'STD':<10} {'':>9} {'':>7} {'':>7} {'':>7} {np.std(corrs):>7.4f} {np.std(dir_accs):>7.4f}")
print()
print(f"Test corr range: {min(corrs):+.4f} to {max(corrs):+.4f}  (mean {np.mean(corrs):+.4f}, std {np.std(corrs):.4f})")
print(f"Dir acc  range: {min(dir_accs):.4f} to {max(dir_accs):.4f}  (mean {np.mean(dir_accs):.4f}, std {np.std(dir_accs):.4f})")
print(f"Dir baseline (always-up) range: 0.467 - 0.493")
print()
print("=== RMSE vs predict-mean baseline ===")
for r, diff in zip(results, rmse_better_than_baseline):
    status = "BEATS" if diff < 0 else "WORSE"
    print(f"  {r['window']}: rmse_test={r['rmse_test']:.4f} - baseline={r['rmse_mean_baseline']:.4f} = {diff:+.4f}  ({status})")
print()
print("=== ANTI-LEAKAGE GUARDS ===")
max_top = max(r['top_feat_pct'] for r in results)
print(f"Guard #3 (no feature >30% of gain):  max top feat = {max_top*100:.1f}%  -> {'PASS' if max_top < 0.30 else 'SUSPICIOUS'}")
max_train_corr = max(r['corr_train'] for r in results)
print(f"Guard #4 (train corr < 0.85):        max train corr = {max_train_corr:+.4f}  -> {'PASS' if max_train_corr < 0.85 else 'ABORT'}")
print(f"Guard #5 (test corr std < 0.05):     std = {np.std(corrs):.4f}  -> {'PASS' if np.std(corrs) < 0.05 else 'UNSTABLE'}")

# Save aggregate
summary = {
    'windows': windows,
    'n_windows': len(results),
    'results': results,
    'mean_test_corr': float(np.mean(corrs)),
    'std_test_corr':  float(np.std(corrs)),
    'mean_dir_acc':   float(np.mean(dir_accs)),
    'std_dir_acc':    float(np.std(dir_accs)),
    'guard_3_max_top_feat_pct': float(max_top),
    'guard_4_max_train_corr':   float(max_train_corr),
    'guard_5_corr_std':         float(np.std(corrs)),
    'all_guards_pass': bool(max_top < 0.30 and max_train_corr < 0.85 and np.std(corrs) < 0.05),
}
with open(OUT_DIR / 'v6_walkforward_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print(f"\nSummary saved: {OUT_DIR / 'v6_walkforward_summary.json'}")
