from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InstrumentInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    isin: str | None = None
    name: str | None = None
    currency: str | None = None


class WalletImpact(BaseModel):
    model_config = ConfigDict(extra="allow")

    currency: str | None = None
    current_value: Decimal | None = Field(default=None, alias="currentValue")
    fx_impact: Decimal | None = Field(default=None, alias="fxImpact")
    total_cost: Decimal | None = Field(default=None, alias="totalCost")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="unrealizedProfitLoss")


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")

    average_price_paid: Decimal | None = Field(
        default=None,
        alias="averagePricePaid",
        description="Per-share average price in instrument currency.",
    )
    created_at: datetime | None = Field(default=None, alias="createdAt")
    current_price: Decimal | None = Field(
        default=None,
        alias="currentPrice",
        description="Per-share current price in instrument currency.",
    )
    instrument: InstrumentInfo
    quantity: Decimal
    quantity_available_for_trading: Decimal | None = Field(
        default=None,
        alias="quantityAvailableForTrading",
    )
    quantity_in_pies: Decimal | None = Field(default=None, alias="quantityInPies")
    wallet_impact: WalletImpact | None = Field(
        default=None,
        alias="walletImpact",
        description="Aggregated values in the account primary currency.",
    )


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    trading212_configured: bool = Field(alias="trading212Configured")
    sqlite_path: str = Field(alias="sqlitePath")


PositionsResponse = list[Position]
