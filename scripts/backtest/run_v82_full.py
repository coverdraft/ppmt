#!/usr/bin/env python3
"""
Runner unificado v82: ejecuta v82a y v82c secuencialmente, guarda progreso,
y al final produce el reporte comparativo vs v81.

Uso:
  python run_v82_full.py                 # corre ambos (96 runs cada uno)
  python run_v82_full.py report          # solo imprime el reporte
  python run_v82_full.py v82a            # solo v82a
  python run_v82_full.py v82c            # solo v82c
"""
import sys, os, json, time, traceback
sys.path.insert(0, '/home/z/my-project/scripts')
sys.path.insert(0, '/home/z/my-project/scripts/backtest')

from v80_direction_token_test import PROFILES, SEEDS


def run_variant(module_name, results_file, force_restart=False):
    """Ejecutar 12 seeds × 8 perfiles = 96 runs de una variante."""
    if force_restart and os.path.exists(results_file):
        os.remove(results_file)

    if module_name == 'v82a':
        from v82a_universal_v3 import run_seed_profile, EngineSimV82a  # noqa: F401
    elif module_name == 'v82c':
        from v82c_rr_fix import run_seed_profile  # noqa: F401
    elif module_name == 'v82b':
        from v82b_rr_fix import run_seed_profile  # noqa: F401
    else:
        raise ValueError(f"Unknown variant: {module_name}")

    all_results = {}
    if os.path.exists(results_file):
        with open(results_file) as f:
            all_results = json.load(f)

    n_existing = sum(len(v) for v in all_results.values())
    print(f"\n{'='*70}")
    print(f"  {module_name}: {n_existing}/96 runs ya hechos, continuando...")
    print(f"{'='*70}\n", flush=True)

    start_total = time.time()
    n_done = 0
    for seed in SEEDS:
        for profile in PROFILES:
            key = str(seed)
            if key in all_results and profile in all_results[key]:
                continue
            try:
                t0 = time.time()
                result = run_seed_profile(seed, profile)
                elapsed = time.time() - t0
                all_results.setdefault(key, {})[profile] = result
                n_done += 1
                print(f"  [{n_existing + n_done:2d}/96] {module_name} seed {seed} × {profile:<10} — P&L {result['pnl']:+8.2f}, WR {result['wr']:5.1f}%, avgR {result['avg_r']:+.3f}, trades {result['trades']:3d}  ({elapsed:.1f}s)", flush=True)
                # Guardar cada 5 runs
                if n_done % 5 == 0:
                    with open(results_file, 'w') as f:
                        json.dump(all_results, f, indent=2)
            except Exception as e:
                print(f"  ❌ ERROR seed {seed} × {profile}: {e}", flush=True)
                traceback.print_exc()
                # Continuar con el siguiente

    with open(results_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    total_elapsed = time.time() - start_total
    print(f"\n  {module_name} COMPLETADO en {total_elapsed:.0f}s — {n_done} runs nuevos, guardado en {results_file}", flush=True)
    return all_results


def report():
    """Imprimir reporte comparativo v81 vs v82a vs v82b vs v82c."""
    import statistics

    variants = [
        ('v81', '/tmp/v81_universal.json'),
        ('v82a', '/tmp/v82a_universal.json'),
        ('v82b', '/tmp/v82b_universal.json'),
        ('v82c', '/tmp/v82c_universal.json'),
        ('v82d', '/tmp/v82d_universal.json'),
    ]

    data = {}
    for name, path in variants:
        if os.path.exists(path):
            with open(path) as f:
                data[name] = json.load(f)

    if 'v81' not in data:
        print("ERROR: falta v81 baseline en /tmp/v81_universal.json")
        return

    print("\n" + "=" * 200)
    print("REPORTE COMPLETIVO v81 vs v82a vs v82b vs v82c — 12 seeds × 8 perfiles = 96 runs c/u")
    print("=" * 200)
    print(f"{'Profile':<10} {'Ver':<6} {'Trades':<7} {'WR%':<7} {'avgR':<7} {'P&L':<11} {'PF':<6} {'MaxDD%':<7} {'Profit%':<8} {'L WR%':<7} {'S WR%':<7} {'PASS?':<10}")
    print("-" * 200)

    summary = []
    for profile in PROFILES:
        row = {'profile': profile}
        for name in ['v81', 'v82a', 'v82b', 'v82c', 'v82d']:
            if name not in data:
                continue
            seeds = [r for r in data[name].values() if profile in r]
            if not seeds:
                continue
            ms = [r[profile] for r in seeds]
            trades = statistics.mean(m['trades'] for m in ms)
            wr = statistics.mean(m['wr'] for m in ms)
            avgr = statistics.mean(m['avg_r'] for m in ms)
            pnl = statistics.mean(m['pnl'] for m in ms)
            pf = statistics.mean(m['pf'] for m in ms)
            maxdd = statistics.mean(m['max_dd'] for m in ms)
            profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            wr_l = statistics.mean(m.get('wr_long', 0) for m in ms)
            wr_s = statistics.mean(m.get('wr_short', 0) for m in ms)
            wr_ok = wr >= 64
            rr_ok = avgr >= 1.8
            both = wr_ok and rr_ok
            verdict = "✅ PASS" if both else "❌"
            row[name] = {'trades': trades, 'wr': wr, 'avgr': avgr, 'pnl': pnl, 'pf': pf,
                         'maxdd': maxdd, 'profit': profit, 'wr_l': wr_l, 'wr_s': wr_s,
                         'pass': both}
            print(f"{profile:<10} {name:<6} {trades:<7.0f} {wr:<7.1f} {avgr:<7.3f} {pnl:<+11.2f} {pf:<6.2f} {maxdd:<7.2f} {profit:<8.0f} {wr_l:<7.1f} {wr_s:<7.1f} {verdict}")
        print("-" * 200)
        summary.append(row)

    # Tabla resumen: gate por variante
    print("\n" + "=" * 100)
    print("QUALITY GATE — WR ≥ 64% AND RR ≥ 1.8")
    print("=" * 100)
    print(f"{'Variant':<10} {'PASS/8':<10} {'WR avg':<10} {'RR avg':<10} {'PASS profiles':<60}")
    print("-" * 100)
    for name in ['v81', 'v82a', 'v82b', 'v82c', 'v82d']:
        if name not in data:
            continue
        pass_profiles = []
        wrs, rrs = [], []
        for row in summary:
            if name in row:
                wrs.append(row[name]['wr'])
                rrs.append(row[name]['avgr'])
                if row[name]['pass']:
                    pass_profiles.append(row['profile'])
        pass_count = len(pass_profiles)
        wr_avg = statistics.mean(wrs) if wrs else 0
        rr_avg = statistics.mean(rrs) if rrs else 0
        marker = "✅" if pass_count >= 6 else "❌"
        print(f"{name:<10} {marker} {pass_count}/8   {wr_avg:<10.1f} {rr_avg:<10.3f} {', '.join(pass_profiles)}")

    # Guardar resumen
    with open('/tmp/v82_full_report.json', 'w') as f:
        # Serializar de forma segura
        safe = []
        for row in summary:
            r = {'profile': row['profile']}
            for k, v in row.items():
                if k != 'profile' and isinstance(v, dict):
                    r[k] = {kk: (vv if not isinstance(vv, bool) else int(vv)) for kk, vv in v.items()}
            safe.append(r)
        json.dump(safe, f, indent=2)
    print(f"\nReporte guardado en /tmp/v82_full_report.json")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'report':
        report()
    elif len(sys.argv) > 1 and sys.argv[1] == 'v82a':
        run_variant('v82a', '/tmp/v82a_universal.json')
        report()
    elif len(sys.argv) > 1 and sys.argv[1] == 'v82b':
        run_variant('v82b', '/tmp/v82b_universal.json')
        report()
    elif len(sys.argv) > 1 and sys.argv[1] == 'v82c':
        run_variant('v82c', '/tmp/v82c_universal.json')
        report()
    else:
        # Full run
        run_variant('v82a', '/tmp/v82a_universal.json')
        run_variant('v82c', '/tmp/v82c_universal.json')
        report()
