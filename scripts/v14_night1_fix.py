#!/usr/bin/env python3
"""
PPMT v14 — Night 1 Fix (basado en análisis de 2 snapshots paralelos)

CONTEXT:
  Tras la noche 1 de operación se analizaron 2 snapshots paralelos:
    Snapshot A: P&L -$38.12, WR 15%, PF 0.24, 20 trades
    Snapshot B: P&L -$13.44, WR 30%, PF 0.41, 20 trades

  Ambos snapshots confirmaron los mismos 3 problemas:
    1. Strategy C (Breakout) con WR 10-20% — pierde siempre
    2. Strategy A (Momentum) hace 0 trades en ambos snapshots
    3. SL demasiado ajustado: 35% cierran en <2 min en snapshot A
    4. LONG WR muy bajo (20% en B vs 40% en SHORT)

  v13 ya hizo mejoras (recent momentum, RSI 40/60, ev_score, CatSL más anchos),
  pero faltaba ajustar SL/TP/cooldown y pausar C.

CAMBIOS v14 (8 cambios en src/lib/paper-trading-engine.ts):

  1. STRATEGY_ALLOCATION:
     A: 3000 → 1000  (bajado: A apenas opera)
     B: 2500 → 4000  (subido: mejor WR)
     C: 2500 → 0     (PAUSADA)
     D: 2000 → 5000  (subido: mejor R/R)

  2. Strategy C PAUSADA — comentario en maybeAutoTrade()
     Ya no se llama this.runStrategyC_Breakout()

  3. SL/TP Strategy A:
     SL  1.5 → 2.0 × ATR  (más aire)
     TP  3   → 2.5 × ATR  (más cercano)
     CatSL 4 → 5 × ATR

  4. SL/TP Strategy B:
     SL  1.5 → 2.0 × ATR
     TP  2   → 2.5 × ATR
     CatSL 3 → 4 × ATR

  5. SL/TP Strategy D:
     SL  1.0 → 1.5 × ATR
     TP  4   → 3 × ATR
     CatSL 3.5 (sin cambio, ya estaba bien)

  6. SL/TP fallback (entradas manuales):
     SL  1.5 → 2.0 × ATR
     TP  3   → 2.5 × ATR

  7. Cooldown post-SL/CatSL:
     30 min → 45 min (evita reentradas prematuras)

  8. Cooldown post-TimeStop:
     30 min → 45 min (mismo criterio)

OBJETIVO:
  - WR: 15-30% → 35-45%
  - PF:  0.24-0.41 → 0.8-1.2
  - SL en <2min: 35% → <15%
  - Strategy C: 100% pérdida → 0 trades (pausada)

ROLLBACK:
  git checkout HEAD~1 -- src/lib/paper-trading-engine.ts

PRUEBA:
  Tras aplicar, dejar correr 24h con una pestaña abierta (caffeinate -d).
  EXPORT snapshot y comparar con night1.
"""

# Este script es documental — los cambios ya están aplicados directamente
# al archivo src/lib/paper-trading-engine.ts en el commit v14.

if __name__ == '__main__':
    print(__doc__)
    print()
    print("✅ Los cambios v14 ya están aplicados al engine.")
    print("   Este script es solo documental — no necesita ejecutarse.")
    print()
    print("Para verificar:")
    print("  grep -n 'v14 NIGHT1 FIX' src/lib/paper-trading-engine.ts")
    print()
    print("Para rollback:")
    print("  git checkout HEAD~1 -- src/lib/paper-trading-engine.ts")
