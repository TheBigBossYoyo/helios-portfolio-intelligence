from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from helios.config import Settings
from helios.db import create_engine, fetch_journal_mode, migrate_database
from helios.models import OrderHistory, PositionLive


@pytest.mark.asyncio
async def test_sqlite_wal_mode_is_enabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="test.sqlite3")
    await migrate_database(settings)
    engine = create_engine(settings)

    assert await fetch_journal_mode(engine) == "wal"
    async with engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0004_align_cash_transactions"

    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_stamps_matching_legacy_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="legacy.sqlite3")
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE raw_snapshots ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
                "endpoint VARCHAR(255) NOT NULL, "
                "ts DATETIME NOT NULL, "
                "http_status INTEGER NOT NULL, "
                "content_type VARCHAR(255), "
                "payload_json JSON NOT NULL"
                ")"
            )
        )
        await connection.execute(
            text("CREATE INDEX ix_raw_snapshots_endpoint ON raw_snapshots (endpoint)")
        )
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
    await engine.dispose()

    await migrate_database(settings)

    migrated_engine = create_engine(settings)
    async with migrated_engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0004_align_cash_transactions"
    await migrated_engine.dispose()


@pytest.mark.asyncio
async def test_decimal_and_utc_round_trip_for_m2_models(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="roundtrip.sqlite3")
    await migrate_database(settings)
    engine = create_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ts = datetime(2024, 4, 5, 6, 7, 8, tzinfo=UTC)

    async with session_factory() as session:
        session.add(
            PositionLive(
                ts=ts,
                t212_ticker="AAPL_US_EQ",
                quantity=Decimal("2.5000000000"),
                average_price_paid=Decimal("101.2300000000"),
                current_price=Decimal("110.4500000000"),
                wallet_current_value=Decimal("276.1250000000"),
            )
        )
        session.add(
            OrderHistory(
                fill_id="fill-1",
                order_id="order-1",
                fill_timestamp=ts,
                t212_ticker="AAPL_US_EQ",
                filled_quantity=Decimal("2.5000000000"),
                fill_price=Decimal("101.2300000000"),
                wallet_fx_rate=Decimal("0.9200000000"),
                wallet_taxes_json=[{"name": "Stamp", "amount": "0.50"}],
            )
        )
        await session.commit()

    async with session_factory() as session:
        stored_position = await session.get(PositionLive, (ts, "AAPL_US_EQ"))
        stored_order = await session.get(OrderHistory, "fill-1")

    assert stored_position is not None
    assert stored_position.ts == ts
    assert stored_position.ts.tzinfo == UTC
    assert stored_position.quantity == Decimal("2.5000000000")
    assert stored_position.wallet_current_value == Decimal("276.1250000000")
    assert stored_order is not None
    assert stored_order.fill_timestamp == ts
    assert stored_order.fill_timestamp is not None
    assert stored_order.fill_timestamp.tzinfo == UTC
    assert stored_order.wallet_fx_rate == Decimal("0.9200000000")
    assert stored_order.wallet_taxes_json == [{"name": "Stamp", "amount": "0.50"}]

    await engine.dispose()


@pytest.mark.asyncio
async def test_exact_large_decimal_round_trip_for_text_backed_storage(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="exact.sqlite3")
    await migrate_database(settings)
    engine = create_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ts = datetime(2024, 4, 5, 6, 7, 8, tzinfo=UTC)
    exact_value = Decimal("9007199254740993.1234567890")

    async with session_factory() as session:
        session.add(
            PositionLive(
                ts=ts,
                t212_ticker="TSLA_US_EQ",
                quantity=exact_value,
                average_price_paid=exact_value,
                current_price=exact_value,
            )
        )
        await session.commit()

    async with session_factory() as session:
        stored_position = await session.get(PositionLive, (ts, "TSLA_US_EQ"))

    assert stored_position is not None
    assert stored_position.quantity == exact_value
    assert stored_position.average_price_paid == exact_value
    assert stored_position.current_price == exact_value
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_0003_uses_text_for_financial_columns(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="schema.sqlite3")
    await migrate_database(settings)
    sync_engine = create_sync_engine(settings.sqlite_url.replace("+aiosqlite", ""))
    try:
        inspector = inspect(sync_engine)
        quantity_column = next(
            column
            for column in inspector.get_columns("positions_live")
            if column["name"] == "quantity"
        )
        replayed_column = next(
            column
            for column in inspector.get_columns("position_reconciliation")
            if column["name"] == "replayed_quantity"
        )
    finally:
        sync_engine.dispose()

    assert quantity_column["type"].__class__.__name__.upper() == "TEXT"
    assert replayed_column["type"].__class__.__name__.upper() == "TEXT"


@pytest.mark.asyncio
async def test_existing_0002_numeric_data_migrates_to_0003_text_without_loss(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="migrate_0003.sqlite3")
    settings.ensure_directories()
    config = _alembic_config(settings)
    command.upgrade(config, "0002_create_m2_foundation")

    sync_engine = create_sync_engine(settings.sqlite_url.replace("+aiosqlite", ""))
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO positions_live "
                    "(ts, t212_ticker, quantity, average_price_paid, current_price) "
                    "VALUES (:ts, :ticker, :quantity, :average_price, :current_price)"
                ),
                {
                    "ts": datetime(2024, 4, 5, 6, 7, 8, tzinfo=UTC),
                    "ticker": "AAPL_US_EQ",
                    "quantity": "2.5000000000",
                    "average_price": "101.2300000000",
                    "current_price": "110.4500000000",
                },
            )
    finally:
        sync_engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        stored_position = await session.scalar(
            select(PositionLive).where(PositionLive.t212_ticker == "AAPL_US_EQ")
        )

    assert stored_position is not None
    assert stored_position.quantity == Decimal("2.5000000000")
    assert stored_position.average_price_paid == Decimal("101.2300000000")
    assert stored_position.current_price == Decimal("110.4500000000")
    await engine.dispose()


@pytest.mark.asyncio
async def test_cash_transaction_table_matches_official_fields(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="transactions_schema.sqlite3")
    await migrate_database(settings)
    sync_engine = create_sync_engine(settings.sqlite_url.replace("+aiosqlite", ""))
    try:
        columns = {column["name"] for column in inspect(sync_engine).get_columns("transactions")}
        indexes = {index["name"] for index in inspect(sync_engine).get_indexes("transactions")}
    finally:
        sync_engine.dispose()

    assert columns == {"reference", "ts", "transaction_type", "currency_code", "amount"}
    assert "ix_transactions_t212_ticker" not in indexes


def _alembic_config(settings: Settings) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlite_url.replace("+aiosqlite", ""))
    return config
