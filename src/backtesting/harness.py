"""Backtesting harness using NautilusTrader for historical strategy validation."""

from decimal import Decimal
from typing import Any

import structlog

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class BacktestResult:
    """Result of a single backtest run."""

    def __init__(self, strategy_name: str) -> None:
        self.strategy_name = strategy_name
        self.trades: list[dict] = []
        self.total_pnl = Decimal("0")
        self.max_drawdown = Decimal("0")
        self.peak_equity = Decimal("0")
        self.final_equity = Decimal("0")
        self.sharpe_ratio: float = 0.0
        self.win_rate: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0

    def add_trade(self, trade: dict) -> None:
        self.trades.append(trade)
        self.total_trades += 1
        pnl = Decimal(str(trade.get("pnl", "0")))
        self.total_pnl += pnl
        if pnl > 0:
            self.winning_trades += 1
        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "total_trades": self.total_trades,
            "win_rate": f"{self.win_rate:.2%}",
            "total_pnl": str(self.total_pnl),
            "max_drawdown": str(self.max_drawdown),
            "sharpe_ratio": f"{self.sharpe_ratio:.2f}",
            "final_equity": str(self.final_equity),
        }


class BacktestHarness:
    """Run a strategy against historical market data.

    For full NautilusTrader integration (order book replay, fill models,
    walk-forward), use evan-kolberg/prediction-market-backtesting directly.
    This harness provides a lightweight alternative for quick strategy validation.
    """

    def __init__(self, initial_equity: Decimal = Decimal("1000")) -> None:
        self.initial_equity = initial_equity
        self._equity = initial_equity
        self._peak_equity = initial_equity

    async def run(
        self,
        strategy: Strategy,
        historical_data: list[MarketData],
        fill_model: str = "midpoint",
    ) -> BacktestResult:
        """Run a strategy against historical data and return performance metrics."""
        await strategy.start()

        result = BacktestResult(strategy.name)
        self._equity = self.initial_equity
        self._peak_equity = self.initial_equity

        for data in historical_data:
            signals = await strategy.on_data(data)

            for signal in signals:
                fill_price = self._simulate_fill(signal, data, fill_model)
                fill_size = signal.size

                await strategy.on_fill(signal, fill_price, fill_size)

                pnl = self._calculate_pnl(signal, fill_price, fill_size)
                self._equity += pnl

                if self._equity > self._peak_equity:
                    self._peak_equity = self._equity

                drawdown = (self._peak_equity - self._equity) / self._peak_equity * 100
                if drawdown > result.max_drawdown:
                    result.max_drawdown = drawdown

                result.add_trade({
                    "timestamp": data.timestamp,
                    "condition_id": data.condition_id,
                    "side": signal.side,
                    "signal_price": str(signal.price),
                    "fill_price": str(fill_price),
                    "size": str(fill_size),
                    "pnl": str(pnl),
                    "equity": str(self._equity),
                    "drawdown": str(drawdown),
                })

        await strategy.stop()

        result.final_equity = self._equity
        result.calculate_sharpe()
        return result

    def _simulate_fill(self, signal: TradeSignal, data: MarketData, model: str) -> Decimal:
        """Simulate order fill based on the selected model."""
        if model == "midpoint":
            return (data.yes_price + data.no_price) / Decimal("2")
        elif model == "worst":
            # Pessimistic: buy at ask, sell at bid
            if signal.side in ("BUY_YES", "BUY_NO"):
                return data.yes_price + data.spread / Decimal("2")
            else:
                return data.yes_price - data.spread / Decimal("2")
        else:
            return signal.price  # Fill at signal price

    def _calculate_pnl(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> Decimal:
        """Calculate PnL for a simulated fill."""
        # Simplified: PnL = (signal price - fill price) * size
        # In reality, PnL depends on resolution, not just price difference
        return (signal.price - fill_price) * fill_size
