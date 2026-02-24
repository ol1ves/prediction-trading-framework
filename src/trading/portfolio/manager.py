"""Portfolio manager (MVP).

This intentionally does NOT implement risk, sizing, or strategy logic yet.
It only:
- sends submit/cancel commands to the execution engine
- consumes execution events and maintains a minimal in-memory view of state
- when TradeIntentBus and MarketResolver are provided, consumes intents and submits orders
- when MarketStateService is provided, uses latest market snapshot for position sizing
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..bus import ExecutionCommandBus, ExecutionEventBus, TradeIntentBus
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
from ..models import TradeIntent
from ..strategy.resolver import MarketResolver

if TYPE_CHECKING:
    from ..market_state import MarketStateService

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Client-facing manager for submitting orders and tracking execution state.

    This component is intentionally minimal in the MVP:
    - It forwards submit/cancel commands to the execution engine.
    - It consumes execution events and maintains an in-memory view of order/position state.
    """

    def __init__(
        self,
        *,
        execution_command_bus: ExecutionCommandBus,
        execution_event_bus: ExecutionEventBus,
        trade_intent_bus: TradeIntentBus | None = None,
        market_resolver: MarketResolver | None = None,
        market_state_service: MarketStateService | None = None,
    ) -> None:
        """Create a portfolio manager attached to the given buses.

        If trade_intent_bus and market_resolver are both provided, the manager
        will consume intents via run_intent_consumer() and submit orders.
        If market_state_service is provided, get_latest() is used for position sizing.
        """
        self._commands = execution_command_bus
        self._events = execution_event_bus
        self._intent_bus = trade_intent_bus
        self._resolver = market_resolver
        self._market_state_service = market_state_service

        self._event_subscription = self._events.subscribe()
        self._intent_subscription = trade_intent_bus.subscribe() if trade_intent_bus else None

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
            event: ExecutionEvent = await self._event_subscription.get()
            await self._handle_event(event)

    async def run_intent_consumer(self) -> None:
        """Consume trade intents forever and submit orders via the market resolver.

        Must only be called when trade_intent_bus and market_resolver were provided.
        """
        if self._intent_subscription is None or self._resolver is None:
            raise RuntimeError("run_intent_consumer requires trade_intent_bus and market_resolver")
        while True:
            intent: TradeIntent = await self._intent_subscription.get()
            await self._handle_intent(intent)

    async def _handle_intent(self, intent: TradeIntent) -> None:
        """Resolve subject to ticker, build OrderRequest, and submit."""
        identity = self._resolver.resolve(intent.subject, intent.timestamp)
        if identity is None:
            logger.warning("No market identity for subject %r; skipping intent %s", intent.subject, intent.trade_id)
            return
        count = 1
        if self._market_state_service is not None:
            snapshot = await self._market_state_service.get_latest(intent.subject)
            if snapshot is not None:
                if snapshot.liquidity == "high":
                    count = 3
                elif snapshot.liquidity == "medium":
                    count = 2
                else:
                    count = 1
        side = "yes" if intent.side == "YES" else "no"
        request = OrderRequest(
            trade_id=intent.trade_id,
            venue=identity.venue,
            ticker=identity.ticker,
            side=side,
            action="buy",
            count=count,
            order_type="limit",
            limit_price_dollars=intent.probability,
            client_order_id=intent.trade_id,
        )
        await self.submit_order(request)

    async def submit_order(self, request: OrderRequest) -> None:
        """Submit an order directly via the execution engine."""
        self._order_submitted_events.setdefault(request.trade_id, asyncio.Event())
        await self._commands.put(SubmitOrder(request=request), stage="portfolio_manager")

    async def cancel_order(self, venue_order_id: VenueOrderId, *, reason: str | None = None) -> None:
        """Request cancellation of an existing order directly via the execution engine."""
        await self._commands.put(CancelOrder(venue_order_id=venue_order_id, reason=reason), stage="portfolio_manager")

    async def wait_for_order_submitted(self, trade_id: TradeId, *, timeout_s: float = 10.0) -> VenueOrderId:
        """Wait until we have a venue order id for a trade directly via the execution engine."""
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

