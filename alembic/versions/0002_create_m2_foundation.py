"""create milestone 2 foundation tables

Revision ID: 0002_create_m2_foundation
Revises: 0001_create_raw_snapshots
Create Date: 2026-08-05 00:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_create_m2_foundation"
down_revision = "0001_create_raw_snapshots"
branch_labels = None
depends_on = None

QUANTITY_NUMERIC = sa.Numeric(28, 10, asdecimal=True)
MONEY_NUMERIC = sa.Numeric(28, 10, asdecimal=True)
FX_NUMERIC = sa.Numeric(28, 10, asdecimal=True)


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("short_name", sa.String(length=255), nullable=True),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("instrument_type", sa.String(length=64), nullable=True),
        sa.Column("added_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extended_hours", sa.Boolean(), nullable=True),
        sa.Column("max_open_quantity", QUANTITY_NUMERIC, nullable=True),
        sa.Column("working_schedule_id", sa.Integer(), nullable=True),
        sa.Column("exchange_id", sa.String(length=64), nullable=True),
        sa.Column("yahoo_ticker", sa.String(length=64), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column(
            "mapping_status",
            sa.String(length=32),
            nullable=False,
            server_default="unresolved",
        ),
        sa.Column("mapping_source", sa.String(length=64), nullable=True),
        sa.Column("mapping_details_json", sa.JSON(), nullable=True),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("t212_ticker", name="pk_instruments"),
    )
    op.create_index("ix_instruments_isin", "instruments", ["isin"])

    op.create_table(
        "positions_live",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("instrument_name", sa.String(length=255), nullable=True),
        sa.Column("instrument_currency", sa.String(length=16), nullable=True),
        sa.Column("quantity", QUANTITY_NUMERIC, nullable=False),
        sa.Column("quantity_available_for_trading", QUANTITY_NUMERIC, nullable=True),
        sa.Column("quantity_in_pies", QUANTITY_NUMERIC, nullable=True),
        sa.Column("average_price_paid", MONEY_NUMERIC, nullable=True),
        sa.Column("current_price", MONEY_NUMERIC, nullable=True),
        sa.Column("position_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wallet_currency", sa.String(length=16), nullable=True),
        sa.Column("wallet_current_value", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_fx_impact", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_total_cost", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_unrealized_profit_loss", MONEY_NUMERIC, nullable=True),
        sa.PrimaryKeyConstraint("ts", "t212_ticker", name="pk_positions_live"),
    )
    op.create_index("ix_positions_live_t212_ticker", "positions_live", ["t212_ticker"])
    op.create_index("ix_positions_live_isin", "positions_live", ["isin"])

    op.create_table(
        "transactions",
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("transaction_type", sa.String(length=64), nullable=True),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("amount", MONEY_NUMERIC, nullable=True),
        sa.PrimaryKeyConstraint("reference", name="pk_transactions"),
    )
    op.create_index("ix_transactions_ts", "transactions", ["ts"])
    op.create_index("ix_transactions_t212_ticker", "transactions", ["t212_ticker"])

    op.create_table(
        "orders_history",
        sa.Column("fill_id", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=True),
        sa.Column("fill_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("instrument_name", sa.String(length=255), nullable=True),
        sa.Column("instrument_currency_code", sa.String(length=16), nullable=True),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("order_type", sa.String(length=64), nullable=True),
        sa.Column("fill_type", sa.String(length=64), nullable=True),
        sa.Column("order_quantity", QUANTITY_NUMERIC, nullable=True),
        sa.Column("filled_quantity", QUANTITY_NUMERIC, nullable=True),
        sa.Column("fill_price", MONEY_NUMERIC, nullable=True),
        sa.Column("order_filled_value", MONEY_NUMERIC, nullable=True),
        sa.Column("limit_price", MONEY_NUMERIC, nullable=True),
        sa.Column("stop_price", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_currency", sa.String(length=16), nullable=True),
        sa.Column("wallet_net_value", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_fx_rate", FX_NUMERIC, nullable=True),
        sa.Column("wallet_realised_profit_loss", MONEY_NUMERIC, nullable=True),
        sa.Column("wallet_taxes_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("fill_id", name="pk_orders_history"),
    )
    op.create_index("ix_orders_history_order_id", "orders_history", ["order_id"])
    op.create_index("ix_orders_history_fill_timestamp", "orders_history", ["fill_timestamp"])
    op.create_index("ix_orders_history_t212_ticker", "orders_history", ["t212_ticker"])

    op.create_table(
        "dividends",
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("paid_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("dividend_type", sa.String(length=64), nullable=True),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("ticker_currency", sa.String(length=16), nullable=True),
        sa.Column("quantity", QUANTITY_NUMERIC, nullable=True),
        sa.Column("amount", MONEY_NUMERIC, nullable=True),
        sa.Column("amount_in_euro", MONEY_NUMERIC, nullable=True),
        sa.Column("gross_amount_per_share", MONEY_NUMERIC, nullable=True),
        sa.PrimaryKeyConstraint("reference", name="pk_dividends"),
    )
    op.create_index("ix_dividends_paid_on", "dividends", ["paid_on"])
    op.create_index("ix_dividends_t212_ticker", "dividends", ["t212_ticker"])

    op.create_table(
        "sync_status",
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("endpoint", name="pk_sync_status"),
    )

    op.create_table(
        "position_reconciliation",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("replayed_quantity", QUANTITY_NUMERIC, nullable=False),
        sa.Column("live_quantity", QUANTITY_NUMERIC, nullable=False),
        sa.Column("difference_quantity", QUANTITY_NUMERIC, nullable=False),
        sa.Column("tolerance_quantity", QUANTITY_NUMERIC, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("ts", "t212_ticker", name="pk_position_reconciliation"),
    )
    op.create_index(
        "ix_position_reconciliation_t212_ticker",
        "position_reconciliation",
        ["t212_ticker"],
    )


def downgrade() -> None:
    op.drop_index("ix_position_reconciliation_t212_ticker", table_name="position_reconciliation")
    op.drop_table("position_reconciliation")
    op.drop_table("sync_status")
    op.drop_index("ix_dividends_t212_ticker", table_name="dividends")
    op.drop_index("ix_dividends_paid_on", table_name="dividends")
    op.drop_table("dividends")
    op.drop_index("ix_orders_history_t212_ticker", table_name="orders_history")
    op.drop_index("ix_orders_history_fill_timestamp", table_name="orders_history")
    op.drop_index("ix_orders_history_order_id", table_name="orders_history")
    op.drop_table("orders_history")
    op.drop_index("ix_transactions_t212_ticker", table_name="transactions")
    op.drop_index("ix_transactions_ts", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_positions_live_isin", table_name="positions_live")
    op.drop_index("ix_positions_live_t212_ticker", table_name="positions_live")
    op.drop_table("positions_live")
    op.drop_index("ix_instruments_isin", table_name="instruments")
    op.drop_table("instruments")
