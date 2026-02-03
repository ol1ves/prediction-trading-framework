"""Demo entrypoint wiring together trading components.

This module intentionally contains a small, end-to-end "smoke test" that:

- Loads configuration from environment.
- Instantiates the Kalshi client and execution adapter.
- Starts the execution engine + portfolio manager.
- Places a buy and (optionally) a sell to exercise the plumbing.

It is **not** intended to be production orchestration logic; it is a convenient
manual integration harness while the framework is still being shaped.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

from config import load_config
from kalshi.client import KalshiClient
from observability import DuckDBObservabilitySink, ObservabilityRecorder
from trading.bus import CommandBus, EventBus
from trading.execution.adapters.kalshi import KalshiExecutionAdapter
from trading.execution.engine import ExecutionEngine
from trading.models import OrderRequest
from trading.portfolio.manager import PortfolioManager


async def _log_events(execution_event_bus: EventBus) -> None:
    """Continuously print events observed on the given event bus."""
    q = execution_event_bus.subscribe()
    while True:
        event = await q.get()
        print(f"[event] {event.type}: {event}")

async def _wait_for_fill_or_timeout(
    *,
    execution_event_bus: EventBus,
    venue_order_id: str,
    timeout_s: float,
) -> bool:
    """Wait until an order reaches an executed/filled state or timeout expires."""
    q = execution_event_bus.subscribe()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while loop.time() < deadline:
            remaining = max(0.0, deadline - loop.time())
            try:
                ev = await asyncio.wait_for(q.get(), timeout=remaining)
            except TimeoutError:
                break

            if getattr(ev, "venue_order_id", None) != venue_order_id:
                continue

            if getattr(ev, "type", None) == "order_update":
                status = getattr(ev, "status", "")
                if status in {"executed", "filled"}:
                    return True
    finally:
        execution_event_bus.unsubscribe(q)

    return False


async def _wait_for_cancel_or_timeout(
    *,
    execution_event_bus: EventBus,
    venue_order_id: str,
    timeout_s: float,
) -> bool:
    """Wait until we observe an order_canceled event or timeout expires."""
    q = execution_event_bus.subscribe()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while loop.time() < deadline:
            remaining = max(0.0, deadline - loop.time())
            try:
                ev = await asyncio.wait_for(q.get(), timeout=remaining)
            except TimeoutError:
                break

            if getattr(ev, "type", None) != "order_canceled":
                continue
            if getattr(ev, "venue_order_id", None) != venue_order_id:
                continue
            return True
    finally:
        execution_event_bus.unsubscribe(q)

    return False


async def run_demo() -> None:
    """Run a minimal end-to-end demo placing a buy then sell (best-effort)."""
    cfg = load_config()

    client = KalshiClient(cfg.kalshi)
    adapter = KalshiExecutionAdapter(client)

    repo_root = Path(__file__).resolve().parent.parent
    db_path = os.getenv("OBSERVABILITY_DB_PATH", str(repo_root / "observability.duckdb"))
    recorder = ObservabilityRecorder(sink=DuckDBObservabilitySink(path=db_path))

    execution_command_bus = CommandBus(recorder=recorder)
    execution_event_bus = EventBus(recorder=recorder)

    engine = ExecutionEngine(adapter=adapter, command_bus=execution_command_bus, event_bus=execution_event_bus)
    pm = PortfolioManager(command_bus=execution_command_bus, event_bus=execution_event_bus)

    engine_task = asyncio.create_task(engine.run(), name="execution-engine")
    pm_task = asyncio.create_task(pm.run(), name="portfolio-manager")
    log_task = asyncio.create_task(_log_events(execution_event_bus), name="event-logger")
    try:
        # Minimal end-to-end exercise.
        # NOTE: These values are intentionally hardcoded for early testing and may fail
        # if the ticker is not valid/open or the price is not allowed for that market.
        trade_id = f"demo-{int(time.time() * 1000)}"

        ticker = os.getenv("DEMO_TICKER", "ABC")  # replace with a real demo ticker when testing
        side = os.getenv("DEMO_SIDE", "yes")
        price = float(os.getenv("DEMO_LIMIT_PRICE", "0.10"))

        buy_request = OrderRequest(
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
        await pm.submit_order(buy_request)

        try:
            buy_venue_order_id = await pm.wait_for_order_submitted(trade_id, timeout_s=10.0)
        except Exception:
            buy_venue_order_id = None

        if buy_venue_order_id is None:
            return

        buy_filled = await _wait_for_fill_or_timeout(
            execution_event_bus=execution_event_bus,
            venue_order_id=buy_venue_order_id,
            timeout_s=30.0,
        )

        if not buy_filled:
            await pm.cancel_order(buy_venue_order_id, reason="demo buy timeout")
            await _wait_for_cancel_or_timeout(
                execution_event_bus=execution_event_bus,
                venue_order_id=buy_venue_order_id,
                timeout_s=5.0,
            )
            return

        # Order filled. Wait briefly, then place a sell and wait for fill or cancel on timeout.
        await asyncio.sleep(5.0)

        sell_trade_id = f"{trade_id}-sell"
        sell_request = OrderRequest(
            trade_id=sell_trade_id,
            venue="kalshi",
            ticker=ticker,
            side=side,  # type: ignore[arg-type]
            action="sell",
            count=1,
            order_type="limit",
            limit_price_dollars=price-0.2,
            client_order_id=sell_trade_id,
        )
        await pm.submit_order(sell_request)

        try:
            sell_venue_order_id = await pm.wait_for_order_submitted(sell_trade_id, timeout_s=10.0)
        except Exception:
            sell_venue_order_id = None

        if sell_venue_order_id is None:
            return

        sell_filled = await _wait_for_fill_or_timeout(
            execution_event_bus=execution_event_bus,
            venue_order_id=sell_venue_order_id,
            timeout_s=30.0,
        )
        if sell_filled:
            return

        await pm.cancel_order(sell_venue_order_id, reason="demo sell timeout")
        await _wait_for_cancel_or_timeout(
            execution_event_bus=execution_event_bus,
            venue_order_id=sell_venue_order_id,
            timeout_s=5.0,
        )
    finally:
        for t in [log_task, pm_task, engine_task]:
            t.cancel()
        await asyncio.gather(log_task, pm_task, engine_task, return_exceptions=True)
        await recorder.aclose()


def main() -> None:
    """CLI entrypoint for running the demo with `python -m src.main` / `python src/main.py`."""
    asyncio.run(run_demo())

if __name__ == "__main__":
    main()
