from __future__ import annotations

from dataclasses import dataclass


_VALID_OPERATORS: set[str] = {"GT", "GTE", "LT", "LTE", "EQ", "IN_RANGE"}


@dataclass(frozen=True, slots=True)
class Subject:
    """Structured representation of a trading subject.

    MVP syntax (all caps, dot-separated):
        {DOMAIN}.{METRIC}.{LOCATION}.{OPERATOR}.{THRESHOLD}

    Examples:
        WEATHER.HIGH_TEMP.NYC.GT.65
        WEATHER.HIGH_TEMP.CHI.GTE.50
        WEATHER.PRECIP.NYC.GT.0.1
        FED.RATE.FOMC.GT.5.25
    """

    raw: str
    domain: str
    metric: str
    location: str
    operator: str
    threshold: float

    @classmethod
    def parse(cls, raw: str) -> Subject:
        """Parse a raw subject string into a structured Subject.

        Raises:
            ValueError: If the subject does not conform to the MVP syntax.
        """
        if not raw or "." not in raw:
            raise ValueError(f"Invalid subject (missing segments): {raw!r}")

        if raw.upper() != raw:
            raise ValueError(f"Subject must be all caps: {raw!r}")

        parts = raw.split(".")
        if len(parts) < 5:
            raise ValueError(f"Invalid subject (expected at least 5 segments): {raw!r}")

        domain, metric, location, operator = parts[0], parts[1], parts[2], parts[3]
        if operator not in _VALID_OPERATORS:
            raise ValueError(f"Invalid operator {operator!r} in subject {raw!r}")

        threshold_str = ".".join(parts[4:])
        try:
            threshold = float(threshold_str)
        except ValueError as exc:
            raise ValueError(f"Invalid threshold {threshold_str!r} in subject {raw!r}") from exc

        return cls(
            raw=raw,
            domain=domain,
            metric=metric,
            location=location,
            operator=operator,
            threshold=threshold,
        )

