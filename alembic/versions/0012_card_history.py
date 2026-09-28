"""Card history: Trading 212 CSV exports, their cash rows, and card/cashback per day

The transactions API reports a card payment as a bare WITHDRAW and card cashback as a DEPOSIT.
Trading 212's CSV export labels both (with merchant and category for card payments), so Helios
now requests one, stores it raw, and uses it to keep card spending apart from bank withdrawals
and to count cashback as income instead of money added.

Revision ID: 0012_card_history
Revises: 0011_daily_holding_flows
Create Date: 2026-09-27 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_card_history"
down_revision = "0011_daily_holding_flows"
branch_labels = None
depends_on = None

NAV_COLUMNS = ("deposit_eur", "withdrawal_eur", "card_spending_eur", "cashback_eur")


def upgrade() -> None:
    op.create_table(
        "t212_exports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_t212_exports"),
    )
    op.create_index("ix_t212_exports_report_id", "t212_exports", ["report_id"])
    op.create_table(
        "t212_export_rows",
        sa.Column("row_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("merchant_name", sa.String(length=255), nullable=True),
        sa.Column("merchant_category", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("export_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("row_id", name="pk_t212_export_rows"),
    )
    op.create_index("ix_t212_export_rows_action", "t212_export_rows", ["action"])
    op.create_index("ix_t212_export_rows_ts", "t212_export_rows", ["ts"])
    with op.batch_alter_table("daily_nav") as batch:
        for name in NAV_COLUMNS:
            batch.add_column(sa.Column(name, sa.Text(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("daily_nav") as batch:
        for name in NAV_COLUMNS:
            batch.drop_column(name)
    op.drop_index("ix_t212_export_rows_ts", table_name="t212_export_rows")
    op.drop_index("ix_t212_export_rows_action", table_name="t212_export_rows")
    op.drop_table("t212_export_rows")
    op.drop_index("ix_t212_exports_report_id", table_name="t212_exports")
    op.drop_table("t212_exports")
