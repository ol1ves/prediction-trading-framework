"""Configuration loading and validation.

This module is responsible for:

- Loading `.env` into the process environment (without overriding existing vars).
- Converting environment variables into strongly-typed Pydantic models.
- Validating required fields and providing actionable error messages.
"""

import os
from typing import TypeVar

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
        
        # Basic validation that it looks like a PEM key
        if not v.strip().startswith('-----BEGIN') or not v.strip().endswith('-----'):
            raise ValueError(
                "Private key must be in PEM format starting with '-----BEGIN' and ending with '-----'. "
                "Make sure to include \\n for line breaks in your .env file."
            )
        
        return v

class Config(BaseModel):
    """Top-level application configuration."""

    kalshi: KalshiConfig = Field(..., description="Kalshi configuration")

def load_config() -> Config:
    """Load application configuration from environment variables.

    Notes:
    - Calls `dotenv.load_dotenv()` so local `.env` values are visible to the process.
    - Raises `ValueError` with actionable messages when required configuration is
      missing or still contains placeholder values.
    """
    # Load variables from `.env` into the process environment (without overriding
    # already-set env vars).
    dotenv.load_dotenv()

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
    return Config(kalshi=kalshi)