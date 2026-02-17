"""Async client for the Kalshi trade API with request signing.

This client implements a small async utility framework:

- Public methods create an `asyncio.Future`, enqueue `(method, path, body, future)`,
  and await the future's result.
- A single background worker consumes the queue serially.
- A token-bucket limiter gates outbound requests.

The HTTP call uses `requests` executed in a thread to avoid introducing a new
async HTTP dependency while this refactor stabilizes.
"""

from __future__ import annotations

import asyncio
import base64
import random
import time
from typing import Any, Final
from urllib.parse import urlencode

import requests #type: ignore
from cryptography.hazmat.primitives import hashes #type: ignore
from cryptography.hazmat.primitives.asymmetric import padding #type: ignore
from cryptography.hazmat.primitives.serialization import load_pem_private_key #type: ignore

from config import KalshiConfig

from .rate_limit import TokenBucketRateLimiter
from .models import KalshiBalance, KalshiMarket, KalshiOrder, KalshiOrderBook, KalshiPosition


class KalshiClient:
    """Authenticated async client for the Kalshi API (RSA-PSS signing).

    Members:
    - Kalshi API key: `api_key`
    - Kalshi private key: `private_key`
    - Config: `config`
    - Base URL: `base_url` (computed from KALSHI_USE_DEMO via config)
    - Request Queue: `request_queue` (single asyncio.Queue)
    - Dedicated Worker Task: `_request_worker_task` (single background task)
    - Rate Limiter: `rate_limiter` (token bucket)
    """

    def __init__(self, config: KalshiConfig):
        """Create a client using the given credentials and tuning configuration."""
        self.config = config
        self.api_key: str = config.api_key
        self.private_key = _load_private_key(config.private_key)

        # Computed from KALSHI_USE_DEMO / config.use_demo
        self.base_url: str = config.base_url

        # Central request queue: (method, path, body, future)
        self.request_queue: asyncio.Queue[tuple[str, str, Any | None, asyncio.Future[Any]]] = asyncio.Queue()

        self.rate_limiter = TokenBucketRateLimiter(rate=config.rate_limit)
        self._request_worker_task: asyncio.Task[None] | None = None

    def _ensure_worker_started(self) -> None:
        """Start the single background worker task (lazily)."""
        if self._request_worker_task is not None and not self._request_worker_task.done():
            return
        loop = asyncio.get_running_loop()
        self._request_worker_task = loop.create_task(self._request_worker(), name="kalshi-request-worker")

    async def _enqueue_request(self, method: str, path: str, body: Any | None) -> Any:
        """Enqueue a request and await its result."""
        self._ensure_worker_started()
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self.request_queue.put((method, path, body, fut))
        return await fut

    async def _request_worker(self) -> None:
        """Consume the queue serially, resolve futures with results/errors."""
        while True:
            method, path, body, fut = await self.request_queue.get()
            try:
                result = await self._send_with_retries(method, path, body)
            except Exception as exc:  # noqa: BLE001 - propagate into awaiting task
                if not fut.cancelled():
                    fut.set_exception(exc)
            else:
                if not fut.cancelled():
                    fut.set_result(result)
            finally:
                self.request_queue.task_done()

    def _sign_request(self, method: str, path: str) -> tuple[str, str]:
        """Sign a request per Kalshi auth docs.

        Message format: `timestamp_ms + HTTP_METHOD + path_without_query`.
        Returns `(timestamp_ms, signature_base64)`.
        """
        method_upper: Final[str] = method.upper()
        path_without_query: Final[str] = path.split("?", 1)[0]
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method_upper}{path_without_query}".encode("utf-8")

        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return timestamp_ms, base64.b64encode(signature).decode("utf-8")

    async def _send_request(self, method: str, path: str, body: Any | None) -> Any:
        """Sign and send a request, returning the decoded JSON response.

        Raises:
        - `KalshiHttpError` for non-2xx responses
        - `requests.RequestException` for transport errors
        """
        timestamp_ms, signature = self._sign_request(method, path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

        url = self.base_url + path

        def _do_request() -> Any:
            """Execute the HTTP request synchronously (runs in a worker thread)."""
            resp = requests.request(method, url, headers=headers, json=body, timeout=30)
            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return None
                return resp.json()

            error_payload: dict[str, Any] | None
            try:
                error_payload = resp.json()
            except Exception:  # noqa: BLE001 - best-effort parsing
                error_payload = None
            raise KalshiHttpError(status_code=resp.status_code, payload=error_payload)

        return await asyncio.to_thread(_do_request)

    async def _send_with_retries(self, method: str, path: str, body: Any | None) -> Any:
        """Send a request with spec-defined retry/backoff behavior."""
        attempt = 0
        start = time.monotonic()

        while True:
            try:
                await self.rate_limiter.acquire()
                return await self._send_request(method, path, body)
            except Exception as exc:  # noqa: BLE001 - classify and retry/raise
                attempt += 1
                if not _is_retryable_error(exc):
                    raise
                if attempt >= self.config.max_attempt:
                    raise

                delay = self.config.base_delay * (self.config.backoff_multiplier ** (attempt - 1))
                delay += random.uniform(0.0, delay * 0.1)  # small jitter

                if (time.monotonic() - start) + delay > self.config.max_delay:
                    raise
                await asyncio.sleep(delay)

    def _build_query_string(self, params: dict) -> str:
        """Build a query string from a dict, omitting None values.

        Notes:
        - Lists/tuples are encoded as comma-separated values.
        - Booleans are encoded as "true"/"false".
        """
        filtered: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                filtered[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                filtered[key] = ",".join(str(v) for v in value)
            else:
                filtered[key] = str(value)

        if not filtered:
            return ""
        return "?" + urlencode(filtered)

    def _normalize_ticker(self, ticker):
        """Convert a single ticker value to uppercase (or None if not provided)."""
        if ticker is None:
            return None
        return str(ticker).upper()

    def _normalize_tickers(self, tickers):
        """Convert one-or-many tickers to uppercase.

        Accepts:
        - list/tuple of tickers
        - comma-separated string of tickers
        - a single ticker value
        """
        if tickers is None:
            return None
        if isinstance(tickers, (list, tuple)):
            return [self._normalize_ticker(t) for t in tickers]

        tickers_str = str(tickers)
        if "," in tickers_str:
            return [self._normalize_ticker(t.strip()) for t in tickers_str.split(",") if t.strip()]
        return self._normalize_ticker(tickers_str)

    async def get_market(self, ticker: str) -> KalshiMarket:
        """Get a single market by ticker."""
        ticker = self._normalize_ticker(ticker)
        response = await self._enqueue_request("GET", f"/trade-api/v2/markets/{ticker}", None)
        return KalshiMarket.from_api(response["market"])

    async def get_market_orderbook(self, ticker: str, depth: int = 10) -> KalshiOrderBook:
        """Get the orderbook for a market ticker."""
        ticker = self._normalize_ticker(ticker)
        query = self._build_query_string({"depth": depth})
        response = await self._enqueue_request("GET", f"/trade-api/v2/markets/{ticker}/orderbook{query}", None)
        return KalshiOrderBook.from_api(response)

    async def get_markets(
        self,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ):
        """Get markets with optional filters/pagination."""
        query = self._build_query_string(
            {
                "limit": limit,
                "cursor": cursor,
                "event_ticker": self._normalize_ticker(event_ticker),
                "series_ticker": self._normalize_ticker(series_ticker),
                "status": status,
            }
        )
        response = await self._enqueue_request("GET", f"/trade-api/v2/markets{query}", None)
        return [KalshiMarket.from_api(m) for m in response.get("markets", [])]

    async def get_event(self, event_ticker: str) -> dict[str, Any]:
        """Get a single event by ticker."""
        event_ticker = self._normalize_ticker(event_ticker)
        return await self._enqueue_request("GET", f"/trade-api/v2/events/{event_ticker}", None)

    async def get_series(self, series_ticker: str) -> dict[str, Any]:
        """Get a series by ticker."""
        series_ticker = self._normalize_ticker(series_ticker)
        response = await self._enqueue_request("GET", f"/trade-api/v2/series/{series_ticker}", None)
        return response["series"]

    async def get_orders(
        self,
        ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> list[KalshiOrder]:
        """Get orders with optional filtering/pagination."""
        query = self._build_query_string(
            {
                "ticker": self._normalize_ticker(ticker),
                "event_ticker": self._normalize_ticker(event_ticker),
                "status": status,
                "limit": limit,
                "cursor": cursor,
            }
        )
        response = await self._enqueue_request("GET", f"/trade-api/v2/portfolio/orders{query}", None)
        return [KalshiOrder.from_api(o) for o in response.get("orders", [])]

    async def get_order(self, order_id: str) -> KalshiOrder:
        """Get a single order by its order_id."""
        response = await self._enqueue_request("GET", f"/trade-api/v2/portfolio/orders/{order_id}", None)
        return KalshiOrder.from_api(response["order"])

    async def create_order(self, request: KalshiOrder) -> KalshiOrder:
        """Create an order."""
        body = _order_to_create_body(request, normalize_ticker=self._normalize_ticker)
        response = await self._enqueue_request("POST", "/trade-api/v2/portfolio/orders", body)
        return KalshiOrder.from_api(response["order"])

    async def cancel_order(self, order_id: str) -> None:
        """Cancel (fully reduce) an order by order_id."""
        await self._enqueue_request("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}", None)
        return None

    async def batch_create_orders(self, requests_list: list[KalshiOrder]) -> list[KalshiOrder]:
        """Create multiple orders in one request."""
        body = {"orders": [_order_to_create_body(o, normalize_ticker=self._normalize_ticker) for o in requests_list]}
        response = await self._enqueue_request("POST", "/trade-api/v2/portfolio/orders/batched", body)

        results: list[KalshiOrder] = []
        for item in response.get("orders", []):
            if item.get("error") is not None:
                raise KalshiHttpError(status_code=400, payload=item.get("error"))
            order_payload = item.get("order")
            if order_payload is None:
                raise KalshiHttpError(status_code=500, payload={"message": "Missing order in batch response"})
            results.append(KalshiOrder.from_api(order_payload))
        return results

    async def batch_cancel_orders(self, order_ids: list[str]) -> None:
        """Cancel multiple orders in one request."""
        body = {"orders": [{"order_id": oid} for oid in order_ids]}
        response = await self._enqueue_request("DELETE", "/trade-api/v2/portfolio/orders/batched", body)
        for item in response.get("orders", []):
            if item.get("error") is not None:
                raise KalshiHttpError(status_code=400, payload=item.get("error"))
        return None

    async def get_balance(self) -> KalshiBalance:
        """Get account balance and portfolio value (both in cents)."""
        response = await self._enqueue_request("GET", "/trade-api/v2/portfolio/balance", None)
        return KalshiBalance.from_api(response)

    async def get_positions(
        self,
        ticker: str | None = None,
        event_ticker: str | None = None,
        limit: int = 100,
    ) -> list[KalshiPosition]:
        """Get market positions with optional filtering."""
        query = self._build_query_string(
            {
                "ticker": self._normalize_ticker(ticker),
                "event_ticker": self._normalize_ticker(event_ticker),
                "limit": limit,
            }
        )
        response = await self._enqueue_request("GET", f"/trade-api/v2/portfolio/positions{query}", None)
        return [KalshiPosition.from_api(p) for p in response.get("market_positions", [])]


def _load_private_key(pem_str: str):
    """Load RSA private key from PEM string (handles \\n from .env)."""
    pem_bytes = pem_str.strip().replace("\\n", "\n").encode("utf-8")
    return load_pem_private_key(pem_bytes, password=None)


class KalshiHttpError(RuntimeError):
    """HTTP-level error returned by the Kalshi API."""

    def __init__(self, *, status_code: int, payload: dict[str, Any] | None):
        """Create an error capturing HTTP status code and parsed payload (if any)."""
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Kalshi API HTTP {status_code}: {payload}")


def _is_retryable_error(exc: BaseException) -> bool:
    """Return True if the error is transient per the project spec."""
    if isinstance(exc, KalshiHttpError):
        # Retry 429 and all 5xx.
        return exc.status_code == 429 or exc.status_code >= 500

    # Network/transport errors.
    return isinstance(exc, requests.RequestException)


def _order_to_create_body(order: KalshiOrder, *, normalize_ticker) -> dict[str, Any]:
    """Convert a KalshiOrder into a Create Order request body.

    The REST API requires fields beyond those present on the returned Order
    object; this helper keeps the mapping minimal and predictable.
    """
    if not order.ticker:
        raise ValueError("create_order requires request.ticker")
    if not order.side:
        raise ValueError("create_order requires request.side")
    if not order.action:
        raise ValueError("create_order requires request.action")

    if order.count is None or order.count <= 0:
        raise ValueError("create_order requires a positive request.count")

    body: dict[str, Any] = {
        "ticker": normalize_ticker(order.ticker),
        "side": order.side,
        "action": order.action,
        "count": int(order.count),
    }
    if order.client_order_id is not None:
        body["client_order_id"] = order.client_order_id
    if order.type is not None:
        body["type"] = order.type

    def _fmt_price(value: float) -> str:
        """Format price fields using the API's fixed-point convention."""
        return f"{value:.4f}"

    if order.side == "yes" and order.yes_price_dollars is not None:
        body["yes_price_dollars"] = _fmt_price(order.yes_price_dollars)
    if order.side == "no" and order.no_price_dollars is not None:
        body["no_price_dollars"] = _fmt_price(order.no_price_dollars)
    return body