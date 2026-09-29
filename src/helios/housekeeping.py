"""Things Helios does for you in the background: daily backups and budget heads-ups.

**Backups.** The database is the only copy of everything Helios knows that Trading 212 does not:
card exports, budgets, alerts, the watchlist, theses and journal notes. The worker makes a
verified copy through SQLite's online backup API (see ``backup.py``) once a day, keeps the last
``backup_keep``, and can write them to any folder -- a synced one (OneDrive, Dropbox) makes them
off-machine too.

**Budget heads-ups.** Once a category's card spending is on pace to pass its monthly budget, one
notification says so; once it has passed, one more says by how much. Each fires at most once per
category per month.
"""

from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .backup import BackupError, backup_database, backup_filename_pattern
from .card_history import card_label
from .config import Settings
from .models import Notification
from .portfolio_repository import PortfolioRepository
from .rate_limit import Clock, SystemClock

#: A budget heads-up waits until this many days into the month: day-one pace is noise.
PACE_MIN_DAY = 5


@dataclass(frozen=True)
class BackupStatus:
    enabled: bool
    directory: Path
    last_path: Path | None
    last_at: datetime | None
    last_size_bytes: int | None
    count: int


def backup_directory(settings: Settings) -> Path:
    return settings.backup_dir or settings.data_dir / "backups"


def backup_status(settings: Settings) -> BackupStatus:
    directory = backup_directory(settings)
    pattern = backup_filename_pattern(settings.sqlite_path.stem)
    files = (
        sorted(
            (path for path in directory.iterdir() if pattern.match(path.name)),
            key=lambda path: path.stat().st_mtime,
        )
        if directory.is_dir()
        else []
    )
    last = files[-1] if files else None
    return BackupStatus(
        enabled=settings.backup_enabled,
        directory=directory,
        last_path=last,
        last_at=datetime.fromtimestamp(last.stat().st_mtime, tz=UTC) if last else None,
        last_size_bytes=last.stat().st_size if last else None,
        count=len(files),
    )


class BackupService:
    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()

    def status(self) -> BackupStatus:
        return backup_status(self._settings)

    async def run(self) -> BackupStatus:
        """Back up now (in a thread: SQLite's backup API blocks)."""

        await asyncio.to_thread(
            backup_database,
            self._settings.sqlite_path,
            dest_dir=backup_directory(self._settings),
            keep=self._settings.backup_keep,
            now=self._clock.utcnow(),
        )
        return self.status()

    async def run_if_due(self) -> BackupStatus | None:
        if not self._settings.backup_enabled:
            return None
        status = self.status()
        due = status.last_at is None or self._clock.utcnow() - status.last_at >= timedelta(
            hours=self._settings.backup_interval_hours
        )
        return await self.run() if due else None


class BudgetNotifier:
    def __init__(self, repository: PortfolioRepository, clock: Clock | None = None) -> None:
        self._repository = repository
        self._clock = clock or SystemClock()

    async def check(self, *, now_local: datetime | None = None) -> list[Notification]:
        budgets = await self._repository.list_card_budgets()
        if not budgets:
            return []
        local = now_local or self._clock.utcnow().astimezone()
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_utc = month_start.astimezone(UTC)
        spent: dict[str, Decimal] = {}
        for row in await self._repository.list_export_rows():
            if card_label(row.action) != "card" or row.total is None or row.ts < start_utc:
                continue
            key = row.merchant_category or "UNCATEGORISED"
            spent[key] = spent.get(key, Decimal(0)) - row.total
        days_in_month = calendar.monthrange(local.year, local.month)[1]
        month_key = f"{local.year:04d}-{local.month:02d}"
        created: list[Notification] = []
        for budget in budgets:
            used = spent.get(budget.category, Decimal(0))
            limit = budget.monthly_limit
            label = budget.category.replace("_", " ").capitalize()
            if limit <= 0:
                continue
            if used > limit:
                notification = Notification(
                    kind="budget",
                    title=f"{label}: over budget",
                    body=f"€{used:,.2f} spent this month against a €{limit:,.2f} budget "
                    f"(€{used - limit:,.2f} over).",
                    url="/card",
                    created_at=self._clock.utcnow(),
                    dedupe_key=f"budget:{month_key}:{budget.category}:over",
                )
            elif local.day >= PACE_MIN_DAY:
                projected = used / local.day * days_in_month
                if projected <= limit:
                    continue
                notification = Notification(
                    kind="budget",
                    title=f"{label}: heading over budget",
                    body=f"€{used:,.2f} spent so far this month; at this pace about "
                    f"€{projected:,.2f} by month end, against a €{limit:,.2f} budget.",
                    url="/card",
                    created_at=self._clock.utcnow(),
                    dedupe_key=f"budget:{month_key}:{budget.category}:pace",
                )
            else:
                continue
            if await self._repository.add_notification(notification):
                created.append(notification)
        return created


__all__ = ["BackupError", "BackupService", "BudgetNotifier", "backup_status"]
