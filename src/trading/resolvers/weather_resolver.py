from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final

from kalshi.client import KalshiClient

from .resolver import MarketIdentity, SubjectResolver
from .subject import Subject


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


# Kalshi uses KX-prefixed series for weather (e.g. KXHIGHNY-26FEB28-B54.5).
SERIES_MAP: Final[dict[tuple[str, str], str]] = {
    ("HIGH_TEMP", "NYC"): "KXHIGHNY",
    ("HIGH_TEMP", "CHI"): "HIGHCHI",
    ("HIGH_TEMP", "MIA"): "HIGHMIA",
    ("HIGH_TEMP", "DC"): "HIGHDC",
    ("HIGH_TEMP", "AUS"): "HIGHAUS",
}


@dataclass(frozen=True)
class _Bracket:
    ticker: str
    event_ticker: str
    series_ticker: str
    low: float | None
    high: float | None


class WeatherResolver(SubjectResolver):
    """Resolver for WEATHER domain high temperature subjects backed by Kalshi."""

    domain = "WEATHER"

    def __init__(self, kalshi_client: KalshiClient) -> None:
        self._client = kalshi_client

    def can_resolve(self, subject: Subject) -> bool:
        return (
            subject.domain == "WEATHER"
            and (subject.metric, subject.location) in SERIES_MAP
            and subject.operator in {"GT", "GTE", "LT", "LTE", "EQ", "IN_RANGE"}
        )

    async def resolve(self, subject: Subject, for_date: date = _utc_now().date()) -> MarketIdentity | None:
        key = (subject.metric, subject.location)
        series = SERIES_MAP.get(key)
        if series is None:
            return None

        # Kalshi date format: YYMONDD e.g. "25FEB24"
        date_str = for_date.strftime("%y%b%d").upper()
        event_ticker = f"{series}-{date_str}"

        markets = await self._client.get_markets(event_ticker=event_ticker)

        brackets: list[_Bracket] = []
        for m in markets:
            bracket = self._parse_bracket_from_ticker(m.ticker)
            if bracket is not None:
                brackets.append(bracket)

        chosen = self._choose_bracket(brackets, subject)
        if chosen is None:
            return None

        return MarketIdentity(
            ticker=chosen.ticker,
            venue="kalshi",
            event_ticker=chosen.event_ticker,
            series_ticker=chosen.series_ticker,
            subject=subject,
            bracket_low=chosen.low,
            bracket_high=chosen.high,
        )

    @staticmethod
    def _parse_bracket_from_ticker(ticker: str) -> _Bracket | None:
        """Heuristic parser for HIGH* weather bracket tickers.

        Expected shape (per Kalshi convention):
            SERIES-YYMONDD-BNN

        For MVP we treat BNN as a center and assume a 2°F wide bracket.
        Edge brackets that do not follow this pattern are ignored for now.
        """
        parts = ticker.split("-")
        if len(parts) < 3:
            return None
        series_ticker = parts[0]
        event_part = parts[1]
        bracket_code = parts[2]

        if not bracket_code.startswith("B"):
            return None
        try:
            center = float(bracket_code[1:])
        except ValueError:
            return None

        low = center - 1.0
        high = center + 1.0
        event_ticker = f"{series_ticker}-{event_part}"
        return _Bracket(
            ticker=ticker,
            event_ticker=event_ticker,
            series_ticker=series_ticker,
            low=low,
            high=high,
        )

    @staticmethod
    def _choose_bracket(brackets: list[_Bracket], subject: Subject) -> _Bracket | None:
        """Select a single bracket that best matches the subject threshold/operator."""
        if not brackets:
            return None

        threshold = subject.threshold

        # Sort by center to make selection deterministic.
        def center(b: _Bracket) -> float:
            if b.low is not None and b.high is not None:
                return (b.low + b.high) / 2.0
            if b.low is not None:
                return b.low
            if b.high is not None:
                return b.high
            return 0.0

        brackets_sorted = sorted(brackets, key=center)

        if subject.operator in {"GT", "GTE"}:
            candidates = [b for b in brackets_sorted if b.low is not None and b.low >= threshold]
            return candidates[0] if candidates else brackets_sorted[-1]

        if subject.operator in {"LT", "LTE"}:
            candidates = [b for b in brackets_sorted if b.high is not None and b.high <= threshold]
            return candidates[-1] if candidates else brackets_sorted[0]

        if subject.operator == "EQ" or subject.operator == "IN_RANGE":
            for b in brackets_sorted:
                if b.low is not None and b.high is not None and b.low <= threshold < b.high:
                    return b

        return None

