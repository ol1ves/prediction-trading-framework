"""Observability primitives (MVP).

This package provides a minimal, modular foundation for:
- Recording internal commands/events/errors as durable records.
- Capturing both "occurred at" and "logged at" timestamps.
- Persisting records to a sink (DuckDB by default) without blocking the event loop.

It is intentionally small so the rest of the system can evolve without tight coupling.
"""

from .models import ObservabilityRecord
from .recorder import ObservabilityRecorder
from .sinks import DuckDBObservabilitySink, InMemoryObservabilitySink, ObservabilitySink

__all__ = [
    "DuckDBObservabilitySink",
    "InMemoryObservabilitySink",
    "ObservabilityRecord",
    "ObservabilityRecorder",
    "ObservabilitySink",
]
