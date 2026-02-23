"""Strategy orchestrator: routes signals/snapshots to strategies and publishes intents."""

from __future__ import annotations

from ..bus import TradeIntentBus
from ..models import MarketSnapshot, Signal, TradeIntent
from .protocol import Strategy


class StrategyOrchestrator:
    """Thin layer between buses and strategies: subject-based routing and intent publishing.

    Subscribes to signal/snapshot buses (when present); for this iteration only
    tick_all() is used to drive strategies. Strategies never touch buses.
    """

    def __init__(self, intent_bus: TradeIntentBus) -> None:
        self._strategies: list[Strategy] = []
        self._latest_signals: dict[str, Signal] = {}
        self._latest_snapshots: dict[str, MarketSnapshot] = {}
        self._intent_bus = intent_bus

    def register(self, strategy: Strategy) -> None:
        """Register a strategy to be evaluated on relevant updates and on tick_all()."""
        self._strategies.append(strategy)

    async def on_signal(self, signal: Signal) -> None:
        """Record the latest signal for the subject and run affected strategies."""
        self._latest_signals[signal.subject] = signal
        await self._run_affected(signal.subject)

    async def on_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Record the latest market snapshot for the subject and run affected strategies."""
        self._latest_snapshots[snapshot.subject] = snapshot
        await self._run_affected(snapshot.subject)

    async def _run_affected(self, subject: str) -> None:
        """Run all strategies that care about this subject and publish their intents."""
        for strategy in self._strategies:
            if subject in strategy.subjects:
                intents = await strategy.evaluate(
                    self._latest_signals,
                    self._latest_snapshots,
                )
                for intent in intents:
                    await self._intent_bus.publish(intent)

    async def tick_all(self) -> None:
        """Run all registered strategies with current state and publish any intents.

        Used to drive timer-based strategies (e.g. stub) when signals/snapshots are empty.
        """
        print(f"Tick all strategies: {self._strategies}")
        for strategy in self._strategies:
            intents = await strategy.evaluate(
                self._latest_signals,
                self._latest_snapshots,
            )
            for intent in intents:
                await self._intent_bus.publish(intent)
