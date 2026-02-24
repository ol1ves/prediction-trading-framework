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
from trading.bus import ExecutionCommandBus, ExecutionEventBus, MarketSnapshotBus, TradeIntentBus
from trading.execution.adapters.kalshi import KalshiExecutionAdapter
from trading.execution.engine import ExecutionEngine
from trading.market_state import MarketStateService
from trading.models import OrderRequest
from trading.portfolio.manager import PortfolioManager
from trading.strategy import MarketResolver, StrategyOrchestrator, StubStrategy


async def _log_events(
    execution_event_bus: ExecutionEventBus,
    trade_intent_bus: TradeIntentBus,
    market_snapshot_bus: MarketSnapshotBus,
) -> None:
    """Subscribe to all event buses and print messages from each."""

    async def log_execution_events() -> None:
        q = execution_event_bus.subscribe()
        while True:
            event = await q.get()
            print(f"[execution_event] {event.type}: {event}")

    async def log_trade_intents() -> None:
        q = trade_intent_bus.subscribe()
        while True:
            intent = await q.get()
            print(f"[trade_intent] {intent}")

    async def log_market_snapshots() -> None:
        q = market_snapshot_bus.subscribe()
        while True:
            snapshot = await q.get()
            print(f"[market_snapshot] subject={snapshot.subject!r} implied_probability={snapshot.implied_probability} bid={snapshot.bid} ask={snapshot.ask} liquidity={snapshot.liquidity}")

    await asyncio.gather(
        log_execution_events(),
        log_trade_intents(),
        log_market_snapshots(),
    )

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
    market_state_poller_interval_s = float(os.getenv("MARKET_STATE_POLLER_INTERVAL_S", 30.0))

    trade_intent_bus = TradeIntentBus(recorder=recorder)
    market_snapshot_bus = MarketSnapshotBus(recorder=recorder)
    resolver = MarketResolver(subject_to_ticker={stub_subject: ticker})
    market_state_service = MarketStateService(
        market_resolver=resolver,
        adapter=adapter,
        market_snapshot_bus=market_snapshot_bus,
    )
    orchestrator = StrategyOrchestrator(
        intent_bus=trade_intent_bus,
        market_snapshot_bus=market_snapshot_bus,
    )

    pm = PortfolioManager(
        execution_command_bus=execution_command_bus,
        execution_event_bus=execution_event_bus,
        trade_intent_bus=trade_intent_bus,
        market_resolver=resolver,
        market_state_service=market_state_service,
    )

    engine_task = asyncio.create_task(engine.run(), name="execution-engine")
    pm_task = asyncio.create_task(pm.run(), name="portfolio-manager")
    intent_task = asyncio.create_task(pm.run_intent_consumer(), name="intent-consumer")
    log_task = asyncio.create_task(
        _log_events(execution_event_bus, trade_intent_bus, market_snapshot_bus),
        name="event-logger",
    )
    poller_task: asyncio.Task | None = None
    snapshot_consumer_task: asyncio.Task | None = None

    if os.getenv("RUN_STUB_STRATEGY", "true") == "true":
        stub = StubStrategy(subject=stub_subject)
        orchestrator.register(stub)
        all_subjects: set[str] = set()
        for s in orchestrator._strategies:
            all_subjects |= s.subjects
        market_state_service.add_subjects(all_subjects)
        poller_task = asyncio.create_task(
            market_state_service.run_poller(market_state_poller_interval_s),
            name="market-state-poller",
        )
        snapshot_consumer_task = asyncio.create_task(
            orchestrator.run_snapshot_consumer(),
            name="snapshot-consumer",
        )
        timer_task = asyncio.create_task(
            _stub_driven_loop(orchestrator, stub_interval_s),
            name="stub-timer",
        )
    else:
        timer_task = None

    try:
        tasks = [engine_task, pm_task, intent_task, log_task]
        if timer_task is not None:
            tasks.append(timer_task)
        if poller_task is not None:
            tasks.append(poller_task)
        if snapshot_consumer_task is not None:
            tasks.append(snapshot_consumer_task)
        await asyncio.gather(*tasks)
    finally:
        for t in [log_task, timer_task, intent_task, snapshot_consumer_task, poller_task, pm_task, engine_task]:
            if t is not None:
                t.cancel()
        await asyncio.gather(
            log_task, timer_task, intent_task, snapshot_consumer_task, poller_task, pm_task, engine_task,
            return_exceptions=True,
        )
        await recorder.aclose()


def main() -> None:
    """CLI entrypoint for running the demo with `python -m src.main` / `python src/main.py`."""
    asyncio.run(run_demo())

if __name__ == "__main__":
    main()
