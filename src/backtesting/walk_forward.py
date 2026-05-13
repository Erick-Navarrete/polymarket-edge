"""Walk-forward validation for detecting overfitting in strategy parameters.

Splits data into rolling train/test windows, optimizes on train, evaluates on test.
Reports out-of-sample degradation vs in-sample to flag overfitting.
"""

from copy import deepcopy
from decimal import Decimal
from typing import Any

import structlog

from src.backtesting.harness import BacktestHarness, BacktestResult
from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy

logger = structlog.get_logger()


class WalkForwardWindow:
    """Result from a single train/test window."""

    def __init__(self, window_idx: int, train_size: int, test_size: int) -> None:
        self.window_idx = window_idx
        self.train_size = train_size
        self.test_size = test_size
        self.train_result: BacktestResult | None = None
        self.test_result: BacktestResult | None = None

    def degradation_pct(self, metric: str = "sharpe_ratio") -> float | None:
        """How much the metric degraded from train to test (0% = no degradation, 100% = total collapse)."""
        if not self.train_result or not self.test_result:
            return None
        train_val = self._get_metric(self.train_result, metric)
        test_val = self._get_metric(self.test_result, metric)
        if train_val == 0:
            return 0.0 if test_val == 0 else None
        return max(0.0, (1 - test_val / train_val) * 100)

    @staticmethod
    def _get_metric(result: BacktestResult, metric: str) -> float:
        if metric == "sharpe_ratio":
            return result.sharpe_ratio
        elif metric == "win_rate":
            return result.win_rate
        elif metric == "total_trades":
            return result.total_trades
        return 0.0


class WalkForwardResult:
    """Aggregated walk-forward validation results."""

    def __init__(self, strategy_name: str) -> None:
        self.strategy_name = strategy_name
        self.windows: list[WalkForwardWindow] = []

    @property
    def avg_test_sharpe(self) -> float:
        sharpes = [
            w.test_result.sharpe_ratio
            for w in self.windows
            if w.test_result and w.test_result.total_trades > 0
        ]
        return sum(sharpes) / len(sharpes) if sharpes else 0.0

    @property
    def avg_test_win_rate(self) -> float:
        rates = [
            w.test_result.win_rate
            for w in self.windows
            if w.test_result and w.test_result.total_trades > 0
        ]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def avg_sharpe_degradation(self) -> float:
        degs = [
            w.degradation_pct("sharpe_ratio")
            for w in self.windows
            if w.degradation_pct("sharpe_ratio") is not None
        ]
        return sum(degs) / len(degs) if degs else 0.0

    @property
    def total_test_trades(self) -> int:
        return sum(
            w.test_result.total_trades
            for w in self.windows
            if w.test_result
        )

    @property
    def total_test_pnl(self) -> Decimal:
        return sum(
            w.test_result.total_pnl
            for w in self.windows
            if w.test_result
        )

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "num_windows": len(self.windows),
            "total_test_trades": self.total_test_trades,
            "avg_test_sharpe": f"{self.avg_test_sharpe:.2f}",
            "avg_test_win_rate": f"{self.avg_test_win_rate:.2%}",
            "avg_sharpe_degradation": f"{self.avg_sharpe_degradation:.1f}%",
            "total_test_pnl": str(self.total_test_pnl),
            "overfitting_risk": "HIGH" if self.avg_sharpe_degradation > 50 else "MODERATE" if self.avg_sharpe_degradation > 25 else "LOW",
        }


class WalkForwardValidator:
    """Rolling window walk-forward analysis.

    Splits data into overlapping or adjacent windows. For each window:
    1. Runs strategy on train portion (in-sample)
    2. Runs strategy on test portion (out-of-sample)
    3. Compares metrics to measure degradation

    For strategies with tunable parameters (e.g. crypto_15m weights),
    subclass and override `tune_strategy()` to optimize on the train window.
    """

    def __init__(
        self,
        initial_equity: Decimal = Decimal("1000"),
        train_ratio: float = 0.7,
        step_ratio: float = 0.15,
        min_test_bars: int = 20,
    ) -> None:
        self.initial_equity = initial_equity
        self.train_ratio = train_ratio
        self.step_ratio = step_ratio
        self.min_test_bars = min_test_bars

    async def validate(
        self,
        strategy: Strategy,
        data: list[MarketData],
    ) -> WalkForwardResult:
        """Run walk-forward validation on a strategy with given data."""
        result = WalkForwardResult(strategy.name)

        n = len(data)
        train_size = int(n * self.train_ratio)
        test_size = int(n * (1 - self.train_ratio))
        step = max(1, int(n * self.step_ratio))

        if test_size < self.min_test_bars:
            logger.warning(
                "wf_insufficient_data",
                total_bars=n,
                test_bars=test_size,
                min_required=self.min_test_bars,
            )
            return result

        window_idx = 0
        start = 0

        while start + train_size + self.min_test_bars <= n:
            train_data = data[start : start + train_size]
            test_end = min(start + train_size + test_size, n)
            test_data = data[start + train_size : test_end]

            if len(test_data) < self.min_test_bars:
                break

            window = WalkForwardWindow(window_idx, len(train_data), len(test_data))

            # Optionally tune strategy on train data
            await self.tune_strategy(strategy, train_data)

            # In-sample evaluation
            harness = BacktestHarness(initial_equity=self.initial_equity)
            window.train_result = await harness.run(strategy, train_data)

            # Reset strategy state for out-of-sample
            await self._reset_strategy(strategy)

            # Out-of-sample evaluation
            harness = BacktestHarness(initial_equity=self.initial_equity)
            window.test_result = await harness.run(strategy, test_data)

            result.windows.append(window)

            logger.info(
                "wf_window_complete",
                window=window_idx,
                train_trades=window.train_result.total_trades if window.train_result else 0,
                test_trades=window.test_result.total_trades if window.test_result else 0,
                train_sharpe=window.train_result.sharpe_ratio if window.train_result else 0,
                test_sharpe=window.test_result.sharpe_ratio if window.test_result else 0,
            )

            start += step
            window_idx += 1

        logger.info(
            "wf_validation_complete",
            strategy=strategy.name,
            windows=len(result.windows),
            avg_degradation=f"{result.avg_sharpe_degradation:.1f}%",
        )

        return result

    async def tune_strategy(self, strategy: Strategy, train_data: list[MarketData]) -> None:
        """Override to optimize strategy parameters on training data. Default: no tuning."""
        pass

    async def _reset_strategy(self, strategy: Strategy) -> None:
        """Reset mutable strategy state between windows."""
        await strategy.stop()
        await strategy.start()


class Crypto15mWFValidator(WalkForwardValidator):
    """Walk-forward validator that tunes crypto_15m signal weights on training data.

    Tests a small grid of weight permutations on the train window,
    picks the one with best Sharpe, then evaluates on test.
    """

    WEIGHT_CONFIGS: list[dict] = [
        # Default
        {"SPIKE_DETECTION": 0.4, "PRICE_DIVERGENCE": 0.3, "MOMENTUM": 0.2, "MEAN_REVERSION": 0.1},
        # Momentum-heavy
        {"SPIKE_DETECTION": 0.2, "PRICE_DIVERGENCE": 0.2, "MOMENTUM": 0.4, "MEAN_REVERSION": 0.2},
        # Mean-reversion-heavy
        {"SPIKE_DETECTION": 0.1, "PRICE_DIVERGENCE": 0.1, "MOMENTUM": 0.2, "MEAN_REVERSION": 0.6},
        # Spike-heavy
        {"SPIKE_DETECTION": 0.6, "PRICE_DIVERGENCE": 0.2, "MOMENTUM": 0.1, "MEAN_REVERSION": 0.1},
        # Balanced
        {"SPIKE_DETECTION": 0.25, "PRICE_DIVERGENCE": 0.25, "MOMENTUM": 0.25, "MEAN_REVERSION": 0.25},
    ]

    async def tune_strategy(self, strategy: Strategy, train_data: list[MarketData]) -> None:
        """Find best weight config on training data."""
        from src.strategies.crypto_15m import SignalType

        best_sharpe = -999.0
        best_weights = self.WEIGHT_CONFIGS[0]

        for config in self.WEIGHT_CONFIGS:
            # Apply weights
            strategy._weights = {
                SignalType[k]: Decimal(str(v)) for k, v in config.items()
            }

            # Evaluate on train data
            harness = BacktestHarness(initial_equity=self.initial_equity)
            await strategy.start()
            result = await harness.run(strategy, train_data)
            await strategy.stop()

            if result.sharpe_ratio > best_sharpe:
                best_sharpe = result.sharpe_ratio
                best_weights = config

        # Apply best weights
        strategy._weights = {
            SignalType[k]: Decimal(str(v)) for k, v in best_weights.items()
        }

        logger.info(
            "wf_tuned_weights",
            best_weights=best_weights,
            train_sharpe=best_sharpe,
        )
