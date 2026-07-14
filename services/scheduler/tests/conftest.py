"""Shared fixtures for the scheduler test suite.

Unit tests set env directly. Integration tests use a real local
Postgres via LANGPROBE_TEST_DSN; the runner is responsible for
applying migrations against it.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def integration_dsn() -> str:
    dsn = os.environ.get("LANGPROBE_TEST_DSN")
    if not dsn:
        pytest.skip("set LANGPROBE_TEST_DSN to run integration tests")
    return dsn
