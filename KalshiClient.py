"""Client for Kalshi trade API with request signing."""

import base64
import datetime

import requests

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from config import KalshiConfig

class KalshiClient:
    """Authenticated client for Kalshi API (RSA-PSS signing)."""

    def __init__(self, config: KalshiConfig):
        """Initialize client from config (api_key, private_key, base_url)."""
        self.api_key = config.api_key
        self.private_key = _load_private_key(config.private_key)
        self.base_url = config.base_url

    def _sign_request(self, method, path):
        """Sign request with RSA-PSS for Kalshi auth headers."""
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
        path_without_query = path.split('?')[0]

        # Create the message to sign
        message = f"{timestamp}{method}{path_without_query}".encode('utf-8')

        # Sign with RSA-PSS
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )

        # Return base64 encoded
        return base64.b64encode(signature).decode('utf-8')

    def _send_request(self, method, path, body):
        """Send signed HTTP request and return JSON response."""
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
        signature = self._sign_request(method, path)

        headers = {
            'KALSHI-ACCESS-KEY': self.api_key,
            'KALSHI-ACCESS-SIGNATURE': signature,
            'KALSHI-ACCESS-TIMESTAMP': timestamp
        }

        response = requests.request(method, self.base_url + path, headers=headers, json=body)
        return response.json()

    def get_balance_cents(self):
        """Fetch balance in cents."""
        response = self._send_request("GET", "/trade-api/v2/portfolio/balance", None)
        
        return response['balance']

    def get_portfolio_value_cents(self):
        """Fetch portfolio value in cents."""
        response = self._send_request("GET", "/trade-api/v2/portfolio/balance", None)
        return response['portfolio_value']
    
    def get_events(self, limit=100, status=None):
        """Fetch events."""
        query_string = f"limit={limit}"
        if status:
            query_string += f"&status={status}"
        response = self._send_request("GET", "/trade-api/v2/events/?{query_string}", None)

        return response['events']


def _load_private_key(pem_str: str):
    """Load RSA private key from PEM string (handles \\n from .env)."""
    pem_bytes = pem_str.strip().replace("\\n", "\n").encode("utf-8")
    return load_pem_private_key(pem_bytes, password=None)