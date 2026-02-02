from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_threads_in_unit_tests(monkeypatch: pytest.MonkeyPatch):
    """Run `asyncio.to_thread` inline for unit tests.

    The client uses `asyncio.to_thread` to avoid introducing an async HTTP
    dependency. In unit tests, this can create threadpool workers that keep the
    Python process alive longer than expected under some runtimes.
    """

    async def _to_thread(func, /, *args, **kwargs):  # noqa: ANN001, D401
        return func(*args, **kwargs)

    monkeypatch.setattr("kalshi.client.asyncio.to_thread", _to_thread)
    yield

