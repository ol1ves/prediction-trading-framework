"""In-process message buses (MVP).

We keep this deliberately simple:
- A single command queue consumed by the execution engine.
- An event pub/sub bus so multiple components can observe execution events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .models import ExecutionCommand, ExecutionEvent
from observability.recorder import ObservabilityRecorder


class CommandBus:
    """Single-consumer command queue (PortfolioManager -> ExecutionEngine)."""

    def __init__(self, *, recorder: ObservabilityRecorder | None = None) -> None:
        """Create a command queue with optional observability recording."""
        self._queue: asyncio.Queue[ExecutionCommand] = asyncio.Queue()
        self._recorder = recorder

    async def put(self, cmd: ExecutionCommand, *, stage: str = "command_bus") -> None:
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


class EventBus:
    """Fan-out event bus (ExecutionEngine -> subscribers)."""

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

    async def publish(self, event: ExecutionEvent, *, stage: str = "event_bus") -> None:
        """Publish an event to all current subscribers (best-effort fan-out)."""
        if self._recorder is not None:
            await self._recorder.record_message(event, kind="event", stage=stage)
        for q in list(self._subscribers):
            await q.put(event)

    async def publish_many(self, events: Iterable[ExecutionEvent], *, stage: str = "event_bus") -> None:
        """Publish multiple events sequentially, preserving order."""
        for event in events:
            await self.publish(event, stage=stage)

