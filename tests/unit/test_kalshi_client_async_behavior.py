from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from typing import Any

import pytest

from config import KalshiConfig
from kalshi.client import KalshiClient


class _DummyPrivateKey:
    def sign(self, message: bytes, *args: Any, **kwargs: Any) -> bytes:  # noqa: ANN401
        return b"sig"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200, content: bytes | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content if content is not None else b"{}"

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_config() -> KalshiConfig:
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    return KalshiConfig(api_key="test_key", private_key=pem, use_demo=True, rate_limit=10_000)


@pytest.mark.asyncio
async def test_worker_serializes_requests_no_concurrent_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kalshi.client._load_private_key", lambda _pem: _DummyPrivateKey())

    active = 0
    lock = threading.Lock()
    calls: list[str] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], json: Any, timeout: int) -> _FakeResponse:
        nonlocal active
        with lock:
            active += 1
            assert active == 1, "requests.request was called concurrently, but worker should be serial"
        try:
            calls.append(url)
            return _FakeResponse({"markets": [], "cursor": ""})
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("kalshi.client.requests.request", fake_request)

    client = KalshiClient(_make_config())
    try:
        # Enqueue many requests concurrently; the worker should still execute them one-at-a-time.
        await asyncio.gather(*[client.get_markets(limit=1) for _ in range(20)])
        assert len(calls) == 20
    finally:
        if client._request_worker_task is not None:
            client._request_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._request_worker_task

