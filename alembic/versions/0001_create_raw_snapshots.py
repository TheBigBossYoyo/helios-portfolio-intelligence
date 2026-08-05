"""create raw snapshots table

Revision ID: 0001_create_raw_snapshots
Revises: 
Create Date: 2026-08-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_create_raw_snapshots"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_raw_snapshots_endpoint", "raw_snapshots", ["endpoint"])


def downgrade() -> None:
    op.drop_index("ix_raw_snapshots_endpoint", table_name="raw_snapshots")
    op.drop_table("raw_snapshots")
