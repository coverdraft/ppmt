#!/usr/bin/env python3
"""Replace the backtest section in v2_server.py with the new implementation."""
import re

filepath = "/home/z/my-project/ppmt/src/ppmt/terminal/v2_server.py"

with open(filepath, "r") as f:
    content = f.read()

# Find the start and end markers
start_marker = "# ─── REST: Backtest ──────────────────────────────────────────────"
end_marker = "# ─── WebSocket: Paper Live ────────────────────────────────────"

start_idx = content.index(start_marker)
end_idx = content.index(end_marker)

print(f"Found backtest section: {start_idx} to {end_idx}")
print(f"Section length: {end_idx - start_idx} chars")

new_section = '''# ─── REST: Backtest ──────────────────────────────────────────────
# v2.1: Run a quick OOS backtest on historical data using the real
# PPMT engine with Config F. Results stream via WebSocket IN REAL-TIME.
#
# v2.1-fix: Rewritten to use EXACTLY the same logic as
# full_replay_v21.py — builds tries from IS data, uses the same
# replay loop, and streams results via WS.

class BacktestPayload(BaseModel):
    symbol: str = "SOL/USDT"
    timeframe: str = "5m"
    days: int = 7


# Track active backtest WebSocket connections so the endpoint can
# stream results to the calling client.
_BACKTEST_WS: dict[str, WebSocket] = {}  # key: "SOL/USDT:5m"

# Track running backtests to prevent duplicates
_BACKTEST_RUNNING: set[str] = set()  # keys like "SOL/USDT:5m"


@app.post("/api/backtest")
async def run_backtest(payload: BacktestPayload):
    """Start a backtest for the given symbol/timeframe/days.

    Runs in a background thread. Results are streamed to the client
    in real-time via the same WebSocket connection the client has open.
    """
    symbol = payload.symbol
    timeframe = payload.timeframe
    days = payload.days

    logger.info(f"[BACKTEST] POST received: {symbol} {timeframe} {days}d")

    if timeframe not in ("5m", "15m"):
        return {"status": "error", "message": f"Timeframe {timeframe} not supported for backtest. Use 5m or 15m."}

    if days < 1 or days > 30:
        return {"status": "error", "message": f"Days must be 1-30, got {days}"}

    # Prevent duplicate backtests for the same symbol:tf
    bt_key = f"{symbol}:{timeframe}"
    if bt_key in _BACKTEST_RUNNING:
        return {"status": "error", "message": f"Backtest already running for {symbol} {timeframe}"}

    # Check if WS client is registered
    ws = _BACKTEST_WS.get(bt_key)
    if not ws:
        logger.warning(f"[BACKTEST] No WS client registered for {bt_key}")
        return {"status": "error", "message": f"No WebSocket connection for {symbol} {timeframe}. Connect to the terminal first."}

    logger.info(f"[BACKTEST] Starting: {symbol} {timeframe} {days}d (WS client found)")

    asyncio.create_task(_run_backtest_async(symbol, timeframe, days))

    return {"status": "started", "symbol": symbol, "timeframe": timeframe, "days": days}


async def _run_backtest_async(symbol: str, timeframe: str, days: int):
    """Run the backtest in a thread and stream results via WebSocket in real-time."""
    bt_key = f"{symbol}:{timeframe}"
    _BACKTEST_RUNNING.add(bt_key)
    msg_queue: queue.Queue = queue.Queue()

    bt_thread = threading.Thread(
        target=_backtest_sync,
        args=(symbol, timeframe, days, msg_queue),
        daemon=True,
    )
    bt_thread.start()
    logger.info(f"[BACKTEST] Thread started for {bt_key}")

    try:
        # Drain the queue and send messages in real-time via WS
        got_complete = False
        while True:
            # Thread finished AND queue empty → done
            if not bt_thread.is_alive() and msg_queue.empty():
                break

            try:
                msg = msg_queue.get(timeout=0.3)
            except queue.Empty:
                await asyncio.sleep(0.05)  # Yield to event loop
                continue

            # Send the message via WS
            ws = _BACKTEST_WS.get(bt_key)
            if ws:
                try:
                    await ws.send_json(msg)
                except Exception as e:
                    logger.error(f"[BACKTEST] WS send failed: {e}")
                    _BACKTEST_WS.pop(bt_key, None)
                    break
            else:
                logger.warning(f"[BACKTEST] WS client gone for {bt_key}, stopping stream")
                break

            # If this was the completion message, we're done
            if msg.get("type") == "backtest_complete":
                got_complete = True
                break

        # If thread died without sending backtest_complete, send error
        if not got_complete:
            logger.error(f"[BACKTEST] Thread ended without sending complete for {bt_key}")
            ws = _BACKTEST_WS.get(bt_key)
            if ws:
                try:
                    await ws.send_json({"type": "backtest_complete", "data": {
                        "error": "Backtest thread crashed (no completion message)",
                        "trades": 0, "wins": 0, "losses": 0,
                        "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
                    }})
                except Exception:
                    _BACKTEST_WS.pop(bt_key, None)

    except Exception as e:
        logger.error(f"[BACKTEST] Async streamer failed: {e}", exc_info=True)
        ws = _BACKTEST_WS.get(bt_key)
        if ws:
            try:
                await ws.send_json({"type": "backtest_complete", "data": {
                    "error": str(e), "trades": 0, "wins": 0, "losses": 0,
                    "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
                }})
            except Exception:
                _BACKTEST_WS.pop(bt_key, None)
    finally:
        _BACKTEST_RUNNING.discard(bt_key)
        logger.info(f"[BACKTEST] Finished: {bt_key}")


def _backtest_sync(symbol: str, timeframe: str, days: int, msg_queue: queue.Queue) -> None:
    """Synchronous backtest logic — runs in a thread.

    This function replicates EXACTLY the same logic as full_replay_v21.py:
    - Loads IS + OOS data (DB first, Binance REST fallback)
    - Builds tries from IS data with Config F alpha=3
    - Runs OOS replay with Config F parameters
    - Streams results via msg_queue

    CRITICAL: ALL SQLite connections must be created INSIDE this function
    because SQLite objects can only be used in the thread that created them.
    """
    import copy
    import requests as _requests
    from ppmt.data.classifier import AssetClassifier
    from ppmt.data.storage import PPMTStorage as _PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
    from ppmt.core.regime import RegimeDetector
    from ppmt.core.profiles import SPREAD_ESTIMATES
    from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
    from ppmt.engine.weights import AdaptiveWeights
    from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES

    def _send(msg_type: str, data: dict):
        msg_queue.put({"type": msg_type, "data": data})

    # Config F parameters (EXACTLY matching full_replay_v21.py Config F)
    EV_THRESHOLD = 0.40
    SL_MULT = 2.0
    ALPHA_N3_N4 = 3  # Config F uses alpha=3
    CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
    HARD_MOVE_FLOOR = 0.10  # Config F: 0.10% for 5m
    CAPITAL_USDT = 1000.0
    RISK_PCT = 0.01
    IS_DAYS = 60  # In-sample period for trie building
    REGIME_WINDOW_SIZE = 10

    try:
        logger.info(f"[BACKTEST] _backtest_sync started: {symbol} {timeframe} {days}d")

        # 1. Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(symbol)
        asset_class = info.asset_class
        weight_profile = info.weight_profile
        logger.info(f"[BACKTEST] Classified: {symbol} → {asset_class}/{weight_profile}")

        # 2. Load data — try DB first, fall back to Binance download
        storage = _PPMTStorage()  # NEW instance in this thread (SQLite safe)
        total_days = IS_DAYS + days
        df = None

        # Try loading from DB
        try:
            db_df = storage.load_ohlcv(symbol, timeframe)
            if db_df is not None and len(db_df) > 1000:
                logger.info(f"[BACKTEST] DB has {len(db_df)} candles for {symbol} {timeframe}")
                df = db_df
        except Exception as e:
            logger.warning(f"[BACKTEST] DB load failed: {e}")

        # Fall back to Binance REST API (direct, like full_replay_v21.py)
        if df is None:
            logger.info(f"[BACKTEST] Fetching {total_days}d of {timeframe} data from Binance REST API...")
            api_symbol = symbol.replace("/", "")
            tf_ms = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}
            candle_ms = tf_ms.get(timeframe, 300000)
            target_candles = total_days * (86400000 // candle_ms)

            all_data = []
            end_ms = int(time.time() * 1000)
            current_end = end_ms
            total_fetched = 0

            while total_fetched < target_candles:
                batch_size = 1000
                start_ms = current_end - (batch_size * candle_ms)
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    "symbol": api_symbol,
                    "interval": timeframe,
                    "limit": batch_size,
                    "startTime": start_ms,
                    "endTime": current_end,
                }
                try:
                    resp = _requests.get(url, params=params, timeout=15)
                    if resp.status_code != 200:
                        logger.warning(f"[BACKTEST] Binance HTTP {resp.status_code}")
                        break
                    data = resp.json()
                    if not data:
                        break
                    parsed = [
                        [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                        for c in data
                    ]
                    all_data.extend(parsed)
                    total_fetched += len(parsed)
                    current_end = int(data[0][0]) - 1
                    if len(parsed) < batch_size:
                        break
                except Exception as e:
                    logger.warning(f"[BACKTEST] Binance download error: {e}")
                    break

            if len(all_data) < 500:
                raise ValueError(f"Not enough data for {symbol} {timeframe}: {len(all_data)} candles (need 500+)")
            logger.info(f"[BACKTEST] Downloaded {len(all_data)} candles from Binance")

            df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            df = df[~df.index.duplicated(keep="first")]
            df.sort_index(inplace=True)

        logger.info(f"[BACKTEST] Data: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

        # 3. IS/OOS split (EXACTLY like full_replay_v21.py)
        total_candles = len(df)
        is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + days))
        is_df = df.iloc[:is_cutoff]
        oos_df = df.iloc[is_cutoff:]
        logger.info(f"[BACKTEST] IS/OOS split: {len(is_df)} IS + {len(oos_df)} OOS candles")

        # 4. Load or build tries with Config F alpha
        tf_key = f"{timeframe}_a{ALPHA_N3_N4}"  # e.g. "5m_a3"
        tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)

        trie_counts = {}
        for lvl in ("n1", "n2", "n3", "n4"):
            t = tries.get(lvl)
            trie_counts[lvl] = t.pattern_count if t else 0
        logger.info(f"[BACKTEST] Tries from '{tf_key}': N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

        # If no tries under the alpha key, build them from IS data
        if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
            logger.info(f"[BACKTEST] No tries under '{tf_key}', building from {len(is_df)} IS candles...")

            saved_n3_b = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
            saved_n4_b = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
            saved_tf_b = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)

            LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
            for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
                for lvl in ["n3", "n4"]:
                    LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)

            try:
                build_engine = PPMT(
                    symbol=symbol,
                    asset_class=asset_class,
                    weight_profile=weight_profile,
                    dual_sax=True,
                    min_confidence=0.08,
                    timeframe=timeframe,
                )
                build_count = build_engine.build(is_df)
                logger.info(f"[BACKTEST] Built {build_count} patterns from {len(is_df)} IS candles (α={ALPHA_N3_N4})")

                # Save tries to storage for future use
                if build_engine.trie_n1 and build_engine.trie_n1.pattern_count > 0:
                    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", build_engine.trie_n1, timeframe=tf_key)
                if build_engine.trie_n2 and build_engine.trie_n2.pattern_count > 0:
                    storage.save_trie(class_pool_key(asset_class), "n2", build_engine.trie_n2, timeframe=tf_key)
                if build_engine.trie_n3 and build_engine.trie_n3.pattern_count > 0:
                    storage.save_trie(symbol, "n3", build_engine.trie_n3, timeframe=tf_key)
                if build_engine.trie_n4 and build_engine.trie_n4.pattern_count > 0:
                    storage.save_trie(symbol, "n4", build_engine.trie_n4, timeframe=tf_key)
            finally:
                LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_b
                LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_b
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_b)

            # Reload tries
            tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)
            trie_counts = {}
            for lvl in ("n1", "n2", "n3", "n4"):
                t = tries.get(lvl)
                trie_counts[lvl] = t.pattern_count if t else 0
            logger.info(f"[BACKTEST] Rebuilt tries: N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

        # Final check — still no tries?
        if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
            raise ValueError(f"No tries for {symbol} {timeframe}! Backtest cannot run.")

        # 5. Override alpha + hard_move_floor for Config F
        saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
        saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe, 0.15)

        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
        for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
        TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = HARD_MOVE_FLOOR

        try:
            # 6. Create PPMT engine for replay
            engine = PPMT(
                symbol=symbol,
                asset_class=asset_class,
                weight_profile=weight_profile,
                dual_sax=True,
                min_confidence=0.08,
                timeframe=timeframe,
            )
            logger.info(f"[BACKTEST] PPMT engine created: symbol={symbol} asset_class={asset_class}")

            # 7. Apply Config F weights: N1=10%, N2=0%, N3=90%, N4=0%
            engine.weights = AdaptiveWeights(
                n1_universal=CONFIG_F_WEIGHTS["n1"],
                n2_asset_class=CONFIG_F_WEIGHTS["n2"],
                n3_per_asset=CONFIG_F_WEIGHTS["n3"],
                n4_per_asset_regime=CONFIG_F_WEIGHTS["n4"],
                n5_btc_context=CONFIG_F_WEIGHTS["n5"],
            )
            logger.info(f"[BACKTEST] Config F applied: EV>={EV_THRESHOLD} SL={SL_MULT}xDD N3=90% floor={HARD_MOVE_FLOOR}%")

            engine.set_tries(
                trie_n1=tries["n1"] if tries["n1"] else PPMTTrie(name="empty_n1"),
                trie_n2=tries["n2"] if tries["n2"] else PPMTTrie(name="empty_n2"),
                trie_n3=tries["n3"] if tries["n3"] else PPMTTrie(name="empty_n3"),
                trie_n4=tries["n4"] if tries["n4"] else engine.trie_n4,
            )
            logger.info(f"[BACKTEST] Tries set on engine")
        finally:
            # Restore global state IMMEDIATELY after engine creation
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)
            TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = saved_hmf

        # 8. OOS replay — EXACTLY matching full_replay_v21.py run_replay()
        # No separate warmup phase: the engine processes OOS candles directly.
        # The first ~10-20 candles will not produce signals (SAX buffer filling),
        # which is expected and matches the script behavior.
        executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
        executor._position = None
        regime_detector = RegimeDetector()
        regime_window: list[dict] = []
        _last_engine_ts = 0

        trades = []
        wins = 0
        losses = 0
        total_pnl = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        max_drawdown = 0.0
        peak_pnl = 0.0
        long_count = 0
        short_count = 0
        total_signals_raw = 0
        signals_rejected_spread = 0
        signals_rejected_ev = 0
        regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}

        spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)
        logger.info(f"[BACKTEST] Starting OOS replay: {len(oos_df)} candles, spread={spread_pct}%")

        for idx in range(len(oos_df)):
            row = oos_df.iloc[[idx]]
            current_price = float(row["close"].iloc[0])
            candle_high = float(row["high"].iloc[0])
            candle_low = float(row["low"].iloc[0])
            ts = oos_df.index[idx]
            ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

            # ── Check SL/TP (script style: NO continue after close) ──
            if executor.is_in_position:
                pos = executor.position
                closed = None
                if pos.direction == "LONG":
                    if candle_low <= pos.catastrophic_sl:
                        closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_low <= pos.current_sl:
                        closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_high >= pos.current_tp:
                        closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if candle_high >= pos.catastrophic_sl:
                        closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_high >= pos.current_sl:
                        closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_low <= pos.current_tp:
                        closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")

                if closed:
                    pnl = closed.pnl_pct or 0.0
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    else:
                        losses += 1
                        gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    dd = peak_pnl - total_pnl
                    max_drawdown = max(max_drawdown, dd)

                    _send("backtest_trade", {
                        "direction": pos.direction, "entry": round(pos.entry_price, 6),
                        "exit": round(closed.close_price, 6), "pnl_pct": round(pnl, 2),
                        "close_reason": closed.close_reason or "UNKNOWN",
                        "timestamp": int(ts.timestamp()),
                    })
                    executor._position = None
                    # Script does NOT continue here — falls through to feed candle

            # ── Feed candle to engine (script style: uses iloc[[idx]] row) ──
            result: Optional[PPMTResult] = None
            if ts_sec > _last_engine_ts:
                _last_engine_ts = ts_sec

                # Regime detection (on every candle, script style)
                regime_window.append({
                    "open": float(row["open"].iloc[0]),
                    "high": candle_high,
                    "low": candle_low,
                    "close": current_price,
                    "volume": float(row["volume"].iloc[0]),
                })
                if len(regime_window) > REGIME_WINDOW_SIZE:
                    regime_window = regime_window[-REGIME_WINDOW_SIZE:]
                if len(regime_window) >= 2:
                    try:
                        rw_df = pd.DataFrame(regime_window)
                        detected = regime_detector.detect_simple(rw_df, timeframe=timeframe)
                        regime_counts[detected] += 1
                        engine.set_regime(detected)
                    except Exception:
                        regime_counts["ranging"] += 1
                        engine.set_regime("ranging")

                result = engine.process_new_candle(
                    candle_df=row,
                    current_price=current_price,
                    is_in_position=executor.is_in_position,
                    entry_price=executor.position.entry_price if executor.position else None,
                )

            if result is None:
                continue

            sig = result.signal if result and result.signal else None
            if sig is None or not sig.is_entry:
                continue
            if executor.is_in_position:
                continue

            total_signals_raw += 1

            # ── Net EV Gate (Config F) — same as full_replay_v21.py ──
            best_node = None
            for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
                if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                    best_node = _mr.node
                    break

            favorable_pct = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
            drawdown_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5

            if favorable_pct < 0.001:
                favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
            if drawdown_pct < 0.001:
                drawdown_pct = 0.5

            net_favorable = favorable_pct - spread_pct
            if net_favorable <= 0:
                signals_rejected_spread += 1
                continue

            net_rr = min(net_favorable / drawdown_pct, 3.0)
            net_ev = sig.confidence * net_rr

            if net_ev < EV_THRESHOLD:
                signals_rejected_ev += 1
                continue

            # ── Signal passed EV gate → open position ──
            direction = sig.direction or "LONG"
            expected_move_pct = sig.expected_move_pct or 1.0
            size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
            size_usdt = min(size_usdt, CAPITAL_USDT)

            try:
                pos = executor.open_position_sync(
                    symbol=symbol,
                    direction=direction,
                    entry_price=current_price,
                    expected_move_pct=expected_move_pct,
                    predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                    size_usdt=size_usdt,
                )
            except RuntimeError:
                continue

            # Config F: SL = max(default 1.2xEM, drawdown_pct x SL_MULT)
            sl_dist_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
            dd_sl_pct = drawdown_pct * SL_MULT
            if dd_sl_pct > sl_dist_pct:
                extra = dd_sl_pct - sl_dist_pct
                if pos.direction == "LONG":
                    pos.current_sl -= pos.entry_price * (extra / 100.0)
                    pos.catastrophic_sl -= pos.entry_price * (extra / 100.0)
                else:
                    pos.current_sl += pos.entry_price * (extra / 100.0)
                    pos.catastrophic_sl += pos.entry_price * (extra / 100.0)

            if direction == "LONG":
                long_count += 1
            else:
                short_count += 1

            _send("backtest_signal", {
                "symbol": symbol, "direction": direction,
                "entry": round(current_price, 6), "confidence": round(sig.confidence, 3),
                "ev_score": round(net_ev, 2), "ev_passed": True,
                "timestamp": int(ts.timestamp()),
            })

            # ── Check entry candle for immediate SL/TP (script style) ──
            if executor.is_in_position:
                entry_closed = None
                if pos.direction == "LONG":
                    if candle_low <= pos.catastrophic_sl:
                        entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_low <= pos.current_sl:
                        entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_high >= pos.current_tp:
                        entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if candle_high >= pos.catastrophic_sl:
                        entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_high >= pos.current_sl:
                        entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_low <= pos.current_tp:
                        entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")

                if entry_closed:
                    pnl = entry_closed.pnl_pct or 0.0
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    else:
                        losses += 1
                        gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    dd = peak_pnl - total_pnl
                    max_drawdown = max(max_drawdown, dd)

                    _send("backtest_trade", {
                        "direction": pos.direction, "entry": round(pos.entry_price, 6),
                        "exit": round(entry_closed.close_price, 6), "pnl_pct": round(pnl, 2),
                        "close_reason": entry_closed.close_reason or "ENTRY_CANDLE",
                        "timestamp": int(ts.timestamp()),
                    })
                    executor._position = None
                    continue  # Script continues after entry candle close

            # Walk-Forward check (script style)
            if result and executor.is_in_position:
                current_sax = []
                buf = getattr(engine, '_streaming_buffer', None)
                if buf and buf._pattern_buffer:
                    last_sym = buf._pattern_buffer[-1]
                    if isinstance(last_sym, (tuple, list)):
                        current_sax = [str(s) for s in last_sym]
                    else:
                        current_sax = [str(last_sym)]
                if current_sax:
                    executor.check_walk_forward(current_sax, current_price)

        # ── Force-close remaining position at end ──
        if executor.is_in_position and executor._position:
            last_price = float(oos_df["close"].iloc[-1])
            closed = executor.force_close(last_price, "REPLAY_END")
            pnl = closed.pnl_pct or 0.0
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)
            total_pnl += pnl

            _send("backtest_trade", {
                "direction": closed.direction, "entry": round(closed.entry_price, 6),
                "exit": round(last_price, 6), "pnl_pct": round(pnl, 2),
                "close_reason": "REPLAY_END",
                "timestamp": int(oos_df.index[-1].timestamp()) if isinstance(oos_df.index[-1], pd.Timestamp) else 0,
            })
            executor._position = None

        # ── Summary ──
        total_trades = wins + losses
        wr = round((wins / total_trades * 100), 1) if total_trades > 0 else 0
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0.0)

        logger.info(
            f"[BACKTEST] Replay complete: {symbol} {timeframe} {days}d | "
            f"OOS={len(oos_df)} raw_signals={total_signals_raw} "
            f"rej_spread={signals_rejected_spread} rej_ev={signals_rejected_ev} | "
            f"trades={total_trades} WR={wr}% PnL={round(total_pnl,2)}% PF={pf} | "
            f"regimes={regime_counts}"
        )

        summary = {
            "trades": total_trades, "longs": long_count, "shorts": short_count,
            "wins": wins, "losses": losses, "wr": wr,
            "pnl_pct": round(total_pnl, 2), "profit_factor": pf,
            "max_drawdown": round(max_drawdown, 2),
            "signals_total": total_signals_raw,
            "signals_rejected_spread": signals_rejected_spread,
            "signals_rejected_ev": signals_rejected_ev,
        }

        _send("backtest_complete", summary)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[BACKTEST] _backtest_sync FAILED: {e}\\n{tb}")
        _send("backtest_complete", {
            "error": str(e), "trades": 0, "wins": 0, "losses": 0,
            "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
        })


'''

new_content = content[:start_idx] + new_section + content[end_idx:]

with open(filepath, "w") as f:
    f.write(new_content)

print(f"Replacement done! New file length: {len(new_content)} chars")
