import dotenv
from pydantic import BaseModel, Field, field_validator
import os

dotenv.load_dotenv()

class KalshiConfig(BaseModel):
    api_key: str = Field(..., description="Kalshi API key")
    private_key: str = Field(..., description="Kalshi API private key")
    use_demo: bool = Field(default=True, description="Use demo mode")

    @property
    def base_url(self) -> str:
        """Get the base URL for the Kalshi API."""
        if self.use_demo:
            return "https://demo-api.kalshi.co"
        return "https://api.elections.kalshi.com"

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
    kalshi: KalshiConfig = Field(..., description="Kalshi configuration")

def load_config() -> Config:
    kalshi = KalshiConfig(
        api_key=os.getenv("KALSHI_API_KEY", ""),
        private_key=os.getenv("KALSHI_PRIVATE_KEY", ""),
        use_demo=os.getenv("KALSHI_USE_DEMO", "true").lower() == "true",
    )
    return Config(kalshi=kalshi)