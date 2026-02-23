"""Strategy protocol: declare interests and evaluate signals/snapshots to intents."""

from __future__ import annotations

from typing import Protocol

from ..models import MarketSnapshot, Signal, TradeIntent


class Strategy(Protocol):
    """Protocol for strategies that consume signals and snapshots and emit trade intents.

    Strategies declare which subjects they care about; the orchestrator routes
    updates by subject and calls evaluate with the latest state.
    """

    strategy_id: str
    subjects: set[str]

    async def evaluate(
        self,
        signals: dict[str, Signal],
        snapshots: dict[str, MarketSnapshot],
    ) -> list[TradeIntent]:
        """Produce trade intents from current signals and market snapshots.

        Called by the orchestrator when relevant data is updated or on tick_all().
        """
        ...
