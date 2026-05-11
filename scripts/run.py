"""Run script — launch the full Polymarket Edge system."""

import asyncio
import argparse
import sys

import structlog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket Edge — Launch the trading system")
    parser.add_argument("--live", action="store_true", help="Enable live trading (default: paper mode)")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["arbitrage", "market_making"],
        choices=["arbitrage", "market_making", "copy_trading", "ai_agent", "crypto_15m", "weather", "all"],
        help="Strategies to run",
    )
    parser.add_argument("--markets", nargs="+", default=[], help="Token IDs / condition IDs to trade")
    parser.add_argument("--copy-wallets", nargs="+", default=[], help="Wallet addresses for copy trading")
    parser.add_argument("--equity", type=float, default=1000, help="Starting equity in USD")
    parser.add_argument("--dashboard", action="store_true", help="Also start the web dashboard")
    return parser.parse_args()


async def run_dashboard() -> None:
    """Start the FastAPI dashboard in a subprocess."""
    import uvicorn

    config = uvicorn.Config("src.dashboard.app:app", host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    args = parse_args()
    from decimal import Decimal

    from src.core.config import get_settings
    from src.core.data_feed import DataFeed
    from src.core.engine import Engine
    from src.core.wallet_tracker import WalletTracker
    from src.strategies.arbitrage import ArbitrageStrategy
    from src.strategies.market_making import MarketMakingStrategy, BandsConfig
    from src.strategies.copy_trading import CopyTradingStrategy, CopyTarget
    from src.strategies.ai_agent import AIAgentStrategy
    from src.strategies.crypto_15m import Crypto15mStrategy
    from src.strategies.weather import WeatherStrategy

    settings = get_settings()
    if args.live:
        settings = settings.model_copy(update={"live_mode": True})

    logger = structlog.get_logger()
    mode = "LIVE" if settings.live_mode else "PAPER"
    logger.info("starting_polymarket_edge", mode=mode, equity=args.equity)

    strategies = args.strategies
    if "all" in strategies:
        strategies = ["arbitrage", "market_making", "copy_trading", "ai_agent", "crypto_15m", "weather"]

    data_feed = DataFeed(settings)
    engine = Engine(settings)
    engine._equity = Decimal(str(args.equity))

    if "arbitrage" in strategies:
        engine.add_strategy(ArbitrageStrategy(settings, data_feed))

    if "market_making" in strategies:
        engine.add_strategy(MarketMakingStrategy(settings, BandsConfig()))

    if "copy_trading" in strategies:
        ct = CopyTradingStrategy(settings)
        for addr in args.copy_wallets:
            ct.add_target(CopyTarget(address=addr))
        engine.add_strategy(ct)

        # Start wallet tracker in background
        tracker = WalletTracker(settings)
        for addr in args.copy_wallets:
            tracker.add_wallet(addr)

    if "ai_agent" in strategies:
        engine.add_strategy(AIAgentStrategy(settings))

    if "crypto_15m" in strategies:
        engine.add_strategy(Crypto15mStrategy(settings))

    if "weather" in strategies:
        engine.add_strategy(WeatherStrategy(settings))

    await engine.start()

    tasks = []

    if args.markets:
        tasks.append(engine.run_market_loop(args.markets))

    if args.dashboard:
        tasks.append(run_dashboard())

    if tasks:
        await asyncio.gather(*tasks)
    else:
        logger.info("no_markets_or_dashboard", msg="Use --markets <ids> and/or --dashboard")
        # Keep running until interrupted
        try:
            while engine._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

    await engine.stop()
    logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
