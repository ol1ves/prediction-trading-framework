"""Unit tests for strategy layer: stub strategy, orchestrator, resolver, PM intent handling."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from trading.bus import ExecutionCommandBus, ExecutionEventBus, TradeIntentBus
from trading.models import SubmitOrder
from trading.portfolio.manager import PortfolioManager
from trading.strategy import MarketResolver, StrategyOrchestrator, StubStrategy
from trading.models import TradeIntent


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
    assert intent.side == "YES"
    assert intent.probability == 0.82
    assert intent.confidence == 0.91
    assert _is_uuid(intent.trade_id)
    # Each call produces a new trade_id
    intents2 = await stub.evaluate({}, {})
    assert intents2[0].trade_id != intent.trade_id


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
    identity = resolver.resolve("STUB_SUBJECT")
    assert identity is not None
    assert identity.ticker == "DEMO_TICKER"
    assert identity.venue == "kalshi"


@pytest.mark.asyncio
async def test_market_resolver_returns_none_for_unknown_subject() -> None:
    """Resolver returns None when subject is not in the map."""
    resolver = MarketResolver(subject_to_ticker={"ONLY_THIS": "TICK"})
    assert resolver.resolve("UNKNOWN") is None


@pytest.mark.asyncio
async def test_market_resolver_default_map_has_stub_subject() -> None:
    """Resolver with no map defaults to STUB_SUBJECT -> ABC."""
    resolver = MarketResolver()
    identity = resolver.resolve("STUB_SUBJECT")
    assert identity is not None
    assert identity.ticker == "ABC"


@pytest.mark.asyncio
async def test_pm_handle_intent_submits_order_via_resolver() -> None:
    """PM receives one TradeIntent, resolves subject, puts one SubmitOrder on command bus."""
    execution_command_bus = ExecutionCommandBus()
    execution_event_bus = ExecutionEventBus()
    intent_bus = TradeIntentBus()
    resolver = MarketResolver(subject_to_ticker={"SUB_A": "TICKER_X"})

    pm = PortfolioManager(
        execution_command_bus=execution_command_bus,
        execution_event_bus=execution_event_bus,
        trade_intent_bus=intent_bus,
        market_resolver=resolver,
    )

    intent = TradeIntent(
        trade_id="test-trade-uuid-1",
        strategy_id="test_strategy",
        subject="SUB_A",
        side="YES",
        probability=0.50,
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
    assert cmd.request.limit_price_dollars == 0.50
