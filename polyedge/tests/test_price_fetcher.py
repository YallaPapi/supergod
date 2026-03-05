"""Tests for price_fetcher -- all API calls mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polyedge.data.price_fetcher import (
    fetch_price_history,
    get_token_id,
    load_checkpoint,
    process_market,
    save_checkpoint,
    OUTPUT_FIELDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_HISTORY = [
    {"t": 1700000000, "p": 0.45},
    {"t": 1700003600, "p": 0.52},
    {"t": 1700007200, "p": 0.60},
]


# ---------------------------------------------------------------------------
# process_market
# ---------------------------------------------------------------------------


@patch("polyedge.data.price_fetcher.fetch_price_history")
@patch("polyedge.data.price_fetcher.get_token_id")
def test_process_market_success(mock_get_token, mock_fetch_history):
    """Happy path: token ID found, history returned, rows formatted."""
    mock_get_token.return_value = "tok_abc123"
    mock_fetch_history.return_value = SAMPLE_HISTORY

    rows = process_market("market_1")

    assert rows is not None
    assert len(rows) == 3
    for row in rows:
        assert row["market_id"] == "market_1"
        assert "timestamp" in row
        assert "yes_price" in row
    assert rows[0]["yes_price"] == 0.45
    assert rows[2]["yes_price"] == 0.60


@patch("polyedge.data.price_fetcher.get_token_id")
def test_process_market_no_token(mock_get_token):
    """Returns None when Gamma API has no token ID."""
    mock_get_token.return_value = None

    result = process_market("market_no_token")
    assert result is None


@patch("polyedge.data.price_fetcher.fetch_price_history")
@patch("polyedge.data.price_fetcher.get_token_id")
def test_process_market_no_history(mock_get_token, mock_fetch_history):
    """Returns None when CLOB API returns no price history."""
    mock_get_token.return_value = "tok_abc123"
    mock_fetch_history.return_value = None

    result = process_market("market_no_history")
    assert result is None


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------


def test_load_checkpoint_empty(tmp_path):
    """Empty set when checkpoint file does not exist."""
    fake_path = tmp_path / "nonexistent.txt"
    result = load_checkpoint(checkpoint_path=fake_path)
    assert result == set()


def test_load_checkpoint_with_data(tmp_path):
    """Loads previously saved market IDs."""
    cp = tmp_path / "checkpoint.txt"
    cp.write_text("m1\nm2\nm3\n")
    result = load_checkpoint(checkpoint_path=cp)
    assert result == {"m1", "m2", "m3"}


def test_save_checkpoint_appends(tmp_path):
    """save_checkpoint appends IDs without overwriting."""
    cp = tmp_path / "checkpoint.txt"
    save_checkpoint("m1", checkpoint_path=cp)
    save_checkpoint("m2", checkpoint_path=cp)
    lines = cp.read_text().strip().split("\n")
    assert lines == ["m1", "m2"]


# ---------------------------------------------------------------------------
# output row format
# ---------------------------------------------------------------------------


@patch("polyedge.data.price_fetcher.fetch_price_history")
@patch("polyedge.data.price_fetcher.get_token_id")
def test_output_row_format(mock_get_token, mock_fetch_history):
    """Each output dict has exactly the expected keys."""
    mock_get_token.return_value = "tok_xyz"
    mock_fetch_history.return_value = SAMPLE_HISTORY

    rows = process_market("fmt_test")
    assert rows is not None
    for row in rows:
        assert set(row.keys()) == set(OUTPUT_FIELDS)


# ---------------------------------------------------------------------------
# get_token_id
# ---------------------------------------------------------------------------


@patch("polyedge.data.price_fetcher.session")
def test_get_token_id_success(mock_session):
    """Parses clobTokenIds from Gamma API response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"clobTokenIds": '["tok_a", "tok_b"]'}
    mock_session.get.return_value = resp

    result = get_token_id("market_x")
    assert result == "tok_a"


@patch("polyedge.data.price_fetcher.session")
def test_get_token_id_404(mock_session):
    """Returns None on non-200 response."""
    resp = MagicMock()
    resp.status_code = 404
    mock_session.get.return_value = resp

    result = get_token_id("bad_market")
    assert result is None


# ---------------------------------------------------------------------------
# fetch_price_history
# ---------------------------------------------------------------------------


@patch("polyedge.data.price_fetcher.session")
def test_fetch_price_history_success(mock_session):
    """Returns history list from CLOB API."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"history": SAMPLE_HISTORY}
    mock_session.get.return_value = resp

    result = fetch_price_history("m1", "tok1")
    assert result == SAMPLE_HISTORY


@patch("polyedge.data.price_fetcher.session")
def test_fetch_price_history_too_short(mock_session):
    """Returns None when history has fewer than 2 data points."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"history": [{"t": 1, "p": 0.5}]}
    mock_session.get.return_value = resp

    result = fetch_price_history("m1", "tok1")
    assert result is None
