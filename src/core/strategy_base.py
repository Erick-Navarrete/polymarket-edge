from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from src.core.config import Settings


class StrategyState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class MarketData:
    condition_id: str
    question: str
    yes_price: Decimal
    no_price: Decimal
    spread: Decimal
    volume_24h: Decimal
    timestamp: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeSignal:
    condition_id: str
    side: str  # "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"
    price: Decimal
    size: Decimal
    reason: str
    confidence: float = 0.0  # 0.0 - 1.0
    strategy: str = ""


@dataclass
class Position:
    condition_id: str
    side: str
    entry_price: Decimal
    size: Decimal
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    def update_price(self, current_price: Decimal) -> None:
        self.current_price = current_price
        if self.side in ("BUY_YES", "BUY_NO"):
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size


class Strategy(ABC):
    """Base class for all trading strategies."""

    def __init__(self, name: str, settings: Settings) -> None:
        self.name = name
        self.settings = settings
        self.state = StrategyState.STOPPED
        self.positions: dict[str, Position] = {}
        self._total_pnl = Decimal("0")

    @abstractmethod
    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        """Process incoming market data and return trade signals (if any)."""

    @abstractmethod
    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        """Handle a confirmed fill. Update position tracking."""

    async def start(self) -> None:
        """Initialize strategy resources (connections, models, etc.)."""
        self.state = StrategyState.RUNNING

    async def stop(self) -> None:
        """Clean up strategy resources."""
        self.state = StrategyState.STOPPED

    async def pause(self) -> None:
        """Temporarily pause signal generation."""
        self.state = StrategyState.PAUSED

    async def resume(self) -> None:
        """Resume from paused state."""
        self.state = StrategyState.RUNNING

    @property
    def total_pnl(self) -> Decimal:
        return self._total_pnl

    @property
    def total_exposure(self) -> Decimal:
        return sum(p.entry_price * p.size for p in self.positions.values())
