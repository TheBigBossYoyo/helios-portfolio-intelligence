"""card_budgets: a monthly spending limit per card category

Revision ID: 0013_card_budgets
Revises: 0012_card_history
Create Date: 2026-09-28 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_card_budgets"
down_revision = "0012_card_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_budgets",
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("monthly_limit", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("category", name="pk_card_budgets"),
    )


def downgrade() -> None:
    op.drop_table("card_budgets")
