from __future__ import annotations

import pytest

from observability import InMemoryObservabilitySink, ObservabilityRecorder
from trading.bus import ExecutionCommandBus, ExecutionEventBus
from trading.models import OrderRequest, OrderSubmitted, SubmitOrder


@pytest.mark.asyncio
async def test_observability_records_commands_and_events() -> None:
    sink = InMemoryObservabilitySink()
    recorder = ObservabilityRecorder(sink=sink, max_queue_size=100)

    execution_command_bus = ExecutionCommandBus(recorder=recorder)
    execution_event_bus = ExecutionEventBus(recorder=recorder)

    trade_id = "t1"
    request = OrderRequest(
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

    await execution_command_bus.put(SubmitOrder(request=request), stage="portfolio_manager")
    await execution_event_bus.publish(
        OrderSubmitted(trade_id=trade_id, venue="kalshi", venue_order_id="OID1", request=request),
        stage="execution_engine",
    )

    await recorder.aclose()

    records = sink.snapshot()
    assert any(r.kind == "command" and r.event_type == "submit_order" and r.stage == "portfolio_manager" for r in records)
    assert any(
        r.kind == "event" and r.event_type == "order_submitted" and r.stage == "execution_engine" for r in records
    )
    assert all(r.correlation_id == trade_id for r in records)
    assert all(r.logged_at >= r.occurred_at for r in records)

