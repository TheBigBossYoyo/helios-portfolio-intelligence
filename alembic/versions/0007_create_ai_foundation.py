"""create milestone 6 AI analysis tables

Revision ID: 0007_create_ai_foundation
Revises: 0006_create_news_foundation
Create Date: 2026-08-06 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_create_ai_foundation"
down_revision = "0006_create_news_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("prompt_json", sa.JSON(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("served_by_model", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ai_runs"),
    )
    op.create_index("ix_ai_runs_ts", "ai_runs", ["ts"])
    op.create_index("ix_ai_runs_status", "ai_runs", ["status"])

    op.create_table(
        "ai_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_observations"),
    )
    op.create_index("ix_ai_observations_run_id", "ai_observations", ["run_id"])
    op.create_index("ix_ai_observations_t212_ticker", "ai_observations", ["t212_ticker"])


def downgrade() -> None:
    op.drop_index("ix_ai_observations_t212_ticker", table_name="ai_observations")
    op.drop_index("ix_ai_observations_run_id", table_name="ai_observations")
    op.drop_table("ai_observations")
    op.drop_index("ix_ai_runs_status", table_name="ai_runs")
    op.drop_index("ix_ai_runs_ts", table_name="ai_runs")
    op.drop_table("ai_runs")
