"""Shared SQLAlchemy filters for dashboard trade cohorts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_

from polyedge.models import Market, PaperTrade


def noise_market_predicate():
    """Known high-frequency/noise cohort currently excluded from live metrics."""
    return or_(
        Market.market_category == "crypto_updown",
        Market.question.ilike("%up or down%"),
    )


def real_trade_predicates(
    *,
    now: datetime | None = None,
    resolved: bool | None = None,
    require_future_end: bool = False,
) -> list:
    """Canonical predicate set for user-facing paper-trade metrics."""
    predicates: list = [PaperTrade.entry_price > 0.02, ~noise_market_predicate()]

    if resolved is not None:
        predicates.append(PaperTrade.resolved == resolved)  # noqa: E712

    if require_future_end:
        if now is None:
            raise ValueError("now is required when require_future_end=True")
        predicates.extend([Market.end_date.is_not(None), Market.end_date >= now])

    return predicates
