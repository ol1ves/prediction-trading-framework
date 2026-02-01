"""Client for Kalshi trade API with request signing."""

import base64
import datetime

import requests
from urllib.parse import urlencode

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

    def _build_query_string(self, params: dict) -> str:
        """Build a query string from a dict, omitting None values.

        Notes:
        - Lists/tuples are encoded as comma-separated values.
        - Booleans are encoded as "true"/"false".
        """
        filtered = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                filtered[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                filtered[key] = ",".join(str(v) for v in value)
            else:
                filtered[key] = str(value)

        if not filtered:
            return ""
        return "?" + urlencode(filtered)

    def _normalize_ticker(self, ticker):
        """Convert a single ticker value to uppercase (or None if not provided)."""
        if ticker is None:
            return None
        return str(ticker).upper()

    def _normalize_tickers(self, tickers):
        """Convert one-or-many tickers to uppercase.

        Accepts:
        - list/tuple of tickers
        - comma-separated string of tickers
        - a single ticker value
        """
        if tickers is None:
            return None
        if isinstance(tickers, (list, tuple)):
            return [self._normalize_ticker(t) for t in tickers]

        tickers_str = str(tickers)
        if "," in tickers_str:
            return [self._normalize_ticker(t.strip()) for t in tickers_str.split(",") if t.strip()]
        return self._normalize_ticker(tickers_str)

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
        query = self._build_query_string({"limit": limit, "status": status})
        response = self._send_request("GET", f"/trade-api/v2/events{query}", None)

        return response['events']

    def get_markets(
        self,
        limit=100,
        cursor=None,
        event_ticker=None,
        series_ticker=None,
        min_created_ts=None,
        max_created_ts=None,
        min_updated_ts=None,
        max_close_ts=None,
        min_close_ts=None,
        min_settled_ts=None,
        max_settled_ts=None,
        status=None,
        tickers=None,
        mve_filter=None,
    ):
        """Get markets (optionally filtered/paginated)."""
        query = self._build_query_string(
            {
                "limit": limit,
                "cursor": cursor,
                "event_ticker": self._normalize_ticker(event_ticker),
                "series_ticker": self._normalize_ticker(series_ticker),
                "min_created_ts": min_created_ts,
                "max_created_ts": max_created_ts,
                "min_updated_ts": min_updated_ts,
                "max_close_ts": max_close_ts,
                "min_close_ts": min_close_ts,
                "min_settled_ts": min_settled_ts,
                "max_settled_ts": max_settled_ts,
                "status": status,
                "tickers": self._normalize_tickers(tickers),
                "mve_filter": mve_filter,
            }
        )
        return self._send_request("GET", f"/trade-api/v2/markets{query}", None)

    def get_market(self, ticker: str):
        """Get a single market by ticker."""
        ticker = self._normalize_ticker(ticker)
        response = self._send_request("GET", f"/trade-api/v2/markets/{ticker}", None)
        return response["market"]

    def get_event(self, event_ticker: str, with_nested_markets: bool = False):
        """Get a single event by ticker (optionally with nested markets)."""
        event_ticker = self._normalize_ticker(event_ticker)
        query = self._build_query_string({"with_nested_markets": with_nested_markets or None})
        return self._send_request("GET", f"/trade-api/v2/events/{event_ticker}{query}", None)

    def get_orders(
        self,
        ticker=None,
        event_ticker=None,
        min_ts=None,
        max_ts=None,
        status=None,
        limit=100,
        cursor=None,
        subaccount=None,
    ):
        """Get orders with optional filtering/pagination."""
        query = self._build_query_string(
            {
                "ticker": self._normalize_ticker(ticker),
                "event_ticker": self._normalize_ticker(event_ticker),
                "min_ts": min_ts,
                "max_ts": max_ts,
                "status": status,
                "limit": limit,
                "cursor": cursor,
                "subaccount": subaccount,
            }
        )
        return self._send_request("GET", f"/trade-api/v2/portfolio/orders{query}", None)

    def create_order(self, ticker: str, side: str, action: str, count: int, price: float, **kwargs):
        """Create an order (buy/sell) for a given market ticker.

        Required:
        - ticker: market ticker
        - count: number of contracts to buy/sell
        - side: "yes" | "no"
        - action: "buy" | "sell"

        Optional fields should be passed via kwargs and will be forwarded into the JSON body.
        """
        body = {"ticker": self._normalize_ticker(ticker), "side": side, "action": action, "count": count, f"{side}_price": price}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        response = self._send_request("POST", "/trade-api/v2/portfolio/orders", body)
        return response["order"]

    def get_order(self, order_id: str):
        """Get a single order by its order_id."""
        response = self._send_request("GET", f"/trade-api/v2/portfolio/orders/{order_id}", None)
        return response["order"]

    def cancel_order(self, order_id: str, subaccount=None):
        """Cancel (fully reduce) an order by order_id."""
        query = self._build_query_string({"subaccount": subaccount})
        return self._send_request("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}{query}", None)

    def amend_order(self, order_id: str, ticker: str, side: str, action: str, **kwargs):
        """Amend an existing order's price and/or max fillable contracts.

        Optional fields should be passed via kwargs and will be forwarded into the JSON body.
        """
        body = {"ticker": self._normalize_ticker(ticker), "side": side, "action": action}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        return self._send_request("POST", f"/trade-api/v2/portfolio/orders/{order_id}/amend", body)


def _load_private_key(pem_str: str):
    """Load RSA private key from PEM string (handles \\n from .env)."""
    pem_bytes = pem_str.strip().replace("\\n", "\n").encode("utf-8")
    return load_pem_private_key(pem_bytes, password=None)