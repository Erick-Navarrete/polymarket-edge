"""On-chain wallet activity tracker for copy trading.

Polymarket trades are on Polygon — wallet addresses are public.
This module monitors profitable wallets via the Polymarket API
and the Polygon blockchain.
"""

import asyncio
from decimal import Decimal

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings

logger = structlog.get_logger()

POLYGON_SCAN_URL = "https://api.polygonscan.com/api"
POLYMARKET_ACTIVITY_URL = "https://data-api.polymarket.com"


class WalletTracker:
    """Monitor on-chain wallet activity for copy trading.

    Uses Polymarket's data API to fetch recent trades by wallet address,
    then pushes activity to the copy trading strategy.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(timeout=30)
        self._tracked_wallets: dict[str, dict] = {}  # address -> metadata
        self._known_positions: dict[str, dict[str, Decimal]] = {}  # addr -> {condition_id: size}
        self._poll_interval = 10  # seconds between checks

    def add_wallet(self, address: str, label: str = "") -> None:
        """Start tracking a wallet address."""
        self._tracked_wallets[address] = {"label": label}
        self._known_positions.setdefault(address, {})
        logger.info("wallet_tracking_started", address=address, label=label)

    def remove_wallet(self, address: str) -> None:
        """Stop tracking a wallet."""
        self._tracked_wallets.pop(address, None)
        self._known_positions.pop(address, None)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_positions(self, address: str) -> list[dict]:
        """Fetch current positions for a wallet from Polymarket API."""
        try:
            resp = await self._http.get(
                f"{POLYMARKET_ACTIVITY_URL}/positions",
                params={"user": address.lower()},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("wallet_fetch_failed", address=address, error=str(e))
            return []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_trade_history(self, address: str, limit: int = 50) -> list[dict]:
        """Fetch recent trade history for a wallet."""
        try:
            resp = await self._http.get(
                f"{POLYMARKET_ACTIVITY_URL}/trades",
                params={"user": address.lower(), "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("wallet_history_failed", address=address, error=str(e))
            return []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_leaderboard(self, period: str = "30d", limit: int = 20) -> list[dict]:
        """Fetch top profitable traders from Polymarket leaderboard."""
        try:
            resp = await self._http.get(
                f"{POLYMARKET_ACTIVITY_URL}/leaderboard",
                params={"period": period, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("leaderboard_fetch_failed", error=str(e))
            return []

    def detect_new_trades(
        self, address: str, current_positions: list[dict]
    ) -> list[dict]:
        """Compare current positions to known state and return new trades."""
        known = self._known_positions.get(address, {})
        new_trades = []

        for pos in current_positions:
            condition_id = pos.get("condition_id", "")
            current_size = Decimal(str(pos.get("size", "0")))
            known_size = known.get(condition_id, Decimal("0"))

            if current_size != known_size:
                delta = current_size - known_size
                new_trades.append({
                    "address": address,
                    "condition_id": condition_id,
                    "side": "BUY" if delta > 0 else "SELL",
                    "size": abs(delta),
                    "price": Decimal(str(pos.get("avg_price", "0"))),
                    "market": pos.get("question", ""),
                })
                known[condition_id] = current_size

        # Check for closed positions (was in known, not in current)
        current_ids = {p.get("condition_id", "") for p in current_positions}
        for cid in list(known.keys()):
            if cid not in current_ids and known[cid] > 0:
                new_trades.append({
                    "address": address,
                    "condition_id": cid,
                    "side": "CLOSE",
                    "size": known[cid],
                    "price": Decimal("0"),
                    "market": "",
                })
                known[cid] = Decimal("0")

        self._known_positions[address] = known
        return new_trades

    async def poll_loop(self, callback) -> None:
        """Continuously poll tracked wallets and call callback with new trades."""
        while True:
            for address in list(self._tracked_wallets.keys()):
                positions = await self.fetch_positions(address)
                new_trades = self.detect_new_trades(address, positions)

                for trade in new_trades:
                    logger.info(
                        "wallet_new_trade",
                        address=address,
                        condition_id=trade["condition_id"],
                        side=trade["side"],
                        size=str(trade["size"]),
                    )
                    await callback(trade)

            await asyncio.sleep(self._poll_interval)

    async def close(self) -> None:
        await self._http.aclose()
