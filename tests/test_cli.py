from __future__ import annotations

from helios.cli import render_positions
from helios.schemas import Position


def test_render_positions_uses_native_and_account_currency_fields() -> None:
    position = Position.model_validate(
        {
            "instrument": {"ticker": "AAPL_US_EQ", "currency": "USD"},
            "quantity": "1.5",
            "currentPrice": "225.10",
            "averagePricePaid": "180.00",
            "walletImpact": {"currency": "EUR", "currentValue": "310.25"},
        }
    )

    output = render_positions([position])

    assert "AAPL_US_EQ" in output
    assert "225.10" in output
    assert "180.00" in output
    assert "310.25" in output
