"""Daily backups and budget heads-ups."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.housekeeping import BackupService, BudgetNotifier
from helios.models import T212ExportRow
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock

D = Decimal


class MovableClock(Clock):
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        return None


async def _repository(settings: Settings) -> PortfolioRepository:
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(factory)


@pytest.mark.asyncio
async def test_backups_run_once_a_day_into_the_chosen_folder(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, sqlite_filename="house.sqlite3", backup_dir=tmp_path / "synced"
    )
    await _repository(settings)
    clock = MovableClock(datetime.now(UTC))
    service = BackupService(settings, clock)

    first = await service.run_if_due()
    second = await service.run_if_due()
    clock.current += timedelta(hours=settings.backup_interval_hours + 1)
    third = await service.run_if_due()

    assert first is not None and first.count == 1
    assert first.directory == tmp_path / "synced"
    assert first.last_path is not None and first.last_path.exists()
    assert second is None
    assert third is not None and third.count == 2


@pytest.mark.asyncio
async def test_switched_off_backups_never_run(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="off.sqlite3", backup_enabled=False)
    await _repository(settings)

    assert await BackupService(settings).run_if_due() is None
    assert BackupService(settings).status().count == 0


def _card(row_id: str, ts: datetime, total: str, category: str) -> T212ExportRow:
    return T212ExportRow(
        row_id=row_id,
        action="Card debit",
        ts=ts,
        total=D(total),
        currency="EUR",
        merchant_name="Shop",
        merchant_category=category,
        export_id=1,
    )


@pytest.mark.asyncio
async def test_budget_heads_up_then_over_budget_once_each(tmp_path: Path) -> None:
    repository = await _repository(Settings(data_dir=tmp_path, sqlite_filename="budget.sqlite3"))
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    await repository.set_card_budget("MEMBERSHIPS", D("90"), now=now)
    await repository.set_card_budget("UTILITIES", D("100"), now=now)
    await repository.upsert_export_rows(
        [
            _card("a", datetime(2026, 9, 2, tzinfo=UTC), "-40", "MEMBERSHIPS"),
            _card("b", datetime(2026, 8, 30, tzinfo=UTC), "-500", "MEMBERSHIPS"),  # last month
            _card("c", datetime(2026, 9, 3, tzinfo=UTC), "-10", "UTILITIES"),
        ]
    )
    notifier = BudgetNotifier(repository, MovableClock(now))
    tenth = datetime(2026, 9, 10, 12, tzinfo=UTC).astimezone()

    first = await notifier.check(now_local=tenth)
    again = await notifier.check(now_local=tenth)

    # 40 in 10 days of a 30-day month is on pace for 120 against 90; utilities is fine.
    assert [item.title for item in first] == ["Memberships: heading over budget"]
    assert "about €120.00 by month end" in first[0].body
    assert again == []

    await repository.upsert_export_rows(
        [_card("d", datetime(2026, 9, 12, tzinfo=UTC), "-60", "MEMBERSHIPS")]
    )
    over = await notifier.check(now_local=datetime(2026, 9, 12, 12, tzinfo=UTC).astimezone())

    assert [item.title for item in over] == ["Memberships: over budget"]
    assert "(€10.00 over)" in over[0].body


@pytest.mark.asyncio
async def test_no_pace_warning_in_the_first_days_of_a_month(tmp_path: Path) -> None:
    repository = await _repository(Settings(data_dir=tmp_path, sqlite_filename="early.sqlite3"))
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    await repository.set_card_budget("MEMBERSHIPS", D("90"), now=now)
    await repository.upsert_export_rows(
        [_card("a", datetime(2026, 9, 1, 12, tzinfo=UTC), "-20", "MEMBERSHIPS")]
    )

    assert await BudgetNotifier(repository, MovableClock(now)).check(now_local=now) == []


def test_a_blank_backup_folder_means_the_default(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, backup_dir="")

    assert settings.backup_dir is None
    assert BackupService(settings).status().directory == tmp_path / "backups"
