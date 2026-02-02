from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from config import KalshiConfig
from kalshi.client import KalshiClient
from kalshi.models import KalshiOrder


class _DummyPrivateKey:
    def __init__(self) -> None:
        self.last_message: bytes | None = None

    def sign(self, message: bytes, *args: Any, **kwargs: Any) -> bytes:  # noqa: ANN401
        self.last_message = message
        return b"sig"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None, *, status_code: int = 200, content: bytes | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content if content is not None else (b"{}" if payload is not None else b"")

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def _make_config() -> KalshiConfig:
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    # Big rate to avoid unrelated sleeps in most unit tests.
    return KalshiConfig(api_key="test_key", private_key=pem, use_demo=True, rate_limit=1000)


@pytest.mark.asyncio
async def test_get_market_orderbook_parses_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        assert method == "GET"
        assert url == "https://demo-api.kalshi.co/trade-api/v2/markets/ABC/orderbook?depth=2"
        assert json is None
        return _FakeResponse(
            {
                "orderbook": {
                    "yes_dollars": [["0.1500", 100], ["0.1400", 50]],
                    "no_dollars": [["0.8500", 25]],
                }
            }
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        ob = await client.get_market_orderbook("abc", depth=2)
        assert [lvl.dollars for lvl in ob.yes_dollars] == [0.15, 0.14]
        assert [lvl.count for lvl in ob.yes_dollars] == [100, 50]
        assert ob.no_dollars[0].dollars == 0.85
        assert ob.no_dollars[0].count == 25
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_get_orders_parses_list(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        assert method == "GET"
        assert url == "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders?limit=1"
        assert json is None
        return _FakeResponse(
            {
                "orders": [
                    {
                        "order_id": "OID",
                        "ticker": "ABC",
                        "side": "yes",
                        "action": "buy",
                        "type": "limit",
                        "status": "resting",
                        "initial_count": 2,
                        "fill_count": 0,
                        "yes_price_dollars": "0.1000",
                        "no_price_dollars": "0.9000",
                    }
                ],
                "cursor": "",
            }
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        orders = await client.get_orders(limit=1)
        assert len(orders) == 1
        assert orders[0].order_id == "OID"
        assert orders[0].ticker == "ABC"
        assert orders[0].count == 2
        assert orders[0].yes_price_dollars == 0.1
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_create_order_sends_expected_body_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(
            {
                "order": {
                    "order_id": "OID",
                    "ticker": "ABC",
                    "side": "yes",
                    "action": "buy",
                    "type": "limit",
                    "status": "resting",
                    "initial_count": 2,
                    "fill_count": 0,
                    "yes_price_dollars": "0.2500",
                    "no_price_dollars": "0.7500",
                }
            },
            status_code=201,
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        req = KalshiOrder(ticker="abc", side="yes", action="buy", type="limit", count=2, yes_price_dollars=0.25)
        created = await client.create_order(req)

        assert captured["method"] == "POST"
        assert captured["url"] == "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders"
        assert captured["json"] == {
            "ticker": "ABC",
            "side": "yes",
            "action": "buy",
            "count": 2,
            "type": "limit",
            "yes_price_dollars": "0.2500",
        }

        assert created.order_id == "OID"
        assert created.ticker == "ABC"
        assert created.count == 2
        assert created.yes_price_dollars == 0.25
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_cancel_order_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        assert method == "DELETE"
        assert url == "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders/OID"
        assert json is None
        return _FakeResponse(None, status_code=200, content=b"")

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        result = await client.cancel_order("OID")
        assert result is None
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task

