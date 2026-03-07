"""Tests for database settings configuration."""

from polyedge.db import Settings


def test_default_database_url_is_local():
    settings = Settings(_env_file=None)
    assert ("localhost" in settings.database_url) or ("127.0.0.1" in settings.database_url)


def test_database_url_reads_polyedge_env_prefix(monkeypatch):
    expected = "postgresql+asyncpg://user:pass@db.local:5432/polyedge_test"
    monkeypatch.setenv("POLYEDGE_DATABASE_URL", expected)
    settings = Settings(_env_file=None)
    assert settings.database_url == expected


def test_prediction_metrics_cutoff_reads_env(monkeypatch):
    cutoff = "2026-03-06T04:00:00Z"
    monkeypatch.setenv("POLYEDGE_PREDICTION_METRICS_CUTOFF", cutoff)
    settings = Settings(_env_file=None)
    assert settings.prediction_metrics_cutoff == cutoff


def test_prediction_resolution_sources_reads_env(monkeypatch):
    value = "polymarket_api,manual_override"
    monkeypatch.setenv("POLYEDGE_PREDICTION_RESOLUTION_SOURCES", value)
    settings = Settings(_env_file=None)
    assert settings.prediction_resolution_sources == value
