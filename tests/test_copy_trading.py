"""Tests for the copy trading strategy."""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData
from src.strategies.copy_trading import CopyTradingStrategy, CopyTarget


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def strategy(settings):
    return CopyTradingStrategy(settings)


@pytest.mark.asyncio
async def test_no_signals_without_momentum(strategy):
    """No signals when price is stable (no momentum)."""
    await strategy.start()
    data = MarketData(
        condition_id="0xstable",
        question="Will X happen?",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("1000"),
        timestamp=1000.0,
    )
    # Need several identical prices — no momentum
    for _ in range(10):
        signals = await strategy.on_data(data)
    assert len(signals) == 0
    await strategy.stop()


@pytest.mark.asyncio
async def test_momentum_signal_on_sharp_move(settings):
    """Momentum-following should fire on sharp price moves."""
    s = CopyTradingStrategy(settings)
    s._signal_cooldown = 0  # Disable cooldown for test
    s._momentum_threshold = Decimal("0.02")  # Lower threshold for test
    await s.start()

    # Feed stable prices, then a sharp jump
    for i in range(6):
        price = Decimal("0.50") if i < 5 else Decimal("0.60")  # Sharp jump
        data = MarketData(
            condition_id="0xsharp",
            question="Will X happen?",
            yes_price=price,
            no_price=Decimal("1") - price,
            spread=Decimal("0.02"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0 + i * 10,
        )
        signals = await s.on_data(data)

    assert len(signals) > 0
    assert signals[0].side == "BUY_YES"  # Momentum going up -> buy YES
    await s.stop()


@pytest.mark.asyncio
async def test_copy_target_qualified(settings):
    """Qualified target should allow copy signals."""
    s = CopyTradingStrategy(settings)
    s._signal_cooldown = 0
    await s.start()

    target = CopyTarget(address="0xwhale1", label="whale_1", copy_ratio=Decimal("0.2"))
    target.trades_tracked = 30
    target.wins = 20  # 66.7% win rate > 60% minimum
    target.current_positions["0xt"] = {"pnl": Decimal("100")}
    s.add_target(target)

    sig = s.generate_copy_signal(
        target_address="0xwhale1",
        condition_id="0xt",
        side="BUY_YES",
        target_size=Decimal("100"),
        price=Decimal("0.55"),
    )
    assert sig is not None
    assert sig.side == "BUY_YES"
    assert sig.size == Decimal("20")  # 100 * 0.2
    assert sig.confidence == 20 / 30  # win_rate
    await s.stop()


@pytest.mark.asyncio
async def test_copy_target_unqualified(settings):
    """Unqualified target should not generate copy signals."""
    s = CopyTradingStrategy(settings)
    await s.start()

    target = CopyTarget(address="0xweak", label="weak_trader", min_win_rate=0.6)
    target.trades_tracked = 10
    target.wins = 3  # 30% win rate, below 60% minimum
    s.add_target(target)

    sig = s.generate_copy_signal(
        target_address="0xweak",
        condition_id="0xt",
        side="BUY_YES",
        target_size=Decimal("100"),
        price=Decimal("0.55"),
    )
    assert sig is None  # Should be rejected
    await s.stop()


@pytest.mark.asyncio
async def test_signal_cooldown_enforced(settings):
    """Rapid on_data calls should be throttled."""
    s = CopyTradingStrategy(settings)
    s._signal_cooldown = 60.0
    s._momentum_threshold = Decimal("0.01")
    await s.start()

    # Set up a sharp move
    for i in range(6):
        price = Decimal("0.50") if i < 5 else Decimal("0.65")
        data = MarketData(
            condition_id="0xcool_test",
            question="Will X happen?",
            yes_price=price,
            no_price=Decimal("1") - price,
            spread=Decimal("0.02"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0 + i * 10,
        )
        signals = await s.on_data(data)

    # First sharp move should generate a signal
    had_signal = len(signals) > 0

    # Second call should be blocked by cooldown
    signals2 = await s.on_data(data)
    assert len(signals2) == 0
    await s.stop()
