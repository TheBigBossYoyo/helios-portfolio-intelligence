"""price_alerts and notifications: alerts on a price or on your gain, and what the tray shows

Revision ID: 0014_alerts_notifications
Revises: 0013_card_budgets
Create Date: 2026-09-28 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_alerts_notifications"
down_revision = "0013_card_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("threshold", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_price", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_price_alerts"),
    )
    op.create_index("ix_price_alerts_ticker", "price_alerts", ["ticker"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_index("ix_price_alerts_ticker", table_name="price_alerts")
    op.drop_table("price_alerts")
