"""Polymarket Edge — CLI entry point."""

import asyncio
import argparse

import structlog

from src.core.config import get_settings
from src.core.data_feed import DataFeed
from src.core.engine import Engine
from src.core.risk_manager import RiskManager
from src.core.executor import Executor
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_making import MarketMakingStrategy, BandsConfig
from src.strategies.copy_trading import CopyTradingStrategy, CopyTarget
from src.strategies.ai_agent import AIAgentStrategy
from src.strategies.crypto_15m import Crypto15mStrategy
from src.strategies.weather import WeatherStrategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket Edge — Predictive Trading Platform")
    parser.add_argument("--live", action="store_true", help="Enable live trading (default: paper mode)")
    parser.add_argument("--strategy", nargs="+", default=["arbitrage", "market_making"],
                        choices=["arbitrage", "market_making", "copy_trading", "ai_agent", "crypto_15m", "weather"],
                        help="Strategies to run")
    parser.add_argument("--markets", nargs="+", default=[],
                        help="Token IDs / condition IDs to trade")
    return parser.parse_args()


def build_engine(settings, strategies: list[str], data_feed: DataFeed) -> Engine:
    """Construct the engine with selected strategies."""
    engine = Engine(settings)

    if "arbitrage" in strategies:
        engine.add_strategy(ArbitrageStrategy(settings, data_feed))

    if "market_making" in strategies:
        engine.add_strategy(MarketMakingStrategy(settings, BandsConfig()))

    if "copy_trading" in strategies:
        engine.add_strategy(CopyTradingStrategy(settings))

    if "ai_agent" in strategies:
        engine.add_strategy(AIAgentStrategy(settings))

    if "crypto_15m" in strategies:
        engine.add_strategy(Crypto15mStrategy(settings))

    if "weather" in strategies:
        engine.add_strategy(WeatherStrategy(settings))

    return engine


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    args = parse_args()
    settings = get_settings()

    if args.live:
        settings = settings.model_copy(update={"live_mode": True})

    logger = structlog.get_logger()
    mode = "LIVE" if settings.live_mode else "PAPER"
    logger.info("polymarket_edge_starting", mode=mode, strategies=args.strategy)

    data_feed = DataFeed(settings)
    engine = build_engine(settings, args.strategy, data_feed)

    await engine.start()

    if args.markets:
        await engine.run_market_loop(args.markets)
    else:
        logger.info("no_markets_specified", msg="Use --markets to specify token IDs, or use the dashboard to browse")

    await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
