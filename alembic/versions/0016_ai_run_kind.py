"""ai_runs.kind: tell the on-demand analysis and the weekly review apart

Revision ID: 0016_ai_run_kind
Revises: 0015_watchlist
Create Date: 2026-09-28 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_ai_run_kind"
down_revision = "0015_watchlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_runs") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="analysis")
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_runs") as batch:
        batch.drop_column("kind")
