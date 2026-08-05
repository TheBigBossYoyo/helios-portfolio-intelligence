from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
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
    assert revision == "0002_create_m2_foundation"

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
    assert revision == "0002_create_m2_foundation"
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
