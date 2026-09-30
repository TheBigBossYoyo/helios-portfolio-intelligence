"""company_facts, fund_holdings, goals, push_subscriptions, notifications.pushed_at

Revision ID: 0022_facts_goals_push
Revises: 0021_allocation_targets
Create Date: 2026-09-30 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_facts_goals_push"
down_revision = "0021_allocation_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_facts",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("sic", sa.Integer(), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("fiscal_year_end", sa.String(length=8), nullable=True),
        sa.Column("figures", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("t212_ticker", name="pk_company_facts"),
    )
    op.create_table(
        "fund_holdings",
        sa.Column("proxy_symbol", sa.String(length=16), nullable=False),
        sa.Column("series_id", sa.String(length=16), nullable=False),
        sa.Column("accession", sa.String(length=32), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("holdings_count", sa.Integer(), nullable=False),
        sa.Column("holdings", sa.JSON(), nullable=False),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.Column("sectors", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("proxy_symbol", name="pk_fund_holdings"),
    )
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("target_amount", sa.Text(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goals"),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True))
    # Everything already there was seen on the computer: phones get new notifications only.
    op.execute("UPDATE notifications SET pushed_at = created_at")


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("pushed_at")
    op.drop_table("push_subscriptions")
    op.drop_table("goals")
    op.drop_table("fund_holdings")
    op.drop_table("company_facts")
