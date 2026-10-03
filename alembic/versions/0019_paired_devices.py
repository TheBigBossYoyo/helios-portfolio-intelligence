"""paired_devices, device_pairings: phones allowed through the phone-access gateway

Revision ID: 0019_paired_devices
Revises: 0018_delta_raw
Create Date: 2026-09-29 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_paired_devices"
down_revision = "0018_delta_raw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paired_devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_paired_devices"),
        sa.UniqueConstraint("token_hash", name="uq_paired_devices_token_hash"),
    )
    op.create_table(
        "device_pairings",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("code", name="pk_device_pairings"),
    )


def downgrade() -> None:
    op.drop_table("device_pairings")
    op.drop_table("paired_devices")
