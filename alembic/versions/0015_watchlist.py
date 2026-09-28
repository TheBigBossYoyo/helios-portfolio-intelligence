"""watchlist: instruments followed without owning them

Revision ID: 0015_watchlist
Revises: 0014_alerts_notifications
Create Date: 2026-09-28 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_watchlist"
down_revision = "0014_alerts_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("t212_ticker", name="pk_watchlist"),
    )


def downgrade() -> None:
    op.drop_table("watchlist")
