from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from trading.bus import CommandBus, EventBus
from trading.execution.engine import ExecutionEngine
from trading.models import OrderRequest, PositionSnapshot
from trading.portfolio.manager import PortfolioManager


class _FakeAdapter:
    def __init__(self) -> None:
        self._orders: dict[str, tuple[str, int]] = {}
        self._next = 1

    async def place_order(self, request: OrderRequest) -> str:
        oid = f"OID{self._next}"
        self._next += 1
        # Start as resting with 0 fills.
        self._orders[oid] = ("resting", 0)
        return oid

    async def cancel_order(self, venue_order_id: str) -> None:
        if venue_order_id not in self._orders:
            raise KeyError(venue_order_id)
        status, fills = self._orders[venue_order_id]
        self._orders[venue_order_id] = ("canceled", fills)

    async def get_order_status(self, venue_order_id: str) -> tuple[str, int]:
        return self._orders[venue_order_id]

    async def get_positions_snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(venue="kalshi", positions=[])

    def set_order(self, venue_order_id: str, *, status: str | None = None, fill_count: int | None = None) -> None:
        cur_status, cur_fills = self._orders[venue_order_id]
        self._orders[venue_order_id] = (status if status is not None else cur_status, fill_count if fill_count is not None else cur_fills)


@pytest.mark.asyncio
async def test_engine_and_pm_message_flow_submit_poll_fill_cancel() -> None:
    adapter = _FakeAdapter()
    command_bus = CommandBus()
    event_bus = EventBus()

    engine = ExecutionEngine(
        adapter=adapter,
        command_bus=command_bus,
        event_bus=event_bus,
        poll_interval_s=0.05,
        positions_interval_s=999.0,
    )
    pm = PortfolioManager(command_bus=command_bus, event_bus=event_bus)

    # Capture events directly for assertions.
    event_q = event_bus.subscribe()

    engine_task = asyncio.create_task(engine.run())
    pm_task = asyncio.create_task(pm.run())
    try:
        trade_id = "t1"
        await pm.submit_order(
            OrderRequest(
                trade_id=trade_id,
                venue="kalshi",
                ticker="ABC",
                side="yes",
                action="buy",
                count=1,
                order_type="limit",
                limit_price_dollars=0.10,
                client_order_id=trade_id,
            )
        )

        venue_order_id = await pm.wait_for_order_submitted(trade_id, timeout_s=2.0)
        assert venue_order_id.startswith("OID")

        # Wait for an order_update (first poll should transition from submitted -> resting).
        seen_update = False
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            ev = await asyncio.wait_for(event_q.get(), timeout=2.0)
            if ev.type == "order_update":
                seen_update = True
                break
        assert seen_update

        # Simulate fill progress and terminal status.
        adapter.set_order(venue_order_id, fill_count=1)
        adapter.set_order(venue_order_id, status="executed")

        seen_fill = False
        seen_executed = False
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline and not (seen_fill and seen_executed):
            ev = await asyncio.wait_for(event_q.get(), timeout=2.0)
            if ev.type == "fill_update":
                seen_fill = True
            if ev.type == "order_update" and getattr(ev, "status", "") == "executed":
                seen_executed = True
        assert seen_fill
        assert seen_executed

        # Canceling an executed order will still exercise the command path; adapter will mark canceled.
        await pm.cancel_order(venue_order_id, reason="test")
        # Wait for order_canceled event.
        seen_canceled = False
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            ev = await asyncio.wait_for(event_q.get(), timeout=2.0)
            if ev.type == "order_canceled":
                seen_canceled = True
                break
        assert seen_canceled
    finally:
        for t in [pm_task, engine_task]:
            t.cancel()
        with suppress(asyncio.CancelledError):
            await pm_task
        with suppress(asyncio.CancelledError):
            await engine_task

