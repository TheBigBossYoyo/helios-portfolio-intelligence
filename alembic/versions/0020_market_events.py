"""earnings_events, dividend_events, event_fetches: the calendar of earnings and dividends

Revision ID: 0020_market_events
Revises: 0019_paired_devices
Create Date: 2026-09-30 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_market_events"
down_revision = "0019_paired_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "earnings_events",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("fiscal_date_ending", sa.Date(), nullable=True),
        sa.Column("estimate_eps", sa.Text(), nullable=True),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("time_of_day", sa.String(length=32), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("t212_ticker", "report_date", name="pk_earnings_events"),
    )
    op.create_table(
        "dividend_events",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("declaration_date", sa.Date(), nullable=True),
        sa.Column("amount_per_share", sa.Text(), nullable=False),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("t212_ticker", "ex_date", name="pk_dividend_events"),
    )
    op.create_table(
        "event_fetches",
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_event_fetches"),
    )


def downgrade() -> None:
    op.drop_table("event_fetches")
    op.drop_table("dividend_events")
    op.drop_table("earnings_events")
