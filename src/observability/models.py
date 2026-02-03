"""Observability record models.

Records are designed to be:
- Durable and append-only (sink decides storage).
- Easy to link across an end-to-end flow via correlation identifiers.
- Safe by default (store summaries + selected fields, not full raw payloads).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


RecordKind = Literal["command", "event", "error"]


class ObservabilityRecord(BaseModel):
    """A durable, structured record derived from an internal message/event."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    # High-level classification for downstream filtering.
    kind: RecordKind

    # A stable, human-readable type label (e.g., "submit_order", "order_update").
    event_type: str

    # Where in the pipeline the record was produced (e.g., "portfolio_manager").
    stage: str

    # Identifiers used to link records into an end-to-end flow.
    correlation_id: str | None = None
    trade_id: str | None = None
    venue_order_id: str | None = None

    # Timing fields.
    occurred_at: datetime
    logged_at: datetime = Field(default_factory=utc_now)

    # A structured summary + selected fields (avoid full raw payloads by default).
    summary: dict[str, Any] = Field(default_factory=dict)

