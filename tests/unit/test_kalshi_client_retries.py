from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest
import requests

from config import KalshiConfig
from kalshi.client import KalshiClient
from kalshi.models import KalshiMarket


class _DummyPrivateKey:
    def sign(self, message: bytes, *args: Any, **kwargs: Any) -> bytes:  # noqa: ANN401
        return b"sig"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None, *, status_code: int, content: bytes | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content if content is not None else (b"{}" if payload is not None else b"")

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def _make_config() -> KalshiConfig:
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    return KalshiConfig(
        api_key="test_key",
        private_key=pem,
        use_demo=True,
        rate_limit=10_000,
        max_attempt=5,
        base_delay=0.5,
        backoff_multiplier=2.0,
        max_delay=30.0,
    )


@pytest.mark.asyncio
async def test_retries_on_http_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: _DummyPrivateKey())
    monkeypatch.setattr("kalshi.client.random.uniform", lambda _a, _b: 0.0)

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("kalshi.client.asyncio.sleep", fake_sleep)

    calls = 0

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls < 3:
            return _FakeResponse({"message": "oops"}, status_code=500)
        return _FakeResponse(
            {
                "market": {
                    "ticker": "ABC",
                    "event_ticker": "EVT",
                    "yes_bid_dollars": "0.1000",
                    "yes_ask_dollars": "0.1100",
                    "no_bid_dollars": "0.8900",
                    "no_ask_dollars": "0.9000",
                    "volume": 0,
                    "status": "active",
                    "close_time": "2023-11-07T05:31:56Z",
                }
            },
            status_code=200,
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        market = await client.get_market("abc")
        assert isinstance(market, KalshiMarket)
        assert market.ticker == "ABC"
        assert calls == 3
        assert slept == [0.5, 1.0]
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_no_retry_on_http_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: _DummyPrivateKey())
    monkeypatch.setattr("kalshi.client.random.uniform", lambda _a, _b: 0.0)

    calls = 0

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({"message": "bad request"}, status_code=400)

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        with pytest.raises(Exception):
            await client.get_markets(limit=1)
        assert calls == 1
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task


@pytest.mark.asyncio
async def test_retries_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: _DummyPrivateKey())
    monkeypatch.setattr("kalshi.client.random.uniform", lambda _a, _b: 0.0)

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("kalshi.client.asyncio.sleep", fake_sleep)

    calls = 0

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.RequestException("network down")
        return _FakeResponse(
            {"balance": 1, "portfolio_value": 2, "updated_ts": 123},
            status_code=200,
        )

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        bal = await client.get_balance()
        assert bal.balance == 1
        assert calls == 2
        assert slept == [0.5]
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task

