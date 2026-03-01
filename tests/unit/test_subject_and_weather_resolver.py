from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from trading.resolvers import MarketIdentity, MarketResolver, SERIES_MAP, Subject, WeatherResolver


def test_subject_parse_basic() -> None:
    s = Subject.parse("WEATHER.HIGH_TEMP.NYC.GT.65")
    assert s.raw == "WEATHER.HIGH_TEMP.NYC.GT.65"
    assert s.domain == "WEATHER"
    assert s.metric == "HIGH_TEMP"
    assert s.location == "NYC"
    assert s.operator == "GT"
    assert s.threshold == 65.0


def test_subject_parse_decimal_threshold() -> None:
    s = Subject.parse("FED.RATE.FOMC.GT.5.25")
    assert s.domain == "FED"
    assert s.metric == "RATE"
    assert s.location == "FOMC"
    assert s.operator == "GT"
    assert s.threshold == pytest.approx(5.25)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "weather.high_temp.nyc.GT.65",
        "WEATHER.HIGH_TEMP.NYC",
        "WEATHER.HIGH_TEMP.NYC.BADOP.65",
        "WEATHER.HIGH_TEMP.NYC.GT.not_a_number",
    ],
)
def test_subject_parse_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        Subject.parse(raw)


class _FakeKalshiClient:
    def __init__(self, markets: list[dict[str, str]]) -> None:
        self._markets = markets

    async def get_markets(self, event_ticker: str | None = None, **_: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(**m) for m in self._markets if event_ticker is None or m["event_ticker"] == event_ticker]


@pytest.mark.asyncio
async def test_weather_resolver_picks_bracket_for_gt() -> None:
    series = SERIES_MAP[("HIGH_TEMP", "NYC")]
    event = f"{series}-25FEB24"
    markets = [
        {"ticker": f"{series}-25FEB24-B60", "event_ticker": event},
        {"ticker": f"{series}-25FEB24-B70", "event_ticker": event},
    ]
    client = _FakeKalshiClient(markets)
    resolver = WeatherResolver(client)  # type: ignore[arg-type]

    subject = Subject.parse("WEATHER.HIGH_TEMP.NYC.GT.62")
    assert resolver.can_resolve(subject)

    identity = await resolver.resolve(subject, date(2024, 2, 25))
    assert isinstance(identity, MarketIdentity)
    assert identity.series_ticker == series
    assert identity.event_ticker == event
    # 62 is above the center of 60, so bracket starting at 70 is the first with low >= threshold
    assert identity.ticker.endswith("B70")


@pytest.mark.asyncio
async def test_market_resolver_routes_to_weather_plugin() -> None:
    series = SERIES_MAP[("HIGH_TEMP", "NYC")]
    event = f"{series}-25FEB24"
    markets = [
        {"ticker": f"{series}-25FEB24-B60", "event_ticker": event},
        {"ticker": f"{series}-25FEB24-B70", "event_ticker": event},
    ]
    client = _FakeKalshiClient(markets)
    weather = WeatherResolver(client)  # type: ignore[arg-type]

    resolver = MarketResolver(subject_to_ticker={"STUB_SUBJECT": "ABC"}, resolvers=[weather])

    # Structured WEATHER subject uses plugin
    subject = "WEATHER.HIGH_TEMP.NYC.GT.62"
    identity = await resolver.resolve(subject)
    assert identity is not None
    assert identity.subject is not None
    assert identity.subject.raw == subject
    assert identity.series_ticker == series

    # Non-structured subject still uses the hardcoded mapping
    stub_identity = await resolver.resolve("STUB_SUBJECT")
    assert stub_identity is not None
    assert stub_identity.ticker == "ABC"

