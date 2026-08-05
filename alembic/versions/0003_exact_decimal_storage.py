"""convert financial numeric columns to exact decimal text storage

Revision ID: 0003_exact_decimal_storage
Revises: 0002_create_m2_foundation
Create Date: 2026-08-05 23:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_exact_decimal_storage"
down_revision = "0002_create_m2_foundation"
branch_labels = None
depends_on = None

NUMERIC_28_10 = sa.Numeric(28, 10, asdecimal=True)
TEXT_TYPE = sa.Text()

TABLE_COLUMNS: dict[str, list[tuple[str, bool]]] = {
    "instruments": [("max_open_quantity", True)],
    "positions_live": [
        ("quantity", False),
        ("quantity_available_for_trading", True),
        ("quantity_in_pies", True),
        ("average_price_paid", True),
        ("current_price", True),
        ("wallet_current_value", True),
        ("wallet_fx_impact", True),
        ("wallet_total_cost", True),
        ("wallet_unrealized_profit_loss", True),
    ],
    "transactions": [("amount", True)],
    "orders_history": [
        ("order_quantity", True),
        ("filled_quantity", True),
        ("fill_price", True),
        ("order_filled_value", True),
        ("limit_price", True),
        ("stop_price", True),
        ("wallet_net_value", True),
        ("wallet_fx_rate", True),
        ("wallet_realised_profit_loss", True),
    ],
    "dividends": [
        ("quantity", True),
        ("amount", True),
        ("amount_in_euro", True),
        ("gross_amount_per_share", True),
    ],
    "position_reconciliation": [
        ("replayed_quantity", False),
        ("live_quantity", False),
        ("difference_quantity", False),
        ("tolerance_quantity", False),
    ],
}


def upgrade() -> None:
    _alter_financial_columns(existing_type=NUMERIC_28_10, target_type=TEXT_TYPE)


def downgrade() -> None:
    _alter_financial_columns(existing_type=TEXT_TYPE, target_type=NUMERIC_28_10)


def _alter_financial_columns(
    *,
    existing_type: sa.TypeEngine[object],
    target_type: sa.TypeEngine[object],
) -> None:
    for table_name, columns in TABLE_COLUMNS.items():
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            for column_name, nullable in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=existing_type,
                    type_=target_type,
                    existing_nullable=nullable,
                )
