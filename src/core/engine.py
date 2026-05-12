"""Main orchestrator — wires strategies, data feeds, risk manager, and executor together."""

import asyncio
import signal as sig
import sys
from decimal import Decimal
from time import monotonic

import structlog

from src.core.config import Settings, get_settings
from src.core.data_feed import DataFeed
from src.core.executor import Executor
from src.core.metrics import (
    daily_pnl,
    drawdown_pct,
    equity_total,
    errors_total,
    execution_latency,
    fills_total,
    halted,
    strategy_pnl,
    strategy_state,
    total_exposure,
    trades_total,
)
from src.core.risk_manager import RiskManager
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

_STATE_VALUES = {"stopped": 0, "starting": 1, "running": 2, "paused": 3, "error": 4}


class Engine:
    """Central orchestrator for the Polymarket Edge trading system."""

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
        strategy_state.labels(strategy=strategy.name).set(0)
        logger.info("strategy_registered", name=strategy.name)

    def remove_strategy(self, name: str) -> None:
        """Remove a strategy by name."""
        self._strategies.pop(name, None)
        logger.info("strategy_removed", name=name)

    async def start(self) -> None:
        """Start the engine: initialize executor, start all strategies."""
        self._running = True
        halted.set(0)

        logger.info(
            "engine_starting",
            mode="LIVE" if self.settings.live_mode else ("SHADOW" if self.settings.shadow_mode else "PAPER"),
            strategies=list(self._strategies.keys()),
        )

        await self.executor.initialize()

        for strategy in self._strategies.values():
            await strategy.start()

        self._update_metrics()

        if sys.platform != "win32":
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

        self._update_metrics()

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

                total_exp = sum(s.total_exposure for s in self._strategies.values())
                self.risk_manager.update_exposure(total_exp)

                if self.risk_manager.is_halted:
                    logger.critical("risk_halt_triggered", reason=self.risk_manager.halt_reason)
                    halted.set(1)
                    await self.stop()
                    break

                self._update_metrics()

        except Exception as e:
            logger.error("market_loop_error", error=str(e))
            errors_total.labels(strategy="engine", error_type="loop").inc()
            await self.stop()

    async def _process_market_data(self, data: MarketData) -> None:
        """Send market data to all strategies and execute any resulting signals."""
        for strategy in self._strategies.values():
            try:
                signals = await strategy.on_data(data)

                for signal in signals:
                    t0 = monotonic()
                    result = await self.executor.execute(signal, self._equity)
                    execution_latency.labels(strategy=strategy.name).observe(monotonic() - t0)

                    trades_total.labels(strategy=strategy.name, side=signal.side).inc()

                    if result.success:
                        fills_total.labels(strategy=strategy.name, side=signal.side).inc()
                        await strategy.on_fill(signal, result.fill_price, result.fill_size)

                        if result.fill_price > 0 and result.fill_size > 0:
                            pnl_change = (signal.price - result.fill_price) * result.fill_size
                            self.risk_manager.record_pnl(pnl_change)

            except Exception as e:
                logger.error("strategy_error", strategy=strategy.name, error=str(e))
                errors_total.labels(strategy=strategy.name, error_type="processing").inc()

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
                trades_total.labels(strategy=strategy.name, side=signal.side).inc()

                if result.success:
                    fills_total.labels(strategy=strategy.name, side=signal.side).inc()
                    results["fills"].append(
                        {"side": signal.side, "fill_price": str(result.fill_price), "fill_size": str(result.fill_size)}
                    )

        results["risk_status"] = self.risk_manager.status()
        return results

    def _update_metrics(self) -> None:
        """Push current state to Prometheus gauges."""
        risk = self.risk_manager.status()
        equity_total.set(float(risk.get("current_equity", 0)))
        daily_pnl.set(float(risk.get("daily_pnl", 0)))
        drawdown_pct.set(float(risk.get("drawdown_pct", 0)))
        total_exposure.set(float(risk.get("total_exposure", 0)))

        if self.risk_manager.is_halted:
            halted.set(1)

        for name, strategy in self._strategies.items():
            strategy_pnl.labels(strategy=name).set(float(strategy.total_pnl))
            strategy_state.labels(strategy=name).set(_STATE_VALUES.get(strategy.state.value, 0))
