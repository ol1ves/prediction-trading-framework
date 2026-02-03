"""Observability sinks (storage backends)."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb

from .models import ObservabilityRecord


class ObservabilitySink(Protocol):
    """A synchronous sink for observability records.

    Sinks are intentionally synchronous because the recorder isolates blocking I/O
    in a background worker (thread) to keep the event loop unblocked.
    """

    def write(self, record: ObservabilityRecord) -> None:
        """Persist a single record."""

    def close(self) -> None:
        """Close any underlying resources."""


class InMemoryObservabilitySink:
    """In-memory sink for tests and local debugging."""

    def __init__(self) -> None:
        """Create an empty in-memory sink."""
        self._lock = threading.Lock()
        self._records: list[ObservabilityRecord] = []

    def write(self, record: ObservabilityRecord) -> None:
        """Append a record to the in-memory list (thread-safe)."""
        with self._lock:
            self._records.append(record)

    def close(self) -> None:  # noqa: D401 - keep interface consistent
        """No-op."""

    def snapshot(self) -> Sequence[ObservabilityRecord]:
        """Return a point-in-time copy of all recorded entries."""
        with self._lock:
            return list(self._records)


@dataclass(frozen=True)
class DuckDBOptions:
    path: Path
    table: str = "observability_records"


class DuckDBObservabilitySink:
    """DuckDB sink for durable local persistence.

    This is intended as a lightweight, embedded store for early-stage observability.
    """

    def __init__(self, *, path: str | Path, table: str = "observability_records") -> None:
        """Create (or open) a DuckDB-backed sink at the given path."""
        self._opts = DuckDBOptions(path=Path(path), table=table)
        self._lock = threading.Lock()
        self._conn = duckdb.connect(str(self._opts.path))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the backing table if it does not exist yet."""
        # DuckDB will happily treat TIMESTAMPTZ strings; we pass Python datetimes directly.
        create_sql = f"""
        create table if not exists {self._opts.table} (
          logged_at timestamptz not null,
          occurred_at timestamptz not null,
          kind varchar not null,
          event_type varchar not null,
          stage varchar not null,
          correlation_id varchar,
          trade_id varchar,
          venue_order_id varchar,
          summary_json varchar not null
        )
        """
        with self._lock:
            self._conn.execute(create_sql)

    def write(self, record: ObservabilityRecord) -> None:
        """Insert a single record into DuckDB.

        Note: the sink stores the record summary as stable JSON.
        """
        summary_json = json.dumps(record.summary, separators=(",", ":"), sort_keys=True, default=str)
        insert_sql = f"""
        insert into {self._opts.table}
        (logged_at, occurred_at, kind, event_type, stage, correlation_id, trade_id, venue_order_id, summary_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            self._conn.execute(
                insert_sql,
                [
                    record.logged_at,
                    record.occurred_at,
                    record.kind,
                    record.event_type,
                    record.stage,
                    record.correlation_id,
                    record.trade_id,
                    record.venue_order_id,
                    summary_json,
                ],
            )

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        with self._lock:
            self._conn.close()

