"""Pytest configuration.

Adds the repo's `src/` directory to `sys.path` so tests can import modules like
`config` and `kalshi.client` without installing the project as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    """Configure pytest before collecting/running tests."""
    src_dir = Path(__file__).resolve().parents[1] / "src"
    src_dir_str = str(src_dir)
    if src_dir_str not in sys.path:
        sys.path.insert(0, src_dir_str)

