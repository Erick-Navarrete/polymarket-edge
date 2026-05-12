from decimal import Decimal

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings
from src.core.risk_manager import RiskManager, RiskViolation
from src.core.strategy_base import TradeSignal

logger = structlog.get_logger()


class ExecutionResult:
    """Result of an order execution attempt."""

    def __init__(
        self,
        signal: TradeSignal,
        success: bool,
        fill_price: Decimal = Decimal("0"),
        fill_size: Decimal = Decimal("0"),
        error: str | None = None,
    ) -> None:
        self.signal = signal
        self.success = success
        self.fill_price = fill_price
        self.fill_size = fill_size
        self.error = error


class Executor:
    """Order routing through py-clob-client with paper/live mode switching."""

    def __init__(self, settings: Settings, risk_manager: RiskManager) -> None:
        self.settings = settings
        self.risk_manager = risk_manager
        self._clob_client = None
        self._paper_fills: list[dict] = []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def initialize(self) -> None:
        """Set up the CLOB client for live trading (skipped in paper mode)."""
        if self.settings.live_mode:
            try:
                from py_clob_client.client import ClobClient

                self._clob_client = ClobClient(
                    self.settings.clob_api_url,
                    key=self.settings.polymarket_api_key,
                    chain_id=137,  # Polygon mainnet
                    timeout=self.settings.order_timeout_seconds,
                )
                logger.info("clob_client_initialized", mode="LIVE")
            except Exception as e:
                logger.error("clob_init_failed", error=str(e))
                raise
        else:
            logger.info("clob_client_initialized", mode="PAPER")

    async def execute(self, signal: TradeSignal, current_equity: Decimal) -> ExecutionResult:
        """Execute a trade signal after risk approval."""
        try:
            self.risk_manager.approve(signal, current_equity)
        except RiskViolation as e:
            logger.warning("trade_rejected", reason=str(e), strategy=signal.strategy)
            return ExecutionResult(signal, success=False, error=str(e))

        if self.settings.live_mode:
            return await self._execute_live(signal)
        else:
            return self._execute_paper(signal)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _execute_live(self, signal: TradeSignal) -> ExecutionResult:
        """Submit order to Polymarket CLOB."""
        if not self._clob_client:
            return ExecutionResult(signal, success=False, error="CLOB client not initialized")

        try:
            order = self._build_order(signal)
            result = self._clob_client.post_order(order, credentials=self._get_credentials())
            logger.info(
                "order_submitted",
                strategy=signal.strategy,
                condition_id=signal.condition_id,
                side=signal.side,
                order_id=result.get("orderID"),
            )
            return ExecutionResult(signal, success=True)
        except Exception as e:
            logger.error("order_failed", error=str(e), strategy=signal.strategy)
            return ExecutionResult(signal, success=False, error=str(e))

    def _execute_paper(self, signal: TradeSignal) -> ExecutionResult:
        """Simulate a fill in paper mode at the signal price."""
        fill_record = {
            "strategy": signal.strategy,
            "condition_id": signal.condition_id,
            "side": signal.side,
            "price": str(signal.price),
            "size": str(signal.size),
            "confidence": signal.confidence,
            "reason": signal.reason,
        }
        self._paper_fills.append(fill_record)
        logger.info(
            "paper_fill",
            **fill_record,
        )
        return ExecutionResult(
            signal,
            success=True,
            fill_price=signal.price,
            fill_size=signal.size,
        )

    def _build_order(self, signal: TradeSignal) -> dict:
        """Convert a TradeSignal to a CLOB order dict."""
        side_map = {
            "BUY_YES": "BUY",
            "BUY_NO": "BUY",
            "SELL_YES": "SELL",
            "SELL_NO": "SELL",
        }
        return {
            "condition_id": signal.condition_id,
            "side": side_map.get(signal.side, "BUY"),
            "price": float(signal.price),
            "size": float(signal.size),
            "order_type": "GTC",  # Good-til-cancel
        }

    def _get_credentials(self) -> dict:
        """Return API credentials for authenticated requests."""
        return {
            "api_key": self.settings.polymarket_api_key,
            "api_secret": self.settings.polymarket_api_secret,
            "api_passphrase": self.settings.polymarket_api_passphrase,
        }

    @property
    def paper_fills(self) -> list[dict]:
        """Access paper trading history."""
        return self._paper_fills.copy()
