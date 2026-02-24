"""Configuration loading and validation.

This module is responsible for:

- Loading `.env` into the process environment (overriding existing vars so .env wins under the debugger).
- Converting environment variables into strongly-typed Pydantic models.
- Validating required fields and providing actionable error messages.
"""

import os
from typing import TypeVar
from pathlib import Path
import dotenv
from pydantic import BaseModel, Field, field_validator

_T = TypeVar("_T", int, float)


def _get_required_env(name: str) -> str:
    """Read a required env var or raise a helpful error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required. Please set it in your .env file.")
    if value.startswith("your_") and value.endswith("_here"):
        raise ValueError(f"{name} is required. Please replace the placeholder value in your .env file.")
    return value


def _get_env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var with a default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false). Got: {raw!r}")


def _get_env_number(name: str, default: _T, cast: type[_T]) -> _T:
    """Read an int/float env var with a default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a {cast.__name__}. Got: {raw!r}") from exc


def _get_required_env_float(name: str) -> float:
    """Read a required float env var or raise a helpful error."""
    raw = _get_required_env(name)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a float. Got: {raw!r}") from exc
    return value


class PortfolioManagerConfig(BaseModel):
    """Configuration for the portfolio manager (sizing and guardrails)."""

    kelly_fraction: float = Field(default=0.25, description="Multiplier applied to full Kelly output")
    min_edge_threshold: float = Field(default=0.05, description="Minimum edge (signal - implied) before trading")
    max_position_fraction: float = Field(default=0.05, description="Max fraction of bankroll per position")
    bankroll: float = Field(..., description="Total capital available for trading (fixed for MVP)")

    @field_validator("kelly_fraction")
    @classmethod
    def validate_kelly_fraction(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("kelly_fraction must be in (0, 1]. Got: {!r}".format(v))
        return v

    @field_validator("min_edge_threshold")
    @classmethod
    def validate_min_edge_threshold(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("min_edge_threshold must be in (0, 1). Got: {!r}".format(v))
        return v

    @field_validator("max_position_fraction")
    @classmethod
    def validate_max_position_fraction(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("max_position_fraction must be in (0, 1]. Got: {!r}".format(v))
        return v

    @field_validator("bankroll")
    @classmethod
    def validate_bankroll(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("bankroll must be > 0. Got: {!r}".format(v))
        return v


class KalshiConfig(BaseModel):
    """Configuration for interacting with the Kalshi API."""

    api_key: str = Field(..., description="Kalshi API key")
    private_key: str = Field(..., description="Kalshi API private key")
    use_demo: bool = Field(default=True, description="Use demo mode")

    # Optional tuning knobs (see env_example.env)
    rate_limit: int = Field(default=20, description="Max requests per second")
    max_attempt: int = Field(default=5, description="Max attempts per request")
    base_delay: float = Field(default=0.5, description="Initial retry delay (seconds)")
    backoff_multiplier: float = Field(default=2.0, description="Exponential backoff multiplier")
    max_delay: float = Field(default=30.0, description="Max total delay before failing (seconds)")
    orderbook_depth: int = Field(default=10, description="Default orderbook depth")

    @property
    def base_url(self) -> str:
        """Get the base URL for the Kalshi API."""
        if self.use_demo:
            return "https://demo-api.kalshi.co"
        return "https://api.elections.kalshi.com"

    @field_validator("api_key")
    def validate_api_key(cls, v: str) -> str:
        """Validate api key is set (not empty/placeholder)."""
        if not v or v == "your_kalshi_api_key_here":
            raise ValueError("KALSHI_API_KEY is required. Please set it in your .env file.")
        return v

    @field_validator('private_key')
    def validate_private_key(cls, v: str) -> str:
        """Validate and format private key."""
        if not v or v == "your_kalshi_private_key_here":
            raise ValueError("KALSHI_PRIVATE_KEY is required. Please set it in your .env file.")
        # Strip optional surrounding double quotes (IDE envFile may inject quoted or truncated values)
        # Include Unicode smart quotes in case .env or IDE uses them
        _QUOTES = ('"', '\u201c', '\u201d')
        v = v.strip()
        if any(v.startswith(q) for q in _QUOTES):
            v = v[1:]
        if any(v.endswith(q) for q in _QUOTES):
            v = v[:-1]
        v = v.strip()
        if not v.startswith('-----BEGIN') or not v.endswith('-----'):
            raise ValueError(
                "Private key must be in PEM format starting with '-----BEGIN' and ending with '-----'. "
                "Make sure to include \\n for line breaks in your .env file."
            )
        return v

class Config(BaseModel):
    """Top-level application configuration."""

    kalshi: KalshiConfig = Field(..., description="Kalshi configuration")
    portfolio_manager: PortfolioManagerConfig = Field(..., description="Portfolio manager sizing and guardrails")


def load_config() -> Config:
    """Load application configuration from environment variables.

    Loads `.env` from the project root (parent of this file's package dir), then
    from the process cwd if not found, so behaviour is consistent for both
    `uv run src/main.py` and the debugger.

    Raises `ValueError` with actionable messages when required configuration is
    missing or still contains placeholder values.
    """
    project_root = Path(__file__).resolve().parent.parent
    env_file = project_root / ".env"
    if not env_file.exists():
        env_file = Path.cwd() / ".env"
    # override=True so .env wins over IDE envFile (avoids truncated/inconsistent private key)
    dotenv.load_dotenv(env_file, override=True)

    kalshi = KalshiConfig(
        api_key=_get_required_env("KALSHI_API_KEY"),
        private_key=_get_required_env("KALSHI_PRIVATE_KEY"),
        use_demo=_get_env_bool("KALSHI_USE_DEMO", True),
        rate_limit=_get_env_number("KALSHI_RATE_LIMIT", 20, int),
        max_attempt=_get_env_number("KALSHI_MAX_ATTEMPT", 5, int),
        base_delay=_get_env_number("KALSHI_BASE_DELAY", 0.5, float),
        backoff_multiplier=_get_env_number("KALSHI_BACKOFF_MULTIPLIER", 2.0, float),
        max_delay=_get_env_number("KALSHI_MAX_DELAY", 30.0, float),
        orderbook_depth=_get_env_number("KALSHI_ORDERBOOK_DEPTH", 10, int),
    )
    portfolio_manager = PortfolioManagerConfig(
        kelly_fraction=_get_env_number("PM_KELLY_FRACTION", 0.25, float),
        min_edge_threshold=_get_env_number("PM_MIN_EDGE_THRESHOLD", 0.05, float),
        max_position_fraction=_get_env_number("PM_MAX_POSITION_FRACTION", 0.05, float),
        bankroll=_get_required_env_float("PM_BANKROLL"),
    )
    return Config(kalshi=kalshi, portfolio_manager=portfolio_manager)