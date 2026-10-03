"""allocation_targets: the weight you want each holding to have

Revision ID: 0021_allocation_targets
Revises: 0020_market_events
Create Date: 2026-09-30 02:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_allocation_targets"
down_revision = "0020_market_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allocation_targets",
        sa.Column("t212_ticker", sa.String(length=64), nullable=False),
        sa.Column("target_weight", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("t212_ticker", name="pk_allocation_targets"),
    )


def downgrade() -> None:
    op.drop_table("allocation_targets")
