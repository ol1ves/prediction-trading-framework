"""Execution engine (MVP).

Responsibilities (minimal):
- consume commands from the portfolio manager
- call a venue adapter to place/cancel
- poll the venue for order status/fill progress + positions
- publish normalized events for downstream consumers
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ..bus import CommandBus, EventBus
from ..models import (
    CancelOrder,
    ExecutionError,
    FillUpdate,
    OrderCanceled,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
    OrderUpdate,
    SubmitOrder,
    VenueOrderId,
)
from .adapters.base import ExecutionAdapter


class ExecutionEngine:
    def __init__(
        self,
        *,
        adapter: ExecutionAdapter,
        command_bus: CommandBus,
        event_bus: EventBus,
        poll_interval_s: float = 0.5,
        positions_interval_s: float = 2.0,
    ) -> None:
        self._adapter = adapter
        self._commands = command_bus
        self._events = event_bus

        self._poll_interval_s = poll_interval_s
        self._positions_interval_s = positions_interval_s

        # venue_order_id -> {status, fill_count}
        self._tracked: dict[VenueOrderId, dict[str, int | str]] = {}

    @property
    def tracked_orders(self) -> Mapping[VenueOrderId, dict[str, int | str]]:
        return dict(self._tracked)

    async def run(self) -> None:
        """Run command consumer and polling loops forever."""
        consumer = asyncio.create_task(self._consume_commands(), name="execution-consume-commands")
        poller = asyncio.create_task(self._poll_orders_loop(), name="execution-poll-orders")
        positions = asyncio.create_task(self._poll_positions_loop(), name="execution-poll-positions")
        await asyncio.gather(consumer, poller, positions)

    async def _consume_commands(self) -> None:
        while True:
            cmd = await self._commands.get()
            try:
                if isinstance(cmd, SubmitOrder):
                    await self._handle_submit(cmd.request)
                elif isinstance(cmd, CancelOrder):
                    await self._handle_cancel(cmd.venue_order_id, reason=cmd.reason)
                else:
                    await self._events.publish(
                        ExecutionError(message=f"Unknown command type: {type(cmd)!r}", retryable=False)
                    )
            finally:
                self._commands.task_done()

    async def _handle_submit(self, request: OrderRequest) -> None:
        try:
            venue_order_id = await self._adapter.place_order(request)
        except Exception as exc:  # noqa: BLE001 - normalize into event stream
            await self._events.publish(
                OrderRejected(
                    trade_id=request.trade_id,
                    venue=request.venue,
                    request=request,
                    message=str(exc),
                )
            )
            return

        self._tracked[venue_order_id] = {"status": "submitted", "fill_count": 0}
        await self._events.publish(
            OrderSubmitted(trade_id=request.trade_id, venue=request.venue, venue_order_id=venue_order_id, request=request)
        )

    async def _handle_cancel(self, venue_order_id: VenueOrderId, *, reason: str | None) -> None:
        try:
            await self._adapter.cancel_order(venue_order_id)
        except Exception as exc:  # noqa: BLE001 - normalize into event stream
            await self._events.publish(
                ExecutionError(
                    venue_order_id=venue_order_id,
                    message=f"cancel_order failed: {exc}",
                    retryable=True,
                )
            )
            return

        await self._events.publish(OrderCanceled(venue="kalshi", venue_order_id=venue_order_id, reason=reason))

    async def _poll_orders_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_s)
            if not self._tracked:
                continue

            for venue_order_id in list(self._tracked.keys()):
                try:
                    status, fill_count = await self._adapter.get_order_status(venue_order_id)
                except Exception as exc:  # noqa: BLE001 - keep going
                    await self._events.publish(
                        ExecutionError(
                            venue_order_id=venue_order_id,
                            message=f"get_order_status failed: {exc}",
                            retryable=True,
                        )
                    )
                    continue

                prev_status = str(self._tracked[venue_order_id].get("status", ""))
                prev_fill = int(self._tracked[venue_order_id].get("fill_count", 0))

                changed = (status != prev_status) or (fill_count != prev_fill)
                if not changed:
                    continue

                self._tracked[venue_order_id] = {"status": status, "fill_count": fill_count}

                await self._events.publish(
                    OrderUpdate(venue="kalshi", venue_order_id=venue_order_id, status=status, fill_count=fill_count)
                )

                if fill_count > prev_fill:
                    await self._events.publish(
                        FillUpdate(
                            venue="kalshi",
                            venue_order_id=venue_order_id,
                            filled_delta=fill_count - prev_fill,
                            filled_total=fill_count,
                        )
                    )

                if status in {"executed", "canceled"}:
                    self._tracked.pop(venue_order_id, None)

    async def _poll_positions_loop(self) -> None:
        while True:
            await asyncio.sleep(self._positions_interval_s)
            try:
                snapshot = await self._adapter.get_positions_snapshot()
            except Exception as exc:  # noqa: BLE001 - normalize and keep going
                await self._events.publish(
                    ExecutionError(message=f"get_positions_snapshot failed: {exc}", retryable=True)
                )
                continue
            await self._events.publish(snapshot)

