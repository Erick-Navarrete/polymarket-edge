"""Tests for walk-forward validation."""

import asyncio
from decimal import Decimal

import pytest

from src.backtesting.walk_forward import (
    WalkForwardResult,
    WalkForwardValidator,
    WalkForwardWindow,
)
from src.backtesting.harness import BacktestResult
from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal


class SimpleTestStrategy(Strategy):
    """Minimal strategy that always trades for testing purposes."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="test_wf", settings=settings)

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if data.yes_price < Decimal("0.50"):
            return [TradeSignal(
                condition_id=data.condition_id,
                side="BUY_YES",
                price=data.yes_price,
                size=Decimal("1"),
                reason="test",
                strategy=self.name,
            )]
        return []

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size


def _make_data(n: int = 60) -> list[MarketData]:
    """Generate alternating data where sub-0.50 prices trigger trades."""
    data = []
    for i in range(n):
        yes = Decimal("0.40") if i % 3 == 0 else Decimal("0.60")
        data.append(MarketData(
            condition_id="0xtest",
            question="Test?",
            yes_price=yes,
            no_price=Decimal("1") - yes,
            spread=Decimal("0.02"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0 + i * 60,
        ))
    return data


@pytest.mark.asyncio
async def test_walk_forward_produces_windows():
    settings = Settings(live_mode=False)
    strategy = SimpleTestStrategy(settings)
    validator = WalkForwardValidator(
        initial_equity=Decimal("1000"),
        train_ratio=0.7,
        step_ratio=0.15,
        min_test_bars=5,
    )
    data = _make_data(60)
    result = await validator.validate(strategy, data)
    assert isinstance(result, WalkForwardResult)
    assert len(result.windows) >= 1
    assert result.strategy_name == "test_wf"


@pytest.mark.asyncio
async def test_walk_forward_test_trades_present():
    settings = Settings(live_mode=False)
    strategy = SimpleTestStrategy(settings)
    validator = WalkForwardValidator(
        initial_equity=Decimal("1000"),
        train_ratio=0.7,
        step_ratio=0.15,
        min_test_bars=5,
    )
    data = _make_data(60)
    result = await validator.validate(strategy, data)
    # At least some out-of-sample trades should be generated
    assert result.total_test_trades > 0


@pytest.mark.asyncio
async def test_walk_forward_summary_keys():
    settings = Settings(live_mode=False)
    strategy = SimpleTestStrategy(settings)
    validator = WalkForwardValidator(
        initial_equity=Decimal("1000"),
        train_ratio=0.7,
        step_ratio=0.15,
        min_test_bars=5,
    )
    data = _make_data(60)
    result = await validator.validate(strategy, data)
    summary = result.summary()
    assert "strategy" in summary
    assert "num_windows" in summary
    assert "overfitting_risk" in summary
    assert summary["overfitting_risk"] in ("LOW", "MODERATE", "HIGH")


def test_window_degradation_calc():
    window = WalkForwardWindow(0, 40, 20)
    train = BacktestResult("test")
    train.sharpe_ratio = 2.0
    train.win_rate = 0.6
    train.total_trades = 10
    test = BacktestResult("test")
    test.sharpe_ratio = 1.0
    test.win_rate = 0.5
    test.total_trades = 5
    window.train_result = train
    window.test_result = test
    # 50% degradation (2.0 -> 1.0)
    assert window.degradation_pct("sharpe_ratio") == 50.0
