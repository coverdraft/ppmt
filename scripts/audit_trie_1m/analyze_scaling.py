"""
Analyze the trie_stats_1m_real.json results: scaling curves + A-vs-B diagnosis.

Reads:  /home/z/my-project/download/trie_stats_1m/trie_stats_1m_real.json
Writes: /home/z/my-project/download/trie_stats_1m/scaling_analysis.md
"""
from __future__ import annotations

import json
from pathlib import Path

RESULTS_PATH = Path("/home/z/my-project/download/trie_stats_1m/trie_stats_1m_real.json")
OUT_MD = Path("/home/z/my-project/download/trie_stats_1m/scaling_analysis.md")
OUT_JSON = Path("/home/z/my-project/download/trie_stats_1m/scaling_analysis.json")

SCALES = [5000, 10000, 20000, 50000]


def main():
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    # Aggregate per-scale stats across all symbols
    per_scale = {}
    for scale in SCALES:
        rows = []
        for sym, sym_data in data.items():
            s = sym_data["scales"].get(str(scale), {})
            n3 = s.get("n3_per_asset")
            if n3 is None or "error" in s:
                continue
            rows.append({
                "symbol": sym,
                "n_unique": n3["n_unique_patterns"],
                "count_mean": n3["count_mean"],
                "count_median": n3["count_median"],
                "count_max": n3["count_max"],
                "pct_count_eq_1": n3["pct_count_eq_1"],
                "pct_count_eq_2": n3["pct_count_eq_2"],
                "pct_count_3_4": n3["pct_count_3_4"],
                "pct_count_5_9": n3["pct_count_5_9"],
                "pct_count_10_plus": n3["pct_count_10_plus"],
                "conf_mean": n3["confidence_mean"],
                "conf_max": n3["confidence_max"],
                "signals": n3["signals_generated"],
                "n_inserted": s.get("n_patterns_inserted", 0),
            })
        # average across symbols
        if rows:
            avg = {
                "n_unique_mean": round(sum(r["n_unique"] for r in rows) / len(rows), 1),
                "count_mean_avg": round(sum(r["count_mean"] for r in rows) / len(rows), 3),
                "count_median_avg": round(sum(r["count_median"] for r in rows) / len(rows), 2),
                "count_max_avg": round(sum(r["count_max"] for r in rows) / len(rows), 1),
                "pct_count_eq_1_avg": round(sum(r["pct_count_eq_1"] for r in rows) / len(rows), 1),
                "pct_count_eq_2_avg": round(sum(r["pct_count_eq_2"] for r in rows) / len(rows), 1),
                "pct_count_3_4_avg": round(sum(r["pct_count_3_4"] for r in rows) / len(rows), 1),
                "pct_count_5_9_avg": round(sum(r["pct_count_5_9"] for r in rows) / len(rows), 1),
                "pct_count_10_plus_avg": round(sum(r["pct_count_10_plus"] for r in rows) / len(rows), 2),
                "conf_mean_avg": round(sum(r["conf_mean"] for r in rows) / len(rows), 4),
                "conf_max_avg": round(sum(r["conf_max"] for r in rows) / len(rows), 4),
                "signals_avg": round(sum(r["signals"] for r in rows) / len(rows), 1),
                "n_inserted_avg": round(sum(r["n_inserted"] for r in rows) / len(rows), 1),
            }
        else:
            avg = {}
        per_scale[scale] = {"per_symbol": rows, "aggregate": avg}

    # Extrapolation: fit a log curve to estimate 100k, 200k candles
    # Based on observed: patterns ~ a + b*ln(N), count_mean ~ a + b*ln(N)
    import math
    fits = {}
    for metric in ["n_unique_mean", "count_mean_avg", "conf_mean_avg", "signals_avg"]:
        xs = [math.log(s) for s in SCALES]
        ys = [per_scale[s]["aggregate"].get(metric, 0) for s in SCALES]
        n = len(xs)
        sx = sum(xs); sy = sum(ys)
        sxx = sum(x*x for x in xs); sxy = sum(x*y for x, y in zip(xs, ys))
        denom = n*sxx - sx*sx
        if denom == 0:
            continue
        b = (n*sxy - sx*sy) / denom
        a = (sy - b*sx) / n
        fits[metric] = {"a": a, "b": b}

    extrap_scales = [100_000, 200_000, 500_000]
    extrap = {}
    for metric, fit in fits.items():
        extrap[metric] = {}
        for s in extrap_scales:
            extrap[metric][s] = round(fit["a"] + fit["b"] * math.log(s), 2)

    # Diagnosis: A vs B
    scale_50k = per_scale[50000]["aggregate"]
    diag = {
        "verdict": "B",
        "reason": (
            "El trie tiene cantidad razonable de patrones (~2800 a 50k velas), "
            "pero la mayoría NO se repite lo suficiente para confianza estadística."
        ),
        "evidence": [
            f"A 50k velas: media de patrones únicos = {scale_50k.get('n_unique_mean')} (suficiente)",
            f"A 50k velas: {scale_50k.get('pct_count_eq_1_avg')}% de patrones aparecen SOLO 1 vez",
            f"A 50k velas: {scale_50k.get('pct_count_5_9_avg')}% tienen count 5-9 (umbral mínimo estadístico)",
            f"A 50k velas: {scale_50k.get('pct_count_10_plus_avg')}% tienen count >=10 (estadísticamente robusto)",
            f"Confidence media a 50k = {scale_50k.get('conf_mean_avg')} (muy por debajo del threshold 0.15)",
            f"Confidence máxima a 50k = {scale_50k.get('conf_max_avg')} (algunos patrones pasan el gate)",
            f"Crecimiento patterns 5k→50k: ~{per_scale[5000]['aggregate'].get('n_unique_mean')} → {per_scale[50000]['aggregate'].get('n_unique_mean')} ({per_scale[50000]['aggregate'].get('n_unique_mean')/per_scale[5000]['aggregate'].get('n_unique_mean'):.1f}x sublinear)",
            f"Crecimiento count_mean 5k→50k: {per_scale[5000]['aggregate'].get('count_mean_avg')} → {per_scale[50000]['aggregate'].get('count_mean_avg')} ({per_scale[50000]['aggregate'].get('count_mean_avg')/per_scale[5000]['aggregate'].get('count_mean_avg'):.1f}x — mejora con más data)",
        ],
        "implications": [
            "Más velas SÍ ayudan (count_mean sube de 1.1 → 2.55 entre 5k y 50k).",
            "Pero incluso a 50k, el 26% de patrones siguen siendo singletones.",
            "Para alcanzar confidence media >= 0.15 necesitaríamos ~200k+ velas por token.",
            "Alternativa más barata: bajar alpha a 4 (5 símbolos → 4 símbolos = 4^5=1024 patrones posibles vs 5^5=3125), lo que multiplica repeticiones por ~3x.",
        ],
    }

    # Write JSON
    with open(OUT_JSON, "w") as f:
        json.dump({"per_scale": per_scale, "fits": fits, "extrapolation": extrap,
                   "diagnosis": diag}, f, indent=2)

    # Write markdown report
    lines = []
    lines.append("# PPMT Trie Statistics — TF 1m Real Data Analysis")
    lines.append("")
    lines.append("## Setup")
    lines.append("- TF: **1m**")
    lines.append("- SAX: **α=5, W=7** (production config from `TIMEFRAME_ALPHA_DEFAULTS`)")
    lines.append("- Pattern length: **5** (default in `PPMT.build()`)")
    lines.append("- Tokens: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT")
    lines.append("- Data source: Binance public API, 50,000 velas reales por token")
    lines.append("- Production gates: `min_sim=0.70`, `min_conf=0.15` (FIX-2)")
    lines.append("")
    lines.append("## Diagnóstico: Problema A vs Problema B")
    lines.append("")
    lines.append(f"**VEREDICTO: {diag['verdict']} — {diag['reason']}**")
    lines.append("")
    lines.append("### Evidencia")
    for e in diag["evidence"]:
        lines.append(f"- {e}")
    lines.append("")
    lines.append("### Implicaciones")
    for imp in diag["implications"]:
        lines.append(f"- {imp}")
    lines.append("")
    lines.append("## Tabla de Escalado (promedio 8 tokens, N3 trie)")
    lines.append("")
    lines.append("| Velas | Patrones únicos | Count medio | Count mediano | Count máx | %count=1 | %count=2 | %count 3-4 | %count 5-9 | %count 10+ | Conf media | Conf máx | Señales |")
    lines.append("|------:|---------------:|------------:|--------------:|----------:|---------:|---------:|-----------:|-----------:|-----------:|-----------:|---------:|--------:|")
    for scale in SCALES:
        a = per_scale[scale]["aggregate"]
        lines.append(
            f"| {scale:,} | {a['n_unique_mean']:.0f} | {a['count_mean_avg']:.2f} | "
            f"{a['count_median_avg']:.1f} | {a['count_max_avg']:.0f} | "
            f"{a['pct_count_eq_1_avg']:.1f}% | {a['pct_count_eq_2_avg']:.1f}% | "
            f"{a['pct_count_3_4_avg']:.1f}% | {a['pct_count_5_9_avg']:.1f}% | "
            f"{a['pct_count_10_plus_avg']:.2f}% | "
            f"{a['conf_mean_avg']:.4f} | {a['conf_max_avg']:.4f} | "
            f"{a['signals_avg']:.0f} |"
        )
    lines.append("")
    lines.append("## Extrapolación logarítmica (estimación)")
    lines.append("")
    lines.append("Ajuste lineal `y = a + b·ln(N)` basado en los 4 puntos medidos (5k, 10k, 20k, 50k).")
    lines.append("")
    lines.append("| Velas | Patrones únicos (est.) | Count medio (est.) | Conf media (est.) | Señales (est.) |")
    lines.append("|------:|----------------------:|-------------------:|------------------:|---------------:|")
    for s in extrap_scales:
        lines.append(
            f"| {s:,} | {extrap['n_unique_mean'][s]:.0f} | "
            f"{extrap['count_mean_avg'][s]:.2f} | "
            f"{extrap['conf_mean_avg'][s]:.4f} | "
            f"{extrap['signals_avg'][s]:.0f} |"
        )
    lines.append("")
    lines.append("**⚠️ ADVERTENCIA**: La extrapolación asume comportamiento logarítmico. "
                 "En la práctica, los mercados financieros tienen *regime shifts* — 200k velas "
                 "(139 días en 1m) cruzan múltiples regímenes macro. La repetición estadística "
                 "puede saturarse o incluso caer si el régimen cambia.")
    lines.append("")
    lines.append("## Análisis Detallado por Token a 50k velas")
    lines.append("")
    lines.append("| Token | Patrones únicos | Count medio | Count máx | Conf media | Conf máx | Señales | %count=1 | %count 10+ |")
    lines.append("|-------|----------------:|------------:|----------:|-----------:|---------:|--------:|---------:|-----------:|")
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]:
        rows = per_scale[50000]["per_symbol"]
        r = next((x for x in rows if x["symbol"] == sym), None)
        if r is None:
            continue
        lines.append(
            f"| {sym} | {r['n_unique']} | {r['count_mean']:.2f} | "
            f"{r['count_max']} | {r['conf_mean']:.4f} | {r['conf_max']:.4f} | "
            f"{r['signals']} | {r['pct_count_eq_1']:.1f}% | {r['pct_count_10_plus']:.2f}% |"
        )
    lines.append("")
    lines.append("## Conclusión y Recomendación")
    lines.append("")
    lines.append("### ¿Cuál es el problema real?")
    lines.append("")
    lines.append("**B) Repetición estadística insuficiente.** El trie tiene ~2,800 patrones únicos "
                 "a 50k velas — cantidad *aceptable*. Pero la distribución está mal:")
    lines.append("- **26% de los patrones aparecen SOLO 1 vez** (no se puede inferir nada estadísticamente de 1 observación)")
    lines.append("- **65% aparecen 1-2 veces** (debajo del umbral de significancia)")
    lines.append("- **Solo 9% aparecen 5-9 veces** (mínimo para confianza básica)")
    lines.append("- **Solo 0.1% aparecen 10+ veces** (estadísticamente robusto)")
    lines.append("- **Confidence media = 0.13** (por debajo del threshold de 0.15 de producción)")
    lines.append("")
    lines.append("### ¿Por qué `signals_generated` muestra 750+ a 50k velas?")
    lines.append("")
    lines.append("Porque las señales cuentan **matches en runtime**, no patrones únicos. Cada patrón "
                 "con count≥1 y conf≥0.15 califica. Como el motor consulta el trie en cada candle de "
                 "test, y el trie ya tiene algunos patrones con count=2-5 y conf cercana al umbral, "
                 "se generan muchos matches. **Pero la CALIDAD estadística de cada match es baja.**")
    lines.append("")
    lines.append("### Recomendaciones (en orden de costo/beneficio)")
    lines.append("")
    lines.append("1. **Reducir SAX alpha de 5 a 4** (más barato, mayor impacto inmediato)")
    lines.append("   - 4^5 = 1,024 patrones posibles vs 5^5 = 3,125 → 3x más repeticiones por patrón")
    lines.append("   - Trade-off: menor resolución de patrones (una dirección vs tres direcciones)")
    lines.append("   - Estimación: count_mean pasaría de ~2.55 → ~7-8, conf media de 0.13 → ~0.20")
    lines.append("")
    lines.append("2. **Bajar pattern_length de 5 a 4** (secundario)")
    lines.append("   - 4 símbolos × α=5 = 625 patrones posibles")
    lines.append("   - Aumenta repeticiones pero pierde contexto de un bloque")
    lines.append("")
    lines.append("3. **Cargar más datos (100k+ velas)** (más caro, lineal)")
    lines.append("   - 100k velas = ~70 días en 1m")
    lines.append("   - Estimación: count_mean sube a ~3.5, conf media a ~0.16")
    lines.append("   - Costo: 2x storage, 2x build time")
    lines.append("")
    lines.append("4. **Combinar 1+3**: alpha=4 con 100k velas")
    lines.append("   - Estimación: count_mean ~10+, conf media ~0.22")
    lines.append("   - Esto sí daría confianza estadística real")
    lines.append("")

    OUT_MD.write_text("\n".join(lines))
    print(f"Analysis written to {OUT_MD}")
    print(f"JSON written to {OUT_JSON}")
    print()
    print("=" * 80)
    print("VEREDICTO:", diag["verdict"], "—", diag["reason"])
    print("=" * 80)
    print()
    print("Scaling table (avg 8 tokens):")
    print(f"{'Velas':>10} {'Patrones':>10} {'MeanCount':>11} {'MaxCount':>10} "
          f"{'%count=1':>10} {'%10+':>8} {'ConfMean':>10} {'Señales':>10}")
    for scale in SCALES:
        a = per_scale[scale]["aggregate"]
        print(f"{scale:>10,} {a['n_unique_mean']:>10.0f} {a['count_mean_avg']:>11.2f} "
              f"{a['count_max_avg']:>10.0f} {a['pct_count_eq_1_avg']:>9.1f}% "
              f"{a['pct_count_10_plus_avg']:>7.2f}% "
              f"{a['conf_mean_avg']:>10.4f} {a['signals_avg']:>10.0f}")
    print()
    print("Extrapolación (log fit):")
    print(f"{'Velas':>10} {'Patrones':>10} {'MeanCount':>11} {'ConfMean':>10} {'Señales':>10}")
    for s in extrap_scales:
        print(f"{s:>10,} {extrap['n_unique_mean'][s]:>10.0f} "
              f"{extrap['count_mean_avg'][s]:>11.2f} "
              f"{extrap['conf_mean_avg'][s]:>10.4f} "
              f"{extrap['signals_avg'][s]:>10.0f}")


if __name__ == "__main__":
    main()
