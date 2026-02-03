"""Async recorder that writes observability records without blocking the event loop."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from .models import ObservabilityRecord, utc_now
from .sinks import ObservabilitySink


MessageKind = Literal["command", "event", "error"]


def _safe_getattr(obj: Any, name: str) -> Any:
    """Best-effort getattr that never raises (defensive for observability)."""
    try:
        return getattr(obj, name)
    except Exception:  # pragma: no cover - defensive
        return None


def _extract_event_type(message: Any) -> str:
    """Derive a stable event type label for a message.

    Prefers `message.type` when present; otherwise falls back to the class name.
    """
    msg_type = _safe_getattr(message, "type")
    if isinstance(msg_type, str) and msg_type:
        return msg_type
    return type(message).__name__


def _extract_trade_id(message: Any) -> str | None:
    """Extract a trade id from common message shapes (direct or nested request)."""
    trade_id = _safe_getattr(message, "trade_id")
    if isinstance(trade_id, str) and trade_id:
        return trade_id
    request = _safe_getattr(message, "request")
    trade_id = _safe_getattr(request, "trade_id")
    if isinstance(trade_id, str) and trade_id:
        return trade_id
    return None


def _extract_venue_order_id(message: Any) -> str | None:
    """Extract a venue order id from a message, if present."""
    venue_order_id = _safe_getattr(message, "venue_order_id")
    if isinstance(venue_order_id, str) and venue_order_id:
        return venue_order_id
    return None


def _extract_occurred_at(message: Any) -> datetime:
    """Extract a message timestamp, falling back to `utc_now()` when absent."""
    ts = _safe_getattr(message, "ts")
    if isinstance(ts, datetime):
        return ts
    return utc_now()


def _extract_summary(message: Any) -> dict[str, Any]:
    """Build a small, safe-to-store summary payload for a message.

    This intentionally avoids storing full raw payloads. It also performs a
    heuristic redaction pass for obvious secret-like fields.
    """
    # Prefer a Pydantic dump when available. Keep it small and stable:
    # - avoid full raw payloads by default
    # - allow adding more fields per event type later
    if hasattr(message, "model_dump"):
        data = message.model_dump()
    elif isinstance(message, dict):
        data = dict(message)
    else:
        data = {"repr": repr(message)}

    # Heuristic redaction: never store obvious secret-like fields.
    for key in ["api_key", "private_key", "secret", "token", "password"]:
        if key in data:
            data[key] = "[REDACTED]"

    # Drop nested request payloads by default; keep selected request fields if present.
    request = data.pop("request", None)
    if isinstance(request, dict):
        selected = {
            k: request.get(k)
            for k in [
                "trade_id",
                "venue",
                "ticker",
                "side",
                "action",
                "count",
                "order_type",
                "limit_price_dollars",
                "client_order_id",
            ]
            if k in request
        }
        if selected:
            data["request"] = selected

    return data


class ObservabilityRecorder:
    """Queues records and writes them in a background task."""

    def __init__(self, *, sink: ObservabilitySink, max_queue_size: int = 10000) -> None:
        """Create a recorder backed by a synchronous sink.

        Args:
            sink: Storage backend used by the background writer.
            max_queue_size: Bound for in-memory buffering; records may be dropped
                when full to avoid blocking trading.
        """
        self._sink = sink
        self._queue: asyncio.Queue[ObservabilityRecord | None] = asyncio.Queue(maxsize=max_queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

        # Degradation tracking (MVP): counts and time window.
        self._write_failures = 0
        self._first_failure_at: datetime | None = None
        self._last_failure_at: datetime | None = None

    def _ensure_started(self) -> None:
        """Start the background writer task if it hasn't been started yet."""
        if self._worker is not None:
            return
        self._worker = asyncio.create_task(self._run_worker(), name="observability-writer")

    async def record_message(
        self,
        message: Any,
        *,
        kind: MessageKind,
        stage: str,
        correlation_id: str | None = None,
    ) -> None:
        """Record a message by enqueueing an ObservabilityRecord (non-blocking)."""
        if self._closed:
            return

        self._ensure_started()

        trade_id = _extract_trade_id(message)
        venue_order_id = _extract_venue_order_id(message)
        corr = correlation_id or trade_id or venue_order_id

        record = ObservabilityRecord(
            kind=kind,
            event_type=_extract_event_type(message),
            stage=stage,
            correlation_id=corr,
            trade_id=trade_id,
            venue_order_id=venue_order_id,
            occurred_at=_extract_occurred_at(message),
            logged_at=utc_now(),
            summary=_extract_summary(message),
        )

        # In overload conditions we prefer dropping records over blocking trading.
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            # Track as a "write failure" window for degraded observability.
            now = utc_now()
            self._write_failures += 1
            self._first_failure_at = self._first_failure_at or now
            self._last_failure_at = now

    async def aclose(self) -> None:
        """Flush and close the recorder.

        Safe to call multiple times.
        """
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            await self._queue.put(None)
            await self._worker
        await asyncio.to_thread(self._sink.close)

    async def _run_worker(self) -> None:
        """Background loop that drains the queue and writes to the sink."""
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await asyncio.to_thread(self._sink.write, item)
            except Exception:  # noqa: BLE001 - observability must not crash trading
                now = utc_now()
                self._write_failures += 1
                self._first_failure_at = self._first_failure_at or now
                self._last_failure_at = now
            finally:
                self._queue.task_done()

    def degraded_status(self) -> dict[str, Any]:
        """Return a minimal degraded-status snapshot."""
        return {
            "write_failures": self._write_failures,
            "first_failure_at": self._first_failure_at,
            "last_failure_at": self._last_failure_at,
        }

