from __future__ import annotations

import asyncio
import os
from contextlib import suppress
import time as _time

import pytest

from config import KalshiConfig, load_config
from kalshi.client import KalshiClient
from kalshi.models import KalshiOrder


def _has_real_kalshi_creds() -> bool:
    # `load_config()` is responsible for loading `.env` via dotenv.load_dotenv().
    # We must call it before reading env vars, otherwise integration tests will
    # skip even when `.env` is present.
    try:
        cfg = load_config().kalshi
    except Exception:
        return False

    return bool(cfg.api_key and cfg.private_key)


def _demo_config() -> KalshiConfig:
    """Load config but force demo environment for integration tests."""
    cfg = load_config().kalshi
    return KalshiConfig(
        api_key=cfg.api_key,
        private_key=cfg.private_key,
        use_demo=True,
        rate_limit=cfg.rate_limit,
        max_attempt=cfg.max_attempt,
        base_delay=cfg.base_delay,
        backoff_multiplier=cfg.backoff_multiplier,
        max_delay=cfg.max_delay,
        orderbook_depth=cfg.orderbook_depth,
    )


async def _close_client(client: KalshiClient) -> None:
    if client._request_worker_task is not None:
        client._request_worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await client._request_worker_task


@pytest.mark.asyncio
@pytest.mark.integration
async def test_integration_get_balance_hits_network() -> None:
    """Hits the real Kalshi API to verify auth/signing + request plumbing.

    To run:
    - set KALSHI_API_KEY / KALSHI_PRIVATE_KEY (and optionally KALSHI_USE_DEMO)
    - run: pytest -m integration
    """
    if not _has_real_kalshi_creds():
        pytest.skip("Missing real KALSHI_API_KEY/KALSHI_PRIVATE_KEY; skipping network integration test.")

    client = KalshiClient(_demo_config())
    try:
        balance = await client.get_balance()
        assert isinstance(balance.balance, int)
        assert balance.balance >= 0
    finally:
        await _close_client(client)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_integration_demo_get_markets_and_orderbook() -> None:
    """Fetch real demo markets and an orderbook."""
    if not _has_real_kalshi_creds():
        pytest.skip("Missing real KALSHI_API_KEY/KALSHI_PRIVATE_KEY; skipping network integration test.")

    client = KalshiClient(_demo_config())
    try:
        markets = await client.get_markets(status="open", limit=5)
        assert markets, "Expected at least one open market in demo"

        market = await client.get_market(markets[0].ticker)
        assert market.ticker

        orderbook = await client.get_market_orderbook(market.ticker, depth=1)
        assert isinstance(orderbook.yes_dollars, list)
        assert isinstance(orderbook.no_dollars, list)
    finally:
        await _close_client(client)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_integration_demo_create_get_cancel_order_roundtrip() -> None:
    """Create a real demo order, fetch it, then cancel it."""
    if not _has_real_kalshi_creds():
        pytest.skip("Missing real KALSHI_API_KEY/KALSHI_PRIVATE_KEY; skipping network integration test.")

    client = KalshiClient(_demo_config())
    created_order_id: str | None = None
    try:
        # Find an open *binary* market ticker (our create_order uses yes/no sides).
        #
        # Runtime evidence: markets returned by GetMarkets(status=open) can still reject
        # orders with `409 market_closed` (likely trading window/state drift). We treat
        # this as a signal to try another market rather than failing the test.
        markets = await client.get_markets(status="open", limit=50)
        assert markets, "Expected open markets in demo"

        candidate_tickers: list[str] = []
        for m in markets:
            event = await client.get_event(m.event_ticker)
            for market_payload in event.get("markets", []):
                if market_payload.get("ticker") == m.ticker and market_payload.get("market_type") == "binary":
                    candidate_tickers.append(m.ticker)
                    break

        if not candidate_tickers:
            pytest.skip("Could not find an open binary market in demo to place an order on.")

        created = None
        ticker_used: str | None = None
        last_error: Exception | None = None

        for ticker in candidate_tickers[:25]:
            # Use a very low limit price to avoid crossing the spread.
            client_order_id = f"integration-{int(_time.time() * 1000)}"
            order_req = KalshiOrder(
                ticker=ticker,
                side="yes",
                action="buy",
                type="limit",
                count=1,
                yes_price_dollars=0.01,
                client_order_id=client_order_id,
            )
            try:
                created = await client.create_order(order_req)
                ticker_used = ticker
                break
            except Exception as exc:  # noqa: BLE001 - integration test classification
                last_error = exc

                # Market/state drift: try next ticker.
                if getattr(exc, "status_code", None) == 409:
                    continue
                raise

        if created is None or ticker_used is None:
            raise AssertionError(f"Could not create a demo order on any candidate market. Last error: {last_error!r}")

        assert created.order_id
        created_order_id = created.order_id

        # Verify via API listing (helps when the web UI is delayed or points at a different env).
        orders_before_cancel = await client.get_orders(ticker=ticker_used, limit=50)
        assert any(o.order_id == created_order_id for o in orders_before_cancel)

        # Runtime evidence: immediate GetOrder can 404 right after create (eventual consistency).
        fetched = None
        for attempt in range(1, 16):
            try:
                fetched = await client.get_order(created_order_id)
                break
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status_code", None) == 404:
                    await asyncio.sleep(0.2)
                    continue
                raise

        assert fetched is not None, "Expected get_order to succeed after create (within retry window)"
        assert fetched.order_id == created_order_id
        assert fetched.ticker == ticker_used

        await client.cancel_order(created_order_id)

        # Cancel is synchronous, but allow a short window for the status to reflect.
        for _ in range(10):
            fetched2 = await client.get_order(created_order_id)
            if fetched2.status == "canceled":
                break
            await asyncio.sleep(0.2)

        final = await client.get_order(created_order_id)

        orders_after_cancel = await client.get_orders(ticker=ticker_used, limit=50)
        assert any(o.order_id == created_order_id for o in orders_after_cancel)
        assert final.status in {"canceled", "executed"}
    finally:
        # Best-effort cleanup: if we created an order but failed before canceling, try once more.
        if created_order_id is not None:
            with suppress(Exception):
                await client.cancel_order(created_order_id)
        await _close_client(client)

