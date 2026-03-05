from datetime import datetime
from sqlalchemy import String, Float, DateTime, Boolean, Text, Integer, ForeignKey, Index
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
    description: Mapped[str] = mapped_column(Text, default="")
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    yes_price: Mapped[float] = mapped_column(Float, default=0.5)
    no_price: Mapped[float] = mapped_column(Float, default=0.5)
    volume: Mapped[float] = mapped_column(Float, default=0)
    liquidity: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(String)
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
