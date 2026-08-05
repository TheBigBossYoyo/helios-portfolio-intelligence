from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires timezone-aware datetimes")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("ExactDecimal requires Decimal values")
        return format(value, "f")

    def process_result_value(self, value: str | None, dialect: object) -> Decimal | None:
        del dialect
        if value is None:
            return None
        return Decimal(value)


QUANTITY_NUMERIC = ExactDecimal()
MONEY_NUMERIC = ExactDecimal()
FX_NUMERIC = ExactDecimal()


class RawSnapshot(Base):
    __tablename__ = "raw_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[object] = mapped_column(JSON, nullable=False)


class Instrument(Base):
    __tablename__ = "instruments"

    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    short_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_on: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    extended_hours: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    max_open_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    working_schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    yahoo_ticker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unresolved")
    mapping_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_details_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    mapped_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PositionLive(Base):
    __tablename__ = "positions_live"

    ts: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instrument_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instrument_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    quantity_available_for_trading: Mapped[Decimal | None] = mapped_column(
        QUANTITY_NUMERIC,
        nullable=True,
    )
    quantity_in_pies: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    average_price_paid: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    current_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    position_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    wallet_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wallet_current_value: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    wallet_fx_impact: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    wallet_total_cost: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    wallet_unrealized_profit_loss: Mapped[Decimal | None] = mapped_column(
        MONEY_NUMERIC,
        nullable=True,
    )


class Transaction(Base):
    __tablename__ = "transactions"

    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)


class OrderHistory(Base):
    __tablename__ = "orders_history"

    fill_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fill_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    t212_ticker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instrument_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instrument_currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fill_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    filled_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    order_filled_value: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    wallet_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wallet_net_value: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    wallet_fx_rate: Mapped[Decimal | None] = mapped_column(FX_NUMERIC, nullable=True)
    wallet_realised_profit_loss: Mapped[Decimal | None] = mapped_column(
        MONEY_NUMERIC,
        nullable=True,
    )
    wallet_taxes_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)


class Dividend(Base):
    __tablename__ = "dividends"

    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    paid_on: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    t212_ticker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dividend_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ticker_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    amount_in_euro: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    gross_amount_per_share: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)


class SyncStatus(Base):
    __tablename__ = "sync_status"

    endpoint: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PositionReconciliation(Base):
    __tablename__ = "position_reconciliation"

    ts: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    replayed_quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    live_quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    difference_quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    tolerance_quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
