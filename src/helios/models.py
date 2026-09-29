from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .compression import CompressedText


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
    #: The JSON payload, compressed (see helios.compression); read and written through
    #: helios.raw_store, which decodes deltas against ``delta_base_id``.
    payload_blob: Mapped[bytes] = mapped_column("payload_json", LargeBinary, nullable=False)
    stream_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    delta_base_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_raw_snapshots_stream", "stream_key", "id"),)


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
    # Indexed because the data-quality report filters on it three times per request, and the
    # T212 instrument metadata cache is ~17k rows -- unindexed that was three full scans and
    # about 5s of the response, enough to trip the frontend's fetch timeout.
    mapping_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unresolved", index=True
    )
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


class FxRateDaily(Base):
    __tablename__ = "fx_rates_daily"

    rate_date: Mapped[date] = mapped_column(primary_key=True)
    currency_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    eur_per_unit: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_date: Mapped[date] = mapped_column(nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MarketPriceDaily(Base):
    __tablename__ = "market_prices_daily"

    price_date: Mapped[date] = mapped_column(primary_key=True)
    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(16), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_date: Mapped[date] = mapped_column(nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)


class RawNews(Base):
    """Raw feed bodies, stored before anything is parsed out of them.

    Same raw-first rule as `raw_snapshots`: the bytes a publisher actually served are the record
    of truth, so a parser bug can be re-run against history instead of losing it.
    """

    __tablename__ = "raw_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The body, compressed (see helios.compression); read and written through helios.raw_store.
    body_blob: Mapped[bytes] = mapped_column("body", LargeBinary, nullable=False)
    stream_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    delta_base_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_raw_news_stream", "stream_key", "id"),)


class NewsItem(Base):
    """A parsed article.

    `t212_ticker` / `isin` come from the feed's *declared* binding in config, never from pattern
    matching a headline — guessing which company a story is about would fabricate a relationship
    the data does not contain.
    """

    __tablename__ = "news_items"

    # sha256 of (url, published_at) — the plan's dedupe rule, expressed as a key so a NULL
    # published_at cannot slip past a UNIQUE index (SQLite treats NULLs as distinct).
    dedupe_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    feed_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="rss")
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    t212_ticker: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Tracking-stripped URL and normalised headline: the two keys that let the same story from
    # Yahoo, Marketaux and a publisher feed collapse into one row across syncs.
    canonical_url: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    title_key: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    raw_news_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Thesis(Base):
    """An investment thesis: why you hold something, written down before the outcome is known.

    The point of recording it is to make later self-assessment honest, so `opened_on` and the
    original `body` are never rewritten by a status change — only `outcome_note` is added.
    """

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    t212_ticker: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    conviction: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="draft")
    opened_on: Mapped[date] = mapped_column(nullable=False)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class JournalEntry(Base):
    """A dated note. Attached to a thesis, or standalone when `thesis_id` is null."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AiRun(Base):
    """One Claude call, stored raw-first so every published insight is auditable.

    `prompt_json` and `response_json` are the exact request and response. If a narrative later
    looks wrong, you can see precisely which numbers were put in front of the model.
    """

    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    #: "analysis" (on demand) or "weekly" (the weekly review).
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="analysis", server_default="analysis"
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    response_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    served_by_model: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AiObservation(Base):
    """A single parsed observation from an AI run.

    Deliberately called an *observation*, not a recommendation: each row restates something
    already present in the analytics, with the evidence it came from. `evidence` must quote the
    figure the observation rests on, which is what makes an unfounded claim visible.
    """

    __tablename__ = "ai_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    t212_ticker: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)


class FactorReturnDaily(Base):
    """Fama-French 5 factor + momentum daily returns from a configured factor provider."""

    __tablename__ = "factor_returns_daily"

    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_free_rate: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    mkt_rf: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    smb: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    hml: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    rmw: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    cma: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)
    mom: Mapped[Decimal] = mapped_column(FX_NUMERIC, nullable=False)


class DailyHolding(Base):
    __tablename__ = "daily_holdings"

    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    price_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    close_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    price_provenance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fx_rate_to_eur: Mapped[Decimal | None] = mapped_column(FX_NUMERIC, nullable=True)
    fx_provenance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_value_local: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    market_value_eur: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    valuation_status: Mapped[str] = mapped_column(String(32), nullable=False)


class DailyHoldingFlow(Base):
    """Cash that moved in or out of one holding on one day, in EUR (all positive magnitudes).

    With the daily market values in `daily_holdings`, this is what splits a period's investment
    result by stock: result = end value - start value - bought + sold + dividends.
    """

    __tablename__ = "daily_holding_flows"

    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    bought_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    sold_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    dividend_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )


class T212Export(Base):
    """One CSV report requested from Trading 212, and its raw body once downloaded.

    Raw-first, like every other source: the CSV is stored exactly as served before any row is
    parsed out of it, so a parser change can be re-run against history. The signed download
    link is never stored -- it is a short-lived credential for the file.
    """

    __tablename__ = "t212_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    time_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    time_to: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: Trading 212's own status (Queued, Processing, Running, Finished, Failed, Canceled), or
    #: "Downloaded" once the body is stored.
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    body: Mapped[str | None] = mapped_column(CompressedText(), nullable=True)


class T212ExportRow(Base):
    """A cash row parsed from an export: card payments, cashback, deposits, conversions.

    Keyed by Trading 212's own ID, which for card payments, cashback and deposits is the same
    `reference` the transactions API returns -- that is how an API "WITHDRAW" learns it was a
    card payment at a named merchant.
    """

    __tablename__ = "t212_export_rows"

    row_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    total: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_id: Mapped[int] = mapped_column(Integer, nullable=False)


class WatchlistItem(Base):
    """An instrument followed without owning it: priced, in the news, and alertable."""

    __tablename__ = "watchlist"

    t212_ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PriceAlert(Base):
    """"Tell me when...": a price level, or a gain or loss on the average cost, for one ticker."""

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    #: above / below (a price, in the instrument's currency) or gain_pct / loss_pct (percent
    #: on the average price paid).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    triggered_price: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)


class Notification(Base):
    """Something to tell the owner: a fired alert or the daily summary.

    The desktop tray shows each undelivered row once and marks it delivered. ``dedupe_key``
    makes creation idempotent: one summary per day, one notification per fired alert.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class CardBudget(Base):
    """The owner's monthly spending limit for one Trading 212 merchant category (EUR)."""

    __tablename__ = "card_budgets"

    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    monthly_limit: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DailyNav(Base):
    __tablename__ = "daily_nav"

    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    cash_balance_eur: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    securities_value_eur: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    nav_eur: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    external_flow_eur: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    internal_cash_flow_eur: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    # The investment-result side of the day, so a period can show where a gain came from.
    dividend_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    interest_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    fee_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    #: `external_flow_eur` split into money in and money out. A day with a deposit and a card
    #: payment nets to one figure; these keep both, so a period's gross deposits and
    #: withdrawals are real sums and card spending is always part of the withdrawals.
    deposit_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    withdrawal_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    #: The card-spending part of `external_flow_eur` (negative for spending, positive for a
    #: refund). Known only once a Trading 212 export has labelled the withdrawals.
    card_spending_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    #: Card cashback: income, not money the owner added, so it is never an external flow.
    cashback_eur: Mapped[Decimal] = mapped_column(
        MONEY_NUMERIC, nullable=False, default=Decimal("0"), server_default="0"
    )
    valuation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    missing_price_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_fx_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
