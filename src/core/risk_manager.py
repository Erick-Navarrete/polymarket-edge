from datetime import date, datetime, timezone
from decimal import Decimal

import structlog

from src.core.config import Settings
from src.core.metrics import risk_violations
from src.core.strategy_base import TradeSignal

logger = structlog.get_logger()


class RiskViolation(Exception):
    """Raised when a trade signal violates risk limits."""


class RiskManager:
    """Centralized risk gate for all trade signals."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._daily_pnl: dict[date, Decimal] = {}
        self._monthly_pnl: dict[str, Decimal] = {}
        self._peak_equity: Decimal = Decimal("0")
        self._current_equity: Decimal = Decimal("0")
        self._total_exposure: Decimal = Decimal("0")
        self._daily_trade_count: dict[date, int] = {}
        self._halted = False
        self._halt_reason: str | None = None

    def set_equity(self, equity: Decimal) -> None:
        """Update current equity for drawdown tracking."""
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity
        self._check_circuit_breakers()

    def record_pnl(self, pnl: Decimal) -> None:
        """Record realized PnL for daily/monthly tracking."""
        today = date.today()
        month_key = today.strftime("%Y-%m")

        self._daily_pnl[today] = self._daily_pnl.get(today, Decimal("0")) + pnl
        self._monthly_pnl[month_key] = self._monthly_pnl.get(month_key, Decimal("0")) + pnl
        self._current_equity += pnl

        self._check_circuit_breakers()

    def update_exposure(self, exposure: Decimal) -> None:
        """Update total portfolio exposure."""
        self._total_exposure = exposure

    def approve(self, signal: TradeSignal, current_equity: Decimal) -> TradeSignal:
        """Validate a trade signal against all risk limits. Returns signal or raises."""
        if self._halted:
            risk_violations.labels(violation_type="halted").inc()
            raise RiskViolation(f"System halted: {self._halt_reason}")

        self.set_equity(current_equity)
        trade_value = signal.price * signal.size

        # Position size limit
        if trade_value > self.settings.max_position_size_usd:
            risk_violations.labels(violation_type="position_size").inc()
            raise RiskViolation(
                f"Position size ${trade_value} exceeds max ${self.settings.max_position_size_usd}"
            )

        # Total exposure limit
        projected_exposure = self._total_exposure + trade_value
        if projected_exposure > self.settings.max_total_exposure_usd:
            risk_violations.labels(violation_type="total_exposure").inc()
            raise RiskViolation(
                f"Total exposure ${projected_exposure} would exceed max ${self.settings.max_total_exposure_usd}"
            )

        # Daily loss limit
        today = date.today()
        daily_pnl = self._daily_pnl.get(today, Decimal("0"))
        if current_equity > 0:
            daily_loss_pct = abs(min(daily_pnl, Decimal("0"))) / current_equity * 100
            if daily_loss_pct >= self.settings.max_daily_loss_pct:
                risk_violations.labels(violation_type="daily_loss").inc()
                raise RiskViolation(
                    f"Daily loss {daily_loss_pct:.1f}% exceeds max {self.settings.max_daily_loss_pct}%"
                )

        # Monthly loss limit
        month_key = today.strftime("%Y-%m")
        monthly_pnl = self._monthly_pnl.get(month_key, Decimal("0"))
        if current_equity > 0:
            monthly_loss_pct = abs(min(monthly_pnl, Decimal("0"))) / current_equity * 100
            if monthly_loss_pct >= self.settings.max_monthly_loss_pct:
                risk_violations.labels(violation_type="monthly_loss").inc()
                raise RiskViolation(
                    f"Monthly loss {monthly_loss_pct:.1f}% exceeds max {self.settings.max_monthly_loss_pct}%"
                )

        # Max drawdown
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - self._current_equity) / self._peak_equity * 100
            if drawdown_pct >= self.settings.max_drawdown_pct:
                risk_violations.labels(violation_type="drawdown").inc()
                raise RiskViolation(
                    f"Drawdown {drawdown_pct:.1f}% exceeds max {self.settings.max_drawdown_pct}%"
                )

        logger.info(
            "trade_approved",
            strategy=signal.strategy,
            condition_id=signal.condition_id,
            side=signal.side,
            price=str(signal.price),
            size=str(signal.size),
        )
        return signal

    def _check_circuit_breakers(self) -> None:
        """Check if any circuit breaker should trigger a system halt."""
        if self._peak_equity <= 0:
            return

        drawdown_pct = (self._peak_equity - self._current_equity) / self._peak_equity * 100

        if drawdown_pct >= Decimal("40"):
            self._halted = True
            self._halt_reason = f"CRITICAL: Drawdown {drawdown_pct:.1f}% hit 40% permanent halt threshold"
            logger.critical(self._halt_reason)
        elif drawdown_pct >= self.settings.max_drawdown_pct:
            self._halted = True
            self._halt_reason = f"Drawdown {drawdown_pct:.1f}% exceeded {self.settings.max_drawdown_pct}% limit"
            logger.error(self._halt_reason)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    def status(self) -> dict:
        """Return current risk status for monitoring."""
        today = date.today()
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "daily_pnl": str(self._daily_pnl.get(today, Decimal("0"))),
            "peak_equity": str(self._peak_equity),
            "current_equity": str(self._current_equity),
            "total_exposure": str(self._total_exposure),
            "drawdown_pct": str(
                (self._peak_equity - self._current_equity) / self._peak_equity * 100
                if self._peak_equity > 0
                else Decimal("0")
            ),
        }
