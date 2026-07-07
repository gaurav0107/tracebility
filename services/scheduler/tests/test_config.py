"""Config loader: env parsing + defaults."""

from __future__ import annotations

import pytest
from langprobe_scheduler.config import load


def test_load_requires_pg_dsn(monkeypatch):
    monkeypatch.delenv("LANGPROBE_PG_DSN", raising=False)
    with pytest.raises(RuntimeError):
        load()


def test_load_defaults(monkeypatch):
    monkeypatch.setenv("LANGPROBE_PG_DSN", "postgres://x/y")
    monkeypatch.delenv("LANGPROBE_SCHEDULER_REAPER_INTERVAL_S", raising=False)
    monkeypatch.delenv("LANGPROBE_SCHEDULER_LEASE_TIMEOUT_S", raising=False)
    settings = load()
    assert settings.pg_dsn == "postgres://x/y"
    assert settings.reaper_interval_s == 60
    assert settings.lease_timeout_s == 120
    assert settings.log_level == "INFO"


def test_load_overrides(monkeypatch):
    monkeypatch.setenv("LANGPROBE_PG_DSN", "postgres://x/y")
    monkeypatch.setenv("LANGPROBE_SCHEDULER_REAPER_INTERVAL_S", "30")
    monkeypatch.setenv("LANGPROBE_SCHEDULER_LEASE_TIMEOUT_S", "300")
    settings = load()
    assert settings.reaper_interval_s == 30
    assert settings.lease_timeout_s == 300


def test_load_clickhouse_and_alert_defaults(monkeypatch):
    monkeypatch.setenv("LANGPROBE_PG_DSN", "postgres://x/y")
    monkeypatch.delenv("LANGPROBE_CLICKHOUSE_URL", raising=False)
    monkeypatch.delenv("LANGPROBE_SCHEDULER_ALERT_INTERVAL_S", raising=False)
    settings = load()
    assert settings.clickhouse_url is None
    assert settings.alert_interval_s == 60


def test_load_clickhouse_and_alert_overrides(monkeypatch):
    monkeypatch.setenv("LANGPROBE_PG_DSN", "postgres://x/y")
    monkeypatch.setenv("LANGPROBE_CLICKHOUSE_URL", "http://ch:8123")
    monkeypatch.setenv("LANGPROBE_SCHEDULER_ALERT_INTERVAL_S", "15")
    settings = load()
    assert settings.clickhouse_url == "http://ch:8123"
    assert settings.alert_interval_s == 15
