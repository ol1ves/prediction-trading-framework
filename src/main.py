from __future__ import annotations

import asyncio
import os
import time

from config import load_config
from kalshi.client import KalshiClient
from trading.bus import CommandBus, EventBus
from trading.execution.adapters.kalshi import KalshiExecutionAdapter
from trading.execution.engine import ExecutionEngine
from trading.models import OrderRequest
from trading.portfolio.manager import PortfolioManager


async def _log_events(event_bus: EventBus) -> None:
    q = event_bus.subscribe()
    while True:
        event = await q.get()
        print(f"[event] {event.type}: {event}")


async def run_demo() -> None:
    cfg = load_config()

    client = KalshiClient(cfg.kalshi)
    adapter = KalshiExecutionAdapter(client)

    command_bus = CommandBus()
    event_bus = EventBus()

    engine = ExecutionEngine(adapter=adapter, command_bus=command_bus, event_bus=event_bus)
    pm = PortfolioManager(command_bus=command_bus, event_bus=event_bus)

    engine_task = asyncio.create_task(engine.run(), name="execution-engine")
    pm_task = asyncio.create_task(pm.run(), name="portfolio-manager")
    log_task = asyncio.create_task(_log_events(event_bus), name="event-logger")

    # Minimal end-to-end exercise.
    # NOTE: These values are intentionally hardcoded for early testing and may fail
    # if the ticker is not valid/open or the price is not allowed for that market.
    trade_id = f"demo-{int(time.time() * 1000)}"

    ticker = os.getenv("DEMO_TICKER", "ABC")  # replace with a real demo ticker when testing
    side = os.getenv("DEMO_SIDE", "yes")
    price = float(os.getenv("DEMO_LIMIT_PRICE", "0.10"))

    await pm.submit_order(
        OrderRequest(
            trade_id=trade_id,
            venue="kalshi",
            ticker=ticker,
            side=side,  # type: ignore[arg-type]
            action="buy",
            count=1,
            order_type="limit",
            limit_price_dollars=price,
            client_order_id=trade_id,
        )
    )

    try:
        venue_order_id = await pm.wait_for_order_submitted(trade_id, timeout_s=10.0)
    except Exception:
        venue_order_id = None

    if venue_order_id is not None:
        await asyncio.sleep(30.0)
        await pm.cancel_order(venue_order_id, reason="demo cancel")

    await asyncio.sleep(5.0)

    for t in [log_task, pm_task, engine_task]:
        t.cancel()
    await asyncio.gather(log_task, pm_task, engine_task, return_exceptions=True)


def main() -> None:
    asyncio.run(run_demo())

if __name__ == "__main__":
    main()
