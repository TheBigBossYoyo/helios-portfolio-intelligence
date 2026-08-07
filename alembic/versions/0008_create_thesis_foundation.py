"""create milestone 7 thesis and journal tables

Revision ID: 0008_create_thesis_foundation
Revises: 0007_create_ai_foundation
Create Date: 2026-08-06 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_create_thesis_foundation"
down_revision = "0007_create_ai_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "theses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("conviction", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_theses"),
    )
    op.create_index("ix_theses_t212_ticker", "theses", ["t212_ticker"])
    op.create_index("ix_theses_isin", "theses", ["isin"])
    op.create_index("ix_theses_status", "theses", ["status"])

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("tags", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_journal_entries"),
    )
    op.create_index("ix_journal_entries_thesis_id", "journal_entries", ["thesis_id"])
    op.create_index("ix_journal_entries_created_at", "journal_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_journal_entries_created_at", table_name="journal_entries")
    op.drop_index("ix_journal_entries_thesis_id", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_index("ix_theses_status", table_name="theses")
    op.drop_index("ix_theses_isin", table_name="theses")
    op.drop_index("ix_theses_t212_ticker", table_name="theses")
    op.drop_table("theses")
