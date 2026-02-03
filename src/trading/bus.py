"""In-process message buses (MVP).

We keep this deliberately simple:
- A single command queue consumed by the execution engine.
- An event pub/sub bus so multiple components can observe execution events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .models import ExecutionCommand, ExecutionEvent


class CommandBus:
    """Single-consumer command queue (PortfolioManager -> ExecutionEngine)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ExecutionCommand] = asyncio.Queue()

    async def put(self, cmd: ExecutionCommand) -> None:
        await self._queue.put(cmd)

    async def get(self) -> ExecutionCommand:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


class EventBus:
    """Fan-out event bus (ExecutionEngine -> subscribers)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[ExecutionEvent]] = set()

    def subscribe(self) -> asyncio.Queue[ExecutionEvent]:
        q: asyncio.Queue[ExecutionEvent] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[ExecutionEvent]) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: ExecutionEvent) -> None:
        for q in list(self._subscribers):
            await q.put(event)

    async def publish_many(self, events: Iterable[ExecutionEvent]) -> None:
        for event in events:
            await self.publish(event)

