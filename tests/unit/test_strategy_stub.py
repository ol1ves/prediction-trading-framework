"""Unit tests for strategy layer: stub strategy, orchestrator, resolver, PM intent handling."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trading.bus import ExecutionCommandBus, ExecutionEventBus, TradeIntentBus
from trading.models import MarketSnapshot, SubmitOrder, TradeIntent
from trading.portfolio.manager import PortfolioManager
from trading.resolvers import MarketResolver
from trading.strategy import StrategyOrchestrator, StubStrategy


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, TypeError):
        return False


@pytest.mark.asyncio
async def test_stub_strategy_evaluate_returns_one_hardcoded_intent() -> None:
    """Stub strategy returns one TradeIntent with expected fields and a UUID trade_id."""
    stub = StubStrategy(subject="STUB_SUBJECT", side="YES", probability=0.82, confidence=0.91)
    intents = await stub.evaluate({}, {})
    assert len(intents) == 1
    intent = intents[0]
    assert intent.strategy_id == "stub_hardcoded"
    assert intent.subject == "STUB_SUBJECT"
    assert isinstance(intent.for_date, date)
    assert intent.side == "YES"
    assert intent.probability == 0.82
    assert intent.confidence == 0.91
    assert _is_uuid(intent.trade_id)
    # Each call produces a new trade_id
    intents2 = await stub.evaluate({}, {})
    assert intents2[0].trade_id != intent.trade_id


@pytest.mark.asyncio
async def test_stub_strategy_date_offset_days_tomorrow() -> None:
    """Stub with date_offset_days=1 produces for_date equal to tomorrow (UTC)."""
    stub = StubStrategy(subject="S1", date_offset_days=1)
    intents = await stub.evaluate({}, {})
    assert len(intents) == 1
    expected = datetime.now(tz=timezone.utc).date() + timedelta(days=1)
    assert intents[0].for_date == expected


@pytest.mark.asyncio
async def test_stub_strategy_date_offset_days_zero_is_today() -> None:
    """Stub with date_offset_days=0 (default) produces for_date equal to today (UTC)."""
    stub = StubStrategy(subject="S1", date_offset_days=0)
    intents = await stub.evaluate({}, {})
    assert len(intents) == 1
    expected = datetime.now(tz=timezone.utc).date()
    assert intents[0].for_date == expected


@pytest.mark.asyncio
async def test_stub_strategy_date_offset_days_negative() -> None:
    """Stub with negative date_offset_days produces for_date in the past."""
    stub = StubStrategy(subject="S1", date_offset_days=-1)
    intents = await stub.evaluate({}, {})
    assert len(intents) == 1
    expected = datetime.now(tz=timezone.utc).date() + timedelta(days=-1)
    assert intents[0].for_date == expected


@pytest.mark.asyncio
async def test_orchestrator_tick_all_publishes_intents_to_subscribers() -> None:
    """Register stub, call tick_all(), one intent appears on a subscribed queue."""
    intent_bus = TradeIntentBus()
    orchestrator = StrategyOrchestrator(intent_bus=intent_bus)
    stub = StubStrategy(subject="S1")
    orchestrator.register(stub)

    q = intent_bus.subscribe()
    await orchestrator.tick_all()

    intent = await asyncio.wait_for(q.get(), timeout=1.0)
    assert isinstance(intent, TradeIntent)
    assert intent.subject == "S1"
    assert intent.strategy_id == "stub_hardcoded"


@pytest.mark.asyncio
async def test_market_resolver_returns_ticker_for_known_subject() -> None:
    """Resolver returns MarketIdentity with ticker for a known subject."""
    resolver = MarketResolver(subject_to_ticker={"STUB_SUBJECT": "DEMO_TICKER"})
    identity = await resolver.resolve("STUB_SUBJECT")
    assert identity is not None
    assert identity.ticker == "DEMO_TICKER"
    assert identity.venue == "kalshi"


@pytest.mark.asyncio
async def test_market_resolver_returns_none_for_unknown_subject() -> None:
    """Resolver returns None when subject is not in the map."""
    resolver = MarketResolver(subject_to_ticker={"ONLY_THIS": "TICK"})
    assert await resolver.resolve("UNKNOWN") is None


@pytest.mark.asyncio
async def test_market_resolver_default_map_has_stub_subject() -> None:
    """Resolver with no map defaults to STUB_SUBJECT -> ABC."""
    resolver = MarketResolver()
    identity = await resolver.resolve("STUB_SUBJECT")
    assert identity is not None
    assert identity.ticker == "ABC"


class _FakeMarketStateService:
    """Returns a fixed snapshot for any subject (for PM intent pipeline tests)."""

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot

    async def get_latest(self, subject: str) -> MarketSnapshot | None:
        return self._snapshot


@pytest.mark.asyncio
async def test_pm_handle_intent_submits_order_via_resolver() -> None:
    """PM receives one TradeIntent, resolves subject, gets snapshot, sizes via Kelly, puts SubmitOrder."""
    execution_command_bus = ExecutionCommandBus()
    execution_event_bus = ExecutionEventBus()
    intent_bus = TradeIntentBus()
    resolver = MarketResolver(subject_to_ticker={"SUB_A": "TICKER_X"})
    # Snapshot: implied 0.40 so edge = 0.60 - 0.40 = 0.20 >= 0.05; ask 0.55 for sizing.
    snapshot = MarketSnapshot(
        subject="SUB_A",
        implied_probability=0.40,
        bid=0.38,
        ask=0.55,
        spread=0.02,
        liquidity="medium",
        time_to_resolution_minutes=60,
    )
    market_state_service = _FakeMarketStateService(snapshot)
    pm_config = SimpleNamespace(
        kelly_fraction=0.25,
        min_edge_threshold=0.05,
        max_position_fraction=0.05,
        bankroll=10_000.0,
    )

    pm = PortfolioManager(
        execution_command_bus=execution_command_bus,
        execution_event_bus=execution_event_bus,
        config=pm_config,
        trade_intent_bus=intent_bus,
        market_resolver=resolver,
        market_state_service=market_state_service,
    )

    intent = TradeIntent(
        trade_id="test-trade-uuid-1",
        strategy_id="test_strategy",
        subject="SUB_A",
        for_date=date(2024, 3, 1),
        side="YES",
        probability=0.60,
        confidence=0.8,
        rationale="test",
    )

    await pm._handle_intent(intent)

    cmd = await asyncio.wait_for(execution_command_bus.get(), timeout=1.0)
    assert isinstance(cmd, SubmitOrder)
    assert cmd.request.trade_id == "test-trade-uuid-1"
    assert cmd.request.ticker == "TICKER_X"
    assert cmd.request.side == "yes"
    assert cmd.request.action == "buy"
    # Limit price is snapshot ask for YES.
    assert cmd.request.limit_price_dollars == 0.55
    # Kelly: f_full = (0.6-0.4)/(1-0.4) = 1/3, f_frac = 0.25/3, dollar = 10000/12 ~ 833, contracts = 833/0.55 = 1514.
    assert cmd.request.count >= 1
