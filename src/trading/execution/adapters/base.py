"""Venue adapter interface.

The execution engine depends on this small interface so venues (exchanges)
can be swapped without changing engine/portfolio code.
"""

from __future__ import annotations

from typing import Protocol

from ...models import OrderRequest, PositionSnapshot, VenueOrderId


class ExecutionAdapter(Protocol):
    async def place_order(self, request: OrderRequest) -> VenueOrderId:
        """Place an order and return the venue-assigned order id."""

    async def cancel_order(self, venue_order_id: VenueOrderId) -> None:
        """Cancel an existing order by venue order id."""

    async def get_order_status(self, venue_order_id: VenueOrderId) -> tuple[str, int]:
        """Return `(status, fill_count)` for an order."""

    async def get_positions_snapshot(self) -> PositionSnapshot:
        """Return a normalized position snapshot."""

