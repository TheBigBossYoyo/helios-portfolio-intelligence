"""Offline replay of the Trading 212 ledger from stored `raw_snapshots` -- no network access.

Every response `Trading212Client._request` gets back is written to `raw_snapshots` before
anything is parsed out of it (see `raw_snapshots.py`), on the same "raw-first" principle as
`raw_news`: the bytes the broker actually served are the record of truth, so a parser bug or a
transform fix can be re-run against everything already fetched instead of losing it. Until this
module, nothing ever read that table back. This is that replay.

## Which endpoints are replayable, and why

Trading 212 responses fall into two shapes, and only one of them survives being replayed out of
order and out of date:

* **History endpoints** -- `/equity/history/orders`, `/equity/history/dividends`,
  `/equity/history/transactions` -- return *events*, each carrying a stable id the broker will
  never reuse (a fill id, a dividend reference, a transaction reference). Two pages covering the
  same fill, fetched hours or months apart, describe the same event and upsert onto the same
  primary key through `PortfolioRepository.ingest_domain_snapshot`'s `session.merge` calls. That
  makes them safe to replay **page by page, in any order**: a missing pagination cursor between
  two stored pages loses nothing, because whichever pages *were* stored still upsert onto their
  own stable keys. `/equity/metadata/instruments` behaves the same way -- it is a reference
  catalogue keyed by ticker, not a timestamped event, so replaying an old catalogue snapshot after
  a newer one is a harmless no-op (`_upsert_instrument_seeds` already coalesces on conflict).

* **Live-state endpoints** -- `/equity/positions`, and any future cash/account summary endpoint --
  describe a moment, not history. `positions_live` is keyed on `(ts, ticker)`, so replaying an old
  positions snapshot would not correct anything; it would just insert a second, stale-dated
  holding next to whatever a live sync last wrote. This module never replays them into a
  "current" table. They are skipped, counted, with the reason recorded.

Only a **2xx** snapshot is a candidate for replay; anything else (transient 5xx, a 401 from an
expired key, a 429) was never a valid response and is skipped, counted, with its status code.
Within a 2xx snapshot for a replayable endpoint, a payload the *current* parser still cannot
handle is counted as a **failure** (endpoint, snapshot id, error) and the run continues -- one bad
row must never abort replaying the rest.

## What this recovers

`raw_snapshots` remembers the exact bytes Trading 212 sent. If a parser bug ever dropped an item
(a `DomainTransformError` on a field that later turned out to be optional) or mis-parsed one, the
raw bytes were never lost -- only the derived row was wrong or missing. Fixing the parser and
running `helios t212-reparse` re-derives every ledger row from history using *today's* parser,
the same domain transforms and the same repository write path (`portfolio_transforms.py`,
`PortfolioRepository.ingest_domain_snapshot`) a live sync uses, so a fix is never a one-off SQL
patch against production data.

## Idempotence

Running this twice changes nothing the second time. Every order, dividend, transaction and
instrument row is upserted on its natural primary key (`fill_id`, `reference`, `reference`,
`t212_ticker`), so a row already present is left as-is rather than duplicated -- this module only
ever *merges* what a raw snapshot's current parse produces, and only for the rows a raw snapshot
actually reveals. Rows already in the ledger that no stored snapshot re-derives are left alone.

## What this does *not* do

* **Instrument resolution (OpenFIGI) never runs here.** Deciding a ticker's Yahoo mapping means an
  outbound HTTP call (`OpenFigiResolver.resolve`), and this module makes none. Instrument rows
  from a replayed `/equity/metadata/instruments` snapshot are still refreshed (name, ISIN,
  currency, and the rest), but every seed carries a `not_required` mapping result, which
  `_upsert_instrument_seeds` treats as "leave whatever mapping is already there alone" -- a
  previously resolved ticker keeps its mapping, and an unresolved one stays unresolved until a
  live `helios sync` (or a manual override) runs the real resolver.
* **Positions are never rewritten.** See "live-state endpoints" above.
* **Sync status bookkeeping for the replayed endpoints is left untouched.** `sync_status` rows for
  `/equity/history/orders` and friends describe the health of *live* fetches; a replay run is
  neither an attempt nor a success at fetching anything, so it does not touch them. It does take
  the same lease a live sync takes (below), which is bookkept separately under its own lease key.

## Reconciliation *is* recomputed, because it is offline-safe

Position reconciliation (`portfolio_sync.build_reconciliations`) is a pure function of a positions
snapshot and the order ledger -- it makes no network call. This module re-runs it after replay,
against the **most recently stored** `positions_live` snapshot (`PortfolioRepository
.get_latest_positions`) and the **replayed** order ledger, writing to the same
`(ts, ticker)`-keyed row a live sync would have written for that snapshot. That is what lets a
parser fix show up in reconciliation without waiting for another live sync: if the old parser
dropped a fill, reconciliation for the positions snapshot that fill applies to was wrong the whole
time the row was missing, and replaying the ledger now corrects it in place. When no positions
have ever been synced, this step is skipped with a note -- there is nothing to reconcile against.

## Concurrency

This reuses the *same* lease a live sync takes (`PortfolioRepository
.acquire_portfolio_sync_lease`), not a lease of its own. A live sync writes into the same tables
this replay writes into, so the two must never run at once; sharing the lease key is what makes
that true rather than merely documented. A reparse racing a live sync, or a second reparse racing
the first, is refused with `SyncAlreadyRunningError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .models import Dividend, OrderHistory, PositionReconciliation, Transaction
from .portfolio_repository import METADATA_ENDPOINT, PortfolioRepository
from .portfolio_sync import (
    DIVIDENDS_ENDPOINT,
    ORDERS_ENDPOINT,
    POSITIONS_ENDPOINT,
    TRANSACTIONS_ENDPOINT,
    build_reconciliations,
)
from .portfolio_transforms import (
    DomainTransformError,
    InstrumentSeed,
    dividend_from_dto,
    instrument_seed_from_metadata,
    is_executed,
    order_history_from_dto,
    transaction_from_dto,
)
from .rate_limit import Clock, SystemClock
from .raw_snapshots import RawSnapshotRepository
from .resolver import InstrumentMappingResult
from .schemas import (
    DividendItem,
    HistoricalOrderItem,
    HistoryPage,
    InstrumentMetadata,
    TransactionItem,
)

#: The mapping result every instrument seed built here carries. `_upsert_instrument_seeds`
#: treats `not_required` as "do not disturb an existing resolved mapping", which is exactly what
#: a network-free replay needs: it can refresh an instrument's descriptive fields without
#: pretending to have re-run OpenFIGI.
_SKIPPED_RESOLUTION = InstrumentMappingResult(
    status="not_required",
    source=None,
    yahoo_ticker=None,
    details={"reason": "t212_reparse_runs_offline_and_never_calls_openfigi"},
)

_metadata_adapter: TypeAdapter[list[InstrumentMetadata]] = TypeAdapter(list[InstrumentMetadata])
_orders_page_adapter: TypeAdapter[HistoryPage[HistoricalOrderItem]] = TypeAdapter(
    HistoryPage[HistoricalOrderItem]
)
_dividends_page_adapter: TypeAdapter[HistoryPage[DividendItem]] = TypeAdapter(
    HistoryPage[DividendItem]
)
_transactions_page_adapter: TypeAdapter[HistoryPage[TransactionItem]] = TypeAdapter(
    HistoryPage[TransactionItem]
)


@dataclass
class _EndpointAccumulator:
    replayed: int = 0
    failed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class T212ReparseEndpointSummary:
    endpoint: str
    replayed: int
    failed: int
    skipped: dict[str, int]


@dataclass(frozen=True)
class T212ReparseSummary:
    as_of: datetime
    snapshots_read: int
    endpoints: list[T212ReparseEndpointSummary]
    items_parsed: int
    rows_written: int
    duplicates_unchanged: int
    reconciliation_rows_written: int
    failures: list[str]
    notes: list[str]


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _classify_endpoint(raw_endpoint: str) -> tuple[str, str]:
    """(category, canonical label) for a stored `raw_snapshots.endpoint` value.

    A raw endpoint may be a bare path (`/equity/history/orders`), a path with a pagination query
    string (`/equity/history/orders?cursor=...`), or -- defensively, mirroring what
    `Trading212Client._resolve_request_url` accepts -- a full URL. `urlsplit` strips the query and
    any scheme/host uniformly, so all three compare equal for the same endpoint regardless of
    which page produced them.
    """
    path = urlsplit(raw_endpoint).path
    if path.startswith("/api/v0/"):
        path = path[len("/api/v0") :]
    if path == METADATA_ENDPOINT:
        return "metadata", METADATA_ENDPOINT
    if path == POSITIONS_ENDPOINT:
        return "positions", POSITIONS_ENDPOINT
    if path == ORDERS_ENDPOINT:
        return "orders", ORDERS_ENDPOINT
    if path == DIVIDENDS_ENDPOINT:
        return "dividends", DIVIDENDS_ENDPOINT
    if path == TRANSACTIONS_ENDPOINT:
        return "transactions", TRANSACTIONS_ENDPOINT
    return "unknown", path or raw_endpoint


class T212ReparseService:
    """Replays stored `raw_snapshots` rows through the *current* parsers -- no network access.

    See the module docstring for which endpoints are replayed, which are skipped as live-state,
    and why reconciliation (but not instrument resolution) is recomputed.
    """

    def __init__(
        self,
        repository: PortfolioRepository,
        snapshot_repository: RawSnapshotRepository,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._snapshot_repository = snapshot_repository
        self._settings = settings
        self._session_factory = session_factory
        self._clock = clock or SystemClock()

    async def reparse(self) -> T212ReparseSummary:
        now = self._clock.utcnow()
        lease = await self._repository.acquire_portfolio_sync_lease(
            acquired_at=now,
            lease_minutes=self._settings.sync_lease_minutes,
        )
        try:
            summary = await self._reparse_without_lease(now)
        except BaseException as exc:
            await self._repository.release_portfolio_sync_lease(
                lease=lease,
                completed_at=self._clock.utcnow(),
                succeeded=False,
                error_message=exc.__class__.__name__,
            )
            raise
        await self._repository.release_portfolio_sync_lease(
            lease=lease,
            completed_at=self._clock.utcnow(),
            succeeded=True,
            error_message=None,
        )
        return summary

    async def _reparse_without_lease(self, now: datetime) -> T212ReparseSummary:
        raw_rows = await self._snapshot_repository.list_snapshots()
        if not raw_rows:
            return T212ReparseSummary(
                as_of=now,
                snapshots_read=0,
                endpoints=[],
                items_parsed=0,
                rows_written=0,
                duplicates_unchanged=0,
                reconciliation_rows_written=0,
                failures=[],
                notes=["No raw_snapshots rows stored yet; run `helios sync` first."],
            )

        existing = await self._repository.load_replay_inputs()
        existing_order_ids = {order.fill_id for order in existing.orders}
        existing_dividend_refs = {dividend.reference for dividend in existing.dividends}
        existing_transaction_refs = {txn.reference for txn in existing.transactions}
        existing_instrument_tickers = {
            instrument.t212_ticker for instrument in existing.instruments
        }
        existing_orders_by_id = {order.fill_id: order for order in existing.orders}

        touched_orders: dict[str, OrderHistory] = {}
        touched_dividends: dict[str, Dividend] = {}
        touched_transactions: dict[str, Transaction] = {}
        touched_instruments: dict[str, InstrumentSeed] = {}

        accumulators: dict[str, _EndpointAccumulator] = {}
        failures: list[str] = []
        items_parsed = 0

        for raw in raw_rows:
            _category, endpoint = _classify_endpoint(raw.endpoint)
            accumulator = accumulators.setdefault(endpoint, _EndpointAccumulator())

            if not (200 <= raw.http_status < 300):
                _bump(accumulator.skipped, f"non-2xx response (status {raw.http_status})")
                continue
            if _category == "positions":
                _bump(
                    accumulator.skipped,
                    "live-state endpoint (current positions); not history, so it is not replayed",
                )
                continue
            if _category == "unknown":
                _bump(accumulator.skipped, "unsupported or unrecognised endpoint for replay")
                continue

            try:
                if _category == "metadata":
                    metadata_items = _metadata_adapter.validate_python(raw.payload_json)
                    items_parsed += len(metadata_items)
                    for metadata in metadata_items:
                        touched_instruments[metadata.ticker] = instrument_seed_from_metadata(
                            metadata,
                            mapping_result=_SKIPPED_RESOLUTION,
                            mapped_at=now,
                        )
                elif _category == "orders":
                    orders_page = _orders_page_adapter.validate_python(raw.payload_json)
                    items_parsed += len(orders_page.items)
                    for order_item in orders_page.items:
                        if not is_executed(order_item):
                            continue
                        order = order_history_from_dto(order_item)
                        touched_orders[order.fill_id] = order
                elif _category == "dividends":
                    dividends_page = _dividends_page_adapter.validate_python(raw.payload_json)
                    items_parsed += len(dividends_page.items)
                    for dividend_item in dividends_page.items:
                        dividend = dividend_from_dto(dividend_item)
                        touched_dividends[dividend.reference] = dividend
                else:
                    transactions_page = _transactions_page_adapter.validate_python(
                        raw.payload_json
                    )
                    items_parsed += len(transactions_page.items)
                    for transaction_item in transactions_page.items:
                        transaction = transaction_from_dto(transaction_item)
                        touched_transactions[transaction.reference] = transaction
            except (ValidationError, DomainTransformError) as error:
                failures.append(f"{endpoint}#{raw.id}: {error.__class__.__name__}")
                accumulator.failed += 1
                continue
            accumulator.replayed += 1

        notes: list[str] = []
        rows_written = 0
        duplicates_unchanged = 0
        for label, touched, already_present in (
            ("instruments", touched_instruments, existing_instrument_tickers),
            ("orders", touched_orders, existing_order_ids),
            ("dividends", touched_dividends, existing_dividend_refs),
            ("transactions", touched_transactions, existing_transaction_refs),
        ):
            if not touched:
                continue
            new_count = sum(1 for key in touched if key not in already_present)
            unchanged_count = len(touched) - new_count
            rows_written += new_count
            duplicates_unchanged += unchanged_count
            notes.append(f"{label}: {new_count} new row(s), {unchanged_count} already stored")

        all_orders_by_id = {**existing_orders_by_id, **touched_orders}
        positions = await self._repository.get_latest_positions()
        reconciliation_rows: list[PositionReconciliation] = []
        if positions:
            position_ts = positions[0].ts
            reconciliation_rows = build_reconciliations(
                synced_at=position_ts,
                positions=positions,
                orders=list(all_orders_by_id.values()),
                tolerance=self._settings.reconciliation_tolerance,
            )
            notes.append(
                "Reconciliation recomputed against the stored positions snapshot from "
                f"{position_ts.isoformat()}."
            )
        else:
            notes.append(
                "No stored positions snapshot; reconciliation was not recomputed. Run "
                "`helios sync` to populate current positions."
            )

        notes.append(
            "Instrument mapping (OpenFIGI) needs network access, which t212-reparse never uses; "
            "existing resolved mappings were left untouched. Run `helios sync` to resolve new "
            "or previously unresolved tickers."
        )

        if touched_orders or touched_dividends or touched_transactions or touched_instruments:
            async with self._session_factory() as session:
                async with session.begin():
                    await self._repository.ingest_domain_snapshot(
                        session,
                        instrument_seeds=list(touched_instruments.values()),
                        positions=[],
                        transactions=list(touched_transactions.values()),
                        orders=list(touched_orders.values()),
                        dividends=list(touched_dividends.values()),
                        sync_statuses=[],
                        reconciliations=reconciliation_rows,
                    )
        elif reconciliation_rows:
            async with self._session_factory() as session:
                async with session.begin():
                    await self._repository.ingest_domain_snapshot(
                        session,
                        instrument_seeds=[],
                        positions=[],
                        transactions=[],
                        orders=[],
                        dividends=[],
                        sync_statuses=[],
                        reconciliations=reconciliation_rows,
                    )

        endpoints = [
            T212ReparseEndpointSummary(
                endpoint=endpoint,
                replayed=accumulator.replayed,
                failed=accumulator.failed,
                skipped=dict(sorted(accumulator.skipped.items())),
            )
            for endpoint, accumulator in sorted(accumulators.items())
        ]

        return T212ReparseSummary(
            as_of=now,
            snapshots_read=len(raw_rows),
            endpoints=endpoints,
            items_parsed=items_parsed,
            rows_written=rows_written,
            duplicates_unchanged=duplicates_unchanged,
            reconciliation_rows_written=len(reconciliation_rows),
            failures=failures,
            notes=notes,
        )
