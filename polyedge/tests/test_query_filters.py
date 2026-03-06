from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from polyedge.models import Market, PaperTrade
from polyedge.query_filters import real_trade_predicates


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_real_trade_predicates_include_core_filters():
    stmt = (
        select(PaperTrade.id)
        .join(Market, Market.id == PaperTrade.market_id)
        .where(*real_trade_predicates(
            now=_utcnow_naive(), resolved=False, require_future_end=True))
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()

    assert "paper_trades.entry_price > 0.02" in sql
    assert "up or down" in sql
    assert "paper_trades.resolved = false" in sql
    assert "markets.end_date >=" in sql


def test_real_trade_predicates_require_now_for_future_end():
    with pytest.raises(ValueError):
        real_trade_predicates(resolved=False, require_future_end=True)
