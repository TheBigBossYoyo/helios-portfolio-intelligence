"""align cash transactions with the official Trading 212 schema

Revision ID: 0004_align_cash_transactions
Revises: 0003_exact_decimal_storage
Create Date: 2026-08-05 23:55:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_align_cash_transactions"
down_revision = "0003_exact_decimal_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_transactions_t212_ticker", table_name="transactions")
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.drop_column("t212_ticker")
        batch_op.drop_column("isin")


def downgrade() -> None:
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("t212_ticker", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("isin", sa.String(length=32), nullable=True))
    op.create_index(
        "ix_transactions_t212_ticker",
        "transactions",
        ["t212_ticker"],
    )
