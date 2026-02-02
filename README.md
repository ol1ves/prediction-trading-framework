## Open Prediction Trading (Active Development)

This repo is a **side / passion project** exploring what a modular, “bottom-up” prediction-market trading system could become over time.

The long-term direction is a framework where you can plug in:
- **Data providers** (to ingest real-world/live data)
- **Trading strategies** (to decide when to act)
- **Market/exchange adapters** (to execute on specific venues, starting with Kalshi)

**Important:** this project is **under active development** and will go through **significant change**. Expect breaking changes, refactors, and incomplete pieces as we build a solid foundation first.

## What’s implemented today

- **Kalshi API wrapper/client**: an authenticated, async-friendly client for Kalshi’s Trade API with request signing.
- **Config + environment setup**: `.env`-based configuration with validation and tuning knobs (rate limiting / retries / defaults).

## What we’re working on now

- A **robust trade execution engine** (order submission, tracking, and safer automation on top of the API client).

## Not yet stable / not financial advice

This code is **experimental**. It’s not production-ready and is not financial advice. Use at your own risk, especially anything that can place orders.

## Development

### Prerequisites

- **Python**: \(>= 3.13\) (this repo targets Python **3.13** via `.python-version`)
- **uv**: recommended for dependency management

### Configure environment

1) Create a `.env` file from the example:

```bash
cp env_example.env .env
```

2) Fill in required values:
- **`KALSHI_API_KEY`**
- **`KALSHI_PRIVATE_KEY`** (PEM; include `\n` for line breaks)
- **`KALSHI_USE_DEMO`** (recommended to keep `true` while developing)

### Install dependencies (including dev)

```bash
uv sync --all-groups
```

### Run tests

```bash
uv run pytest
```

Notes:
- **Integration tests** require valid Kalshi credentials and network access.
- Tests marked **`live_trading`** are opt-in and may place real orders (typically against the demo environment).

## Contact

Questions, ideas, or contributions: **oliver.santana@nyu.edu**

