"""index instruments.mapping_status

The data-quality report filters instruments by mapping_status three times per request
(unresolved, ambiguous, override_required). Against a full Trading 212 metadata cache of ~17k
rows that was three sequential full table scans, ~1.65s each -- about 5s of the response, which
tripped the frontend's fetch timeout and rendered the page as "unavailable".

Revision ID: 0009_index_instrument_mapping_status
Revises: 0008_create_thesis_foundation
Create Date: 2026-08-07 07:30:00
"""

from __future__ import annotations

from alembic import op

revision = "0009_index_instrument_mapping_status"
down_revision = "0008_create_thesis_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_instruments_mapping_status",
        "instruments",
        ["mapping_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_instruments_mapping_status", table_name="instruments")
