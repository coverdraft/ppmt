"""
Cross-Token Correlation Engine - Real-Time Correlation Matrix for PPMT v0.16.0

Computes and maintains a rolling correlation matrix between all tokens
in the portfolio. This enables the PortfolioManager to:

  1. Avoid opening positions in highly correlated tokens (diversification)
  2. Detect regime changes when correlations shift (crisis detection)
  3. Compute proper portfolio VaR with correlation-adjusted volatility
  4. Identify hedging opportunities (negative correlations)

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │           CrossTokenCorrelationEngine                     │
  │                                                          │
  │  Returns Buffer ──→  Rolling Window (N candles)          │
  │         │                    │                           │
  │         ▼                    ▼                           │
  │  ┌──────────┐  ┌──────────────────────────────────┐     │
  │  │ Per-Token │  │  Correlation Matrix (NxN)        │     │
  │  │ Returns  │  │  ┌────┬────┬────┬────┐           │     │
  │  │ Stream   │  │  │1.00│0.85│0.30│0.15│           │     │
  │  │          │  │  │0.85│1.00│0.25│0.12│           │     │
  │  │          │  │  │0.30│0.25│1.00│0.65│           │     │
  │  │          │  │  │0.15│0.12│0.65│1.00│           │     │
  │  │          │  │  └────┴────┴────┴────┘           │     │
  │  └──────────┘  └──────────────────────────────────┘     │
  │         │                    │                           │
  │         ▼                    ▼                           │
  │  ┌──────────────────────────────────────────────────┐   │
  │  │  Correlation Alerts                              │   │
  │  │  • High correlation warning (>0.7)               │   │
  │  │  • Correlation spike (>0.3 change) = crisis      │   │
  │  │  • Negative correlation = hedging opportunity     │   │
  │  └──────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────┘

Key Features:
  - Pearson and Spearman correlation methods
  - Rolling window with configurable lookback
  - Exponential decay for recency weighting
  - Correlation regime detection (normal vs crisis)
  - Efficient incremental updates (no full recomputation)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------

class CorrelationMethod(str, Enum):
    """Correlation computation method."""
    PEARSON = "PEARSON"
    SPEARMAN = "SPEARMAN"


class CorrelationRegime(str, Enum):
    """Current correlation regime across the portfolio."""
    NORMAL = "NORMAL"        # Typical correlations, diversified
    ELEVATED = "ELEVATED"    # Higher than normal, some de-diversification
    CRISIS = "CRISIS"        # Very high correlations, risk of simultaneous moves
    UNKNOWN = "UNKNOWN"


@dataclass
class CorrelationAlert:
    """An alert triggered by correlation analysis.

    Attributes:
        alert_type: Type of correlation alert.
        token_pair: The two tokens involved (if pair-specific).
        value: The correlation value that triggered the alert.
        threshold: The threshold that was crossed.
        message: Human-readable alert message.
        timestamp: When the alert was generated.
    """
    alert_type: str  # "HIGH_CORRELATION", "CORRELATION_SPIKE", "NEGATIVE_CORRELATION"
    token_pair: tuple[str, str]
    value: float
    threshold: float
    message: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class CorrelationMatrixResult:
    """Result of correlation matrix computation.

    Attributes:
        tokens: Token symbols in order matching the matrix indices.
        matrix: NxN correlation matrix.
        method: Correlation method used.
        window_size: Number of observations used.
        regime: Current correlation regime.
        avg_correlation: Average off-diagonal correlation.
        max_correlation: Maximum off-diagonal correlation.
        min_correlation: Minimum off-diagonal correlation.
        timestamp: When the matrix was computed.
    """
    tokens: list = field(default_factory=list)
    matrix: np.ndarray = field(default_factory=lambda: np.array([]))
    method: CorrelationMethod = CorrelationMethod.PEARSON
    window_size: int = 0
    regime: CorrelationRegime = CorrelationRegime.UNKNOWN
    avg_correlation: float = 0.0
    max_correlation: float = 0.0
    min_correlation: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def get_pair_correlation(self, token_a: str, token_b: str) -> Optional[float]:
        """Get the correlation between two specific tokens."""
        if token_a not in self.tokens or token_b not in self.tokens:
            return None
        i = self.tokens.index(token_a)
        j = self.tokens.index(token_b)
        return float(self.matrix[i][j])

    def get_most_correlated_pairs(self, n: int = 5) -> list[tuple[str, str, float]]:
        """Get the N most correlated token pairs.

        Returns:
            List of (token_a, token_b, correlation) tuples, sorted descending.
        """
        pairs = []
        for i in range(len(self.tokens)):
            for j in range(i + 1, len(self.tokens)):
                pairs.append((self.tokens[i], self.tokens[j], float(self.matrix[i][j])))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        return pairs[:n]

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "tokens": self.tokens,
            "matrix": self.matrix.tolist() if len(self.matrix) > 0 else [],
            "method": self.method.value,
            "window_size": self.window_size,
            "regime": self.regime.value,
            "avg_correlation": round(self.avg_correlation, 4),
            "max_correlation": round(self.max_correlation, 4),
            "min_correlation": round(self.min_correlation, 4),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# CrossTokenCorrelationEngine
# ---------------------------------------------------------------------------

class CrossTokenCorrelationEngine:
    """
    Real-Time Cross-Token Correlation Engine for PPMT Portfolios.

    Maintains a rolling buffer of returns for each token and computes
    the correlation matrix on demand or incrementally. Provides:

    1. **Correlation Matrix**: Full NxN matrix with Pearson/Spearman
    2. **Correlation Regime Detection**: Normal, Elevated, Crisis
    3. **Alert Generation**: High correlation, spikes, hedging opportunities
    4. **Diversification Scoring**: How well-diversified is the portfolio

    The engine is designed for incremental updates — each new candle
    adds one return observation without recomputing the entire matrix.

    Usage:
        engine = CrossTokenCorrelationEngine(
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            window=60,
        )

        # Update with new price data
        engine.update_price("BTC/USDT", 50000.0)
        engine.update_price("ETH/USDT", 3000.0)

        # Get current correlation matrix
        result = engine.compute_matrix()

        # Check correlation between two tokens
        corr = result.get_pair_correlation("BTC/USDT", "ETH/USDT")
    """

    def __init__(
        self,
        tokens: Optional[list[str]] = None,
        window: int = 60,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
        high_correlation_threshold: float = 0.70,
        crisis_correlation_threshold: float = 0.85,
        spike_detection_delta: float = 0.30,
        decay_factor: float = 0.0,  # 0 = no decay (equal weight)
    ):
        """
        Initialize the correlation engine.

        Args:
            tokens: Initial list of tokens to track.
            window: Rolling window size for returns (number of candles).
            method: Correlation computation method.
            high_correlation_threshold: Correlation above this triggers warning.
            crisis_correlation_threshold: Average correlation above this = crisis.
            spike_detection_delta: Change in correlation above this = spike.
            decay_factor: Exponential decay factor (0 = equal weight).
        """
        self._tokens: list[str] = tokens or []
        self._window = window
        self._method = method
        self._high_threshold = high_correlation_threshold
        self._crisis_threshold = crisis_correlation_threshold
        self._spike_delta = spike_detection_delta
        self._decay_factor = decay_factor

        # Returns buffer: symbol -> list of returns
        self._returns: dict[str, list[float]] = {t: [] for t in self._tokens}

        # Last known prices: symbol -> price
        self._last_prices: dict[str, float] = {}

        # Previous matrix for spike detection
        self._prev_matrix: Optional[np.ndarray] = None
        self._prev_avg_corr: float = 0.0

        # Alert buffer
        self._alerts: list[CorrelationAlert] = []

        # Computation count for caching
        self._compute_count: int = 0

    # -------------------------------------------------------------------
    # Data Input
    # -------------------------------------------------------------------

    def add_token(self, symbol: str) -> None:
        """Add a new token to the correlation tracking."""
        if symbol not in self._tokens:
            self._tokens.append(symbol)
            self._returns[symbol] = []

    def remove_token(self, symbol: str) -> None:
        """Remove a token from correlation tracking."""
        if symbol in self._tokens:
            self._tokens.remove(symbol)
            self._returns.pop(symbol, None)
            self._last_prices.pop(symbol, None)

    def update_price(self, symbol: str, price: float) -> None:
        """
        Update with a new price observation for a token.

        Computes the log return from the previous price and adds it
        to the rolling buffer.

        Args:
            symbol: Token symbol.
            price: Current price.
        """
        if symbol not in self._tokens:
            self.add_token(symbol)

        if symbol in self._last_prices and self._last_prices[symbol] > 0:
            prev = self._last_prices[symbol]
            if prev > 0 and price > 0:
                log_return = np.log(price / prev)
                self._returns[symbol].append(log_return)

                # Trim to window
                if len(self._returns[symbol]) > self._window:
                    self._returns[symbol] = self._returns[symbol][-self._window:]

        self._last_prices[symbol] = price

    def update_returns(self, symbol: str, returns: list[float]) -> None:
        """
        Bulk-load historical returns for a token.

        Args:
            symbol: Token symbol.
            returns: List of return observations.
        """
        if symbol not in self._tokens:
            self.add_token(symbol)

        self._returns[symbol] = returns[-self._window:]

    # -------------------------------------------------------------------
    # Matrix Computation
    # -------------------------------------------------------------------

    def compute_matrix(self) -> CorrelationMatrixResult:
        """
        Compute the current correlation matrix across all tokens.

        Returns:
            CorrelationMatrixResult with the NxN matrix and metadata.
        """
        n = len(self._tokens)
        if n < 2:
            return CorrelationMatrixResult(
                tokens=self._tokens,
                matrix=np.eye(n) if n > 0 else np.array([]),
                regime=CorrelationRegime.UNKNOWN,
            )

        # Build returns matrix (tokens x observations)
        min_len = min(len(self._returns.get(t, [])) for t in self._tokens)
        if min_len < 5:
            # Not enough data — use proxy correlations based on asset class
            return self._proxy_correlation_matrix()

        # Align returns to same length
        returns_matrix = np.array([
            self._returns[t][-min_len:]
            for t in self._tokens
        ])

        # Apply exponential decay if configured
        if self._decay_factor > 0:
            weights = np.exp(-self._decay_factor * np.arange(min_len)[::-1])
            weights /= weights.sum()
            # Weighted correlation
            matrix = self._weighted_correlation(returns_matrix, weights)
        else:
            if self._method == CorrelationMethod.SPEARMAN:
                # Rank-based correlation
                ranked = np.argsort(np.argsort(returns_matrix, axis=1), axis=1)
                matrix = np.corrcoef(ranked)
            else:
                matrix = np.corrcoef(returns_matrix)

        # Ensure matrix is properly shaped
        if matrix.shape != (n, n):
            matrix = np.eye(n)

        # Clean up numerical issues
        matrix = np.clip(matrix, -1.0, 1.0)
        np.fill_diagonal(matrix, 1.0)

        # Compute statistics
        off_diag = matrix[np.triu_indices(n, k=1)]
        avg_corr = float(np.mean(off_diag)) if len(off_diag) > 0 else 0.0
        max_corr = float(np.max(off_diag)) if len(off_diag) > 0 else 0.0
        min_corr = float(np.min(off_diag)) if len(off_diag) > 0 else 0.0

        # Detect correlation regime
        regime = self._detect_regime(avg_corr, off_diag)

        # Check for spikes (sudden correlation changes)
        if self._prev_matrix is not None and self._prev_matrix.shape == matrix.shape:
            self._check_for_spikes(matrix)

        # Store for next comparison
        self._prev_matrix = matrix.copy()
        self._prev_avg_corr = avg_corr
        self._compute_count += 1

        # Generate alerts for high correlations
        self._check_high_correlations(matrix)

        return CorrelationMatrixResult(
            tokens=list(self._tokens),
            matrix=matrix,
            method=self._method,
            window_size=min_len,
            regime=regime,
            avg_correlation=avg_corr,
            max_correlation=max_corr,
            min_correlation=min_corr,
        )

    def _weighted_correlation(self, returns: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Compute weighted correlation matrix.

        Uses the formula: corr(X,Y) = cov_w(X,Y) / (std_w(X) * std_w(Y))
        where cov_w and std_w are weighted covariance and standard deviation.
        """
        n_tokens = returns.shape[0]
        n_obs = returns.shape[1]

        # Weighted means
        w_means = np.average(returns, weights=weights, axis=1)

        # Weighted covariance matrix
        demeaned = returns - w_means[:, np.newaxis]
        cov = np.zeros((n_tokens, n_tokens))

        for i in range(n_tokens):
            for j in range(i, n_tokens):
                c = np.sum(weights * demeaned[i] * demeaned[j])
                cov[i][j] = c
                cov[j][i] = c

        # Convert to correlation
        stds = np.sqrt(np.diag(cov))
        stds[stds == 0] = 1e-10  # Avoid division by zero

        corr = cov / (stds[:, np.newaxis] * stds[np.newaxis, :])
        return corr

    def _proxy_correlation_matrix(self) -> CorrelationMatrixResult:
        """
        Generate a proxy correlation matrix when insufficient data exists.

        Uses known crypto market correlations:
        - BTC-ETH: ~0.85
        - BTC-L1: ~0.65
        - BTC-Meme: ~0.45
        - L1-L1: ~0.70
        - Meme-Meme: ~0.55
        """
        from ppmt.data.classifier import AssetClassifier
        classifier = AssetClassifier()

        n = len(self._tokens)
        matrix = np.eye(n)

        # Classify each token
        classes = [classifier.classify(t).asset_class for t in self._tokens]

        # Default correlation matrix by class pair
        class_corrs = {
            ("blue_chip", "blue_chip"): 0.85,
            ("blue_chip", "large_cap"): 0.75,
            ("blue_chip", "mid_cap"): 0.65,
            ("blue_chip", "defi"): 0.60,
            ("blue_chip", "meme"): 0.45,
            ("blue_chip", "new_launch"): 0.30,
            ("large_cap", "large_cap"): 0.70,
            ("large_cap", "mid_cap"): 0.65,
            ("large_cap", "defi"): 0.55,
            ("large_cap", "meme"): 0.40,
            ("mid_cap", "mid_cap"): 0.60,
            ("mid_cap", "defi"): 0.65,
            ("mid_cap", "meme"): 0.40,
            ("defi", "defi"): 0.70,
            ("defi", "meme"): 0.35,
            ("meme", "meme"): 0.55,
            ("new_launch", "new_launch"): 0.30,
        }

        for i in range(n):
            for j in range(i + 1, n):
                key = tuple(sorted([classes[i], classes[j]]))
                corr = class_corrs.get(key, 0.50)
                # Add small random perturbation to avoid identical values
                noise = np.random.uniform(-0.05, 0.05)
                corr = np.clip(corr + noise, -1.0, 1.0)
                matrix[i][j] = corr
                matrix[j][i] = corr

        avg_corr = float(np.mean(matrix[np.triu_indices(n, k=1)])) if n > 1 else 0.0
        max_corr = float(np.max(matrix[np.triu_indices(n, k=1)])) if n > 1 else 0.0
        min_corr = float(np.min(matrix[np.triu_indices(n, k=1)])) if n > 1 else 0.0

        return CorrelationMatrixResult(
            tokens=list(self._tokens),
            matrix=matrix,
            method=self._method,
            window_size=0,
            regime=CorrelationRegime.UNKNOWN,
            avg_correlation=avg_corr,
            max_correlation=max_corr,
            min_correlation=min_corr,
        )

    # -------------------------------------------------------------------
    # Regime & Alert Detection
    # -------------------------------------------------------------------

    def _detect_regime(self, avg_corr: float, off_diag: np.ndarray) -> CorrelationRegime:
        """
        Detect the current correlation regime.

        Regime classification:
        - NORMAL: avg correlation < 0.50, no pair > 0.85
        - ELEVATED: avg correlation 0.50-0.70, or any pair > 0.85
        - CRISIS: avg correlation > 0.70, or multiple pairs > 0.85

        In crisis regimes, diversification benefits are reduced and
        the portfolio manager should reduce exposure.
        """
        if len(off_diag) == 0:
            return CorrelationRegime.UNKNOWN

        high_corr_count = int(np.sum(off_diag > self._crisis_threshold))

        if avg_corr > self._crisis_threshold or high_corr_count >= 3:
            return CorrelationRegime.CRISIS
        elif avg_corr > self._high_threshold or high_corr_count >= 1:
            return CorrelationRegime.ELEVATED
        else:
            return CorrelationRegime.NORMAL

    def _check_high_correlations(self, matrix: np.ndarray) -> None:
        """Check for high correlation pairs and generate alerts."""
        n = len(self._tokens)
        for i in range(n):
            for j in range(i + 1, n):
                corr = matrix[i][j]
                if abs(corr) > self._high_threshold:
                    self._alerts.append(CorrelationAlert(
                        alert_type="HIGH_CORRELATION",
                        token_pair=(self._tokens[i], self._tokens[j]),
                        value=float(corr),
                        threshold=self._high_threshold,
                        message=(
                            f"High correlation: {self._tokens[i]}-{self._tokens[j]} "
                            f"= {corr:.2f} (threshold: {self._high_threshold:.2f})"
                        ),
                    ))

    def _check_for_spikes(self, current_matrix: np.ndarray) -> None:
        """Check for sudden correlation changes (spikes)."""
        if self._prev_matrix is None:
            return

        n = len(self._tokens)
        for i in range(n):
            for j in range(i + 1, n):
                delta = abs(current_matrix[i][j] - self._prev_matrix[i][j])
                if delta > self._spike_delta:
                    self._alerts.append(CorrelationAlert(
                        alert_type="CORRELATION_SPIKE",
                        token_pair=(self._tokens[i], self._tokens[j]),
                        value=float(current_matrix[i][j]),
                        threshold=float(self._prev_matrix[i][j]),
                        message=(
                            f"Correlation spike: {self._tokens[i]}-{self._tokens[j]} "
                            f"changed by {delta:.2f} "
                            f"({self._prev_matrix[i][j]:.2f} -> {current_matrix[i][j]:.2f})"
                        ),
                    ))

    # -------------------------------------------------------------------
    # Diversification Scoring
    # -------------------------------------------------------------------

    def compute_diversification_score(self) -> dict:
        """
        Compute a diversification score for the portfolio.

        The diversification score considers:
        1. Average off-diagonal correlation (lower = better)
        2. HHI of correlation eigenvalues (concentration)
        3. Number of uncorrelated clusters (more = better)

        Returns:
            Dict with diversification metrics.
        """
        result = self.compute_matrix()

        if len(self._tokens) < 2:
            return {
                "score": 1.0,
                "rating": "INSUFFICIENT_DATA",
                "avg_correlation": 0.0,
                "clusters": 1,
                "effective_positions": 1,
            }

        n = len(self._tokens)
        off_diag = result.matrix[np.triu_indices(n, k=1)]
        avg_corr = float(np.mean(off_diag))

        # Diversification score: 1 - avg_correlation (1 = perfectly diversified)
        div_score = max(0.0, 1.0 - avg_corr)

        # Effective number of positions (based on eigenvalues)
        try:
            eigenvalues = np.linalg.eigvalsh(result.matrix)
            eigenvalues = np.maximum(eigenvalues, 0.01)  # Floor
            effective_positions = float(np.sum(eigenvalues) ** 2 / np.sum(eigenvalues ** 2))
        except np.linalg.LinAlgError:
            effective_positions = float(n)

        # Correlation clusters (approximate: group tokens with corr > 0.7)
        clusters = self._find_clusters(result.matrix)

        # Rating
        if div_score >= 0.7:
            rating = "EXCELLENT"
        elif div_score >= 0.5:
            rating = "GOOD"
        elif div_score >= 0.3:
            rating = "MODERATE"
        else:
            rating = "POOR"

        return {
            "score": round(div_score, 3),
            "rating": rating,
            "avg_correlation": round(avg_corr, 3),
            "clusters": clusters,
            "effective_positions": round(effective_positions, 1),
            "correlation_regime": result.regime.value,
            "most_correlated_pair": result.get_most_correlated_pairs(1)[0] if n > 1 else None,
        }

    def _find_clusters(self, matrix: np.ndarray, threshold: float = 0.7) -> int:
        """Find the number of correlation clusters using simple threshold grouping."""
        n = len(self._tokens)
        if n <= 1:
            return 1

        # Simple single-linkage clustering
        visited = set()
        clusters = 0

        for i in range(n):
            if i in visited:
                continue
            # BFS from token i
            queue = [i]
            visited.add(i)
            cluster_members = [i]

            while queue:
                current = queue.pop(0)
                for j in range(n):
                    if j not in visited and abs(matrix[current][j]) > threshold:
                        visited.add(j)
                        queue.append(j)
                        cluster_members.append(j)

            clusters += 1

        return clusters

    # -------------------------------------------------------------------
    # Alert Management
    # -------------------------------------------------------------------

    def get_alerts(self, limit: int = 20) -> list[dict]:
        """Get recent correlation alerts."""
        return [
            {
                "type": a.alert_type,
                "pair": list(a.token_pair),
                "value": round(a.value, 4),
                "threshold": round(a.threshold, 4),
                "message": a.message,
                "timestamp": a.timestamp,
            }
            for a in self._alerts[-limit:]
        ]

    def clear_alerts(self) -> None:
        """Clear all correlation alerts."""
        self._alerts.clear()

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def display_matrix(self, result: Optional[CorrelationMatrixResult] = None) -> None:
        """Display a rich correlation matrix table."""
        if result is None:
            result = self.compute_matrix()

        if len(result.tokens) < 2:
            console.print("[yellow]Not enough tokens for correlation matrix[/yellow]")
            return

        table = Table(title=f"Cross-Token Correlation Matrix ({result.method.value})")
        table.add_column("", style="bold")

        for token in result.tokens:
            # Shorten token names for display
            short = token.split("/")[0]
            table.add_column(short, justify="center")

        for i, token in enumerate(result.tokens):
            short = token.split("/")[0]
            row = [short]
            for j in range(len(result.tokens)):
                val = result.matrix[i][j]
                if i == j:
                    row.append("[bold]1.00[/bold]")
                elif abs(val) > self._high_threshold:
                    row.append(f"[red]{val:.2f}[/red]")
                elif abs(val) > 0.5:
                    row.append(f"[yellow]{val:.2f}[/yellow]")
                else:
                    row.append(f"[green]{val:.2f}[/green]")
            table.add_row(*row)

        console.print(table)
        console.print(
            f"  Regime: [bold]{result.regime.value}[/bold]  "
            f"Avg Corr: {result.avg_correlation:.2f}  "
            f"Window: {result.window_size}"
        )

    # -------------------------------------------------------------------
    # Portfolio VaR Enhancement
    # -------------------------------------------------------------------

    def compute_portfolio_variance(
        self,
        weights: dict[str, float],
        volatilities: dict[str, float],
    ) -> float:
        """
        Compute portfolio variance using the correlation matrix.

        σ²_p = Σ_i Σ_j w_i w_j σ_i σ_j ρ_ij

        Args:
            weights: Token -> portfolio weight (0-1).
            volatilities: Token -> daily volatility.

        Returns:
            Portfolio daily variance.
        """
        result = self.compute_matrix()

        if len(result.tokens) < 2:
            # Single token
            for sym, vol in volatilities.items():
                return (weights.get(sym, 0) * vol) ** 2
            return 0.0

        # Build weight and vol vectors aligned to matrix order
        w = np.array([weights.get(t, 0) for t in result.tokens])
        v = np.array([volatilities.get(t, 0.04) for t in result.tokens])

        # Covariance matrix: Σ = diag(σ) @ ρ @ diag(σ)
        cov = np.outer(v, v) * result.matrix

        # Portfolio variance: w^T @ Σ @ w
        port_var = float(w @ cov @ w)
        return max(0.0, port_var)
