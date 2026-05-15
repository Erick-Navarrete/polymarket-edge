"""Copy trading strategy — follow profitable on-chain wallets and momentum signals."""

import time
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

    In shadow mode (without real wallet activity data), detects momentum
    signals — sharp price moves that often indicate whale activity — and
    follows the direction. When real wallet tracking is available via
    add_target(), uses explicit wallet-following logic.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="copy_trading", settings=settings)
        self._targets: dict[str, CopyTarget] = {}
        # Momentum-following state (used when no wallet targets are configured)
        self._price_history: dict[str, list[tuple[float, Decimal]]] = {}
        self._last_signal_time: dict[str, float] = {}
        self._momentum_lookback: int = 5  # Number of ticks to look back
        self._momentum_threshold: Decimal = Decimal("0.03")  # 3% move to trigger
        self._signal_cooldown: float = 30.0  # Seconds between signals per market

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
        if self.state.value != "running":
            return []

        # Cooldown check
        now = time.monotonic()
        last = self._last_signal_time.get(data.condition_id, 0)
        if now - last < self._signal_cooldown:
            return []

        signals: list[TradeSignal] = []

        # If we have wallet targets, try to generate explicit copy signals
        if self._targets:
            for target in self._targets.values():
                if target.is_qualified():
                    sig = self._follow_target(data, target)
                    if sig:
                        signals.append(sig)
                        break  # One signal per market tick

        # Momentum-following: detect sharp price moves (whale activity fingerprint)
        if not signals:
            momentum = self._detect_momentum(data)
            if momentum:
                signals.append(momentum)

        if signals:
            self._last_signal_time[data.condition_id] = now

        return signals

    def _follow_target(self, data: MarketData, target: CopyTarget) -> TradeSignal | None:
        """Generate a copy signal from a qualified target's inferred direction.

        In shadow mode, we infer the target's direction from price action
        since we can't read real wallet activity. When wallet_tracker is
        connected, it calls generate_copy_signal() directly.
        """
        # Follow the direction of significant moves — proxy for wallet activity
        momentum = self._detect_momentum(data)
        if momentum:
            copy_size = min(Decimal("50") * target.copy_ratio, target.max_copy_size)
            return TradeSignal(
                condition_id=data.condition_id,
                side=momentum.side,
                price=momentum.price,
                size=copy_size,
                reason=f"Copy {target.label or target.address[:8]}: following momentum {momentum.side}",
                confidence=target.win_rate,
                strategy=self.name,
            )
        return None

    def _detect_momentum(self, data: MarketData) -> TradeSignal | None:
        """Detect sharp price moves that suggest whale/informed activity."""
        history = self._price_history.get(data.condition_id, [])
        now = time.monotonic()
        history.append((now, data.yes_price))

        # Keep only recent history
        cutoff = now - 300  # 5 min window
        self._price_history[data.condition_id] = [(t, p) for t, p in history if t > cutoff]
        history = self._price_history[data.condition_id]

        if len(history) < self._momentum_lookback:
            return None

        # Compare current price to recent prices
        recent = history[-self._momentum_lookback:]
        avg_price = sum(p for _, p in recent) / Decimal(str(len(recent)))
        change = data.yes_price - avg_price

        # Normalize by average price to get percentage move
        if avg_price == 0:
            return None
        pct_move = abs(change) / avg_price

        if pct_move < self._momentum_threshold:
            return None

        # Follow the momentum direction
        if change > 0:
            side = "BUY_YES"
            price = data.yes_price
            reason = f"Momentum follow: +{pct_move:.1%} move over {len(recent)} ticks"
        else:
            side = "BUY_NO"
            price = data.no_price
            reason = f"Momentum follow: -{pct_move:.1%} move over {len(recent)} ticks"

        return TradeSignal(
            condition_id=data.condition_id,
            side=side,
            price=price,
            size=Decimal("5"),  # Conservative size for momentum trades
            reason=reason,
            confidence=float(min(pct_move / Decimal("0.10"), Decimal("0.8"))),
            strategy=self.name,
        )

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
            reason=f"Copy {target.label or target.address[:8]}: {side} at {price}",
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
