"""Copy trading strategy — follow profitable on-chain wallets."""

import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class CopyTarget:
    """A wallet being tracked for copy trading."""

    def __init__(
        self,
        address: str,
        label: str = "",
        copy_ratio: Decimal = Decimal("0.1"),  # Copy 10% of their position size
        max_copy_size: Decimal = Decimal("50"),
        min_win_rate: float = 0.6,
        min_profit_factor: float = 1.5,
    ) -> None:
        self.address = address
        self.label = label
        self.copy_ratio = copy_ratio
        self.max_copy_size = max_copy_size
        self.min_win_rate = min_win_rate
        self.min_profit_factor = min_profit_factor
        self.trades_tracked: int = 0
        self.wins: int = 0
        self.realized_pnl: Decimal = Decimal("0")
        self.current_positions: dict[str, dict] = {}

    @property
    def win_rate(self) -> float:
        if self.trades_tracked == 0:
            return 0.0
        return self.wins / self.trades_tracked

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(
            t.get("pnl", Decimal("0"))
            for t in self.current_positions.values()
            if t.get("pnl", Decimal("0")) > 0
        )
        gross_loss = abs(
            sum(
                t.get("pnl", Decimal("0"))
                for t in self.current_positions.values()
                if t.get("pnl", Decimal("0")) < 0
            )
        )
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return float(gross_profit / gross_loss)

    def is_qualified(self) -> bool:
        """Check if this target meets our quality thresholds."""
        return self.win_rate >= self.min_win_rate and self.profit_factor >= self.min_profit_factor


class CopyTradingStrategy(Strategy):
    """Monitor profitable wallets and replicate their trades.

    Uses on-chain data from Polymarket (wallet addresses are public)
    to track leading traders and mirror their positions.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="copy_trading", settings=settings)
        self._targets: dict[str, CopyTarget] = {}

    def add_target(self, target: CopyTarget) -> None:
        """Add a wallet to track."""
        self._targets[target.address] = target
        logger.info("copy_target_added", address=target.address, label=target.label)

    def remove_target(self, address: str) -> None:
        """Stop tracking a wallet."""
        self._targets.pop(address, None)
        logger.info("copy_target_removed", address=address)

    async def start(self) -> None:
        await super().start()
        logger.info("copy_trading_started", targets=len(self._targets))

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        """Process market data and check for copy trading signals.

        In a full implementation, this would:
        1. Poll on-chain activity for tracked wallets
        2. Detect new trades from qualified targets
        3. Generate copy signals scaled by copy_ratio
        """
        if self.state.value != "running":
            return []

        signals: list[TradeSignal] = []
        # Copy signals are driven by wallet activity, not market data
        # Market data is used for price validation of copy signals
        return signals

    def generate_copy_signal(
        self,
        target_address: str,
        condition_id: str,
        side: str,
        target_size: Decimal,
        price: Decimal,
    ) -> TradeSignal | None:
        """Generate a copy trade signal from a target wallet's activity."""
        target = self._targets.get(target_address)
        if not target:
            return None

        if not target.is_qualified():
            logger.debug(
                "copy_target_skipped",
                address=target_address,
                win_rate=target.win_rate,
                profit_factor=target.profit_factor,
            )
            return None

        copy_size = min(target_size * target.copy_ratio, target.max_copy_size)

        return TradeSignal(
            condition_id=condition_id,
            side=side,
            price=price,
            size=copy_size,
            reason=f"Copy {target.label or target_address[:8]}: {side} at {price}",
            confidence=target.win_rate,
            strategy=self.name,
        )

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "copy_fill",
            condition_id=signal.condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
            reason=signal.reason,
        )

    @property
    def targets(self) -> dict[str, CopyTarget]:
        return self._targets
