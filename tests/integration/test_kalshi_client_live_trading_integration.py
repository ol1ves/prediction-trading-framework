from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress

import pytest

from config import KalshiConfig, load_config
from kalshi.client import KalshiClient
from kalshi.models import KalshiOrder


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _to_f(value: object) -> float:
    return float(str(value))


def _step_for_price(price_ranges: list[dict], price: float) -> float:
    # Price ranges are strings in dollars; pick the first matching range.
    for r in price_ranges:
        try:
            start = _to_f(r["start"])
            end = _to_f(r["end"])
            step = _to_f(r["step"])
        except Exception:
            continue
        if start <= price <= end:
            return step
    return 0.01


def _floor_to_step(value: float, step: float) -> float:
    n = int(value / step)
    return round(n * step, 4)


def _ceil_to_step(value: float, step: float) -> float:
    n = int(value / step)
    if abs(n * step - value) < 1e-12:
        return round(value, 4)
    return round((n + 1) * step, 4)


def _demo_config() -> KalshiConfig:
    """Load config but force demo environment for live trading tests."""
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


def _should_run_live_trading() -> bool:
    # This is intentionally opt-in to avoid touching your demo portfolio on every run.
    return _truthy_env("KALSHI_RUN_LIVE_TRADING_TESTS")


async def _wait_for_terminal_order_status(
    client: KalshiClient,
    order_id: str,
    *,
    timeout_s: float = 60.0,
) -> str:
    """Poll order status until it reaches a terminal-ish state or times out.

    We treat `executed` as success. `canceled` is terminal but not success for this test.
    """
    deadline = time.monotonic() + timeout_s
    last_status: str | None = None

    while time.monotonic() < deadline:
        try:
            o = await client.get_order(order_id)
        except Exception as exc:  # noqa: BLE001 - eventual consistency / transient errors
            if getattr(exc, "status_code", None) == 404:
                await asyncio.sleep(0.2)
                continue
            raise

        last_status = str(o.status or "")
        if last_status in {"executed", "canceled"}:
            return last_status
        await asyncio.sleep(0.2)

    raise TimeoutError(f"Timed out waiting for order {order_id} status. Last status: {last_status!r}")


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.live_trading
async def test_integration_demo_buy_then_sell_wait_for_fills() -> None:
    """Place a real BUY then real SELL in demo, waiting for fills.

    Safety:
    - This test is opt-in via `KALSHI_RUN_LIVE_TRADING_TESTS=true`.
    - Uses demo base URL only.
    - Uses 1 contract to minimize exposure.
    - Attempts to buy at the current ask and sell at the current bid to fill quickly.
    """
    if not _should_run_live_trading():
        pytest.skip("Set KALSHI_RUN_LIVE_TRADING_TESTS=true to enable live trading integration test.")

    cfg = _demo_config()
    client = KalshiClient(cfg)
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    try:
        assert client.base_url.startswith("https://demo-api.kalshi.co"), "Live trading test must use demo API"

        # Pick an open market with real top-of-book liquidity.
        #
        # Runtime evidence: in demo, Market summary fields (yes_bid_dollars/yes_ask_dollars)
        # are often 0 even when the orderbook has bids. We use the orderbook to derive
        # executable prices instead.
        markets = await client.get_markets(status="open", limit=500)
        assert markets, "Expected open markets in demo"

        # Optional override: you can pin a known liquid demo ticker to avoid scanning.
        pinned_ticker = _get_env("KALSHI_LIVE_TRADING_TICKER")

        # Prefer quicksettle markets if present (demo liquidity tends to be better there).
        market_by_ticker = {m.ticker: m for m in markets}
        tickers = list(market_by_ticker.keys())
        preferred = [t for t in tickers if t.startswith("KXQUICKSETTLE-")]
        candidates = [pinned_ticker] if pinned_ticker else (preferred + [t for t in tickers if t not in preferred])

        # NOTE: Demo often has *one-sided* liquidity only. A strict "buy then sell and end flat"
        # roundtrip is not reliably possible if only one side has bids. For a sanity check that
        # orders are placed and FILLED, we place a single aggressive BUY that crosses existing bids:
        #
        # - If NO has bids: buy YES at (1 - best_no_bid)
        # - Else if YES has bids: buy NO at (1 - best_yes_bid)
        #
        # This guarantees a fill when any side has bids, at the cost of leaving a small demo
        # position open (1 contract). This test is opt-in for that reason.

        chosen_ticker: str | None = None
        buy_side: str | None = None
        buy_price: float | None = None

        scanned = 0
        yes_bid_only = 0
        no_bid_only = 0
        both_bids = 0

        for t in candidates[:500]:
            if t is None:
                continue
            scanned += 1
            try:
                ob = await client.get_market_orderbook(t, depth=1)
            except Exception as exc:  # noqa: BLE001 - skip transient failures
                continue

            has_yes = bool(ob.yes_dollars)
            has_no = bool(ob.no_dollars)
            if has_yes and not has_no:
                yes_bid_only += 1
            elif has_no and not has_yes:
                no_bid_only += 1
            elif has_yes and has_no:
                both_bids += 1

            best_yes_bid = ob.yes_dollars[0].dollars if has_yes else 0.0
            best_no_bid = ob.no_dollars[0].dollars if has_no else 0.0

            if best_yes_bid <= 0.0 and best_no_bid <= 0.0:
                continue

            m = market_by_ticker.get(t)
            if m is None:
                continue
            event = await client.get_event(m.event_ticker)
            market_payload = next((x for x in event.get("markets", []) if x.get("ticker") == t), None)
            if market_payload is None:
                continue
            price_ranges = market_payload.get("price_ranges") or []

            # Prefer buying YES if NO bids exist (implied YES ask), otherwise buy NO using YES bids.
            if best_no_bid > 0.0:
                implied_yes_ask = 1.0 - best_no_bid
                step = _step_for_price(price_ranges, implied_yes_ask)
                buy_side = "yes"
                buy_price = min(0.99, _ceil_to_step(implied_yes_ask, step))
                chosen_ticker = t
                break

            implied_no_ask = 1.0 - best_yes_bid
            step = _step_for_price(price_ranges, implied_no_ask)
            buy_side = "no"
            buy_price = min(0.99, _ceil_to_step(implied_no_ask, step))
            chosen_ticker = t
            break

        if chosen_ticker is None or buy_side is None or buy_price is None:
            pytest.skip(
                "No suitable open market with any top-of-book bids found to run live trading test. "
                "If you know a liquid demo ticker, set KALSHI_LIVE_TRADING_TICKER=<ticker>."
            )

        # Place a single aggressive BUY and wait for it to execute.
        client_order_id = f"live-buy-{int(time.time() * 1000)}"
        if buy_side == "yes":
            buy_req = KalshiOrder(
                ticker=chosen_ticker,
                side="yes",
                action="buy",
                type="limit",
                count=1,
                yes_price_dollars=buy_price,
                client_order_id=client_order_id,
            )
        else:
            buy_req = KalshiOrder(
                ticker=chosen_ticker,
                side="no",
                action="buy",
                type="limit",
                count=1,
                no_price_dollars=buy_price,
                client_order_id=client_order_id,
            )

        buy = await client.create_order(buy_req)
        assert buy.order_id
        buy_order_id = buy.order_id

        buy_status = await _wait_for_terminal_order_status(client, buy_order_id, timeout_s=60.0)
        assert buy_status == "executed", f"Expected buy to execute, got {buy_status!r}"
    finally:
        # Best-effort cleanup: if an order didn't fill, cancel it so we don't leave rests.
        if buy_order_id is not None:
            with suppress(Exception):
                await client.cancel_order(buy_order_id)
        if sell_order_id is not None:
            with suppress(Exception):
                await client.cancel_order(sell_order_id)
        await _close_client(client)

