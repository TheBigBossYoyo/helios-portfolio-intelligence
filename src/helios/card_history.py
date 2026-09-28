"""Card history from Trading 212's CSV export: merchants, categories and cashback.

The transactions API reports a 212 Card payment as a bare ``WITHDRAW`` (amount, date and an
opaque reference) and card cashback as a ``DEPOSIT``. That is enough to keep the cash balance
right, but it cannot say what the money was spent on, and it counts cashback as money the owner
added. Trading 212's CSV export labels both -- ``Card debit`` with merchant name and category,
``Spending cashback`` -- under the same IDs the API uses as references.

So Helios periodically asks for an export, stores the CSV raw (raw-first, as for every source),
and parses its cash rows. The replay uses them to tell card spending from bank withdrawals and to
count cashback as income; the card page summarises spending by month, category and merchant.

Requesting an export is the one POST Helios makes to Trading 212 (see ``client.EXPORTS_PATH``).
It creates a file and moves no money, but Trading 212 notifies the phone app each time, so it is
made at most once per ``card_export_cadence_hours`` and never in a loop.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from .config import Settings
from .models import T212Export, T212ExportRow
from .rate_limit import Clock, SystemClock
from .schemas import ExportReport

ZERO = Decimal("0")

#: Trading 212 report statuses after which a report will not change again.
TERMINAL_REPORT_STATUSES = frozenset({"Finished", "Failed", "Canceled"})
#: Helios' own status for an export whose CSV is stored.
DOWNLOADED = "Downloaded"
#: A report Trading 212 no longer lists after this long is treated as lost, so a new one can be
#: requested instead of waiting forever.
LOST_AFTER = timedelta(hours=6)
#: Each later export overlaps the previous one by this much, so nothing between them is missed.
EXPORT_OVERLAP = timedelta(days=7)

CardLabel = Literal["card", "cashback"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedExportRow:
    row_id: str
    action: str
    ts: datetime
    total: Decimal | None
    currency: str | None
    merchant_name: str | None
    merchant_category: str | None
    notes: str | None


class ExportFormatError(ValueError):
    pass


def parse_export_csv(text: str) -> list[ParsedExportRow]:
    """The cash rows of an export: anything with an ID that is not a trade.

    Trades and dividends already arrive, richer, from their own API endpoints; this keeps card
    payments, cashback, deposits, withdrawals, interest and currency conversions.
    """

    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if "Action" not in fields or "ID" not in fields:
        raise ExportFormatError("Not a Trading 212 export: no Action/ID columns")
    time_field = next((name for name in ("Time (UTC)", "Time") if name in fields), None)
    if time_field is None:
        raise ExportFormatError("Not a Trading 212 export: no time column")

    rows: list[ParsedExportRow] = []
    for raw in reader:
        row_id = (raw.get("ID") or "").strip()
        action = (raw.get("Action") or "").strip()
        if not row_id or not action or (raw.get("Ticker") or "").strip():
            continue
        ts = _parse_time(raw.get(time_field))
        if ts is None:
            continue
        rows.append(
            ParsedExportRow(
                row_id=row_id,
                action=action,
                ts=ts,
                total=_decimal(raw.get("Total")),
                currency=_text(raw.get("Currency (Total)")),
                merchant_name=_text(raw.get("Merchant name")),
                merchant_category=_text(raw.get("Merchant category")),
                notes=_text(raw.get("Notes")),
            )
        )
    return rows


def card_label(action: str) -> CardLabel | None:
    """What an export action means for the replay, if anything special."""

    lowered = action.strip().lower()
    if "cashback" in lowered:
        return "cashback"
    if lowered.startswith("card "):
        return "card"
    return None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def _text(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


# ---------------------------------------------------------------------------
# Refreshing
# ---------------------------------------------------------------------------


class ExportClient(Protocol):
    async def request_export(self, *, time_from: datetime, time_to: datetime) -> int: ...

    async def list_exports(self) -> list[ExportReport]: ...

    async def download_export(self, url: str) -> str: ...


def unlabelled_withdrawals(
    transactions: Sequence[TransactionLike],
    labelled_ids: set[str],
    conversion_legs: set[str],
) -> list[UnlabelledWithdrawal]:
    """Withdrawals no export has labelled yet (newest first), currency conversions excluded.

    Until the next daily export, a card payment is a bare WITHDRAW in the API. On this kind of
    account nearly every one is a card payment, so the card page lists them as "not labelled
    yet" rather than leaving the last day of spending out.
    """

    found = [
        UnlabelledWithdrawal(
            reference=item.reference,
            ts=item.ts,
            amount=item.amount,
            currency=item.currency_code,
        )
        for item in transactions
        if item.ts is not None
        and item.amount is not None
        and item.amount < 0
        and (item.transaction_type or "").upper() in {"WITHDRAW", "WITHDRAWAL"}
        and item.reference not in labelled_ids
        and item.reference not in conversion_legs
    ]
    return sorted(found, key=lambda entry: entry.ts, reverse=True)


class TransactionLike(Protocol):
    @property
    def reference(self) -> str: ...

    @property
    def ts(self) -> datetime | None: ...

    @property
    def transaction_type(self) -> str | None: ...

    @property
    def amount(self) -> Decimal | None: ...

    @property
    def currency_code(self) -> str | None: ...


class CardHistoryRepository(Protocol):
    async def latest_export(self) -> T212Export | None: ...

    async def latest_downloaded_export(self) -> T212Export | None: ...

    async def add_export(self, export: T212Export) -> int: ...

    async def update_export(self, export_id: int, **values: object) -> None: ...

    async def upsert_export_rows(self, rows: Sequence[T212ExportRow]) -> int: ...

    async def first_ledger_event_at(self) -> datetime | None: ...

    async def count_export_rows(self) -> tuple[int, int]: ...


RefreshAction = Literal["requested", "waiting", "downloaded", "up_to_date", "failed", "disabled"]


@dataclass(frozen=True)
class CardRefreshResult:
    action: RefreshAction
    detail: str
    rows_stored: int = 0


@dataclass(frozen=True)
class CardHistoryStatus:
    enabled: bool
    last_requested_at: datetime | None
    last_downloaded_at: datetime | None
    pending: bool
    last_status: str | None
    card_rows: int
    cash_rows: int


class CardHistoryService:
    def __init__(
        self,
        repository: CardHistoryRepository,
        client: ExportClient,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._settings = settings
        self._clock = clock or SystemClock()

    def enabled(self) -> bool:
        return self._settings.card_history_enabled and self._settings.t212_credentials() is not None

    async def status(self) -> CardHistoryStatus:
        latest = await self._repository.latest_export()
        downloaded = await self._repository.latest_downloaded_export()
        card_rows, cash_rows = await self._repository.count_export_rows()
        return CardHistoryStatus(
            enabled=self.enabled(),
            last_requested_at=latest.requested_at if latest else None,
            last_downloaded_at=downloaded.downloaded_at if downloaded else None,
            pending=latest is not None and _pending(latest),
            last_status=latest.status if latest else None,
            card_rows=card_rows,
            cash_rows=cash_rows,
        )

    async def refresh(self, *, force: bool = False) -> CardRefreshResult:
        """One step: collect a pending report, or request a new one when one is due.

        Called on a schedule, so each call does at most one thing and returns. ``force`` asks
        for a new report now instead of waiting for the cadence (never while one is pending).
        """

        if not self.enabled():
            return CardRefreshResult(
                "disabled", "Card history is off or Trading 212 is not set up."
            )
        now = self._clock.utcnow()
        latest = await self._repository.latest_export()
        if latest is not None and _pending(latest):
            return await self._collect(latest, now)

        cadence = timedelta(hours=self._settings.card_export_cadence_hours)
        if not force and latest is not None and now - latest.requested_at < cadence:
            return CardRefreshResult("up_to_date", "The last export is recent enough.")

        time_from = await self._window_start(now)
        report_id = await self._client.request_export(time_from=time_from, time_to=now)
        await self._repository.add_export(
            T212Export(
                report_id=report_id,
                requested_at=now,
                time_from=time_from,
                time_to=now,
                status="Queued",
            )
        )
        return CardRefreshResult(
            "requested", "Asked Trading 212 for a CSV export; it is collected on the next run."
        )

    async def _collect(self, export: T212Export, now: datetime) -> CardRefreshResult:
        reports = await self._client.list_exports()
        report = next((item for item in reports if item.report_id == export.report_id), None)
        if report is None:
            if now - export.requested_at > LOST_AFTER:
                await self._repository.update_export(export.id, status="Failed", checked_at=now)
                return CardRefreshResult("failed", "Trading 212 no longer lists the report.")
            await self._repository.update_export(export.id, checked_at=now)
            return CardRefreshResult("waiting", "Trading 212 has not listed the report yet.")
        if report.status in {"Failed", "Canceled"}:
            await self._repository.update_export(export.id, status=report.status, checked_at=now)
            return CardRefreshResult("failed", f"Trading 212 reported the export {report.status}.")
        if report.status != "Finished" or not report.download_link:
            await self._repository.update_export(export.id, status=report.status, checked_at=now)
            return CardRefreshResult("waiting", f"The export is {report.status.lower()}.")

        body = await self._client.download_export(report.download_link)
        parsed = parse_export_csv(body)
        await self._repository.update_export(
            export.id, status=DOWNLOADED, checked_at=now, downloaded_at=now, body=body
        )
        stored = await self._repository.upsert_export_rows(
            [
                T212ExportRow(
                    row_id=row.row_id,
                    action=row.action,
                    ts=row.ts,
                    total=row.total,
                    currency=row.currency,
                    merchant_name=row.merchant_name,
                    merchant_category=row.merchant_category,
                    notes=row.notes,
                    export_id=export.id,
                )
                for row in parsed
            ]
        )
        return CardRefreshResult(
            "downloaded", f"Stored {stored} cash rows from the export.", stored
        )

    async def _window_start(self, now: datetime) -> datetime:
        previous = await self._repository.latest_downloaded_export()
        if previous is not None:
            return previous.time_to - EXPORT_OVERLAP
        first = await self._repository.first_ledger_event_at()
        start = (first or now - timedelta(days=365)) - timedelta(days=1)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _pending(export: T212Export) -> bool:
    return export.status != DOWNLOADED and export.status not in {"Failed", "Canceled"}


# ---------------------------------------------------------------------------
# Summarising
# ---------------------------------------------------------------------------


class CardRow(Protocol):
    @property
    def row_id(self) -> str: ...

    @property
    def action(self) -> str: ...

    @property
    def ts(self) -> datetime: ...

    @property
    def total(self) -> Decimal | None: ...

    @property
    def currency(self) -> str | None: ...

    @property
    def merchant_name(self) -> str | None: ...

    @property
    def merchant_category(self) -> str | None: ...


@dataclass(frozen=True)
class CardTransaction:
    row_id: str
    ts: datetime
    action: str
    #: Negative for a payment, positive for a refund.
    amount: Decimal
    currency: str | None
    merchant_name: str | None
    merchant_category: str | None


@dataclass(frozen=True)
class UnlabelledWithdrawal:
    """Cash that left the account after the last export, not yet labelled as card or bank."""

    reference: str
    ts: datetime
    #: Negative, in the transaction's own currency.
    amount: Decimal
    currency: str | None


@dataclass(frozen=True)
class CashbackEntry:
    ts: datetime
    amount: Decimal


@dataclass(frozen=True)
class SpendingGroup:
    key: str
    #: Spent, as a positive number (refunds netted off).
    spent: Decimal
    count: int


@dataclass(frozen=True)
class SpendingMonth:
    key: str
    label: str
    spent: Decimal
    cashback: Decimal
    count: int


@dataclass(frozen=True)
class CardSummary:
    currency: str | None
    first_date: date | None
    last_date: date | None
    spent: Decimal
    refunded: Decimal
    cashback: Decimal
    #: cashback / spent, when anything was spent.
    cashback_rate: float | None
    months: list[SpendingMonth] = field(default_factory=list)
    categories: list[SpendingGroup] = field(default_factory=list)
    merchants: list[SpendingGroup] = field(default_factory=list)
    transactions: list[CardTransaction] = field(default_factory=list)
    cashback_entries: list[CashbackEntry] = field(default_factory=list)


def summarise_card_history(
    rows: Iterable[CardRow], *, max_transactions: int | None = None
) -> CardSummary:
    """All-time totals plus every payment and cashback entry.

    Every payment is returned (a personal card makes a few thousand at most) so the dashboard
    can group them by day, week or month in the reader's own time zone.
    """

    card: list[CardTransaction] = []
    cashback_entries: list[CashbackEntry] = []
    cashback_by_month: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cashback = ZERO
    currencies: Counter[str] = Counter()
    for row in rows:
        label = card_label(row.action)
        if label is None or row.total is None:
            continue
        if row.currency:
            currencies[row.currency] += 1
        if label == "cashback":
            cashback += row.total
            cashback_by_month[_month_key(row.ts)] += row.total
            cashback_entries.append(CashbackEntry(ts=row.ts, amount=row.total))
            continue
        card.append(
            CardTransaction(
                row_id=row.row_id,
                ts=row.ts,
                action=row.action,
                amount=row.total,
                currency=row.currency,
                merchant_name=row.merchant_name,
                merchant_category=row.merchant_category,
            )
        )

    card.sort(key=lambda item: item.ts, reverse=True)
    spent = sum((-item.amount for item in card if item.amount < 0), ZERO)
    refunded = sum((item.amount for item in card if item.amount > 0), ZERO)

    months: dict[str, list[CardTransaction]] = defaultdict(list)
    for item in card:
        months[_month_key(item.ts)].append(item)
    month_keys = sorted(set(months) | set(cashback_by_month))

    return CardSummary(
        currency=currencies.most_common(1)[0][0] if currencies else None,
        first_date=min((item.ts.date() for item in card), default=None),
        last_date=max((item.ts.date() for item in card), default=None),
        spent=spent,
        refunded=refunded,
        cashback=cashback,
        cashback_rate=float(cashback / spent) if spent > 0 else None,
        months=[
            SpendingMonth(
                key=key,
                label=_month_label(key),
                spent=-sum((item.amount for item in months.get(key, [])), ZERO),
                cashback=cashback_by_month.get(key, ZERO),
                count=len(months.get(key, [])),
            )
            for key in month_keys
        ],
        categories=_groups(card, lambda item: item.merchant_category or "UNCATEGORISED"),
        merchants=_groups(card, lambda item: item.merchant_name or "Unknown merchant"),
        transactions=card if max_transactions is None else card[:max_transactions],
        cashback_entries=sorted(cashback_entries, key=lambda entry: entry.ts, reverse=True),
    )


def _groups(
    card: Sequence[CardTransaction], key_of: Callable[[CardTransaction], str]
) -> list[SpendingGroup]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: Counter[str] = Counter()
    for item in card:
        key = key_of(item)
        totals[key] -= item.amount
        counts[key] += 1
    return sorted(
        (SpendingGroup(key=key, spent=total, count=counts[key]) for key, total in totals.items()),
        key=lambda group: (-group.spent, group.key),
    )


def _month_key(ts: datetime) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"
