"""Portfolio manager (MVP).

This intentionally does NOT implement risk, sizing, or strategy logic yet.
It only:
- sends submit/cancel commands to the execution engine
- consumes execution events and maintains a minimal in-memory view of state
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ..bus import CommandBus, EventBus
from ..models import (
    CancelOrder,
    ExecutionEvent,
    FillUpdate,
    OrderRequest,
    OrderSubmitted,
    OrderUpdate,
    PositionSnapshot,
    SubmitOrder,
    TradeId,
    VenueOrderId,
)


class PortfolioManager:
    """Client-facing manager for submitting orders and tracking execution state.

    This component is intentionally minimal in the MVP:
    - It forwards submit/cancel commands to the execution engine.
    - It consumes execution events and maintains an in-memory view of order/position state.
    """

    def __init__(self, *, command_bus: CommandBus, event_bus: EventBus) -> None:
        """Create a portfolio manager attached to the given buses."""
        self._commands = command_bus
        self._events = event_bus

        self._subscription = self._events.subscribe()

        self._venue_order_by_trade: dict[TradeId, VenueOrderId] = {}
        self._order_status: dict[VenueOrderId, str] = {}
        self._order_fill_count: dict[VenueOrderId, int] = {}
        self._latest_positions: PositionSnapshot | None = None

        self._order_submitted_events: dict[TradeId, asyncio.Event] = {}

    @property
    def venue_order_by_trade(self) -> Mapping[TradeId, VenueOrderId]:
        """Map of `trade_id -> venue_order_id` observed so far."""
        return dict(self._venue_order_by_trade)

    @property
    def latest_positions(self) -> PositionSnapshot | None:
        """Most recent position snapshot observed from the execution engine."""
        return self._latest_positions

    async def run(self) -> None:
        """Consume execution events forever."""
        while True:
            event: ExecutionEvent = await self._subscription.get()
            await self._handle_event(event)

    async def submit_order(self, request: OrderRequest) -> None:
        """Submit an order via the execution engine."""
        self._order_submitted_events.setdefault(request.trade_id, asyncio.Event())
        await self._commands.put(SubmitOrder(request=request), stage="portfolio_manager")

    async def cancel_order(self, venue_order_id: VenueOrderId, *, reason: str | None = None) -> None:
        """Request cancellation of an existing order via the execution engine."""
        await self._commands.put(CancelOrder(venue_order_id=venue_order_id, reason=reason), stage="portfolio_manager")

    async def wait_for_order_submitted(self, trade_id: TradeId, *, timeout_s: float = 10.0) -> VenueOrderId:
        """Wait until we have a venue order id for a trade."""
        ev = self._order_submitted_events.setdefault(trade_id, asyncio.Event())
        await asyncio.wait_for(ev.wait(), timeout=timeout_s)
        return self._venue_order_by_trade[trade_id]

    async def _handle_event(self, event: ExecutionEvent) -> None:
        """Update local state in response to an execution event."""
        if isinstance(event, OrderSubmitted):
            self._venue_order_by_trade[event.trade_id] = event.venue_order_id
            self._order_status[event.venue_order_id] = "submitted"
            self._order_fill_count[event.venue_order_id] = 0
            self._order_submitted_events.setdefault(event.trade_id, asyncio.Event()).set()
            return

        if isinstance(event, OrderUpdate):
            self._order_status[event.venue_order_id] = event.status
            self._order_fill_count[event.venue_order_id] = event.fill_count
            return

        if isinstance(event, FillUpdate):
            self._order_fill_count[event.venue_order_id] = event.filled_total
            return

        if isinstance(event, PositionSnapshot):
            self._latest_positions = event
            return

