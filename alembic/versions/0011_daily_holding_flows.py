"""daily_holding_flows: money into and out of each holding, per day

A period's investment result can only be split by stock if each stock's own cash is known: what
was spent buying it, what selling it returned, and what it paid in dividends. Without that, a
stock bought mid-period reads as a gain of its whole purchase price, and one sold reads as a
loss of all of it. The replay rewrites this table on every run, so existing databases fill it on
the next replay.

Revision ID: 0011_daily_holding_flows
Revises: 0010_daily_nav_income_breakdown
Create Date: 2026-09-27 21:30:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_daily_holding_flows"
down_revision = "0010_daily_nav_income_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_holding_flows",
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("bought_eur", sa.Text(), nullable=False, server_default="0"),
        sa.Column("sold_eur", sa.Text(), nullable=False, server_default="0"),
        sa.Column("dividend_eur", sa.Text(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("as_of_date", "t212_ticker", name="pk_daily_holding_flows"),
    )


def downgrade() -> None:
    op.drop_table("daily_holding_flows")
