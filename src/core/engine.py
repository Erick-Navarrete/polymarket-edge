"""Main orchestrator — wires strategies, data feeds, risk manager, and executor together."""

import asyncio
import signal as sig
from decimal import Decimal

import structlog

from src.core.config import Settings, get_settings
from src.core.data_feed import DataFeed
from src.core.executor import Executor
from src.core.risk_manager import RiskManager
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class Engine:
    """Central orchestrator for the Polymarket Edge trading system.

    Responsibilities:
    - Load and manage all active strategies
    - Route market data from the feed to each strategy
    - Route trade signals through risk management and execution
    - Handle fills and update position tracking
    - Graceful shutdown on SIGTERM/SIGINT
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.data_feed = DataFeed(self.settings)
        self.risk_manager = RiskManager(self.settings)
        self.executor = Executor(self.settings, self.risk_manager)
        self._strategies: dict[str, Strategy] = {}
        self._running = False
        self._equity = Decimal("1000")  # Default starting equity

    def add_strategy(self, strategy: Strategy) -> None:
        """Register a strategy with the engine."""
        self._strategies[strategy.name] = strategy
        logger.info("strategy_registered", name=strategy.name)

    def remove_strategy(self, name: str) -> None:
        """Remove a strategy by name."""
        self._strategies.pop(name, None)
        logger.info("strategy_removed", name=name)

    async def start(self) -> None:
        """Start the engine: initialize executor, start all strategies."""
        self._running = True
        logger.info(
            "engine_starting",
            mode="LIVE" if self.settings.live_mode else "PAPER",
            strategies=list(self._strategies.keys()),
        )

        await self.executor.initialize()

        for strategy in self._strategies.values():
            await strategy.start()

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for s in (sig.SIGTERM, sig.SIGINT):
            loop.add_signal_handler(s, lambda: asyncio.create_task(self.stop()))

        logger.info("engine_started")

    async def stop(self) -> None:
        """Graceful shutdown: stop all strategies, close connections."""
        self._running = False
        logger.info("engine_stopping")

        for strategy in self._strategies.values():
            await strategy.stop()

        await self.data_feed.close()
        logger.info("engine_stopped")

    async def run_market_loop(self, token_ids: list[str]) -> None:
        """Main loop: stream market data and process through strategy pipeline."""
        logger.info("market_loop_starting", tokens=len(token_ids))

        try:
            async for market_data in self.data_feed.stream_market(token_ids):
                if not self._running:
                    break

                await self._process_market_data(market_data)

                # Update risk manager exposure
                total_exposure = sum(s.total_exposure for s in self._strategies.values())
                self.risk_manager.update_exposure(total_exposure)

                if self.risk_manager.is_halted:
                    logger.critical("risk_halt_triggered", reason=self.risk_manager.halt_reason)
                    await self.stop()
                    break

        except Exception as e:
            logger.error("market_loop_error", error=str(e))
            await self.stop()

    async def _process_market_data(self, data: MarketData) -> None:
        """Send market data to all strategies and execute any resulting signals."""
        for strategy in self._strategies.values():
            try:
                signals = await strategy.on_data(data)

                for signal in signals:
                    result = await self.executor.execute(signal, self._equity)
                    if result.success:
                        await strategy.on_fill(signal, result.fill_price, result.fill_size)

                        # Update PnL tracking
                        if result.fill_price > 0 and result.fill_size > 0:
                            pnl_change = (signal.price - result.fill_price) * result.fill_size
                            self.risk_manager.record_pnl(pnl_change)

            except Exception as e:
                logger.error(
                    "strategy_error",
                    strategy=strategy.name,
                    error=str(e),
                )

    async def run_once(self, market_data: MarketData) -> dict:
        """Single-shot processing for testing — process one data point and return results."""
        results = {"signals": [], "fills": [], "risk_status": self.risk_manager.status()}

        for strategy in self._strategies.values():
            signals = await strategy.on_data(market_data)
            results["signals"].extend(
                [{"strategy": s.strategy, "side": s.side, "price": str(s.price), "size": str(s.size)} for s in signals]
            )

            for signal in signals:
                result = await self.executor.execute(signal, self._equity)
                if result.success:
                    results["fills"].append(
                        {"side": signal.side, "fill_price": str(result.fill_price), "fill_size": str(result.fill_size)}
                    )

        results["risk_status"] = self.risk_manager.status()
        return results
