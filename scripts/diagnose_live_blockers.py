#!/usr/bin/env python3
"""
v0.38.4: Diagnóstico COMPLETO de por qué no se ejecutan trades en el dashboard.

El script diagnose_signal_flow.py SOLO prueba can_open() en aislamiento,
pero hay MÁS bloqueos en el camino que ese script no ve:

  Bloqueo 1 — VALIDATION GATE (server.py:951)
    El live trader NUNCA ARRANCA si la validación guardada no es "PASS".

  Bloqueo 2 — SKIP FILTERS en realtime.py:970-1033 (run_live)
    Aunque el trader arranque, hay 6 filtros de skip ANTES de que
    la señal llegue a can_open().

  Bloqueo 3 — Token sin datos / sin trie.

Uso:
  python3 scripts/diagnose_live_blockers.py                       # lista TODOS los tokens
  python3 scripts/diagnose_live_blockers.py BTC/USDT 1h           # diagnostica un token
"""
import sys
import os
import sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'src'))


def find_db():
    candidates = [
        os.path.expanduser("~/.ppmt/ppmt.db"),
        "/home/z/.ppmt/ppmt.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def section(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


def check_validation_gate(db_path, symbol=None, timeframe=None):
    section("[BLOQUEO 1] Validation Gate — ¿el trader arrancaría?")
    print("""
En server.py:_run_one_token() línea 951:
  if latest_val is None or latest_val.get("verdict") != "PASS":
      verdict = await validate_token(...)
      if verdict != "PASS":
          sess["status"] = "VALIDATION_FAILED"
          return  # EL TRADER NUNCA ARRANCA
""")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='validations'")
    if not cur.fetchone():
        print("  WARN: Tabla 'validations' NO existe — nunca se ha corrido ninguna validacion.")
        conn.close()
        return None

    if symbol and timeframe:
        cur.execute("""
            SELECT symbol, timeframe, verdict, win_rate, profit_factor,
                   risk_of_ruin, total_trades, backtest_pnl_pct, mc_verdict, created_at
            FROM validations
            WHERE symbol = ? AND timeframe = ?
            ORDER BY created_at DESC LIMIT 5
        """, (symbol, timeframe))
        rows = cur.fetchall()
        if not rows:
            print(f"  X  NO HAY validaciones guardadas para {symbol} {timeframe}")
            print(f"     -> Al iniciar multi-session, el trader intentara validar inline.")
            print(f"     -> Si la validacion inline FALLA, status='VALIDATION_FAILED' y no arranca.")
            conn.close()
            return "NO_VALIDATION"

        latest = rows[0]
        sym, tf, verdict, wr, pf, ror, trades, pnl, mc_v, ts = latest
        print(f"  Ultima validacion para {symbol} {timeframe}:")
        print(f"    Verdict:       {verdict}")
        if wr is not None:
            print(f"    Win rate:      {wr*100:.1f}%")
        if pf is not None:
            print(f"    Profit factor: {pf:.2f}")
        if ror is not None:
            print(f"    Risk of ruin:  {ror*100:.2f}%")
        print(f"    Trades:        {trades}")
        print(f"    PnL:           {pnl:+.2f}%")
        print(f"    MC verdict:    {mc_v}")
        print(f"    Fecha:         {ts}")

        if verdict == "PASS":
            print(f"\n  OK  PASS -> El trader ARRANCARIA para este token.")
            print(f"     Si aun ves Trades=0, el problema esta en Bloqueo 2 (skip filters).")
        elif verdict == "FAIL":
            print(f"\n  X  FAIL -> El trader NUNCA ARRANCA para este token.")
            wr_str = f"{wr*100:.1f}%" if wr else "N/A"
            pf_str = f"{pf:.2f}" if pf else "N/A"
            print(f"     Causa: win_rate={wr_str} (req >40%), PF={pf_str} (req >0.8).")
            print(f"     Esto explica por que ves Signals>0 pero Trades=0.")
        elif verdict == "INSUFFICIENT_DATA":
            print(f"\n  X  INSUFFICIENT_DATA -> El trader NUNCA ARRANCA.")
            print(f"     Causa: backtest produjo solo {trades} trades (req >=5).")
            print(f"     Los skip filters del Bloqueo 2 estan rechazando casi todo.")
        else:
            print(f"\n  ?  Verdict desconocido: {verdict}")

        if len(rows) > 1:
            print(f"\n  Validaciones anteriores:")
            for r in rows[1:]:
                wr_prev = f"{r[3]*100:.1f}%" if r[3] else "N/A"
                print(f"    {r[9]} - {r[2]} (WR={wr_prev}, PF={r[4]}, trades={r[6]})")

        conn.close()
        return verdict

    else:
        cur.execute("""
            SELECT v.symbol, v.timeframe, v.verdict, v.win_rate, v.profit_factor,
                   v.total_trades, v.created_at
            FROM validations v
            INNER JOIN (
                SELECT symbol, timeframe, MAX(created_at) as max_ts
                FROM validations
                GROUP BY symbol, timeframe
            ) latest ON v.symbol = latest.symbol
                    AND v.timeframe = latest.timeframe
                    AND v.created_at = latest.max_ts
            ORDER BY v.verdict, v.symbol
        """)
        rows = cur.fetchall()
        if not rows:
            print("  WARN: No hay validaciones guardadas en la DB.")
            conn.close()
            return None

        from collections import defaultdict
        by_verdict = defaultdict(list)
        for r in rows:
            by_verdict[r[2]].append(r)

        for verdict, items in sorted(by_verdict.items()):
            print(f"\n  {verdict} ({len(items)} tokens):")
            for sym, tf, v, wr, pf, tr, ts in items[:50]:
                wr_str = f"{wr*100:5.1f}%" if wr else "  N/A"
                pf_str = f"{pf:4.2f}" if pf else "  N/A"
                print(f"    {sym:14s} {tf:4s}  WR={wr_str}  PF={pf_str}  trades={tr}")

        total = len(rows)
        pass_count = len(by_verdict.get("PASS", []))
        fail_count = len(by_verdict.get("FAIL", []))
        insuf_count = len(by_verdict.get("INSUFFICIENT_DATA", []))
        print(f"\n  RESUMEN: {total} tokens validados")
        print(f"    PASS:                {pass_count}  ({pass_count*100//total if total else 0}%) -> trader arrancaria")
        print(f"    FAIL:                {fail_count}  ({fail_count*100//total if total else 0}%) -> trader NO arrancaria")
        print(f"    INSUFFICIENT_DATA:   {insuf_count}  ({insuf_count*100//total if total else 0}%) -> trader NO arrancaria")

        conn.close()
        return by_verdict


def check_skip_filters(symbol, timeframe):
    section("[BLOQUEO 2] Skip Filters en run_live() — realtime.py:970-1033")

    try:
        from ppmt.data.storage import PPMTStorage
        from ppmt.core.sax import SAXEncoder
        from ppmt.engine.prediction import PredictionEngine
        from ppmt.core.profiles import TokenProfile
    except ImportError as e:
        print(f"  X  No se pudo importar ppmt: {e}")
        print(f"     Ejecuta desde el directorio raiz del repo: cd ~/ppmt && python3 scripts/diagnose_live_blockers.py")
        return

    storage = PPMTStorage()

    all_tries = storage.load_all_tries(symbol)
    trie = all_tries.get("n3")
    if trie is None:
        print(f"  X  No hay trie N3 para {symbol}. Corre 'ppmt ingest -s {symbol} -t {timeframe}' primero.")
        storage.close()
        return

    print(f"  OK Trie N3 cargado: {trie.pattern_count} patrones")

    df = storage.load_ohlcv(symbol, timeframe)
    if df is None or df.empty:
        print(f"  X  No hay datos para {symbol} {timeframe}")
        storage.close()
        return

    print(f"  OK {len(df)} candles cargadas (rango: {df.index[0]} -> {df.index[-1]})")

    try:
        profile = TokenProfile.from_storage(storage, symbol, timeframe)
        if profile is None:
            profile = TokenProfile.calibrate_from_data(df, symbol=symbol)
        print(f"  TokenProfile: alpha={profile.alpha}, window={profile.window}")
    except Exception as e:
        print(f"  WARN: TokenProfile fallo ({e}), usando defaults alpha=3 window=7")
        class _P:
            alpha = 3
            window = 7
        profile = _P()

    sax = SAXEncoder(alphabet_size=profile.alpha, window_size=profile.window)

    try:
        symbols, paa_mean, paa_std = sax.encode_with_normalization(df)
        if len(symbols) < 5:
            print(f"  X  SAX solo produjo {len(symbols)} simbolos")
            storage.close()
            return
        current_symbols = list(symbols[-5:])
        print(f"  OK Pattern actual: {''.join(current_symbols)}")
    except Exception as e:
        print(f"  X  Error generando SAX: {e}")
        storage.close()
        return

    pred_engine = PredictionEngine(trie, prediction_depth=5)
    try:
        prediction = pred_engine.predict(current_symbols)
    except Exception as e:
        print(f"  X  Error en predict(): {e}")
        storage.close()
        return

    print(f"\n  Prediccion:")
    print(f"    Direction:         {prediction.direction}")
    print(f"    Confidence:        {prediction.confidence:.4f}")
    print(f"    Overall prob:      {prediction.overall_probability:.4f}")
    print(f"    Expected move:     {prediction.expected_total_move_pct:+.4f}%")

    try:
        from ppmt.core.regime import RegimeClassifier
        regime_info = RegimeClassifier().classify(df.tail(100))
        current_regime = regime_info.regime
        print(f"    Regime:            {current_regime}")
    except Exception as e:
        print(f"    WARN: No se pudo clasificar regime: {e}")
        current_regime = "unknown"

    print(f"\n  Simulando skip filters (validation_mode=True, paper trading):")

    base_prob_gate = 0.30
    ranging_prob_gate = 0.40
    volatile_prob_gate = 0.45
    counter_trend_gate = 0.45
    move_threshold = 0.50

    blockers = []

    if prediction.overall_probability < base_prob_gate:
        blockers.append(
            f"a) prob={prediction.overall_probability:.2f} < {base_prob_gate} gate (base_prob_gate)"
        )
    else:
        print(f"  OK a) prob {prediction.overall_probability:.2f} >= {base_prob_gate} (base_prob_gate)")

    if abs(prediction.expected_total_move_pct) < 0.5:
        blockers.append(
            f"b) move={prediction.expected_total_move_pct:.2f}% < 0.5% (move_threshold)"
        )
    else:
        print(f"  OK b) move {prediction.expected_total_move_pct:.2f}% >= 0.5%")

    if current_regime == "ranging":
        if prediction.overall_probability < ranging_prob_gate:
            blockers.append(
                f"c) ranging prob={prediction.overall_probability:.2f} < {ranging_prob_gate}"
            )
        else:
            print(f"  OK c) ranging prob OK")
        if abs(prediction.expected_total_move_pct) < 0.80:
            blockers.append(
                f"d) ranging move={prediction.expected_total_move_pct:.2f}% < 0.80%"
            )
        else:
            print(f"  OK d) ranging move OK")
    elif current_regime == "volatile":
        if prediction.overall_probability < volatile_prob_gate:
            blockers.append(
                f"e) volatile prob={prediction.overall_probability:.2f} < {volatile_prob_gate}"
            )
        else:
            print(f"  OK e) volatile prob OK")
        if abs(prediction.expected_total_move_pct) < 1.20:
            blockers.append(
                f"f) volatile move={prediction.expected_total_move_pct:.2f}% < 1.20%"
            )
        else:
            print(f"  OK f) volatile move OK")
    elif current_regime == "trending_down" and prediction.direction == "LONG":
        if prediction.overall_probability < counter_trend_gate:
            blockers.append(
                f"g) counter-trend LONG in downtrend prob={prediction.overall_probability:.2f} < {counter_trend_gate}"
            )
        else:
            print(f"  OK g) counter-trend LONG OK")
    elif current_regime == "trending_up" and prediction.direction == "SHORT":
        if prediction.overall_probability < counter_trend_gate:
            blockers.append(
                f"h) counter-trend SHORT in uptrend prob={prediction.overall_probability:.2f} < {counter_trend_gate}"
            )
        else:
            print(f"  OK h) counter-trend SHORT OK")
    else:
        print(f"  OK c-h) Regime={current_regime} no aplica filtros extra")

    boosted_confidence = prediction.confidence
    boost_prob_trigger = 0.40
    boost_move_trigger = 0.80
    if (prediction.overall_probability >= boost_prob_trigger
            and abs(prediction.expected_total_move_pct) >= boost_move_trigger):
        boosted_confidence = max(
            prediction.confidence,
            prediction.confidence * (1 + prediction.overall_probability),
        )

    effective_min_conf = 0.08
    if boosted_confidence < effective_min_conf:
        blockers.append(
            f"i) boosted_confidence={boosted_confidence:.4f} < effective_min_conf={effective_min_conf}"
        )
    else:
        print(f"  OK i) boosted_confidence {boosted_confidence:.4f} >= {effective_min_conf}")

    if (prediction.direction != "FLAT"
            and boosted_confidence >= effective_min_conf
            and abs(prediction.expected_total_move_pct) > move_threshold
            and prediction.overall_probability > 0.30):
        print(f"  OK j) Entry gate: la señal PASARIA todos los filtros")
    else:
        blockers.append(
            f"j) Entry gate final: dir={prediction.direction}, "
            f"boosted_conf={boosted_confidence:.4f}, "
            f"move={abs(prediction.expected_total_move_pct):.2f}%, "
            f"prob={prediction.overall_probability:.2f}"
        )

    if blockers:
        print(f"\n  X  BLOQUEOS DETECTADOS ({len(blockers)}):")
        for b in blockers:
            print(f"     -> {b}")
    else:
        print(f"\n  OK NINGUN skip filter bloquea - la señal pasaria al risk manager.")

    storage.close()


def check_data_availability(symbol, timeframe):
    section("[BLOQUEO 3] Datos & Trie disponibles")

    try:
        from ppmt.data.storage import PPMTStorage
    except ImportError as e:
        print(f"  X  No se pudo importar ppmt: {e}")
        print(f"     Ejecuta desde el directorio raiz del repo: cd ~/ppmt && python3 scripts/diagnose_live_blockers.py")
        return False

    storage = PPMTStorage()

    df = storage.load_ohlcv(symbol, timeframe)
    if df is None or df.empty:
        print(f"  X  No hay candles para {symbol} {timeframe}")
        print(f"     Posibles causas:")
        print(f"       - El simbolo no existe en el exchange configurado")
        print(f"       - Nunca se hizo 'ppmt ingest -s {symbol}'")
        print(f"       - PHA/USDT por ejemplo NO esta en Binance, solo en MEXC")
        storage.close()
        return False

    print(f"  OK {len(df)} candles para {symbol} {timeframe}")
    print(f"     Rango: {df.index[0]} -> {df.index[-1]}")

    all_tries = storage.load_all_tries(symbol)
    n3 = all_tries.get("n3")
    if n3 is None:
        print(f"  X  No hay trie N3 para {symbol}")
        print(f"     Corre: ppmt build -s {symbol}")
        storage.close()
        return False
    print(f"  OK Trie N3: {n3.pattern_count} patrones")

    storage.close()
    return True


def main():
    if len(sys.argv) >= 3:
        symbol = sys.argv[1]
        timeframe = sys.argv[2]
    else:
        symbol = None
        timeframe = None

    db_path = find_db()
    if not db_path:
        print("X  No encuentro la DB de PPMT en ~/.ppmt/ppmt.db")
        print("   Estás corriendo esto en la máquina donde está PPMT instalado?")
        sys.exit(1)

    print(f"DB: {db_path}")

    if symbol and timeframe:
        if check_data_availability(symbol, timeframe):
            check_validation_gate(db_path, symbol, timeframe)
            check_skip_filters(symbol, timeframe)
        else:
            check_validation_gate(db_path, symbol, timeframe)
    else:
        check_validation_gate(db_path)

        print(f"\n{'='*72}")
        print(f"  Para diagnosticar un token especifico, ejecuta:")
        print(f"    python3 scripts/diagnose_live_blockers.py SYMBOL TIMEFRAME")
        print(f"  Ejemplos:")
        print(f"    python3 scripts/diagnose_live_blockers.py BTC/USDT 1h")
        print(f"    python3 scripts/diagnose_live_blockers.py WLD/USDT 5m")
        print(f"{'='*72}")


if __name__ == "__main__":
    main()
