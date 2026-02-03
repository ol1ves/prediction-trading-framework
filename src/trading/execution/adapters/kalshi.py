"""Kalshi venue adapter for the execution engine (MVP)."""

from __future__ import annotations

from ...models import OrderRequest, Position, PositionSnapshot, Venue, VenueOrderId
from .base import ExecutionAdapter

from kalshi.client import KalshiClient
from kalshi.models import KalshiOrder


class KalshiExecutionAdapter(ExecutionAdapter):
    """ExecutionAdapter implementation backed by `KalshiClient`."""

    venue: Venue = "kalshi"

    def __init__(self, client: KalshiClient):
        """Create an adapter that will place/cancel/poll orders via the given client."""
        self._client = client

    async def place_order(self, request: OrderRequest) -> VenueOrderId:
        """Place a normalized `OrderRequest` as a Kalshi order and return order id."""
        order = KalshiOrder(
            ticker=request.ticker,
            side=request.side,
            action=request.action,
            type=request.order_type,
            count=request.count,
            client_order_id=request.client_order_id,
        )

        if request.order_type == "limit":
            if request.limit_price_dollars is None:
                raise ValueError("limit orders require request.limit_price_dollars")
            if request.side == "yes":
                order = order.model_copy(update={"yes_price_dollars": request.limit_price_dollars})
            else:
                order = order.model_copy(update={"no_price_dollars": request.limit_price_dollars})

        created = await self._client.create_order(order)
        if not created.order_id:
            raise RuntimeError("Kalshi create_order did not return order_id")
        return created.order_id

    async def cancel_order(self, venue_order_id: VenueOrderId) -> None:
        """Cancel an order by venue id."""
        await self._client.cancel_order(venue_order_id)

    async def get_order_status(self, venue_order_id: VenueOrderId) -> tuple[str, int]:
        """Return `(status, fill_count)` for the given venue order id."""
        o = await self._client.get_order(venue_order_id)
        status = str(o.status or "")
        fill_count = int(o.fill_count or 0)
        return status, fill_count

    async def get_positions_snapshot(self) -> PositionSnapshot:
        """Fetch and normalize current positions from Kalshi."""
        positions = await self._client.get_positions(limit=200)
        normalized = [
            Position(
                ticker=p.ticker,
                position=int(p.position or 0),
                market_exposure_dollars=float(p.market_exposure_dollars or 0.0),
                last_updated_ts=p.last_updated_ts,
            )
            for p in positions
        ]
        return PositionSnapshot(venue=self.venue, positions=normalized)

