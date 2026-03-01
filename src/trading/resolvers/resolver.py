"""Market resolver: structured subject -> market identity.

MVP: supports both:
- hardcoded subject -> ticker mappings (for stub/demo subjects), and
- pluggable domain-specific resolvers (e.g. WEATHER) that can call out to venues.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .subject import Subject

Venue = Literal["kalshi"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class MarketIdentity(_Model):
    """Resolved market identity for a subject (ticker + optional metadata)."""

    ticker: str
    venue: Venue = "kalshi"
    expires_at: datetime | None = None
    resolution_rules: dict[str, str] | None = None

    # Extended metadata (optional, used by structured resolvers such as WEATHER).
    event_ticker: str | None = None
    series_ticker: str | None = None
    subject: Subject | None = None
    bracket_low: float | None = None
    bracket_high: float | None = None


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@runtime_checkable
class SubjectResolver(Protocol):
    """Domain-scoped resolver for structured subjects."""

    domain: str

    def can_resolve(self, subject: Subject) -> bool: ...

    async def resolve(self, subject: Subject, for_date: date) -> MarketIdentity | None: ...


class MarketResolver:
    """Resolves subject -> MarketIdentity via plugins and/or hardcoded mapping."""

    def __init__(
        self,
        *,
        subject_to_ticker: dict[str, str] | None = None,
        resolvers: list[SubjectResolver] | None = None,
    ) -> None:
        """Create a resolver with optional plugins and hardcoded subject -> ticker map.

        If subject_to_ticker is None or empty, a default map is used (stub subject -> ABC).
        Callers can pass a dict to override or extend (e.g. from env). Plugins handle
        structured subjects by domain (e.g. WEATHER).
        """
        self._map = dict(subject_to_ticker) if subject_to_ticker else {}
        if not self._map:
            self._map["STUB_SUBJECT"] = "ABC"

        self._resolvers: dict[str, SubjectResolver] = {}
        if resolvers:
            for r in resolvers:
                # Last wins if duplicate domains are passed.
                self._resolvers[r.domain] = r

    async def resolve(
        self,
        subject_raw: str,
        timestamp: datetime | None = None,
        for_date: date | None = None,
    ) -> MarketIdentity | None:
        """Resolve a (possibly structured) subject to a MarketIdentity.

        Resolution order:
        1. Try to parse into a structured Subject and route to a domain plugin.
        2. If parsing fails or no plugin can handle it, fall back to the hardcoded
           subject -> ticker map.
        """
        try:
            subject = Subject.parse(subject_raw)
        except ValueError:
            subject = None

        if subject is not None:
            resolver = self._resolvers.get(subject.domain)
            if resolver and resolver.can_resolve(subject):
                resolved_date = for_date if for_date is not None else (timestamp or _utc_now()).date()
                return await resolver.resolve(subject, resolved_date)
            # Parsed subject but no resolver or resolver declined/returned None: do not use map.
            return None

        # Fallback: hardcoded mapping only when subject did not parse (e.g. STUB_SUBJECT).
        ticker = self._map.get(subject_raw)
        if ticker is None:
            return None
        return MarketIdentity(ticker=ticker)

