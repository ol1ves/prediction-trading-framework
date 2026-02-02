from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from config import KalshiConfig
from kalshi.client import KalshiClient


class _DummyPrivateKey:
    def __init__(self) -> None:
        self.last_message: bytes | None = None

    def sign(self, message: bytes, *args: Any, **kwargs: Any) -> bytes:  # noqa: ANN401
        self.last_message = message
        return b"sig"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200, content: bytes | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        # `KalshiClient` checks `resp.content` to decide whether to parse JSON.
        self.content = content if content is not None else b"{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_config() -> KalshiConfig:
    # KalshiConfig validates "looks like PEM", but we replace key loading in tests.
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    return KalshiConfig(api_key="test_key", private_key=pem, use_demo=True, rate_limit=1000)


@pytest.mark.asyncio
async def test_get_market_enqueues_and_signs(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(
            {
                "market": {
                    "ticker": "ABC",
                    "event_ticker": "EVT",
                    "yes_sub_title": "YES",
                    "no_sub_title": "NO",
                    "yes_bid_dollars": "0.1000",
                    "yes_ask_dollars": "0.1100",
                    "no_bid_dollars": "0.8900",
                    "no_ask_dollars": "0.9000",
                    "volume": 0,
                    "status": "active",
                    "close_time": "2023-11-07T05:31:56Z",
                }
            }
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        market = await client.get_market("abc")
        assert market.ticker == "ABC"

        assert captured["method"] == "GET"
        assert captured["url"] == "https://demo-api.kalshi.co/trade-api/v2/markets/ABC"
        assert captured["json"] is None

        headers = captured["headers"]
        assert headers["KALSHI-ACCESS-KEY"] == "test_key"
        assert headers["KALSHI-ACCESS-SIGNATURE"] == "c2ln"  # base64(b"sig")
        assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()

        assert dummy_key.last_message is not None
        assert dummy_key.last_message.decode("utf-8").endswith("GET/trade-api/v2/markets/ABC")
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_get_markets_signature_strips_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_key = _DummyPrivateKey()
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: dummy_key)

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        return _FakeResponse({"markets": [], "cursor": None})

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        await client.get_markets(limit=1, cursor="CUR")

        assert dummy_key.last_message is not None
        # Important: message signs path without query params.
        assert b"?" not in dummy_key.last_message
        assert dummy_key.last_message.decode("utf-8").endswith("GET/trade-api/v2/markets")
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


def test_build_query_string_omits_none_and_encodes_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: _DummyPrivateKey())
    client = KalshiClient(_make_config())

    qs = client._build_query_string({"a": 1, "b": None, "c": [1, 2], "d": True, "e": False})
    # Ordering is deterministic due to dict insertion order.
    assert qs == "?a=1&c=1%2C2&d=true&e=false"

