from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .models import Instrument, OrderHistory, PositionLive, PositionReconciliation, SyncStatus
from .portfolio_repository import (
    METADATA_ENDPOINT,
    PortfolioRepository,
)
from .portfolio_transforms import (
    DomainTransformError,
    InstrumentSeed,
    dividend_from_dto,
    instrument_seed_from_metadata,
    order_history_from_dto,
    position_live_from_dto,
    transaction_from_dto,
)
from .rate_limit import Clock, SystemClock
from .resolver import InstrumentMappingResult, InstrumentResolutionRequest, InstrumentResolver
from .schemas import (
    DividendItem,
    HistoricalOrderItem,
    InstrumentMetadata,
    PortfolioSyncSummary,
    Position,
    SyncEndpointSummary,
    TransactionItem,
)

type Fetcher[T] = Callable[[], Awaitable[T]]


class Trading212Reader(Protocol):
    async def get_instruments(self) -> list[InstrumentMetadata]: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_history_orders(self) -> list[HistoricalOrderItem]: ...

    async def get_history_dividends(self) -> list[DividendItem]: ...

    async def get_history_transactions(self) -> list[TransactionItem]: ...

POSITIONS_ENDPOINT = "/equity/positions"
ORDERS_ENDPOINT = "/equity/history/orders"
DIVIDENDS_ENDPOINT = "/equity/history/dividends"
TRANSACTIONS_ENDPOINT = "/equity/history/transactions"


@dataclass(frozen=True)
class ObservedInstrument:
    t212_ticker: str
    isin: str | None = None
    name: str | None = None
    currency_code: str | None = None
    instrument_type: str | None = None
    metadata: InstrumentMetadata | None = None


class PortfolioSyncService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        client: Trading212Reader,
        repository: PortfolioRepository,
        resolver: InstrumentResolver,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._client = client
        self._repository = repository
        self._resolver = resolver
        self._clock = clock or SystemClock()

    async def sync(self, *, force_metadata: bool = False) -> PortfolioSyncSummary:
        synced_at = self._clock.utcnow()
        lease = await self._repository.acquire_portfolio_sync_lease(
            acquired_at=synced_at,
            lease_minutes=self._settings.sync_lease_minutes,
        )
        try:
            summary = await self._sync_without_lease(
                force_metadata=force_metadata,
                synced_at=synced_at,
            )
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

    async def _sync_without_lease(
        self,
        *,
        force_metadata: bool,
        synced_at: datetime,
    ) -> PortfolioSyncSummary:
        metadata_freshness = await self._repository.get_metadata_freshness(
            checked_at=synced_at,
            ttl_hours=self._settings.instrument_metadata_ttl_hours,
        )
        should_fetch_metadata = force_metadata or not metadata_freshness.fresh

        instruments: list[InstrumentMetadata] = []
        endpoint_summaries: list[SyncEndpointSummary] = []
        if should_fetch_metadata:
            instruments = await self._fetch_endpoint(
                METADATA_ENDPOINT,
                self._client.get_instruments,
            )
            endpoint_summaries.append(
                SyncEndpointSummary(
                    endpoint=METADATA_ENDPOINT,
                    fetched=True,
                    itemCount=len(instruments),
                )
            )
        else:
            endpoint_summaries.append(
                SyncEndpointSummary(endpoint=METADATA_ENDPOINT, fetched=False, itemCount=None)
            )

        positions = await self._fetch_endpoint(POSITIONS_ENDPOINT, self._client.get_positions)
        endpoint_summaries.append(
            SyncEndpointSummary(endpoint=POSITIONS_ENDPOINT, fetched=True, itemCount=len(positions))
        )
        orders = await self._fetch_endpoint(ORDERS_ENDPOINT, self._client.get_history_orders)
        endpoint_summaries.append(
            SyncEndpointSummary(endpoint=ORDERS_ENDPOINT, fetched=True, itemCount=len(orders))
        )
        dividends = await self._fetch_endpoint(
            DIVIDENDS_ENDPOINT,
            self._client.get_history_dividends,
        )
        endpoint_summaries.append(
            SyncEndpointSummary(endpoint=DIVIDENDS_ENDPOINT, fetched=True, itemCount=len(dividends))
        )
        transactions = await self._fetch_endpoint(
            TRANSACTIONS_ENDPOINT,
            self._client.get_history_transactions,
        )
        endpoint_summaries.append(
            SyncEndpointSummary(
                endpoint=TRANSACTIONS_ENDPOINT,
                fetched=True,
                itemCount=len(transactions),
            )
        )

        observed = _collect_observed_instruments(positions, orders, dividends)
        instrument_seeds = await self._build_instrument_seeds(
            observed=observed,
            metadata_items=instruments,
            mapped_at=synced_at,
        )
        position_rows = await self._transform_items(
            endpoint=POSITIONS_ENDPOINT,
            items=positions,
            transform=lambda item: position_live_from_dto(item, synced_at=synced_at),
        )
        order_rows = await self._transform_items(
            endpoint=ORDERS_ENDPOINT,
            items=orders,
            transform=order_history_from_dto,
        )
        dividend_rows = await self._transform_items(
            endpoint=DIVIDENDS_ENDPOINT,
            items=dividends,
            transform=dividend_from_dto,
        )
        transaction_rows = await self._transform_items(
            endpoint=TRANSACTIONS_ENDPOINT,
            items=transactions,
            transform=transaction_from_dto,
        )
        reconciliation_rows = build_reconciliations(
            synced_at=synced_at,
            positions=position_rows,
            orders=order_rows,
            tolerance=self._settings.reconciliation_tolerance,
        )
        sync_statuses = [
            SyncStatus(
                endpoint=summary.endpoint,
                last_attempt_at=synced_at,
                last_success_at=synced_at,
                last_status="success",
                item_count=summary.item_count,
                last_error=None,
            )
            for summary in endpoint_summaries
            if summary.fetched
        ]

        async with self._session_factory() as session:
            async with session.begin():
                await self._repository.ingest_domain_snapshot(
                    session,
                    instrument_seeds=instrument_seeds,
                    positions=position_rows,
                    transactions=transaction_rows,
                    orders=order_rows,
                    dividends=dividend_rows,
                    sync_statuses=sync_statuses,
                    reconciliations=reconciliation_rows,
                )

        return PortfolioSyncSummary.model_validate(
            {
                "asOf": synced_at,
                "metadataFetched": should_fetch_metadata,
                "endpoints": [
                    summary.model_dump(mode="json", by_alias=True)
                    for summary in endpoint_summaries
                ],
            }
        )

    async def _fetch_endpoint[T](self, endpoint: str, fetcher: Fetcher[T]) -> T:
        try:
            payload = await fetcher()
        except Exception as exc:
            await self._repository.record_sync_failure(
                endpoint=endpoint,
                attempted_at=self._clock.utcnow(),
                error_message=exc.__class__.__name__,
            )
            raise
        return payload

    async def _resolve_instruments(
        self,
        observed: Iterable[ObservedInstrument],
        mapped_at: datetime,
    ) -> list[InstrumentSeed]:
        seeds: list[InstrumentSeed] = []
        for item in sorted(
            observed,
            key=lambda observed_instrument: observed_instrument.t212_ticker,
        ):
            mapping_result = await self._resolver.resolve(
                InstrumentResolutionRequest(
                    t212_ticker=item.t212_ticker,
                    isin=item.isin,
                    name=item.name,
                    currency_code=item.currency_code,
                )
            )
            metadata = item.metadata
            if metadata is not None:
                seeds.append(
                    instrument_seed_from_metadata(
                        metadata,
                        mapping_result=mapping_result,
                        mapped_at=mapped_at,
                    )
                )
                continue
            seeds.append(
                InstrumentSeed(
                    t212_ticker=item.t212_ticker,
                    isin=item.isin,
                    name=item.name,
                    short_name=None,
                    currency_code=item.currency_code,
                    instrument_type=item.instrument_type,
                    added_on=None,
                    extended_hours=None,
                    max_open_quantity=None,
                    working_schedule_id=None,
                    exchange_id=None,
                    mapping_result=mapping_result,
                    mapped_at=mapped_at,
                    observed=True,
                )
            )
        return seeds

    async def _build_instrument_seeds(
        self,
        *,
        observed: dict[str, ObservedInstrument],
        metadata_items: list[InstrumentMetadata],
        mapped_at: datetime,
    ) -> list[InstrumentSeed]:
        metadata_by_ticker = {item.ticker: item for item in metadata_items}
        cached_by_ticker = await self._repository.get_cached_instruments_by_tickers(set(observed))
        enriched_observed = {
            ticker: ObservedInstrument(
                t212_ticker=item.t212_ticker,
                isin=(
                    item.isin
                    or _metadata_field(metadata_by_ticker.get(ticker), "isin")
                    or _cached_field(cached_by_ticker.get(ticker), "isin")
                ),
                name=(
                    item.name
                    or _metadata_field(metadata_by_ticker.get(ticker), "name")
                    or _cached_field(cached_by_ticker.get(ticker), "name")
                ),
                currency_code=(
                    item.currency_code
                    or _metadata_field(metadata_by_ticker.get(ticker), "currency_code")
                    or _cached_field(cached_by_ticker.get(ticker), "currency_code")
                ),
                instrument_type=(
                    item.instrument_type
                    or _metadata_field(metadata_by_ticker.get(ticker), "type")
                    or _cached_field(cached_by_ticker.get(ticker), "instrument_type")
                ),
                metadata=metadata_by_ticker.get(ticker),
            )
            for ticker, item in observed.items()
        }
        observed_seeds = await self._resolve_instruments(enriched_observed.values(), mapped_at)
        observed_tickers = set(enriched_observed)
        unobserved_seeds = [
            instrument_seed_from_metadata(
                metadata,
                mapping_result=InstrumentMappingResult(
                    status="not_required",
                    source=None,
                    yahoo_ticker=None,
                    details={"reason": "unobserved_metadata"},
                ),
                mapped_at=mapped_at,
            )
            for metadata in metadata_items
            if metadata.ticker not in observed_tickers
        ]
        return [*observed_seeds, *unobserved_seeds]

    async def _transform_items[TInput, TOutput](
        self,
        *,
        endpoint: str,
        items: list[TInput],
        transform: Callable[[TInput], TOutput],
    ) -> list[TOutput]:
        try:
            return [transform(item) for item in items]
        except DomainTransformError as exc:
            await self._repository.record_sync_failure(
                endpoint=endpoint,
                attempted_at=self._clock.utcnow(),
                error_message=exc.__class__.__name__,
            )
            raise


def build_reconciliations(
    *,
    synced_at: datetime,
    positions: list[PositionLive],
    orders: list[OrderHistory],
    tolerance: Decimal,
) -> list[PositionReconciliation]:
    live_quantities: dict[str, Decimal] = {}
    for position in positions:
        if position.t212_ticker not in live_quantities:
            live_quantities[position.t212_ticker] = Decimal("0")
        live_quantities[position.t212_ticker] += position.quantity

    replayed_quantities: dict[str, Decimal] = {}
    unsupported_tickers: set[str] = set()
    for order in sorted(orders, key=lambda item: item.fill_id):
        ticker = order.t212_ticker
        if ticker is None:
            continue
        if order.fill_type != "TRADE":
            unsupported_tickers.add(ticker)
            continue
        if order.filled_quantity is None or order.side not in {"BUY", "SELL"}:
            unsupported_tickers.add(ticker)
            continue
        if ticker not in replayed_quantities:
            replayed_quantities[ticker] = Decimal("0")
        sign = Decimal("1") if order.side == "BUY" else Decimal("-1")
        replayed_quantities[ticker] += order.filled_quantity * sign

    rows: list[PositionReconciliation] = []
    for ticker in sorted(set(live_quantities) | set(replayed_quantities) | unsupported_tickers):
        live_quantity = live_quantities.get(ticker, Decimal("0"))
        replayed_quantity = replayed_quantities.get(ticker, Decimal("0"))
        difference = live_quantity - replayed_quantity
        if ticker in unsupported_tickers:
            status = "UNSUPPORTED_ACTION"
        elif abs(difference) <= tolerance:
            status = "MATCH"
        else:
            status = "MISMATCH"
        rows.append(
            PositionReconciliation(
                ts=synced_at,
                t212_ticker=ticker,
                replayed_quantity=replayed_quantity,
                live_quantity=live_quantity,
                difference_quantity=difference,
                tolerance_quantity=tolerance,
                status=status,
            )
        )
    return rows


def _collect_observed_instruments(
    positions: list[Position],
    orders: list[HistoricalOrderItem],
    dividends: list[DividendItem],
) -> dict[str, ObservedInstrument]:
    observed: dict[str, ObservedInstrument] = {}
    for position in positions:
        _merge_observation(
            observed,
            t212_ticker=position.instrument.ticker,
            isin=position.instrument.isin,
            name=position.instrument.name,
            currency_code=position.instrument.currency,
        )
    for order in orders:
        order_instrument = order.order.instrument
        ticker = order_instrument.ticker if order_instrument is not None else order.order.ticker
        if ticker is None:
            continue
        _merge_observation(
            observed,
            t212_ticker=ticker,
            isin=order_instrument.isin if order_instrument is not None else None,
            name=order_instrument.name if order_instrument is not None else None,
            currency_code=(
                order_instrument.currency if order_instrument is not None else order.order.currency
            ),
            instrument_type=None,
        )
    for dividend in dividends:
        dividend_instrument = dividend.instrument
        ticker = dividend_instrument.ticker if dividend_instrument is not None else dividend.ticker
        if ticker is None:
            continue
        _merge_observation(
            observed,
            t212_ticker=ticker,
            isin=dividend_instrument.isin if dividend_instrument is not None else None,
            name=dividend_instrument.name if dividend_instrument is not None else None,
            currency_code=(
                dividend_instrument.currency if dividend_instrument is not None else None
            ),
            instrument_type=None,
        )
    return observed


def _merge_observation(
    observed: dict[str, ObservedInstrument],
    *,
    t212_ticker: str,
    isin: str | None,
    name: str | None,
    currency_code: str | None,
    instrument_type: str | None = None,
) -> None:
    existing = observed.get(t212_ticker)
    if existing is None:
        observed[t212_ticker] = ObservedInstrument(
            t212_ticker=t212_ticker,
            isin=isin,
            name=name,
            currency_code=currency_code,
            instrument_type=instrument_type,
        )
        return
    observed[t212_ticker] = ObservedInstrument(
        t212_ticker=t212_ticker,
        isin=existing.isin or isin,
        name=existing.name or name,
        currency_code=existing.currency_code or currency_code,
        instrument_type=existing.instrument_type or instrument_type,
        metadata=existing.metadata,
    )


def _metadata_field(metadata: InstrumentMetadata | None, field_name: str) -> str | None:
    if metadata is None:
        return None
    value = getattr(metadata, field_name)
    if value is None:
        return None
    return str(value)


def _cached_field(instrument: Instrument | None, field_name: str) -> str | None:
    if instrument is None:
        return None
    value = getattr(instrument, field_name)
    if value is None:
        return None
    return str(value)
