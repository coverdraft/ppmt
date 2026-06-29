# PPMT — Estado del Proyecto (Jun 2026)

> Documento vivo. Última actualización: **v58d** (commit `e341fe6`).
> Idioma: español (consistencia con el equipo).

---

## 1. Qué es PPMT

PPMT (**Paper Trading Multi-Strategy**) es un motor de trading paper que corre
100% en el navegador (Next.js + Zustand). Genera precios sintéticos con GBM +
regime switching, simula comisiones y slippage, y ejecuta 4 estrategias en
paralelo sobre 10 tokens durante ventanas de 4–6 h.

**Objetivo**: validar por backtest (12 seeds × 10 tokens × 14.400 ticks) que el
sistema es **profitable estable** antes de mandarlo a producción (HF Space 24/7).

---

## 2. Composición actual del motor

### 2.1 Estrategias activas

| Strat | Tipo | Allocation | Tamaño base | RSI/Signal | Estado |
|------|------|-----------|-------------|------------|--------|
| **A** | Momentum 24h | 30% = 3.000 USDT | 0.030 (v58d) | top movers \|chg\| + RSI 25/75 | Activa |
| **B** | Mean Reversion | 25% = 2.500 USDT | 0.15 (v57i) | RSI 30/70 oversold/overbought | Workhorse |
| **C** | Range Breakout | 25% = 2.500 USDT | 0.025 | rolling 60-tick high/low | Pausada (inerte) |
| **D** | Vol Squeeze | 20% = 2.000 USDT | 0.025 | Bollinger squeeze + first move | Inerte (no dispara) |

### 2.2 Gestión de riesgo por posición

| Parámetro | Valor | Origen |
|-----------|-------|--------|
| SL | 1.5 × ATR | v51e (sweet spot; 1.4 ahoga, 1.6 suelta) |
| TP parcial 1 | +0.5R → cierra 5% | v53h |
| TP parcial 2 | +1.0R → cierra 10% | v53h |
| TP parcial 3 | +1.25R → cierra 15% + activa trail | v53h (3-partial system) |
| Lock (BE) | +0.5R → SL a entry+0.35R (lock_offset 0.35) | v51e |
| Trail | 0.30 × ATR (fijo; el dinámico HACE DAÑO) | v49c |
| Cooldown | 30 min post-stop por token | v11 |
| Time stop | 4 h máx hold | v11 |
| ATR floor | 0.58 % (mínimo vol para operar) | v34 |

### 2.3 Adaptación por régimen (v56d → v58d)

```ts
// v56d/v57i/v58d — Adaptive ATR sizing
if (atrPct < 0.6) {           // mercado tranquilo
  size_mult = 0.5;            // Mitad de tamaño
}
// Si ATR ≥ 0.6 → size_mult = 1.0
```

**Por qué funciona**: en régimen calmado el motor pierde más a menudo (señales
débiles). Reducir tamaño a la mitad corta MaxDD de 0.28 % → 0.19 % y sube
Profit% de 58 % → 67 %.

### 2.4 Costes de transacción simulados

- Fee: 0.10 % por trade (entrada + salida)
- Slippage: 0.05 % (modelo simple)
- Total round-trip: ~0.25 % — el motor tiene que superar esto para ser
  profitable.

---

## 3. Stack de versiones (trayectoria)

Cada versión se valida en **12 seeds** (`[42, 1337, 31337, 7, 99, 1234, 7777,
2025, 314, 555, 888, 2024]`). Menos de 12 seeds = overfit (lección v44).

| Version | WR% | P&L / 4h | MaxDD% | Profit% | PF | AvgR | Notas |
|---------|-----|----------|--------|---------|----|------|-------|
| v11 | — | — | — | — | — | — | Tick count bug fix |
| v12 | — | — | — | — | — | — | Strategy overhaul |
| v13–v15 | — | — | — | — | — | — | Filter tuning |
| v16 | — | — | — | — | — | — | REVERTED |
| v31b | — | — | — | — | — | — | Position sizing |
| v37e | — | — | — | — | — | — | Multi-partial foundation |
| v38g | 61.8 % | +13.92 | 0.31 % | 67 % | 1.46 | +0.41 | Baseline pre-v43a |
| v43a | 72.5 % | +13.92 | 0.28 % | 67 % | 1.53 | +0.61 | Multi-partial TP (15/25/60) |
| v49c | 73.1 % | +20.18 | 0.27 % | 67 % | 1.75 | +0.66 | Stricter momentum 0.55 + trail 0.30 |
| v51e | 75.3 % | +23.07 | 0.26 % | 67 % | 1.90 | +0.64 | SL 1.5 + lock_offset 0.35 + 10/20/70 |
| v53h | 79.4 % | +27.00 | 0.28 % | 58 % | 2.04 | +0.77 | **3-partial TP @1.25R + B 0.125** |
| v56d | 79.4 % | +26.76 | 0.17 % | 67 % | 2.53 | +0.77 | **Adaptive ATR sizing (ATR<0.6 → 0.5x)** |
| v57i | 79.4 % | +28.83 | 0.19 % | 67 % | 2.63 | +0.77 | B size 0.15 |
| **v58d** ⭐ | 79.4 % | **+32.12** | 0.21 % | 67 % | **2.85** | +0.77 | **A size 0.030** |

**Proyección v58d**: `+32.12 / 4h × 6 = +192 USDT/día` (en 10.000 USDT paper).

---

## 4. Lecciones aprendidas (qué NO funciona)

Estos caminos se probaron y **se descartaron con evidencia**:

| Idea | Resultado | Veredicto |
|------|-----------|-----------|
| Filtro de tendencia SMA(100) | Reduce P&L | ❌ Descartado |
| SL más ajustado (1.3 ATR) | Reduce P&L | ❌ Descartado |
| ATR floor 0.65 % | Solo 17 % seeds profitable | ❌ Descartado |
| ATR ceiling 2.0 % | Salta trades ganadores | ❌ Descartado |
| Risk gates (max_concurrent, dd_cooldown, reentry) | Inerte o dañino | ❌ Descartado (v46, v55) |
| Session kill switch | Corta ganadores más que perdedores | ❌ Descartado (v56) |
| Trail dinámico basado en R | Peor que trail fijo 0.30 | ❌ Descartado (v53) |
| Trail 0.25 o 0.35 | Peor que 0.30 | ❌ Descartado (v52) |
| Partial2 a +0.8R o +1.2R | Peor que +1.0R | ❌ Descartado (v50) |
| Partial3 a 1.15R o 1.30R | Peor que 1.25R | ❌ Descartado (v58e/f) |
| RSI 28/72 o 32/68 (B) | Peor que 30/70 | ❌ Descartado (v50) |
| RSI 30/70 (A) | Mata performance | ❌ Descartado (v51g) |
| SL 1.45 o 1.6 | Peor que 1.5 | ❌ Descartado (v52) |
| Tiered sizing 0.4/0.7/1.0 | MaxDD baja pero P&L cae mucho | ⚠️ No adoptado (v57h) |
| B 0.20 sin adaptativo | MaxDD >0.30 % | ⚠️ Demasiado arriesgado (v58b) |

---

## 5. Cuello de botella actual

- **WR 79.4 %** está en plateau desde v53h (no baja con cambios menores).
- **4 seeds siempre pierden** (314, 1234, 99, 2025) — parece inherente al
  régimen de mercado que generan, NO a parámetros.
- **Profit% 67 %** (8/12 seeds) es el techo actual; v56d lo rompió desde 58 %
  pero v58d no lo supera.
- **MaxDD 0.21 %** es excelente; queda margen para subir tamaños.

---

## 6. Próximas exploraciones (v59+)

El usuario pide **más ganancia en menos tiempo**. Líneas de trabajo:

### 6.1 Más frecuencia de trades
- **v59a**: Reducir ATR floor a 0.50 % (más trades en mercado calmado)
- **v59b**: Cooldown 20 min en vez de 30 (más re-entradas)
- **v59c**: Strategy D reactivada con parámetros más sensibles

### 6.2 Tamaños agresivos con protección
- **v59d**: B 0.175 con tiered sizing 0.4/0.7/1.0 (max P&L con MaxDD controlado)
- **v59e**: A 0.035 + B 0.175 (ambos suben con adaptativo)
- **v59f**: Pyramiding — añadir 50 % más tamaño si trade va a +1.0R

### 6.3 Sistemas de TP más agresivos
- **v59g**: 4-partial TP (5/10/15/15 + trailing 70)
- **v59h**: Partial3 a 1.10R (más rápido, captura antes)
- **v59i**: Trailing con ATR escalonado por régimen

### 6.4 Nuevas estrategias
- **v59j**: Scalping RSI 5/95 (extremos muy marcados)
- **v59k**: Vol breakout (cuando ATR sube 50 % de golpe, entrar en dirección)
- **v59l**: Grid en rango detectado (cuando ATR < 0.4 %)

### 6.5 Optimización de tiempo
- **v59m**: Tick más rápido (1.0 s en vez de 1.5 s) → más trades por hora
- **v59n**: Warmup reducido (15 min en vez de 30)

---

## 7. Trazabilidad

- **Repo**: https://github.com/coverdraft/ppmt
- **Branch activa**: `terminal-web`
- **Worklog completo**: `worklog.md` (32 tareas documentadas)
- **Scripts backtest**: `scripts/backtest/v38_push_v37e.py` → `v58_push.py`
- **Backups motor**: `src/lib/paper-trading-engine.ts.bak.{v14,v15,v31b,v38g,v43a,v49c,v51e,v53h}`
- **Engine actual**: `src/lib/paper-trading-engine.ts` (v58d, commit `e341fe6`)

Para revertir a cualquier versión:
```bash
cp src/lib/paper-trading-engine.ts.bak.v53h src/lib/paper-trading-engine.ts
```

---

## 8. Cómo reproducir un backtest

```bash
cd /home/z/my-project
python scripts/v58_push.py          # 12 seeds × 11 configs ≈ 4-6 min
# Output: tabla comparativa en stdout + JSON con métricas
```

Para validar un campeón en 12 seeds:
```bash
python scripts/v44_validate.py      # template de validación
```

---

## 9. Próximos commits planeados

1. `feat(v59x): <descripción>` — nuevo campeón si v59 mejora v58d
2. `test(v59): N variantes explorando <idea>` — batch de exploración
3. `docs: actualiza STATUS.md` — este archivo

Regla: **todo campeón se valida en 12 seeds** antes de commit. Menos de 12 =
overfit (probado en v44).
