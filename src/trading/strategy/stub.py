"""Stub strategy: emits a hardcoded trade intent (for testing and wiring)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..models import MarketSnapshot, Signal, TradeIntent


class StubStrategy:
    """Timer-driven strategy that ignores signals/snapshots and returns one hardcoded intent."""

    strategy_id = "stub_hardcoded"

    def __init__(
        self,
        *,
        subject: str = "STUB_SUBJECT",
        side: str = "YES",
        probability: float = 0.75,
        confidence: float = 0.9,
        rationale: str = "stub hardcoded intent",
    ) -> None:
        self.subjects = {subject}
        self._subject = subject
        self._side = "YES" if side.upper() == "YES" else "NO"
        self._probability = probability
        self._confidence = confidence
        self._rationale = rationale

    async def evaluate(
        self,
        signals: dict[str, Signal],
        snapshots: dict[str, MarketSnapshot],
    ) -> list[TradeIntent]:
        """Ignore inputs; return a single hardcoded TradeIntent with a new trade_id."""
        trade_id = str(uuid.uuid4())
        return [
            TradeIntent(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                subject=self._subject,
                side=self._side,
                probability=self._probability,
                confidence=self._confidence,
                rationale=self._rationale,
                timestamp=datetime.now(tz=timezone.utc),
            )
        ]
