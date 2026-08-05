from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from helios.schemas import HistoricalOrderItem, HistoryPage, InstrumentMetadata, Position


def test_position_schema_is_permissive() -> None:
    payload = {
        "quantity": "2.5",
        "currentPrice": "10.15",
        "averagePricePaid": "9.10",
        "instrument": {
            "ticker": "AAPL_US_EQ",
            "currency": "USD",
            "unexpectedInstrumentField": "kept",
        },
        "walletImpact": {
            "currency": "EUR",
            "currentValue": "100.00",
            "unknownWalletField": 42,
        },
        "surpriseTopLevel": True,
    }

    position = Position.model_validate(payload)

    assert position.quantity == Decimal("2.5")
    assert position.instrument.model_extra == {"unexpectedInstrumentField": "kept"}
    assert position.wallet_impact is not None
    assert position.wallet_impact.model_extra == {"unknownWalletField": 42}
    assert position.model_extra == {"surpriseTopLevel": True}


def test_instrument_metadata_schema_parses_decimals_and_allows_unknown_fields() -> None:
    payload = {
        "addedOn": "2024-01-02T03:04:05Z",
        "currencyCode": "USD",
        "extendedHours": True,
        "isin": "US0378331005",
        "maxOpenQuantity": "1234.5678000000",
        "name": "Apple Inc.",
        "shortName": "Apple",
        "ticker": "AAPL_US_EQ",
        "type": "STOCK",
        "workingScheduleId": 42,
        "betaField": "kept",
    }

    instrument = InstrumentMetadata.model_validate(payload)

    assert instrument.added_on == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert instrument.max_open_quantity == Decimal("1234.5678000000")
    assert instrument.model_extra == {"betaField": "kept"}


def test_history_order_page_schema_is_permissive_and_nullable_next_path() -> None:
    payload = {
        "items": [
            {
                "fill": {
                    "id": "fill-1",
                    "price": "101.25",
                    "quantity": "2.5000",
                    "filledAt": "2024-03-01T10:11:12Z",
                    "type": "TRADE",
                    "walletImpact": {
                        "netValue": "253.1250",
                        "fxRate": "0.9200000000",
                        "realisedProfitLoss": "12.3400",
                        "taxes": [{"name": "STAMP_DUTY", "quantity": "0.50", "mystery": True}],
                        "futureField": "kept",
                    },
                },
                "order": {
                    "id": "order-1",
                    "filledValue": "253.1250",
                    "instrument": {"ticker": "AAPL_US_EQ", "currency": "USD"},
                    "side": "SELL",
                    "type": "MARKET",
                    "unknownOrderField": "kept",
                },
            }
        ],
        "nextPagePath": None,
        "topLevelExtra": 7,
    }

    page = HistoryPage[HistoricalOrderItem].model_validate(payload)

    assert page.next_page_path is None
    assert page.items[0].fill.quantity == Decimal("2.5000")
    assert page.items[0].fill.wallet_impact is not None
    assert page.items[0].fill.wallet_impact.net_value == Decimal("253.1250")
    assert page.items[0].fill.wallet_impact.taxes is not None
    assert page.items[0].fill.wallet_impact.taxes[0].quantity == Decimal("0.50")
    assert page.items[0].order.model_extra == {"unknownOrderField": "kept"}
    assert page.model_extra == {"topLevelExtra": 7}
