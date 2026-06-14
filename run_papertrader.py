#!/usr/bin/env python3
"""
PPMT PaperTrader Bridge Script

Called by the Next.js API route to run the PaperTrader and output
clean JSON to a result file. This avoids Rich console output contaminating
the JSON stdout that the Node.js process needs to parse.

Usage:
    python3 run_papertrader.py --result-file /tmp/ppmt_result.json \
        --symbol BTC/USDT --timeframe 1h --alpha 3 --window 7 \
        --capital 10000 --cat-loss 8 --pattern-length 3 \
        --min-confidence 0.2 --pruning-interval 1000 \
        --living-trie --regime-aware

Output:
    Writes JSON result to --result-file
    Prints only "OK" or "ERROR: <msg>" to stdout
"""

import argparse
import json
import os
import sys

# Suppress Rich output during PaperTrader run
os.environ["PPMT_QUIET"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))


def main():
    parser = argparse.ArgumentParser(description='PPMT PaperTrader Bridge')
    parser.add_argument('--result-file', required=True, help='Path to write JSON result')
    parser.add_argument('--symbol', required=True, help='Trading pair (e.g. BTC/USDT)')
    parser.add_argument('--timeframe', default='1h', help='Candle timeframe')
    parser.add_argument('--capital', type=float, default=10000, help='Initial capital')
    parser.add_argument('--alpha', type=int, default=3, help='SAX alphabet size')
    parser.add_argument('--window', type=int, default=7, help='SAX window size')
    parser.add_argument('--cat-loss', type=float, default=8.0, help='Catastrophic loss %%')
    parser.add_argument('--pattern-length', type=int, default=3, help='Pattern length')
    parser.add_argument('--min-confidence', type=float, default=0.2, help='Min confidence threshold')
    parser.add_argument('--pruning-interval', type=int, default=1000, help='Trie pruning interval')
    parser.add_argument('--living-trie', action='store_true', default=False, help='Enable living trie')
    parser.add_argument('--regime-aware', action='store_true', default=False, help='Enable regime-aware sizing')
    parser.add_argument('--no-token-profile', action='store_true', default=False,
                        help='Disable auto TokenProfile (use explicit params)')
    args = parser.parse_args()

    try:
        # Import after args are parsed to fail fast on bad args
        from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

        # Build config - use_token_profile=True lets the engine auto-configure
        # alpha/window/cat_loss/short/fuzzy from the asset class + timeframe.
        # Only override if --no-token-profile is set.
        use_token_profile = not args.no_token_profile

        config = PaperTraderConfig(
            symbol=args.symbol,
            timeframe=args.timeframe,
            initial_capital=args.capital,
            use_token_profile=use_token_profile,
            # When use_token_profile=True, these are auto-selected:
            #   sax_alphabet_size, sax_window_size, catastrophic_loss_pct
            # When False, use the explicit values from the dashboard:
            sax_alphabet_size=args.alpha if not use_token_profile else 0,
            sax_window_size=args.window if not use_token_profile else 0,
            catastrophic_loss_pct=args.cat_loss if not use_token_profile else 0.0,
            pattern_length=args.pattern_length,
            min_confidence=args.min_confidence,
            pruning_interval=args.pruning_interval,
            living_trie=args.living_trie,
            regime_aware=args.regime_aware,
            auto_calibrate=True,
            use_multi_level=True,
            verbose=False,
        )

        trader = PaperTrader(config=config)
        result = trader.run()

        # Build output - safe attribute access with defaults
        equity_curve = getattr(result, 'equity_curve', []) or []

        # Downsample equity curve for dashboard (max 500 points)
        if len(equity_curve) > 500:
            step = len(equity_curve) // 500
            equity_sampled = equity_curve[::step]
            if equity_sampled[-1] != equity_curve[-1]:
                equity_sampled.append(equity_curve[-1])
            equity_curve = equity_sampled

        trades_data = []
        for t in (getattr(result, 'trades', None) or [])[:50]:
            trades_data.append({
                'id': getattr(t, 'trade_id', 0),
                'dir': getattr(t, 'direction', ''),
                'entry': getattr(t, 'entry_price', 0),
                'exit': getattr(t, 'exit_price', 0),
                'pnl': round(getattr(t, 'pnl', 0), 4),
                'pnlPct': round(getattr(t, 'pnl_pct', 0), 4),
                'confidence': round(getattr(t, 'confidence', 0), 4),
                'exitReason': getattr(t, 'exit_reason', ''),
                'regime': getattr(t, 'regime', ''),
            })

        # Safely get pattern count - matched_pattern might be empty
        pattern_count = 0
        if result.trades:
            first_trade = result.trades[0]
            mp = getattr(first_trade, 'matched_pattern', None)
            if mp is not None:
                try:
                    pattern_count = len(mp)
                except TypeError:
                    pattern_count = 0

        output = {
            'totalPnl': round(getattr(result, 'total_pnl', 0), 4),
            'totalPnlPct': round(getattr(result, 'total_pnl_pct', 0), 4),
            'winRate': getattr(result, 'win_rate', 0),
            'sharpeRatio': round(getattr(result, 'sharpe_ratio', 0), 4),
            'maxDrawdown': getattr(result, 'max_drawdown', 0),
            'profitFactor': round(getattr(result, 'profit_factor', 0), 4),
            'totalTrades': getattr(result, 'total_trades', 0),
            'winningTrades': getattr(result, 'winning_trades', 0),
            'losingTrades': getattr(result, 'losing_trades', 0),
            'patternCount': pattern_count,
            'recalibrations': getattr(result, 'recalibrations', 0),
            'pruningRuns': getattr(result, 'pruning_runs', 0),
            'catastrophicStops': getattr(result, 'catastrophic_stops', 0),
            'equityCurve': equity_curve,
            'trades': trades_data,
            'symbol': args.symbol,
            'timeframe': args.timeframe,
        }

        # Write result to file (avoids stdout contamination from Rich)
        result_dir = os.path.dirname(args.result_file)
        if result_dir:
            os.makedirs(result_dir, exist_ok=True)

        with open(args.result_file, 'w') as f:
            json.dump(output, f)

        print("OK")

    except Exception as e:
        # Write error to result file
        error_output = {'error': str(e), 'errorType': type(e).__name__}
        try:
            result_dir = os.path.dirname(args.result_file)
            if result_dir:
                os.makedirs(result_dir, exist_ok=True)
            with open(args.result_file, 'w') as f:
                json.dump(error_output, f)
        except Exception:
            pass
        print(f"ERROR: {e}")


if __name__ == '__main__':
    main()
