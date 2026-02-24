"""In-process message buses (MVP).

Buses are named by the domain of messages they carry, so that multiple buses
can coexist without confusion. Current buses:

- ExecutionCommandBus: single-consumer queue for execution commands (submit/cancel);
  PortfolioManager -> ExecutionEngine.
- ExecutionEventBus: fan-out pub/sub for execution lifecycle events (submitted,
  fills, position snapshots); ExecutionEngine -> subscribers.
- TradeIntentBus: fan-out pub/sub for trade intents; StrategyOrchestrator -> subscribers
  (e.g. PortfolioManager).

- MarketSnapshotBus: fan-out pub/sub for market snapshots; MarketStateService -> subscribers
  (e.g. StrategyOrchestrator).

Planned buses (same naming pattern):
- SignalBus: signals published to subscribers that emit trade intents.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .models import ExecutionCommand, ExecutionEvent, MarketSnapshot, TradeIntent
from observability.recorder import ObservabilityRecorder


class ExecutionCommandBus:
    """Single-consumer queue for execution commands (PortfolioManager -> ExecutionEngine).

    Carries ExecutionCommand (SubmitOrder, CancelOrder) to the execution engine.
    """

    def __init__(self, *, recorder: ObservabilityRecorder | None = None) -> None:
        """Create a command queue with optional observability recording."""
        self._queue: asyncio.Queue[ExecutionCommand] = asyncio.Queue()
        self._recorder = recorder

    async def put(self, cmd: ExecutionCommand, *, stage: str = "execution_command_bus") -> None:
        """Enqueue a command for the execution engine.

        If a recorder is configured, the command is also recorded for observability.
        """
        if self._recorder is not None:
            await self._recorder.record_message(cmd, kind="command", stage=stage)
        await self._queue.put(cmd)

    async def get(self) -> ExecutionCommand:
        """Dequeue the next command (awaits until one is available)."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Mark the most recently processed command as done."""
        self._queue.task_done()


class ExecutionEventBus:
    """Fan-out bus for execution lifecycle events (ExecutionEngine -> subscribers).

    Carries ExecutionEvent (order submitted, updates, fills, position snapshots).
    """

    def __init__(self, *, recorder: ObservabilityRecorder | None = None) -> None:
        """Create an event fan-out bus with optional observability recording."""
        self._subscribers: set[asyncio.Queue[ExecutionEvent]] = set()
        self._recorder = recorder

    def subscribe(self) -> asyncio.Queue[ExecutionEvent]:
        """Create a new subscriber queue that will receive published events."""
        q: asyncio.Queue[ExecutionEvent] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[ExecutionEvent]) -> None:
        """Remove a subscriber queue (no further events will be delivered)."""
        self._subscribers.discard(q)

    async def publish(self, event: ExecutionEvent, *, stage: str = "execution_event_bus") -> None:
        """Publish an event to all current subscribers (best-effort fan-out)."""
        if self._recorder is not None:
            await self._recorder.record_message(event, kind="event", stage=stage)
        for q in list(self._subscribers):
            await q.put(event)

    async def publish_many(self, events: Iterable[ExecutionEvent], *, stage: str = "execution_event_bus") -> None:
        """Publish multiple events sequentially, preserving order."""
        for event in events:
            await self.publish(event, stage=stage)


class TradeIntentBus:
    """Fan-out bus for trade intents (StrategyOrchestrator -> subscribers, e.g. PortfolioManager).

    Carries TradeIntent from the strategy layer to the portfolio manager.
    """

    def __init__(self, *, recorder: ObservabilityRecorder | None = None) -> None:
        """Create an intent fan-out bus with optional observability recording."""
        self._subscribers: set[asyncio.Queue[TradeIntent]] = set()
        self._recorder = recorder

    def subscribe(self) -> asyncio.Queue[TradeIntent]:
        """Create a new subscriber queue that will receive published intents."""
        q: asyncio.Queue[TradeIntent] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TradeIntent]) -> None:
        """Remove a subscriber queue (no further intents will be delivered)."""
        self._subscribers.discard(q)

    async def publish(self, intent: TradeIntent, *, stage: str = "trade_intent_bus") -> None:
        """Publish an intent to all current subscribers (best-effort fan-out)."""
        if self._recorder is not None:
            await self._recorder.record_message(intent, kind="event", stage=stage)
        for queue in list(self._subscribers):
            await queue.put(intent)


class MarketSnapshotBus:
    """Fan-out bus for market snapshots (MarketStateService -> subscribers, e.g. StrategyOrchestrator).

    Carries MarketSnapshot so strategies receive normalized market belief updates by subject.
    """

    def __init__(self, *, recorder: ObservabilityRecorder | None = None) -> None:
        """Create a snapshot fan-out bus with optional observability recording."""
        self._subscribers: set[asyncio.Queue[MarketSnapshot]] = set()
        self._recorder = recorder

    def subscribe(self) -> asyncio.Queue[MarketSnapshot]:
        """Create a new subscriber queue that will receive published snapshots."""
        q: asyncio.Queue[MarketSnapshot] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[MarketSnapshot]) -> None:
        """Remove a subscriber queue (no further snapshots will be delivered)."""
        self._subscribers.discard(q)

    async def publish(self, snapshot: MarketSnapshot, *, stage: str = "market_snapshot_bus") -> None:
        """Publish a snapshot to all current subscribers (best-effort fan-out)."""
        if self._recorder is not None:
            await self._recorder.record_message(snapshot, kind="event", stage=stage)
        for queue in list(self._subscribers):
            await queue.put(snapshot)
