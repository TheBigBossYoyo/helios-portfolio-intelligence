"""create milestone 3 performance foundation tables

Revision ID: 0005_m3_performance_foundation
Revises: 0004_align_cash_transactions
Create Date: 2026-08-06 00:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_m3_performance_foundation"
down_revision = "0004_align_cash_transactions"
branch_labels = None
depends_on = None

TEXT_TYPE = sa.Text()


def upgrade() -> None:
    op.create_table(
        "fx_rates_daily",
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(length=16), nullable=False),
        sa.Column("eur_per_unit", TEXT_TYPE, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("rate_date", "currency_code", name="pk_fx_rates_daily"),
    )
    op.create_index("ix_fx_rates_daily_currency_code", "fx_rates_daily", ["currency_code"])

    op.create_table(
        "market_prices_daily",
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("provider_symbol", sa.String(length=64), nullable=False),
        sa.Column("currency_code", sa.String(length=16), nullable=False),
        sa.Column("close_price", TEXT_TYPE, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("price_date", "t212_ticker", name="pk_market_prices_daily"),
    )
    op.create_index(
        "ix_market_prices_daily_t212_ticker",
        "market_prices_daily",
        ["t212_ticker"],
    )

    op.create_table(
        "factor_returns_daily",
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("risk_free_rate", TEXT_TYPE, nullable=False),
        sa.Column("mkt_rf", TEXT_TYPE, nullable=False),
        sa.Column("smb", TEXT_TYPE, nullable=False),
        sa.Column("hml", TEXT_TYPE, nullable=False),
        sa.Column("rmw", TEXT_TYPE, nullable=False),
        sa.Column("cma", TEXT_TYPE, nullable=False),
        sa.Column("mom", TEXT_TYPE, nullable=False),
        sa.PrimaryKeyConstraint("as_of_date", name="pk_factor_returns_daily"),
    )

    op.create_table(
        "daily_holdings",
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("quantity", TEXT_TYPE, nullable=False),
        sa.Column("price_currency", sa.String(length=16), nullable=True),
        sa.Column("close_price", TEXT_TYPE, nullable=True),
        sa.Column("price_provenance", sa.String(length=32), nullable=True),
        sa.Column("fx_rate_to_eur", TEXT_TYPE, nullable=True),
        sa.Column("fx_provenance", sa.String(length=32), nullable=True),
        sa.Column("market_value_local", TEXT_TYPE, nullable=True),
        sa.Column("market_value_eur", TEXT_TYPE, nullable=True),
        sa.Column("valuation_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("as_of_date", "t212_ticker", name="pk_daily_holdings"),
    )
    op.create_index("ix_daily_holdings_t212_ticker", "daily_holdings", ["t212_ticker"])

    op.create_table(
        "daily_nav",
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cash_balance_eur", TEXT_TYPE, nullable=False),
        sa.Column("securities_value_eur", TEXT_TYPE, nullable=True),
        sa.Column("nav_eur", TEXT_TYPE, nullable=True),
        sa.Column("external_flow_eur", TEXT_TYPE, nullable=False),
        sa.Column("internal_cash_flow_eur", TEXT_TYPE, nullable=False),
        sa.Column("valuation_status", sa.String(length=32), nullable=False),
        sa.Column("missing_price_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_fx_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("as_of_date", name="pk_daily_nav"),
    )


def downgrade() -> None:
    op.drop_table("daily_nav")
    op.drop_table("factor_returns_daily")
    op.drop_index("ix_daily_holdings_t212_ticker", table_name="daily_holdings")
    op.drop_table("daily_holdings")
    op.drop_index("ix_market_prices_daily_t212_ticker", table_name="market_prices_daily")
    op.drop_table("market_prices_daily")
    op.drop_index("ix_fx_rates_daily_currency_code", table_name="fx_rates_daily")
    op.drop_table("fx_rates_daily")
