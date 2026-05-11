"""Smoke test — connect to Polymarket API in read-only mode and verify data pipeline."""

import asyncio
import sys
from decimal import Decimal

import structlog

from src.core.config import get_settings
from src.core.data_feed import DataFeed
from src.core.engine import Engine
from src.core.risk_manager import RiskManager
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_making import MarketMakingStrategy, BandsConfig


async def test_gamma_api() -> bool:
    """Test 1: Fetch available markets from Gamma API."""
    settings = get_settings()
    feed = DataFeed(settings)

    print("=" * 60)
    print("TEST 1: Gamma API — Fetch active markets")
    print("=" * 60)

    try:
        markets = await feed.get_markets(limit=5)
        print(f"  Fetched {len(markets)} markets")

        for m in markets[:3]:
            question = m.get("question", "N/A")[:80]
            condition_id = m.get("condition_id", "N/A")[:16]
            print(f"  - [{condition_id}...] {question}")

        print("  PASSED")
        await feed.close()
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        await feed.close()
        return False


async def test_clob_api() -> bool:
    """Test 2: Fetch midpoint price from CLOB API."""
    settings = get_settings()
    feed = DataFeed(settings)

    print()
    print("=" * 60)
    print("TEST 2: CLOB API — Fetch midpoint price")
    print("=" * 60)

    try:
        # First get a market to find a token ID
        markets = await feed.get_markets(limit=1)
        if not markets:
            print("  SKIPPED: No markets available")
            await feed.close()
            return True

        tokens = markets[0].get("tokens", [])
        if not tokens:
            print("  SKIPPED: No tokens in market data")
            await feed.close()
            return True

        token_id = tokens[0].get("token_id", "")
        print(f"  Token ID: {token_id[:20]}...")

        midpoint = await feed.get_midpoint(token_id)
        print(f"  Midpoint: {midpoint}")
        print("  PASSED")
        await feed.close()
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        await feed.close()
        return False


async def test_engine_paper_mode() -> bool:
    """Test 3: Run engine in paper mode with fake data through risk + execution."""
    settings = get_settings()
    feed = DataFeed(settings)
    engine = Engine(settings)

    engine.add_strategy(ArbitrageStrategy(settings, feed))
    engine.add_strategy(MarketMakingStrategy(settings, BandsConfig()))

    print()
    print("=" * 60)
    print("TEST 3: Engine — Paper mode with simulated arb data")
    print("=" * 60)

    try:
        await engine.start()

        from src.core.strategy_base import MarketData

        # Simulate a market with internal arb (YES + NO < $1)
        arb_data = MarketData(
            condition_id="0xtest_arb",
            question="Will smoke test pass?",
            yes_price=Decimal("0.45"),
            no_price=Decimal("0.50"),  # Total = $0.95
            spread=Decimal("0.05"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0,
        )

        results = await engine.run_once(arb_data)
        print(f"  Signals generated: {len(results['signals'])}")
        print(f"  Fills executed: {len(results['fills'])}")
        print(f"  Risk status: halted={results['risk_status']['halted']}")

        assert not results["risk_status"]["halted"], "Should not be halted on first trade"
        assert len(results["signals"]) > 0, "Arbitrage strategy should detect YES+NO < $1"

        print("  PASSED")
        await engine.stop()
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        await engine.stop()
        return False


async def test_risk_circuit_breaker() -> bool:
    """Test 4: Risk manager halts after drawdown exceeds limit."""
    settings = get_settings()
    rm = RiskManager(settings)

    print()
    print("=" * 60)
    print("TEST 4: Risk Manager — Circuit breaker at 25% drawdown")
    print("=" * 60)

    try:
        rm.set_equity(Decimal("1000"))
        rm.set_equity(Decimal("740"))  # 26% drawdown

        assert rm.is_halted, "Risk manager should halt at 25%+ drawdown"
        print(f"  Halted: {rm.is_halted}")
        print(f"  Reason: {rm.halt_reason}")
        print("  PASSED")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    print("Polymarket Edge — Smoke Test")
    print("=" * 60)

    results = [
        await test_gamma_api(),
        await test_clob_api(),
        await test_engine_paper_mode(),
        await test_risk_circuit_breaker(),
    ]

    passed = sum(results)
    total = len(results)

    print()
    print("=" * 60)
    print(f"RESULTS: {passed}/{total} tests passed")
    if passed < total:
        print("Some API tests may fail without network access or valid credentials.")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
