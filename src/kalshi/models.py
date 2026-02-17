"""Kalshi data models used by `KalshiClient`.

These models are a small, purpose-built subset of Kalshi REST API responses.
They intentionally include only the fields required by the project spec.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator #type: ignore


def _parse_rfc3339_datetime(value: Any) -> datetime | None:
    """Parse an RFC3339 timestamp into an aware datetime (UTC if tz missing)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value)
        # Kalshi uses RFC3339 timestamps like "2023-11-07T05:31:56Z".
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_fixed_point_dollars(value: Any) -> float:
    """Parse a fixed-point dollars field from Kalshi (commonly a string)."""
    # API docs define dollars fields as fixed-point strings (4 decimals).
    if value is None or value == "":
        return 0.0
    return float(value)


MarketStatus = Literal[
    "initialized",
    "inactive",
    "active",
    "closed",
    "determined",
    "disputed",
    "amended",
    "finalized",
]


class _Model(BaseModel):
    # We validate against API payloads that contain many more fields than our spec.
    model_config = ConfigDict(extra="ignore", frozen=True)


class KalshiMarket(_Model):
    """Subset of Kalshi market fields used by this project."""

    ticker: str
    event_ticker: str
    yes_sub_title: str = ""
    no_sub_title: str = ""

    yes_bid_dollars: float = 0.0
    yes_ask_dollars: float = 0.0
    no_bid_dollars: float = 0.0
    no_ask_dollars: float = 0.0

    volume: int = 0
    status: MarketStatus | str = ""
    close_time: datetime

    @field_validator(
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        mode="before",
    )
    @classmethod
    def _coerce_dollars(cls, v: Any) -> float:
        """Coerce fixed-point dollars fields into floats."""
        return _parse_fixed_point_dollars(v)

    @field_validator("close_time", mode="before")
    @classmethod
    def _coerce_close_time(cls, v: Any) -> datetime:
        """Coerce `close_time` into an aware datetime."""
        dt = _parse_rfc3339_datetime(v)
        if dt is None:
            raise ValueError("close_time is required")
        return dt

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "KalshiMarket":
        """Parse a market payload from the Kalshi REST API."""
        return cls.model_validate(payload)


class KalshiPriceLevel(_Model):
    """Single orderbook level (price + count)."""

    dollars: float
    count: int

    @field_validator("dollars", mode="before")
    @classmethod
    def _coerce_dollars(cls, v: Any) -> float:
        """Coerce fixed-point dollars into float dollars."""
        return _parse_fixed_point_dollars(v)


class KalshiOrderBook(_Model):
    """Orderbook snapshot with YES/NO ladders."""

    yes_dollars: list[KalshiPriceLevel]
    no_dollars: list[KalshiPriceLevel]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "KalshiOrderBook":
        """Parse an orderbook payload returned by the Kalshi REST API."""
        orderbook = payload.get("orderbook") or {}
        yes_raw = orderbook.get("yes_dollars") or []
        no_raw = orderbook.get("no_dollars") or []

        def _levels(raw: list[list[Any]]) -> list[KalshiPriceLevel]:
            """Convert raw `[price, count]` arrays into validated price levels."""
            levels: list[KalshiPriceLevel] = []
            for item in raw:
                if not item or len(item) < 2:
                    continue
                levels.append(KalshiPriceLevel(dollars=item[0], count=item[1]))
            return levels

        return cls(yes_dollars=_levels(yes_raw), no_dollars=_levels(no_raw))


OrderSide = Literal["yes", "no"]
OrderAction = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]
OrderStatus = Literal["resting", "canceled", "executed"]


class KalshiOrder(_Model):
    """Subset of order fields used for create + polling in this project."""

    # Identity / routing
    order_id: str | None = None
    user_id: str | None = None
    client_order_id: str | None = None
    ticker: str | None = None

    # Order intent
    side: OrderSide | str | None = None
    action: OrderAction | str | None = None
    type: OrderType | str | None = None
    status: OrderStatus | str | None = None

    # Pricing (fixed-point dollars in the REST API)
    yes_price_dollars: float | None = None
    no_price_dollars: float | None = None

    # Create request
    count: int | None = None

    # Execution / accounting
    fill_count: int = 0
    queue_position: int = 0
    taker_fees_dollars: float | None = None
    maker_fees_dollars: float | None = None

    # Timestamps
    expiration_time: datetime | None = None
    created_time: datetime | None = None
    last_update_time: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _populate_count_from_api(cls, data: Any) -> Any:
        """Map REST `initial_count` into our `count` field when missing."""
        # REST responses contain `initial_count` (not `count`). We map it into our
        # spec's `count` field if the caller didn't explicitly set count.
        if isinstance(data, dict) and "count" not in data and "initial_count" in data:
            data = dict(data)
            data["count"] = data.get("initial_count")
        return data

    @field_validator(
        "yes_price_dollars",
        "no_price_dollars",
        "taker_fees_dollars",
        "maker_fees_dollars",
        mode="before",
    )
    @classmethod
    def _coerce_optional_dollars(cls, v: Any) -> float | None:
        """Coerce optional fixed-point dollars fields into floats."""
        if v is None:
            return None
        return _parse_fixed_point_dollars(v)

    @field_validator("expiration_time", "created_time", "last_update_time", mode="before")
    @classmethod
    def _coerce_optional_time(cls, v: Any) -> datetime | None:
        """Coerce optional RFC3339 timestamp fields into aware datetimes."""
        return _parse_rfc3339_datetime(v)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "KalshiOrder":
        """Parse an order payload from the Kalshi REST API."""
        return cls.model_validate(payload)


class KalshiPosition(_Model):
    """Subset of position fields used for the normalized position snapshot."""

    ticker: str
    total_traded_dollars: float = 0.0
    position: int = 0
    market_exposure_dollars: float = 0.0
    realized_pnl_dollars: str = "0.0000"
    fees_paid_dollars: str = "0.0000"
    last_updated_ts: datetime | None = None

    @field_validator("total_traded_dollars", "market_exposure_dollars", mode="before")
    @classmethod
    def _coerce_dollars(cls, v: Any) -> float:
        """Coerce fixed-point dollars fields into float dollars."""
        return _parse_fixed_point_dollars(v)

    @field_validator("last_updated_ts", mode="before")
    @classmethod
    def _coerce_last_updated_ts(cls, v: Any) -> datetime | None:
        """Coerce `last_updated_ts` into an aware datetime (if present)."""
        return _parse_rfc3339_datetime(v)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "KalshiPosition":
        """Parse a position payload from the Kalshi REST API."""
        return cls.model_validate(payload)


class KalshiBalance(_Model):
    """Account balance fields returned by the Kalshi REST API."""

    balance: int
    portfolio_value: int
    updated_ts: int

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "KalshiBalance":
        """Parse a balance payload from the Kalshi REST API."""
        return cls.model_validate(payload)