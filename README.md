## Prediction Trading Framework (Active Development)

This repo is a **side / passion project** exploring what a modular, “bottom-up” prediction-market trading system could become over time.

The long-term direction is a framework where you can plug in:
- **Data providers** (to ingest real-world/live data)
- **Trading strategies** (to decide when to act)
- **Market/exchange adapters** (to execute on specific venues, starting with Kalshi)

**Important:** this project is **under active development** and will go through **significant change**. Expect breaking changes, refactors, and incomplete pieces as we build a solid foundation first.

## What’s implemented today

- **Kalshi API wrapper/client**: an authenticated, async-friendly client for Kalshi’s Trade API with request signing.
- **Config + environment setup**: `.env`-based configuration with validation and tuning knobs (rate limiting / retries / defaults).
- **Execution + portfolio plumbing (MVP)**: normalized models + an in-process message bus wiring a minimal Portfolio Manager to a basic Execution Engine (polling-based).

## What we’re working on now

- Hardening the **trade execution engine** (more robust order lifecycle handling, reconciliation, and safety rails).
- Building out the **portfolio manager** beyond plumbing (risk, sizing, and position management rules).

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

### Demo runtime (very early)

There is a minimal end-to-end demo runtime that wires up:
- `KalshiClient` → `KalshiExecutionAdapter` → `ExecutionEngine`
- `PortfolioManager` ↔ (command/event buses) ↔ `ExecutionEngine`

Run it with:

```bash
uv run python src/main.py
```

By default it submits a small **demo** order and may cancel it shortly after. You should set `DEMO_TICKER` to a real demo-market ticker, and `DEMO_LIMIT_PRICE` to something reasonable for testing first (see `env_example.env`).

## Contact

Questions, ideas, or contributions: **oliver.santana@nyu.edu**

