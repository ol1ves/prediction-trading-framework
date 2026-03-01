"""MarketStateService: provides normalized market belief snapshots via query and subscription."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING

from ..bus import MarketSnapshotBus
from ..models import MarketSnapshot, TickerMarketSnapshot

if TYPE_CHECKING:
    from ..execution.adapters.base import ExecutionAdapter
    from ..resolvers import MarketResolver

logger = logging.getLogger(__name__)


class MarketStateService:
    """Provides normalized market belief snapshots for a subject.

    Uses MarketResolver to resolve subject -> ticker, then the execution adapter
    to fetch a ticker-scoped snapshot, normalizes to subject, and either returns
    (query path) or publishes to MarketSnapshotBus (subscription path via poller).
    """

    def __init__(
        self,
        *,
        market_resolver: MarketResolver,
        adapter: ExecutionAdapter,
        market_snapshot_bus: MarketSnapshotBus,
    ) -> None:
        self._resolver = market_resolver
        self._adapter = adapter
        self._bus = market_snapshot_bus
        self._tracked_subjects: set[str] = set()

    def add_subjects(self, subjects: set[str] | None = None, *subject: str) -> None:
        """Add subjects to the tracked set (for the poller)."""
        if subjects is not None:
            self._tracked_subjects |= set(subjects)
        if subject:
            self._tracked_subjects |= set(subject)

    async def get_latest(self, subject: str, for_date: date | None = None) -> MarketSnapshot | None:
        """Resolve subject to ticker, fetch snapshot from adapter, normalize to subject and return."""
        identity = await self._resolver.resolve(subject, for_date=for_date)
        if identity is None:
            return None
        try:
            raw = await self._adapter.get_market_snapshot(identity.ticker)
        except Exception as e:
            logger.warning("Adapter get_market_snapshot failed for ticker %s: %s", identity.ticker, e)
            return None
        return self._to_market_snapshot(subject, raw)

    async def get_latest_by_ticker(self, ticker: str) -> TickerMarketSnapshot | None:
        """Fetch ticker-scoped snapshot from adapter. No subject resolution."""
        try:
            return await self._adapter.get_market_snapshot(ticker)
        except Exception as e:
            logger.warning("Adapter get_market_snapshot failed for ticker %s: %s", ticker, e)
            return None

    async def get_latest_subject_snapshots(
        self,
        subjects: Iterable[str] | None = None,
    ) -> dict[str, MarketSnapshot]:
        """Return latest subject-scoped snapshots. If subjects is None, use tracked subjects."""
        to_fetch = list(subjects if subjects is not None else self._tracked_subjects)
        result: dict[str, MarketSnapshot] = {}
        for subj in to_fetch:
            snapshot = await self.get_latest(subj)
            if snapshot is not None:
                result[subj] = snapshot
        return result

    async def get_latest_ticker_snapshots(
        self,
        tickers: Iterable[str],
    ) -> dict[str, TickerMarketSnapshot]:
        """Return latest ticker-scoped snapshots for the given tickers."""
        result: dict[str, TickerMarketSnapshot] = {}
        for ticker in tickers:
            snapshot = await self.get_latest_by_ticker(ticker)
            if snapshot is not None:
                result[ticker] = snapshot
        return result

    async def run_poller(self, interval_s: float) -> None:
        """Run forever: for each tracked subject, fetch snapshot and publish to the bus."""
        while True:
            for subj in list(self._tracked_subjects):
                snapshot = await self.get_latest(subj)
                if snapshot is not None:
                    await self._bus.publish(snapshot)
            await asyncio.sleep(interval_s)

    @staticmethod
    def _to_market_snapshot(subject: str, raw: TickerMarketSnapshot) -> MarketSnapshot:
        """Build a subject-scoped MarketSnapshot from a ticker-scoped raw snapshot."""
        return MarketSnapshot(
            subject=subject,
            implied_probability=raw.implied_probability,
            bid=raw.bid,
            ask=raw.ask,
            spread=raw.spread,
            liquidity=raw.liquidity,
            time_to_resolution_minutes=raw.time_to_resolution_minutes,
            timestamp=raw.timestamp,
        )
