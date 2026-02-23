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
- **Execution + portfolio plumbing (MVP)**: normalized models + in-process buses wiring Portfolio Manager to the Execution Engine (polling-based).
- **Strategy layer (MVP)**: protocol, orchestrator, stub strategy, and **trade intent bus** (strategy → intents → PM). A **market resolver** maps strategy subjects to venue/ticker for order submission.
- **Observability (MVP)**: optional command/event logging to a local DuckDB file for debugging and post-run inspection.

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

For the demo, set **`DEMO_TICKER`** to a real demo-market ticker (and **`DEMO_LIMIT_PRICE`** if using the legacy flow). When using the stub strategy (default), **`STUB_STRATEGY_SUBJECT`** is resolved to **`DEMO_TICKER`** via the market resolver.

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

The demo exercises end-to-end wiring: strategy layer → trade intents → portfolio manager → execution engine.

**Default (stub strategy):** With **`RUN_STUB_STRATEGY=true`** (default), the app runs a **stub strategy** on a timer. Each tick the stub may emit a **trade intent**; the **Portfolio Manager** consumes intents from the **trade intent bus**, uses the **market resolver** to map the stub subject to `DEMO_TICKER`, and submits orders through the execution engine. This flow is for testing strategy → intent bus → PM → execution wiring.

- **`STUB_STRATEGY_SUBJECT`** — subject the stub uses (resolver maps this to `DEMO_TICKER`).
- **`STUB_STRATEGY_INTERVAL_S`** — seconds between orchestrator ticks (default `60.0`).

**Legacy flow:** Set **`RUN_STUB_STRATEGY=false`** to run the previous manual buy-then-sell demo (single order then cancel). In that mode, `DEMO_TICKER`, `DEMO_SIDE`, and `DEMO_LIMIT_PRICE` control the order.

Run the demo:

```bash
uv run python src/main.py
```

Observability records are written to DuckDB at `observability.duckdb` by default; set **`OBSERVABILITY_DB_PATH`** to override.

## Contact

Questions, ideas, or contributions: **oliver.santana@nyu.edu**

