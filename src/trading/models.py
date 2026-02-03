"""Normalized models for execution + portfolio plumbing (MVP).

These models intentionally include only the minimum fields needed to:
- submit/cancel orders via a venue adapter
- observe order status and fill progress
- observe coarse position snapshots

They are venue-agnostic and are expected to evolve as the system grows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

TradeId: TypeAlias = str
ClientOrderId: TypeAlias = str
VenueOrderId: TypeAlias = str

Venue = Literal["kalshi"]

OrderSide = Literal["yes", "no"]
OrderAction = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class _Model(BaseModel):
    # Keep these models small and forward-compatible with evolving payloads.
    model_config = ConfigDict(extra="ignore", frozen=True)


class OrderRequest(_Model):
    """A venue-agnostic order intent from the portfolio manager."""

    trade_id: TradeId
    venue: Venue
    ticker: str
    side: OrderSide
    action: OrderAction
    count: int
    order_type: OrderType

    # For limit orders. Interpreted as YES or NO price dollars based on `side`.
    limit_price_dollars: float | None = None

    client_order_id: ClientOrderId | None = None


class SubmitOrder(_Model):
    type: Literal["submit_order"] = "submit_order"
    request: OrderRequest


class CancelOrder(_Model):
    type: Literal["cancel_order"] = "cancel_order"
    venue_order_id: VenueOrderId
    reason: str | None = None


ExecutionCommand = SubmitOrder | CancelOrder


class OrderSubmitted(_Model):
    type: Literal["order_submitted"] = "order_submitted"
    trade_id: TradeId
    venue: Venue
    venue_order_id: VenueOrderId
    request: OrderRequest
    ts: datetime = Field(default_factory=utc_now)


class OrderRejected(_Model):
    type: Literal["order_rejected"] = "order_rejected"
    trade_id: TradeId
    venue: Venue
    request: OrderRequest
    message: str
    payload: dict[str, Any] | None = None
    ts: datetime = Field(default_factory=utc_now)


class OrderCanceled(_Model):
    type: Literal["order_canceled"] = "order_canceled"
    venue: Venue
    venue_order_id: VenueOrderId
    reason: str | None = None
    ts: datetime = Field(default_factory=utc_now)


class OrderUpdate(_Model):
    type: Literal["order_update"] = "order_update"
    venue: Venue
    venue_order_id: VenueOrderId
    status: str
    fill_count: int
    ts: datetime = Field(default_factory=utc_now)


class FillUpdate(_Model):
    type: Literal["fill_update"] = "fill_update"
    venue: Venue
    venue_order_id: VenueOrderId
    filled_delta: int
    filled_total: int
    ts: datetime = Field(default_factory=utc_now)


class Position(_Model):
    ticker: str
    position: int = 0
    market_exposure_dollars: float = 0.0
    last_updated_ts: datetime | None = None


class PositionSnapshot(_Model):
    type: Literal["position_snapshot"] = "position_snapshot"
    venue: Venue
    positions: list[Position]
    ts: datetime = Field(default_factory=utc_now)


class ExecutionError(_Model):
    type: Literal["execution_error"] = "execution_error"
    venue: Venue | None = None
    venue_order_id: VenueOrderId | None = None
    message: str
    retryable: bool = False
    ts: datetime = Field(default_factory=utc_now)


ExecutionEvent = (
    OrderSubmitted | OrderRejected | OrderCanceled | OrderUpdate | FillUpdate | PositionSnapshot | ExecutionError
)

