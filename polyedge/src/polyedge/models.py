from datetime import date, datetime
from sqlalchemy import String, Float, DateTime, Date, Boolean, Text, Integer, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import uuid


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String, index=True, default="")
    market_category: Mapped[str | None] = mapped_column(String(30), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    yes_price: Mapped[float] = mapped_column(Float, default=0.5)
    no_price: Mapped[float] = mapped_column(Float, default=0.5)
    volume: Mapped[float] = mapped_column(Float, default=0)
    liquidity: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(String)
    resolution_source: Mapped[str] = mapped_column(String, default="")
    clob_token_ids: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    yes_price: Mapped[float] = mapped_column(Float)
    no_price: Mapped[float] = mapped_column(Float)
    volume_24h: Mapped[float] = mapped_column(Float, default=0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Factor(Base):
    __tablename__ = "factors"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    market_id: Mapped[str | None] = mapped_column(String, ForeignKey("markets.id"), index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    subcategory: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (Index("ix_factor_cat_ts", "category", "timestamp"),)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    predicted_outcome: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    entry_yes_price: Mapped[float] = mapped_column(Float)
    factor_ids: Mapped[str] = mapped_column(Text, default="")
    factor_categories: Mapped[str] = mapped_column(Text, default="")
    correct: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class FactorWeight(Base):
    __tablename__ = "factor_weights"
    category: Mapped[str] = mapped_column(String, primary_key=True)
    total_predictions: Mapped[int] = mapped_column(Integer, default=0)
    correct_predictions: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[float] = mapped_column(Float, default=0.5)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyFeature(Base):
    """One numeric feature for one date. This is the feature matrix."""
    __tablename__ = "daily_features"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    value: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        Index("ix_feat_date_name", "date", "name", unique=True),
    )


class MarketPriceHistory(Base):
    """Historical CLOB trading prices for a market."""
    __tablename__ = "market_price_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    yes_price: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        Index("ix_mph_market_ts", "market_id", "timestamp"),
    )


class TradingRule(Base):
    """A discovered correlation rule."""
    __tablename__ = "trading_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(50))
    conditions_json: Mapped[str] = mapped_column(Text)
    predicted_side: Mapped[str] = mapped_column(String(3))
    win_rate: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer)
    breakeven_price: Mapped[float] = mapped_column(Float)
    avg_roi: Mapped[float] = mapped_column(Float, default=0)
    market_filter: Mapped[str] = mapped_column(Text, default="")
    tier: Mapped[int] = mapped_column(Integer, default=3)
    quality_label: Mapped[str] = mapped_column(String(20), default="exploratory")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PaperTrade(Base):
    """Paper trade tracking -- both YES and NO sides."""
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("trading_rules.id"), index=True)
    side: Mapped[str] = mapped_column(String(3))
    entry_price: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    bet_size: Mapped[float] = mapped_column(Float, default=1.0)
    trade_source: Mapped[str] = mapped_column(String(20), default="ngram", index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    won: Mapped[bool | None] = mapped_column(Boolean)
    pnl: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class NgramStat(Base):
    """Win rate stats for word phrases in market questions."""
    __tablename__ = "ngram_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ngram: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    n: Mapped[int] = mapped_column(Integer)
    total_markets: Mapped[int] = mapped_column(Integer)
    yes_count: Mapped[int] = mapped_column(Integer)
    no_count: Mapped[int] = mapped_column(Integer)
    yes_rate: Mapped[float] = mapped_column(Float)
    no_rate: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ServiceHeartbeat(Base):
    """Runtime heartbeat for scheduler services/loops."""
    __tablename__ = "service_heartbeats"
    service: Mapped[str] = mapped_column(String(80), primary_key=True)
    host: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    details: Mapped[str] = mapped_column(Text, default="")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
