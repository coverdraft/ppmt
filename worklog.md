---
Task ID: 1
Agent: main
Task: TAREA 16 — Enhance 1m SAX inputs with volume + candle anatomy (body/wick ratio)

Work Log:
- PASO 1: Added LEVEL_DUAL_ALPHA_TF_OVERRIDES in sax.py — 1m N3/N4/N5 get volume=2
- PASO 1: Updated get_dual_alpha_for_level() with timeframe parameter + n5 support
- PASO 2: Added "body_anatomy" strategy in SAXEncoder._extract_series() — body_score = (close-open)/(high-low)
- PASO 2: Updated ppmt.py to use body_anatomy for 1m N3/N4/N5 price_strategy
- PASO 3: Verified 6^3=216 combinations, ~80 obs/pattern
- PASO 4: Deleted 20 old tries, rebuilt 10 tokens × 1m with new encoding
- PASO 5: OOS DOGE 1m → N3_WR=45.27%, N3_conf=0.3878, Weighted_confidence=0.3671
- Git commit + push: "feat: enhance 1m SAX inputs with volume dimension and candle anatomy (body/wick ratio)"
- Updated TRAZABILIDAD.md with v0.55.0 section

Stage Summary:
- body_anatomy + volume encoding implemented and verified
- OOS result: WR unchanged (~45%), confidence slightly lower due to more patterns (216 vs 27)
- Key finding: body_anatomy improves pattern grouping but doesn't improve aggregate WR
- All 5 ENTREGABLES delivered: diffs, build stats, OOS results, git push, Trazabilidad
