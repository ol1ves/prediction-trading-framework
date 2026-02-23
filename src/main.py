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
from trading.bus import ExecutionCommandBus, ExecutionEventBus, TradeIntentBus
from trading.execution.adapters.kalshi import KalshiExecutionAdapter
from trading.execution.engine import ExecutionEngine
from trading.models import OrderRequest
from trading.portfolio.manager import PortfolioManager
from trading.strategy import MarketResolver, StrategyOrchestrator, StubStrategy


async def _log_events(execution_event_bus: ExecutionEventBus) -> None:
    """Continuously print events observed on the given event bus."""
    q = execution_event_bus.subscribe()
    while True:
        event = await q.get()
        print(f"[event] {event.type}: {event}")

async def _stub_driven_loop(
    orchestrator: StrategyOrchestrator,
    interval_s: float,
) -> None:
    """Run orchestrator.tick_all() every interval_s until cancelled."""
    while True:
        await orchestrator.tick_all()
        await asyncio.sleep(interval_s)


async def run_demo() -> None:
    """Run a minimal end-to-end demo.

    When RUN_STUB_STRATEGY=true (default): stub strategy emits intents on a timer;
    PM consumes intents and submits orders. When false: legacy manual buy/sell flow.
    """
    cfg = load_config()

    client = KalshiClient(cfg.kalshi)
    adapter = KalshiExecutionAdapter(client)

    repo_root = Path(__file__).resolve().parent.parent
    db_path = os.getenv("OBSERVABILITY_DB_PATH", str(repo_root / "observability.duckdb"))
    recorder = ObservabilityRecorder(sink=DuckDBObservabilitySink(path=db_path))

    execution_command_bus = ExecutionCommandBus(recorder=recorder)
    execution_event_bus = ExecutionEventBus(recorder=recorder)

    engine = ExecutionEngine(
        adapter=adapter,
        execution_command_bus=execution_command_bus,
        execution_event_bus=execution_event_bus,
    )

    ticker = os.getenv("DEMO_TICKER", "ABC")
    stub_subject = os.getenv("STUB_STRATEGY_SUBJECT", "STUB_SUBJECT")
    stub_interval_s = float(os.getenv("STUB_STRATEGY_INTERVAL_S", 60.0))

    trade_intent_bus = TradeIntentBus(recorder=recorder)
    resolver = MarketResolver(subject_to_ticker={stub_subject: ticker})
    orchestrator = StrategyOrchestrator(intent_bus=trade_intent_bus)
    stub = StubStrategy(subject=stub_subject)
    orchestrator.register(stub)

    pm = PortfolioManager(
        execution_command_bus=execution_command_bus,
        execution_event_bus=execution_event_bus,
        trade_intent_bus=trade_intent_bus,
        market_resolver=resolver,
    )

    engine_task = asyncio.create_task(engine.run(), name="execution-engine")
    pm_task = asyncio.create_task(pm.run(), name="portfolio-manager")
    intent_task = asyncio.create_task(pm.run_intent_consumer(), name="intent-consumer")
    timer_task = asyncio.create_task(
        _stub_driven_loop(orchestrator, stub_interval_s),
        name="stub-timer",
    )
    log_task = asyncio.create_task(_log_events(execution_event_bus), name="event-logger")

    try:
        await asyncio.gather(engine_task, pm_task, intent_task, timer_task, log_task)
    finally:
        for t in [log_task, timer_task, intent_task, pm_task, engine_task]:
            t.cancel()
        await asyncio.gather(
            log_task, timer_task, intent_task, pm_task, engine_task,
            return_exceptions=True,
        )
        await recorder.aclose()


def main() -> None:
    """CLI entrypoint for running the demo with `python -m src.main` / `python src/main.py`."""
    asyncio.run(run_demo())

if __name__ == "__main__":
    main()
