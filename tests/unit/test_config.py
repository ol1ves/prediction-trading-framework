import pytest

from config import KalshiConfig, load_config


def test_kalshi_config_base_url_demo_and_prod():
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    demo_cfg = KalshiConfig(api_key="k", private_key=pem, use_demo=True)
    prod_cfg = KalshiConfig(api_key="k", private_key=pem, use_demo=False)

    assert demo_cfg.base_url == "https://demo-api.kalshi.co"
    assert prod_cfg.base_url == "https://api.elections.kalshi.com"


@pytest.mark.parametrize("private_key", ["", "your_kalshi_private_key_here"])
def test_kalshi_config_private_key_required(private_key: str):
    with pytest.raises(ValueError):
        KalshiConfig(api_key="k", private_key=private_key, use_demo=True)


def test_load_config_reads_required_and_defaults(monkeypatch: pytest.MonkeyPatch):
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    monkeypatch.setenv("KALSHI_API_KEY", "test_key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.delenv("KALSHI_RATE_LIMIT", raising=False)
    monkeypatch.delenv("KALSHI_MAX_ATTEMPT", raising=False)
    monkeypatch.delenv("KALSHI_BASE_DELAY", raising=False)
    monkeypatch.delenv("KALSHI_BACKOFF_MULTIPLIER", raising=False)
    monkeypatch.delenv("KALSHI_MAX_DELAY", raising=False)
    monkeypatch.delenv("KALSHI_ORDERBOOK_DEPTH", raising=False)

    cfg = load_config().kalshi
    assert cfg.api_key == "test_key"
    assert cfg.private_key == pem
    assert cfg.rate_limit == 20
    assert cfg.max_attempt == 5
    assert cfg.base_delay == 0.5
    assert cfg.backoff_multiplier == 2.0
    assert cfg.max_delay == 30.0
    assert cfg.orderbook_depth == 10


def test_load_config_parses_optional_fields(monkeypatch: pytest.MonkeyPatch):
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    monkeypatch.setenv("KALSHI_API_KEY", "test_key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.setenv("KALSHI_USE_DEMO", "false")
    monkeypatch.setenv("KALSHI_RATE_LIMIT", "7")
    monkeypatch.setenv("KALSHI_MAX_ATTEMPT", "9")
    monkeypatch.setenv("KALSHI_BASE_DELAY", "0.25")
    monkeypatch.setenv("KALSHI_BACKOFF_MULTIPLIER", "1.5")
    monkeypatch.setenv("KALSHI_MAX_DELAY", "12.5")
    monkeypatch.setenv("KALSHI_ORDERBOOK_DEPTH", "42")

    cfg = load_config().kalshi
    assert cfg.use_demo is False
    assert cfg.rate_limit == 7
    assert cfg.max_attempt == 9
    assert cfg.base_delay == 0.25
    assert cfg.backoff_multiplier == 1.5
    assert cfg.max_delay == 12.5
    assert cfg.orderbook_depth == 42

