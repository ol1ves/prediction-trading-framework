"""Portfolio manager (MVP).

Sizing is a function of edge and bankroll with guardrails; every intent produces
a structured decision log. Bankroll is a fixed configured value for the MVP and
will need to be replaced by a dynamic value derived from execution events once
real P&L tracking is implemented.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from ..bus import ExecutionCommandBus, ExecutionEventBus, TradeIntentBus
from ..models import (
    CancelOrder,
    ExecutionEvent,
    FillUpdate,
    MarketSnapshot,
    OrderRequest,
    OrderSubmitted,
    OrderUpdate,
    PositionSnapshot,
    SubmitOrder,
    TradeId,
    VenueOrderId,
)
from ..models import TradeIntent
from ..strategy.resolver import MarketResolver

if TYPE_CHECKING:
    from ..market_state import MarketStateService

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class PortfolioManagerConfigLike(Protocol):
    """Protocol for PM config (avoids importing app config into trading package)."""

    kelly_fraction: float
    min_edge_threshold: float
    max_position_fraction: float
    bankroll: float


@dataclass
class PMDecisionRecord:
    """Structured decision log for one intent (order or rejection)."""

    type: str = "pm_decision"
    trade_id: str = ""
    strategy_id: str = ""
    subject: str = ""
    trade_intent_probability: float | None = None
    confidence: float | None = None
    implied_probability: float | None = None
    bid: float | None = None
    ask: float | None = None
    edge: float | None = None
    full_kelly_fraction: float | None = None
    fractional_kelly_fraction: float | None = None
    uncapped_dollar_size: float | None = None
    capped_dollar_size: float | None = None
    final_contract_count: int | None = None
    rejection_reason: str | None = None
    timestamp: datetime = field(default_factory=_utc_now)

    def to_log_dict(self) -> dict[str, Any]:
        """JSON-serializable dict for logging and observability."""
        out: dict[str, Any] = {
            "type": self.type,
            "trade_id": self.trade_id,
            "strategy_id": self.strategy_id,
            "subject": self.subject,
            "rejection_reason": self.rejection_reason,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.trade_intent_probability is not None:
            out["trade_intent_probability"] = self.trade_intent_probability
        if self.confidence is not None:
            out["confidence"] = self.confidence
        if self.implied_probability is not None:
            out["implied_probability"] = self.implied_probability
        if self.bid is not None:
            out["bid"] = self.bid
        if self.ask is not None:
            out["ask"] = self.ask
        if self.edge is not None:
            out["edge"] = self.edge
        if self.full_kelly_fraction is not None:
            out["full_kelly_fraction"] = self.full_kelly_fraction
        if self.fractional_kelly_fraction is not None:
            out["fractional_kelly_fraction"] = self.fractional_kelly_fraction
        if self.uncapped_dollar_size is not None:
            out["uncapped_dollar_size"] = self.uncapped_dollar_size
        if self.capped_dollar_size is not None:
            out["capped_dollar_size"] = self.capped_dollar_size
        if self.final_contract_count is not None:
            out["final_contract_count"] = self.final_contract_count
        return out


class PMDecisionObservabilityMessage(BaseModel):
    """Wrapper for observability recorder so event_type is pm_decision and summary is full payload."""

    model_config = ConfigDict(extra="allow", frozen=False)

    type: Literal["pm_decision"] = "pm_decision"
    trade_id: str = ""
    strategy_id: str = ""
    subject: str = ""
    rejection_reason: str | None = None
    timestamp: str = ""
    ts: datetime | None = None  # For recorder occurred_at
    trade_intent_probability: float | None = None
    confidence: float | None = None
    implied_probability: float | None = None
    bid: float | None = None
    ask: float | None = None
    edge: float | None = None
    full_kelly_fraction: float | None = None
    fractional_kelly_fraction: float | None = None
    uncapped_dollar_size: float | None = None
    capped_dollar_size: float | None = None
    final_contract_count: int | None = None

    @classmethod
    def from_record(cls, record: PMDecisionRecord) -> PMDecisionObservabilityMessage:
        d = record.to_log_dict()
        return cls(
            type="pm_decision",
            trade_id=d.get("trade_id", ""),
            strategy_id=d.get("strategy_id", ""),
            subject=d.get("subject", ""),
            rejection_reason=d.get("rejection_reason"),
            timestamp=d.get("timestamp", ""),
            ts=record.timestamp,
            trade_intent_probability=d.get("trade_intent_probability"),
            confidence=d.get("confidence"),
            implied_probability=d.get("implied_probability"),
            bid=d.get("bid"),
            ask=d.get("ask"),
            edge=d.get("edge"),
            full_kelly_fraction=d.get("full_kelly_fraction"),
            fractional_kelly_fraction=d.get("fractional_kelly_fraction"),
            uncapped_dollar_size=d.get("uncapped_dollar_size"),
            capped_dollar_size=d.get("capped_dollar_size"),
            final_contract_count=d.get("final_contract_count"),
        )


class PortfolioManager:
    """Client-facing manager for submitting orders and tracking execution state.

    Uses config for sizing (Kelly fraction, edge threshold, position cap, bankroll)
    and emits a structured decision log for every intent.
    """

    def __init__(
        self,
        *,
        execution_command_bus: ExecutionCommandBus,
        execution_event_bus: ExecutionEventBus,
        config: PortfolioManagerConfigLike,
        trade_intent_bus: TradeIntentBus | None = None,
        market_resolver: MarketResolver | None = None,
        market_state_service: MarketStateService | None = None,
        recorder: Any = None,
    ) -> None:
        """Create a portfolio manager attached to the given buses and config.

        If trade_intent_bus, market_resolver, and market_state_service are all
        provided, run_intent_consumer() will consume intents and submit orders
        using the six-step pipeline. config is required for intent handling.
        recorder, when provided, receives each PM decision for observability.
        """
        self._commands = execution_command_bus
        self._events = execution_event_bus
        self._config = config
        self._intent_bus = trade_intent_bus
        self._resolver = market_resolver
        self._market_state_service = market_state_service
        self._recorder = recorder

        self._event_subscription = self._events.subscribe()
        self._intent_subscription = trade_intent_bus.subscribe() if trade_intent_bus else None

        self._venue_order_by_trade: dict[TradeId, VenueOrderId] = {}
        self._order_status: dict[VenueOrderId, str] = {}
        self._order_fill_count: dict[VenueOrderId, int] = {}
        self._latest_positions: PositionSnapshot | None = None

        self._order_submitted_events: dict[TradeId, asyncio.Event] = {}

    @property
    def venue_order_by_trade(self) -> Mapping[TradeId, VenueOrderId]:
        """Map of `trade_id -> venue_order_id` observed so far."""
        return dict(self._venue_order_by_trade)

    @property
    def latest_positions(self) -> PositionSnapshot | None:
        """Most recent position snapshot observed from the execution engine."""
        return self._latest_positions

    async def run(self) -> None:
        """Consume execution events forever."""
        while True:
            event: ExecutionEvent = await self._event_subscription.get()
            await self._handle_event(event)

    async def run_intent_consumer(self) -> None:
        """Consume trade intents forever and submit orders via the six-step pipeline.

        Requires trade_intent_bus, market_resolver, market_state_service, and config.
        """
        if self._intent_subscription is None or self._resolver is None:
            raise RuntimeError("run_intent_consumer requires trade_intent_bus and market_resolver")
        if self._market_state_service is None:
            raise RuntimeError("run_intent_consumer requires market_state_service")
        while True:
            intent: TradeIntent = await self._intent_subscription.get()
            await self._handle_intent(intent)

    async def _log_decision(self, record: PMDecisionRecord) -> None:
        """Emit structured decision log and send to observability if recorder present."""
        d = record.to_log_dict()
        msg = json.dumps(d)
        if record.rejection_reason:
            logger.warning("pm_decision (rejected): %s", msg)
        else:
            logger.info("pm_decision: %s", msg)
        if self._recorder is not None and hasattr(self._recorder, "record_message"):
            # Observability: pass a message with .type so event_type is pm_decision; summary = model_dump()
            obs_msg = PMDecisionObservabilityMessage.from_record(record)
            await self._recorder.record_message(obs_msg, kind="event", stage="portfolio_manager")

    async def _handle_intent(self, intent: TradeIntent) -> None:
        """Six-step pipeline: resolve -> snapshot -> edge -> Kelly -> cap -> submit."""
        record = PMDecisionRecord(
            trade_id=intent.trade_id,
            strategy_id=intent.strategy_id,
            subject=intent.subject,
            trade_intent_probability=intent.probability,
            confidence=intent.confidence,
            timestamp=_utc_now(),
        )

        # Step 1 — Resolve market
        identity = self._resolver.resolve(intent.subject, intent.timestamp)
        if identity is None:
            record.rejection_reason = "no_market_identity"
            await self._log_decision(record)
            return

        # Step 2 — Fetch snapshot
        snapshot: MarketSnapshot | None = await self._market_state_service.get_latest(intent.subject)
        if snapshot is None:
            record.rejection_reason = "snapshot_unavailable"
            await self._log_decision(record)
            return

        record.implied_probability = snapshot.implied_probability
        record.bid = snapshot.bid
        record.ask = snapshot.ask

        # Step 3 — Edge
        if intent.side == "YES":
            edge = intent.probability - snapshot.implied_probability
        else:
            edge = snapshot.implied_probability - intent.probability
        record.edge = edge

        if edge < self._config.min_edge_threshold:
            record.rejection_reason = "edge_below_threshold"
            await self._log_decision(record)
            return

        # Step 4 — Kelly size
        q = snapshot.implied_probability
        p = intent.probability
        if q >= 1.0:
            record.rejection_reason = "implied_probability_ge_one"
            await self._log_decision(record)
            return
        f_full = (p - q) / (1.0 - q)
        f_full = max(0.0, min(1.0, f_full))
        record.full_kelly_fraction = f_full

        f_frac = self._config.kelly_fraction * f_full
        record.fractional_kelly_fraction = f_frac

        dollar_size = self._config.bankroll * f_frac
        record.uncapped_dollar_size = dollar_size

        if intent.side == "YES":
            cost_per_contract = snapshot.ask
        else:
            cost_per_contract = 1.0 - snapshot.bid
        if cost_per_contract <= 0:
            record.rejection_reason = "invalid_cost_per_contract"
            await self._log_decision(record)
            return
        contracts_uncapped = int(math.floor(dollar_size / cost_per_contract))
        if contracts_uncapped == 0:
            record.rejection_reason = "zero_contracts_after_kelly"
            record.capped_dollar_size = dollar_size
            record.final_contract_count = 0
            await self._log_decision(record)
            return

        # Step 5 — Position cap
        max_dollars = self._config.bankroll * self._config.max_position_fraction
        capped_dollar_size = dollar_size
        if dollar_size > max_dollars:
            capped_dollar_size = max_dollars
        record.capped_dollar_size = capped_dollar_size
        final_contract_count = int(math.floor(capped_dollar_size / cost_per_contract))
        record.final_contract_count = final_contract_count
        if dollar_size > max_dollars:
            logger.info(
                "pm_decision position_cap_applied trade_id=%s uncapped_dollar_size=%.2f capped_dollar_size=%.2f",
                intent.trade_id,
                dollar_size,
                capped_dollar_size,
            )

        # Step 6 — Build and submit
        side = "yes" if intent.side == "YES" else "no"
        if intent.side == "YES":
            limit_price = snapshot.ask
        else:
            limit_price = 1.0 - snapshot.bid

        request = OrderRequest(
            trade_id=intent.trade_id,
            venue=identity.venue,
            ticker=identity.ticker,
            side=side,
            action="buy",
            count=final_contract_count,
            order_type="limit",
            limit_price_dollars=limit_price,
            client_order_id=intent.trade_id,
        )
        await self._log_decision(record)
        await self.submit_order(request)

    async def submit_order(self, request: OrderRequest) -> None:
        """Submit an order directly via the execution engine."""
        self._order_submitted_events.setdefault(request.trade_id, asyncio.Event())
        await self._commands.put(SubmitOrder(request=request), stage="portfolio_manager")

    async def cancel_order(self, venue_order_id: VenueOrderId, *, reason: str | None = None) -> None:
        """Request cancellation of an existing order directly via the execution engine."""
        await self._commands.put(CancelOrder(venue_order_id=venue_order_id, reason=reason), stage="portfolio_manager")

    async def wait_for_order_submitted(self, trade_id: TradeId, *, timeout_s: float = 10.0) -> VenueOrderId:
        """Wait until we have a venue order id for a trade directly via the execution engine."""
        ev = self._order_submitted_events.setdefault(trade_id, asyncio.Event())
        await asyncio.wait_for(ev.wait(), timeout=timeout_s)
        return self._venue_order_by_trade[trade_id]

    async def _handle_event(self, event: ExecutionEvent) -> None:
        """Update local state in response to an execution event."""
        if isinstance(event, OrderSubmitted):
            self._venue_order_by_trade[event.trade_id] = event.venue_order_id
            self._order_status[event.venue_order_id] = "submitted"
            self._order_fill_count[event.venue_order_id] = 0
            self._order_submitted_events.setdefault(event.trade_id, asyncio.Event()).set()
            return

        if isinstance(event, OrderUpdate):
            self._order_status[event.venue_order_id] = event.status
            self._order_fill_count[event.venue_order_id] = event.fill_count
            return

        if isinstance(event, FillUpdate):
            self._order_fill_count[event.venue_order_id] = event.filled_total
            return

        if isinstance(event, PositionSnapshot):
            self._latest_positions = event
            return
