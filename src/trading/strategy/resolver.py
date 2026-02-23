"""Market resolver: subject -> market identity (hardcoded for now).

Handles all market identity complexity; in this iteration only a hardcoded
mapping is implemented. No templates, cache, or API calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Venue = Literal["kalshi"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class MarketIdentity(_Model):
    """Resolved market identity for a subject (ticker + optional metadata)."""

    ticker: str
    venue: Venue = "kalshi"
    expires_at: datetime | None = None
    resolution_rules: dict[str, str] | None = None


class MarketResolver:
    """Resolves subject -> MarketIdentity. Hardcoded mapping only."""

    def __init__(self, *, subject_to_ticker: dict[str, str] | None = None) -> None:
        """Create a resolver with optional hardcoded subject -> ticker map.

        If subject_to_ticker is None or empty, a default map is used (stub subject -> ABC).
        Callers can pass a dict to override or extend (e.g. from env).
        """
        self._map = dict(subject_to_ticker) if subject_to_ticker else {}
        if not self._map:
            self._map["STUB_SUBJECT"] = "ABC"

    def resolve(self, subject: str, timestamp: datetime | None = None) -> MarketIdentity | None:
        """Resolve a subject to market identity. Returns None if unknown."""
        ticker = self._map.get(subject)
        if ticker is None:
            return None
        return MarketIdentity(ticker=ticker)
