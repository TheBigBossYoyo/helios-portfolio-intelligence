from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import Dividend, Instrument, OrderHistory, PositionLive, Transaction
from .resolver import InstrumentMappingResult
from .schemas import (
    DividendItem,
    HistoricalOrderItem,
    InstrumentMetadata,
    Position,
    TransactionItem,
)


class DomainTransformError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentSeed:
    t212_ticker: str
    isin: str | None
    name: str | None
    short_name: str | None
    currency_code: str | None
    instrument_type: str | None
    added_on: datetime | None
    extended_hours: bool | None
    max_open_quantity: Decimal | None
    working_schedule_id: int | None
    exchange_id: str | None
    mapping_result: InstrumentMappingResult
    mapped_at: datetime
    observed: bool


def instrument_seed_from_metadata(
    dto: InstrumentMetadata,
    *,
    mapping_result: InstrumentMappingResult,
    mapped_at: datetime,
) -> InstrumentSeed:
    return InstrumentSeed(
        t212_ticker=dto.ticker,
        isin=dto.isin,
        name=dto.name,
        short_name=dto.short_name,
        currency_code=dto.currency_code,
        instrument_type=dto.type,
        added_on=dto.added_on,
        extended_hours=dto.extended_hours,
        max_open_quantity=dto.max_open_quantity,
        working_schedule_id=dto.working_schedule_id,
        exchange_id=_extra_string(dto, "exchangeId"),
        mapping_result=mapping_result,
        mapped_at=mapped_at,
        observed=(mapping_result.status != "not_required"),
    )


def position_live_from_dto(dto: Position, *, synced_at: datetime) -> PositionLive:
    return PositionLive(
        ts=synced_at,
        t212_ticker=dto.instrument.ticker,
        isin=dto.instrument.isin,
        instrument_name=dto.instrument.name,
        instrument_currency=dto.instrument.currency,
        quantity=dto.quantity,
        quantity_available_for_trading=dto.quantity_available_for_trading,
        quantity_in_pies=dto.quantity_in_pies,
        average_price_paid=dto.average_price_paid,
        current_price=dto.current_price,
        position_created_at=dto.created_at,
        wallet_currency=dto.wallet_impact.currency if dto.wallet_impact is not None else None,
        wallet_current_value=(
            dto.wallet_impact.current_value if dto.wallet_impact is not None else None
        ),
        wallet_fx_impact=dto.wallet_impact.fx_impact if dto.wallet_impact is not None else None,
        wallet_total_cost=dto.wallet_impact.total_cost if dto.wallet_impact is not None else None,
        wallet_unrealized_profit_loss=(
            dto.wallet_impact.unrealized_profit_loss if dto.wallet_impact is not None else None
        ),
    )


def transaction_from_dto(dto: TransactionItem) -> Transaction:
    if not dto.reference:
        raise DomainTransformError("Transaction is missing a stable reference")
    return Transaction(
        reference=str(dto.reference),
        ts=dto.date_time,
        t212_ticker=None,
        isin=None,
        transaction_type=dto.type,
        currency_code=dto.currency,
        amount=dto.amount,
    )


def order_history_from_dto(dto: HistoricalOrderItem) -> OrderHistory:
    if dto.fill.id is None:
        raise DomainTransformError("Order fill is missing a stable id")
    order = dto.order
    instrument = order.instrument
    wallet_impact = dto.fill.wallet_impact
    return OrderHistory(
        fill_id=str(dto.fill.id),
        order_id=str(order.id) if order.id is not None else None,
        fill_timestamp=dto.fill.filled_at,
        t212_ticker=(instrument.ticker if instrument is not None else order.ticker),
        isin=instrument.isin if instrument is not None else None,
        instrument_name=instrument.name if instrument is not None else None,
        instrument_currency_code=instrument.currency if instrument is not None else order.currency,
        side=order.side,
        order_type=order.type,
        fill_type=dto.fill.type,
        order_quantity=order.quantity,
        filled_quantity=dto.fill.quantity,
        fill_price=dto.fill.price,
        order_filled_value=order.filled_value,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        wallet_currency=wallet_impact.currency if wallet_impact is not None else None,
        wallet_net_value=wallet_impact.net_value if wallet_impact is not None else None,
        wallet_fx_rate=wallet_impact.fx_rate if wallet_impact is not None else None,
        wallet_realised_profit_loss=(
            wallet_impact.realised_profit_loss if wallet_impact is not None else None
        ),
        wallet_taxes_json=(
            [tax.model_dump(mode="json", exclude_none=True) for tax in wallet_impact.taxes]
            if wallet_impact is not None and wallet_impact.taxes is not None
            else None
        ),
    )


def dividend_from_dto(dto: DividendItem) -> Dividend:
    if not dto.reference:
        raise DomainTransformError("Dividend is missing a stable reference")
    instrument = dto.instrument
    return Dividend(
        reference=str(dto.reference),
        paid_on=dto.paid_on,
        t212_ticker=instrument.ticker if instrument is not None else dto.ticker,
        isin=instrument.isin if instrument is not None else None,
        dividend_type=dto.type,
        currency_code=dto.currency,
        ticker_currency=dto.ticker_currency,
        quantity=dto.quantity,
        amount=dto.amount,
        amount_in_euro=dto.amount_in_euro,
        gross_amount_per_share=dto.gross_amount_per_share,
    )


def apply_instrument_seed(target: Instrument, seed: InstrumentSeed) -> None:
    target.t212_ticker = seed.t212_ticker
    target.isin = seed.isin or target.isin
    target.name = seed.name or target.name
    target.short_name = seed.short_name or target.short_name
    target.currency_code = seed.currency_code or target.currency_code
    target.instrument_type = seed.instrument_type or target.instrument_type
    target.added_on = seed.added_on or target.added_on
    target.extended_hours = (
        seed.extended_hours if seed.extended_hours is not None else target.extended_hours
    )
    target.max_open_quantity = (
        seed.max_open_quantity if seed.max_open_quantity is not None else target.max_open_quantity
    )
    target.working_schedule_id = (
        seed.working_schedule_id
        if seed.working_schedule_id is not None
        else target.working_schedule_id
    )
    target.exchange_id = seed.exchange_id or target.exchange_id
    if _should_preserve_existing_mapping(target, seed.mapping_result):
        return
    target.yahoo_ticker = seed.mapping_result.yahoo_ticker
    target.mapping_status = seed.mapping_result.status
    target.mapping_source = seed.mapping_result.source
    target.mapping_details_json = seed.mapping_result.details
    target.mapped_at = seed.mapped_at


def _should_preserve_existing_mapping(
    target: Instrument,
    mapping_result: InstrumentMappingResult,
) -> bool:
    if target.mapping_status != "resolved" or target.yahoo_ticker is None:
        return False
    return mapping_result.status != "resolved"


def _extra_string(dto: InstrumentMetadata, field_name: str) -> str | None:
    if dto.model_extra is None:
        return None
    value = dto.model_extra.get(field_name)
    if value is None:
        return None
    return str(value)
