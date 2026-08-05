from __future__ import annotations

from decimal import Decimal

from helios.schemas import Position


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
