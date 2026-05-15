import asyncio
import json
from decimal import Decimal
from typing import AsyncIterator

import httpx
import structlog
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings
from src.core.strategy_base import MarketData

logger = structlog.get_logger()

WS_RECONNECT_DELAY = 3  # seconds between reconnect attempts


class DataFeed:
    """Unified market data pipeline from Polymarket CLOB + Gamma APIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http_client = httpx.AsyncClient(
            base_url=settings.clob_api_url,
            timeout=settings.order_timeout_seconds,
        )
        self._ws_url = settings.clob_ws_url
        self._token_metadata: dict[str, dict] = {}  # token_id -> {question, condition_id, ...}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Fetch available markets from Gamma API."""
        async with httpx.AsyncClient(
            base_url=self.settings.gamma_api_url,
            timeout=self.settings.order_timeout_seconds,
        ) as client:
            resp = await client.get(
                "/markets",
                params={"limit": limit, "offset": offset, "closed": False},
            )
            resp.raise_for_status()
            return resp.json()

    async def prefetch_metadata(self, token_ids: list[str]) -> None:
        """Pre-fetch market metadata from Gamma API so WebSocket data can be enriched.

        The CLOB WebSocket only sends price/orderbook data (no question text).
        We query Gamma /markets to get question, condition_id, and group them
        by clob token ID so on_data callbacks receive meaningful question text.
        """
        if not token_ids:
            return

        try:
            async with httpx.AsyncClient(
                base_url=self.settings.gamma_api_url,
                timeout=self.settings.order_timeout_seconds,
            ) as client:
                # Fetch enough markets to cover our token_ids
                batch_size = 100
                offset = 0
                found = 0
                needed = set(token_ids)

                while needed and offset < 1000:
                    resp = await client.get(
                        "/markets",
                        params={"limit": batch_size, "offset": offset, "closed": False, "order": "volume", "ascending": False},
                    )
                    if resp.status_code != 200:
                        break
                    markets = resp.json()
                    if not markets:
                        break

                    for m in markets:
                        raw = m.get("clobTokenIds", "[]")
                        ids = json.loads(raw) if isinstance(raw, str) else raw
                        question = m.get("question", "")
                        condition_id = m.get("conditionId", "")

                        for tid in ids:
                            if tid in needed:
                                self._token_metadata[tid] = {
                                    "question": question,
                                    "condition_id": condition_id,
                                }
                                needed.discard(tid)
                                found += 1

                    offset += batch_size

                logger.info("metadata_prefetched", requested=len(token_ids), found=found)

        except Exception as e:
            logger.warning("metadata_prefetch_failed", error=str(e))

    

    def set_metadata(self, token_id: str, question: str, condition_id: str) -> None:
        """Directly set metadata for a token (avoid re-fetching from Gamma API)."""
        self._token_metadata[token_id] = {
            "question": question,
            "condition_id": condition_id,
        }

    def _enrich_from_metadata(self, asset_id: str) -> dict:
        """Get metadata for a token_id from the prefetched cache."""
        return self._token_metadata.get(asset_id, {})

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_market(self, condition_id: str) -> dict:
        """Fetch a single market by condition ID."""
        resp = await self._http_client.get(f"/markets/{condition_id}")
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_orderbook(self, token_id: str) -> dict:
        """Fetch current orderbook for a token."""
        resp = await self._http_client.get("/book", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_midpoint(self, token_id: str) -> Decimal:
        """Get current midpoint price for a token."""
        resp = await self._http_client.get("/midpoint", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        return Decimal(str(data.get("mid", "0")))

    async def stream_market(self, token_ids: list[str]) -> AsyncIterator[MarketData]:
        """Stream real-time market data via WebSocket with auto-reconnect.

        Uses the Polymarket CLOB Market Channel WebSocket:
        wss://ws-subscriptions-clob.polymarket.com/ws/market
        Subscribe with assets_ids (token IDs) per the CLOB API spec.

        Server sends two message types:
        1. Initial snapshot: JSON list of orderbook dicts (asset_id, bids, asks)
        2. Incremental updates: dict with market + price_changes array
        """
        subscribe_msg = {
            "assets_ids": token_ids,
            "type": "market",
        }

        while True:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_timeout=30,
                    close_timeout=10,
                ) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("ws_subscribed", tokens=len(token_ids))

                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                            async for data in self._parse_ws_message(msg):
                                yield data
                        except Exception as e:
                            logger.warning("ws_msg_parse_error", error=str(e))

            except (
                websockets.ConnectionClosed,
                websockets.InvalidStatusCode,
                OSError,
            ) as e:
                logger.warning(
                    "ws_disconnected",
                    error=str(e),
                    reconnect_in=WS_RECONNECT_DELAY,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)
                logger.info("ws_reconnecting")

    async def _parse_ws_message(self, msg) -> AsyncIterator[MarketData]:
        """Parse a WebSocket message into one or more MarketData objects.

        Handles the actual CLOB Market Channel wire format:
        - Initial snapshot: JSON list of orderbook dicts
        - Incremental: dict with `market` + `price_changes` array
        """
        if isinstance(msg, list):
            # Initial orderbook snapshot — one entry per asset
            for entry in msg:
                if isinstance(entry, dict):
                    data = self._parse_orderbook_snapshot(entry)
                    if data:
                        yield data
            return

        if not isinstance(msg, dict):
            return

        # Incremental price change update
        price_changes = msg.get("price_changes", [])
        if price_changes:
            for change in price_changes:
                data = self._parse_price_change(change, msg.get("market", ""))
                if data:
                    yield data
            return

    def _parse_orderbook_snapshot(self, entry: dict) -> MarketData | None:
        """Parse an initial orderbook snapshot entry into MarketData."""
        try:
            asset_id = entry.get("asset_id", "")
            bids = entry.get("bids", [])
            asks = entry.get("asks", [])

            best_bid = Decimal(str(bids[0]["price"])) if bids else Decimal("0")
            best_ask = Decimal(str(asks[0]["price"])) if asks else Decimal("0")

            if best_bid <= 0 and best_ask <= 0:
                return None

            mid = (best_bid + best_ask) / Decimal("2") if (best_bid > 0 and best_ask > 0) else (best_bid or best_ask)
            spread = (best_ask - best_bid) if (best_bid > 0 and best_ask > 0) else Decimal("0")

            meta = self._enrich_from_metadata(asset_id)

            return MarketData(
                condition_id=meta.get("condition_id") or asset_id,
                question=meta.get("question", ""),
                yes_price=mid,
                no_price=Decimal("1") - mid,
                spread=spread,
                volume_24h=Decimal("0"),
                timestamp=int(entry.get("timestamp", 0)),
                raw=entry,
            )
        except Exception:
            logger.warning("ws_parse_error", raw=entry)
            return None

    def _parse_price_change(self, change: dict, market: str) -> MarketData | None:
        """Parse a single price_change entry from a price_changes array.

        Each entry has: asset_id, price, size, side, best_bid, best_ask, hash.
        """
        try:
            asset_id = change.get("asset_id", "")
            best_bid = Decimal(str(change.get("best_bid", "0")))
            best_ask = Decimal(str(change.get("best_ask", "0")))
            trade_price = Decimal(str(change.get("price", "0")))

            if best_bid > 0 and best_ask > 0:
                mid = (best_bid + best_ask) / Decimal("2")
                spread = best_ask - best_bid
            elif trade_price > 0:
                mid = trade_price
                spread = Decimal("0")
            else:
                return None

            if mid <= 0 or mid > Decimal("1"):
                return None

            meta = self._enrich_from_metadata(asset_id)

            return MarketData(
                condition_id=meta.get("condition_id") or asset_id,
                question=meta.get("question", ""),
                yes_price=mid,
                no_price=Decimal("1") - mid,
                spread=spread,
                volume_24h=Decimal(str(change.get("size", "0"))),
                timestamp=0,
                raw=change,
            )
        except Exception:
            logger.warning("ws_parse_error", raw=change)
            return None

    async def close(self) -> None:
        """Clean up HTTP client."""
        await self._http_client.aclose()
