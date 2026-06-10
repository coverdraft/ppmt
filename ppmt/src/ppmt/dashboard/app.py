"""
PPMT Dashboard Flask Application

Serves backtest results via API endpoints and renders
interactive charts using Chart.js on the frontend.
"""

from __future__ import annotations

import json
import os
import glob
from datetime import datetime
from typing import Optional

from flask import Flask, jsonify, render_template_string, request

from ppmt.data.storage import PPMTStorage

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKTEST_DIR = os.path.join(os.path.expanduser("~/.ppmt"), "backtest_results")
DB_PATH = os.path.join(os.path.expanduser("~/.ppmt"), "ppmt.db")


def create_app(backtest_dir: Optional[str] = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    bt_dir = backtest_dir or BACKTEST_DIR
    os.makedirs(bt_dir, exist_ok=True)

    # ===================================================================
    # Page Routes
    # ===================================================================
    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    # ===================================================================
    # API: List available backtest result files
    # ===================================================================
    @app.route("/api/backtest-results")
    def api_list_results():
        """Return list of all backtest result JSON files."""
        files = []
        for f in sorted(glob.glob(os.path.join(bt_dir, "*.json")), reverse=True):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                files.append({
                    "filename": os.path.basename(f),
                    "symbol": data.get("symbol", "unknown"),
                    "version": data.get("version", ""),
                    "type": "rolling" if "rolling" in os.path.basename(f) else "static",
                    "total_trades": data.get("total_trades", 0),
                    "win_rate": data.get("win_rate", 0),
                    "total_pnl_pct": data.get("total_pnl_pct", 0),
                    "timestamp": os.path.basename(f).split("_")[-1].replace(".json", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return jsonify(files)

    # ===================================================================
    # API: Load a specific backtest result
    # ===================================================================
    @app.route("/api/backtest/<path:filename>")
    def api_load_result(filename: str):
        """Load and return a specific backtest result JSON."""
        filepath = os.path.join(bt_dir, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404

        with open(filepath) as f:
            data = json.load(f)

        # Convert any non-serializable types
        def sanitize(obj):
            if isinstance(obj, (int, float, str, bool, type(None))):
                return obj
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [sanitize(v) for v in obj]
            return str(obj)

        return jsonify(sanitize(data))

    # ===================================================================
    # API: List tracked assets from SQLite
    # ===================================================================
    @app.route("/api/assets")
    def api_assets():
        """Return list of tracked assets from the PPMT database."""
        try:
            storage = PPMTStorage()
            assets = storage.get_assets()
            storage.close()
            return jsonify(assets)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ===================================================================
    # API: Quick summary for a symbol (latest result)
    # ===================================================================
    @app.route("/api/summary/<symbol>")
    def api_summary(symbol: str):
        """Get the latest backtest summary for a symbol.

        Symbol can use / (e.g., BTC/USDT) or _ (e.g., BTC_USDT).
        """
        # Normalize: support both BTC/USDT and BTC_USDT
        safe_symbol = symbol.replace("_", "/")
        search_symbol = symbol.replace("/", "_")
        pattern = os.path.join(bt_dir, f"*{search_symbol}*.json")
        files = sorted(glob.glob(pattern), reverse=True)

        if not files:
            return jsonify({"error": f"No results for {symbol}"}), 404

        with open(files[0]) as f:
            data = json.load(f)

        # Build summary - support both rolling and static backtest formats
        trades = data.get("trades", [])
        # Rolling backtest stores windows in "windows", static in "window_results"
        windows = data.get("window_results", data.get("windows", []))
        # Summary may be in root-level or nested in "summary" key
        s = data.get("summary", {})

        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        equity_curve = []
        drawdown_curve = []

        for t in trades:
            pnl = t.get("pnl_pct", 0)
            cumulative_pnl += pnl
            peak = max(peak, cumulative_pnl)
            dd = cumulative_pnl - peak
            max_dd = min(max_dd, dd)
            equity_curve.append(round(cumulative_pnl, 2))
            drawdown_curve.append(round(dd, 2))

        # If no trades but summary has total_pnl, use max_drawdown from summary
        if not trades and s.get("max_drawdown_pct"):
            max_dd = s["max_drawdown_pct"]

        summary = {
            "symbol": symbol,
            "filename": os.path.basename(files[0]),
            "total_trades": s.get("total_trades", len(trades)),
            "win_rate": s.get("win_rate", data.get("win_rate", 0)),
            "total_pnl_pct": s.get("total_pnl_pct", data.get("total_pnl_pct", 0)),
            "avg_rr": s.get("avg_rr", data.get("avg_rr", 0)),
            "max_drawdown": round(max_dd, 2) if max_dd != 0 else s.get("max_drawdown_pct", 0),
            "long_trades": s.get("long_trades", data.get("long_trades", 0)),
            "short_trades": s.get("short_trades", data.get("short_trades", 0)),
            "long_wr": s.get("long_wr", data.get("long_wr", 0)),
            "short_wr": s.get("short_wr", data.get("short_wr", 0)),
            "n_windows": len(windows),
            "profitable_windows": sum(1 for w in windows if w.get("pnl_pct", 0) > 0),
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
            "window_results": windows,
            "trades_sample": trades[-50:] if len(trades) > 50 else trades,
        }

        return jsonify(summary)

    # ===================================================================
    # API: Compare multiple symbols
    # ===================================================================
    @app.route("/api/compare")
    def api_compare():
        """Get comparison data for all symbols with results."""
        symbols_data = {}

        for f in sorted(glob.glob(os.path.join(bt_dir, "*.json")), reverse=True):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                sym = data.get("symbol", "")
                if sym and sym not in symbols_data:
                    # Take only the latest result per symbol
                    trades = data.get("trades", [])
                    s = data.get("summary", {})
                    windows = data.get("window_results", data.get("windows", []))

                    # Equity curve
                    cum_pnl = 0.0
                    peak = 0.0
                    max_dd = 0.0
                    equity = []
                    for t in trades:
                        cum_pnl += t.get("pnl_pct", 0)
                        peak = max(peak, cum_pnl)
                        dd = cum_pnl - peak
                        max_dd = min(max_dd, dd)
                        equity.append(round(cum_pnl, 2))

                    symbols_data[sym] = {
                        "total_trades": s.get("total_trades", len(trades)),
                        "win_rate": s.get("win_rate", data.get("win_rate", 0)),
                        "total_pnl_pct": s.get("total_pnl_pct", data.get("total_pnl_pct", 0)),
                        "avg_rr": s.get("avg_rr", data.get("avg_rr", 0)),
                        "max_drawdown": round(max_dd, 2) if max_dd != 0 else s.get("max_drawdown_pct", 0),
                        "long_trades": s.get("long_trades", data.get("long_trades", 0)),
                        "short_trades": s.get("short_trades", data.get("short_trades", 0)),
                        "equity_curve": equity,
                        "n_windows": len(windows),
                        "profitable_windows": sum(
                            1 for w in windows if w.get("pnl_pct", 0) > 0
                        ),
                    }
            except (json.JSONDecodeError, OSError):
                continue

        return jsonify(symbols_data)

    return app


# ===================================================================
# Dashboard HTML Template (Single-page app with Chart.js)
# ===================================================================
DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PPMT Dashboard V9.0</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <style>
        :root {
            --bg-primary: #0f1117;
            --bg-secondary: #1a1d29;
            --bg-card: #1e2233;
            --border: #2a2f42;
            --text-primary: #e4e7f1;
            --text-secondary: #8b8fa3;
            --accent-blue: #4f8cff;
            --accent-green: #00d68f;
            --accent-red: #ff6b6b;
            --accent-yellow: #ffc107;
            --accent-purple: #a855f7;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }

        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .header h1 {
            font-size: 20px;
            font-weight: 600;
            color: var(--accent-blue);
        }

        .header .version {
            font-size: 12px;
            color: var(--text-secondary);
            margin-left: 8px;
        }

        .controls {
            display: flex;
            gap: 12px;
            align-items: center;
        }

        .controls select {
            background: var(--bg-card);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 14px;
            cursor: pointer;
            outline: none;
        }

        .controls select:focus {
            border-color: var(--accent-blue);
        }

        .btn {
            background: var(--accent-blue);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 14px;
            cursor: pointer;
            transition: opacity 0.2s;
        }

        .btn:hover { opacity: 0.85; }
        .btn:active { opacity: 0.7; }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        /* Stats Cards */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }

        .stat-card .label {
            font-size: 12px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }

        .stat-card .value {
            font-size: 24px;
            font-weight: 700;
        }

        .stat-card .value.green { color: var(--accent-green); }
        .stat-card .value.red { color: var(--accent-red); }
        .stat-card .value.blue { color: var(--accent-blue); }
        .stat-card .value.yellow { color: var(--accent-yellow); }
        .stat-card .value.purple { color: var(--accent-purple); }

        /* Charts Grid */
        .charts-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            margin-bottom: 24px;
        }

        @media (max-width: 900px) {
            .charts-grid { grid-template-columns: 1fr; }
        }

        .chart-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }

        .chart-card h3 {
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .chart-card canvas {
            width: 100% !important;
            max-height: 300px;
        }

        .chart-card.full-width {
            grid-column: 1 / -1;
        }

        /* Trades Table */
        .trades-section {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }

        .trades-section h3 {
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .table-wrapper {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }

        table th {
            text-align: left;
            padding: 8px 12px;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
            font-weight: 500;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }

        table td {
            padding: 8px 12px;
            border-bottom: 1px solid rgba(42, 47, 66, 0.5);
        }

        table tr:hover {
            background: rgba(79, 140, 255, 0.05);
        }

        .pnl-positive { color: var(--accent-green); }
        .pnl-negative { color: var(--accent-red); }
        .dir-long { color: var(--accent-green); }
        .dir-short { color: var(--accent-red); }

        /* Loading */
        .loading {
            text-align: center;
            padding: 60px;
            color: var(--text-secondary);
        }

        .loading .spinner {
            display: inline-block;
            width: 40px;
            height: 40px;
            border: 3px solid var(--border);
            border-top-color: var(--accent-blue);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 4px;
            margin-bottom: 20px;
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 4px;
        }

        .tab {
            flex: 1;
            text-align: center;
            padding: 10px;
            cursor: pointer;
            border-radius: 6px;
            font-size: 14px;
            color: var(--text-secondary);
            transition: all 0.2s;
        }

        .tab:hover { color: var(--text-primary); }

        .tab.active {
            background: var(--bg-card);
            color: var(--accent-blue);
            font-weight: 600;
        }

        .tab-content { display: none; }
        .tab-content.active { display: block; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>PPMT Dashboard<span class="version">V9.0</span></h1>
        </div>
        <div class="controls">
            <select id="symbolSelect">
                <option value="">Select symbol...</option>
            </select>
            <button class="btn" onclick="loadSymbol()">Load</button>
            <button class="btn" onclick="loadCompare()" style="background: var(--accent-purple);">Compare All</button>
        </div>
    </div>

    <div class="container">
        <!-- Tabs -->
        <div class="tabs">
            <div class="tab active" onclick="switchTab('overview')">Overview</div>
            <div class="tab" onclick="switchTab('windows')">Windows</div>
            <div class="tab" onclick="switchTab('trades')">Trades</div>
            <div class="tab" onclick="switchTab('compare')">Compare</div>
        </div>

        <!-- Overview Tab -->
        <div id="tab-overview" class="tab-content active">
            <div class="stats-grid" id="statsGrid">
                <div class="stat-card">
                    <div class="label">Total Trades</div>
                    <div class="value blue" id="stat-trades">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">Win Rate</div>
                    <div class="value green" id="stat-wr">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">Total P&L</div>
                    <div class="value" id="stat-pnl">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">Avg R:R</div>
                    <div class="value purple" id="stat-rr">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">Max Drawdown</div>
                    <div class="value red" id="stat-dd">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">Profitable Windows</div>
                    <div class="value yellow" id="stat-pw">-</div>
                </div>
            </div>

            <div class="charts-grid">
                <div class="chart-card full-width">
                    <h3>Equity Curve</h3>
                    <canvas id="equityChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Drawdown</h3>
                    <canvas id="drawdownChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>P&L Distribution</h3>
                    <canvas id="pnlDistChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Direction Split</h3>
                    <canvas id="directionChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>R:R Distribution</h3>
                    <canvas id="rrDistChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Windows Tab -->
        <div id="tab-windows" class="tab-content">
            <div class="charts-grid">
                <div class="chart-card full-width">
                    <h3>Per-Window P&L</h3>
                    <canvas id="windowPnlChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Per-Window Win Rate</h3>
                    <canvas id="windowWrChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Per-Window Trade Count</h3>
                    <canvas id="windowTradeChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Trades Tab -->
        <div id="tab-trades" class="tab-content">
            <div class="trades-section">
                <h3>Recent Trades (Last 50)</h3>
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Direction</th>
                                <th>Entry</th>
                                <th>Exit</th>
                                <th>P&L %</th>
                                <th>R:R</th>
                                <th>Pattern</th>
                                <th>Match Level</th>
                                <th>WR (hist)</th>
                            </tr>
                        </thead>
                        <tbody id="tradesBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Compare Tab -->
        <div id="tab-compare" class="tab-content">
            <div class="charts-grid">
                <div class="chart-card full-width">
                    <h3>Equity Curves Comparison</h3>
                    <canvas id="compareEquityChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Win Rate Comparison</h3>
                    <canvas id="compareWrChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>P&L Comparison</h3>
                    <canvas id="comparePnlChart"></canvas>
                </div>
            </div>
        </div>
    </div>

    <script>
    // ===================================================================
    // Chart instances (to destroy before recreating)
    // ===================================================================
    const charts = {};

    function destroyChart(id) {
        if (charts[id]) {
            charts[id].destroy();
            delete charts[id];
        }
    }

    // ===================================================================
    // Tab switching
    // ===================================================================
    function switchTab(name) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById('tab-' + name).classList.add('active');
    }

    // ===================================================================
    // Chart.js defaults for dark theme
    // ===================================================================
    Chart.defaults.color = '#8b8fa3';
    Chart.defaults.borderColor = '#2a2f42';
    Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

    // ===================================================================
    // Load symbols into dropdown
    // ===================================================================
    async function loadSymbols() {
        try {
            const res = await fetch('/api/backtest-results');
            const files = await res.json();
            const select = document.getElementById('symbolSelect');
            const seen = new Set();

            files.forEach(f => {
                if (!seen.has(f.symbol)) {
                    seen.add(f.symbol);
                    const opt = document.createElement('option');
                    opt.value = f.symbol;
                    opt.textContent = f.symbol + ' (' + f.type + ')';
                    select.appendChild(opt);
                }
            });
        } catch (e) {
            console.error('Error loading symbols:', e);
        }
    }

    // ===================================================================
    // Load a specific symbol's backtest data
    // ===================================================================
    let currentData = null;

    async function loadSymbol() {
        const symbol = document.getElementById('symbolSelect').value;
        if (!symbol) return;

        try {
            const res = await fetch('/api/summary/' + symbol.replace('/', '_'));
            currentData = await res.json();

            if (currentData.error) {
                alert(currentData.error);
                return;
            }

            renderOverview(currentData);
            renderWindows(currentData);
            renderTrades(currentData);
        } catch (e) {
            console.error('Error loading symbol:', e);
            alert('Error loading data');
        }
    }

    // ===================================================================
    // Render Overview Tab
    // ===================================================================
    function renderOverview(data) {
        // Stats
        document.getElementById('stat-trades').textContent = data.total_trades;
        document.getElementById('stat-wr').textContent = (data.win_rate * 100).toFixed(1) + '%';
        document.getElementById('stat-wr').className = 'value ' + (data.win_rate >= 0.5 ? 'green' : 'red');

        const pnlEl = document.getElementById('stat-pnl');
        pnlEl.textContent = (data.total_pnl_pct >= 0 ? '+' : '') + data.total_pnl_pct.toFixed(2) + '%';
        pnlEl.className = 'value ' + (data.total_pnl_pct >= 0 ? 'green' : 'red');

        document.getElementById('stat-rr').textContent = data.avg_rr.toFixed(2);
        document.getElementById('stat-dd').textContent = data.max_drawdown.toFixed(2) + '%';

        const pw = data.n_windows > 0
            ? data.profitable_windows + '/' + data.n_windows + ' (' + Math.round(data.profitable_windows / data.n_windows * 100) + '%)'
            : '-';
        document.getElementById('stat-pw').textContent = pw;

        // Equity Curve
        destroyChart('equityChart');
        const eqCtx = document.getElementById('equityChart').getContext('2d');
        charts['equityChart'] = new Chart(eqCtx, {
            type: 'line',
            data: {
                labels: data.equity_curve.map((_, i) => i + 1),
                datasets: [{
                    label: 'Cumulative P&L %',
                    data: data.equity_curve,
                    borderColor: '#4f8cff',
                    backgroundColor: 'rgba(79, 140, 255, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: true, title: { display: true, text: 'Trade #' } },
                    y: { display: true, title: { display: true, text: 'P&L %' } },
                }
            }
        });

        // Drawdown
        destroyChart('drawdownChart');
        const ddCtx = document.getElementById('drawdownChart').getContext('2d');
        charts['drawdownChart'] = new Chart(ddCtx, {
            type: 'line',
            data: {
                labels: data.drawdown_curve.map((_, i) => i + 1),
                datasets: [{
                    label: 'Drawdown %',
                    data: data.drawdown_curve,
                    borderColor: '#ff6b6b',
                    backgroundColor: 'rgba(255, 107, 107, 0.15)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: true, title: { display: true, text: 'Trade #' } },
                    y: { display: true, title: { display: true, text: 'DD %' } },
                }
            }
        });

        // P&L Distribution
        const trades = data.trades_sample || [];
        const pnlValues = trades.map(t => t.pnl_pct);
        const bins = createHistogramBins(pnlValues, 20);
        destroyChart('pnlDistChart');
        const pnlCtx = document.getElementById('pnlDistChart').getContext('2d');
        charts['pnlDistChart'] = new Chart(pnlCtx, {
            type: 'bar',
            data: {
                labels: bins.labels,
                datasets: [{
                    label: 'Frequency',
                    data: bins.counts,
                    backgroundColor: bins.labels.map(l => parseFloat(l) >= 0 ? 'rgba(0, 214, 143, 0.6)' : 'rgba(255, 107, 107, 0.6)'),
                    borderColor: bins.labels.map(l => parseFloat(l) >= 0 ? '#00d68f' : '#ff6b6b'),
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { title: { display: true, text: 'P&L %' } },
                    y: { title: { display: true, text: 'Count' } },
                }
            }
        });

        // Direction Split
        destroyChart('directionChart');
        const dirCtx = document.getElementById('directionChart').getContext('2d');
        charts['directionChart'] = new Chart(dirCtx, {
            type: 'doughnut',
            data: {
                labels: ['LONG (' + data.long_trades + ')', 'SHORT (' + data.short_trades + ')'],
                datasets: [{
                    data: [data.long_trades, data.short_trades],
                    backgroundColor: ['rgba(0, 214, 143, 0.7)', 'rgba(255, 107, 107, 0.7)'],
                    borderColor: ['#00d68f', '#ff6b6b'],
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom' },
                }
            }
        });

        // R:R Distribution
        const rrValues = trades.map(t => t.rr).filter(v => v > 0 && v < 50);
        const rrBins = createHistogramBins(rrValues, 15);
        destroyChart('rrDistChart');
        const rrCtx = document.getElementById('rrDistChart').getContext('2d');
        charts['rrDistChart'] = new Chart(rrCtx, {
            type: 'bar',
            data: {
                labels: rrBins.labels,
                datasets: [{
                    label: 'Frequency',
                    data: rrBins.counts,
                    backgroundColor: 'rgba(168, 85, 247, 0.6)',
                    borderColor: '#a855f7',
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { title: { display: true, text: 'R:R' } },
                    y: { title: { display: true, text: 'Count' } },
                }
            }
        });
    }

    // ===================================================================
    // Render Windows Tab
    // ===================================================================
    function renderWindows(data) {
        const windows = data.window_results || [];

        // Per-Window P&L
        destroyChart('windowPnlChart');
        const wpCtx = document.getElementById('windowPnlChart').getContext('2d');
        charts['windowPnlChart'] = new Chart(wpCtx, {
            type: 'bar',
            data: {
                labels: windows.map(w => 'W' + w.window),
                datasets: [{
                    label: 'P&L %',
                    data: windows.map(w => w.pnl_pct),
                    backgroundColor: windows.map(w => w.pnl_pct >= 0 ? 'rgba(0, 214, 143, 0.6)' : 'rgba(255, 107, 107, 0.6)'),
                    borderColor: windows.map(w => w.pnl_pct >= 0 ? '#00d68f' : '#ff6b6b'),
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { title: { display: true, text: 'Window' } },
                    y: { title: { display: true, text: 'P&L %' } },
                }
            }
        });

        // Per-Window Win Rate
        destroyChart('windowWrChart');
        const wrCtx = document.getElementById('windowWrChart').getContext('2d');
        charts['windowWrChart'] = new Chart(wrCtx, {
            type: 'bar',
            data: {
                labels: windows.map(w => 'W' + w.window),
                datasets: [{
                    label: 'Win Rate %',
                    data: windows.map(w => (w.win_rate * 100).toFixed(1)),
                    backgroundColor: 'rgba(79, 140, 255, 0.6)',
                    borderColor: '#4f8cff',
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { title: { display: true, text: 'Window' } },
                    y: { title: { display: true, text: 'WR %' }, min: 0, max: 100 },
                }
            }
        });

        // Per-Window Trade Count
        destroyChart('windowTradeChart');
        const tcCtx = document.getElementById('windowTradeChart').getContext('2d');
        charts['windowTradeChart'] = new Chart(tcCtx, {
            type: 'bar',
            data: {
                labels: windows.map(w => 'W' + w.window),
                datasets: [{
                    label: 'Trades',
                    data: windows.map(w => w.trades),
                    backgroundColor: 'rgba(255, 193, 7, 0.6)',
                    borderColor: '#ffc107',
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { title: { display: true, text: 'Window' } },
                    y: { title: { display: true, text: 'Trades' } },
                }
            }
        });
    }

    // ===================================================================
    // Render Trades Tab
    // ===================================================================
    function renderTrades(data) {
        const trades = data.trades_sample || [];
        const tbody = document.getElementById('tradesBody');
        tbody.innerHTML = '';

        trades.forEach((t, i) => {
            const row = document.createElement('tr');
            const pnlClass = t.pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative';
            const dirClass = t.direction === 'LONG' ? 'dir-long' : 'dir-short';

            row.innerHTML = `
                <td>${i + 1}</td>
                <td class="${dirClass}">${t.direction}</td>
                <td>${parseFloat(t.entry_price).toFixed(2)}</td>
                <td>${parseFloat(t.exit_price).toFixed(2)}</td>
                <td class="${pnlClass}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%</td>
                <td>${parseFloat(t.rr).toFixed(2)}</td>
                <td style="font-family: monospace; font-size: 12px;">${t.pattern || '-'}</td>
                <td style="font-size: 12px;">${t.match_level || '-'}</td>
                <td>${(t.win_rate_historical * 100).toFixed(0)}%</td>
            `;
            tbody.appendChild(row);
        });
    }

    // ===================================================================
    // Load Comparison Data
    // ===================================================================
    async function loadCompare() {
        try {
            const res = await fetch('/api/compare');
            const data = await res.json();
            renderCompare(data);
            switchTab('compare');
            // activate compare tab
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab')[3].classList.add('active');
        } catch (e) {
            console.error('Error loading comparison:', e);
        }
    }

    function renderCompare(data) {
        const symbols = Object.keys(data);
        if (symbols.length === 0) return;

        const colors = ['#4f8cff', '#00d68f', '#ff6b6b', '#ffc107', '#a855f7', '#ff9f43', '#6bcb77', '#ee5a24'];

        // Normalize equity curves to percentage for comparison
        // Sample down to max 200 points
        function sample(arr, maxPts) {
            if (arr.length <= maxPts) return arr;
            const step = arr.length / maxPts;
            const result = [];
            for (let i = 0; i < maxPts; i++) {
                result.push(arr[Math.floor(i * step)]);
            }
            return result;
        }

        const maxLen = Math.max(...symbols.map(s => data[s].equity_curve.length));
        const labels = Array.from({ length: Math.min(maxLen, 200) }, (_, i) => i + 1);

        // Equity comparison
        destroyChart('compareEquityChart');
        const ceCtx = document.getElementById('compareEquityChart').getContext('2d');
        charts['compareEquityChart'] = new Chart(ceCtx, {
            type: 'line',
            data: {
                labels,
                datasets: symbols.map((sym, idx) => ({
                    label: sym,
                    data: sample(data[sym].equity_curve, 200),
                    borderColor: colors[idx % colors.length],
                    backgroundColor: 'transparent',
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'top' } },
                scales: {
                    x: { title: { display: true, text: 'Trade # (sampled)' } },
                    y: { title: { display: true, text: 'Cumulative P&L %' } },
                }
            }
        });

        // Win Rate comparison
        destroyChart('compareWrChart');
        const cwrCtx = document.getElementById('compareWrChart').getContext('2d');
        charts['compareWrChart'] = new Chart(cwrCtx, {
            type: 'bar',
            data: {
                labels: symbols,
                datasets: [{
                    label: 'Win Rate %',
                    data: symbols.map(s => (data[s].win_rate * 100).toFixed(1)),
                    backgroundColor: symbols.map((_, i) => colors[i % colors.length] + '99'),
                    borderColor: symbols.map((_, i) => colors[i % colors.length]),
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { min: 0, max: 100 } }
            }
        });

        // P&L comparison
        destroyChart('comparePnlChart');
        const cpnlCtx = document.getElementById('comparePnlChart').getContext('2d');
        charts['comparePnlChart'] = new Chart(cpnlCtx, {
            type: 'bar',
            data: {
                labels: symbols,
                datasets: [{
                    label: 'Total P&L %',
                    data: symbols.map(s => data[s].total_pnl_pct),
                    backgroundColor: symbols.map(s => data[s].total_pnl_pct >= 0 ? 'rgba(0, 214, 143, 0.6)' : 'rgba(255, 107, 107, 0.6)'),
                    borderColor: symbols.map(s => data[s].total_pnl_pct >= 0 ? '#00d68f' : '#ff6b6b'),
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
            }
        });
    }

    // ===================================================================
    // Histogram helper
    // ===================================================================
    function createHistogramBins(values, numBins) {
        if (values.length === 0) return { labels: [], counts: [] };

        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const binWidth = range / numBins;

        const counts = new Array(numBins).fill(0);
        const labels = [];

        for (let i = 0; i < numBins; i++) {
            const low = min + i * binWidth;
            const high = low + binWidth;
            labels.push(((low + high) / 2).toFixed(2));
        }

        values.forEach(v => {
            let idx = Math.floor((v - min) / binWidth);
            if (idx >= numBins) idx = numBins - 1;
            if (idx < 0) idx = 0;
            counts[idx]++;
        });

        return { labels, counts };
    }

    // ===================================================================
    // Init
    // ===================================================================
    loadSymbols();
    </script>
</body>
</html>
"""
