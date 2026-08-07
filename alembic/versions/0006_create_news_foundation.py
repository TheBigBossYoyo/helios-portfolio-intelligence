"""create milestone 5 news tables

Revision ID: 0006_create_news_foundation
Revises: 0005_m3_performance_foundation
Create Date: 2026-08-06 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_create_news_foundation"
down_revision = "0005_m3_performance_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feed_key", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_raw_news"),
    )
    op.create_index("ix_raw_news_feed_key", "raw_news", ["feed_key"])
    op.create_index("ix_raw_news_ts", "raw_news", ["ts"])

    op.create_table(
        "news_items",
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("feed_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="rss"),
        sa.Column("source_label", sa.String(length=255), nullable=False),
        sa.Column("t212_ticker", sa.String(length=64), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("title_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_news_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("dedupe_key", name="pk_news_items"),
    )
    op.create_index("ix_news_items_feed_key", "news_items", ["feed_key"])
    op.create_index("ix_news_items_t212_ticker", "news_items", ["t212_ticker"])
    op.create_index("ix_news_items_isin", "news_items", ["isin"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_canonical_url", "news_items", ["canonical_url"])
    op.create_index("ix_news_items_title_key", "news_items", ["title_key"])


def downgrade() -> None:
    op.drop_index("ix_news_items_title_key", table_name="news_items")
    op.drop_index("ix_news_items_canonical_url", table_name="news_items")
    op.drop_index("ix_news_items_published_at", table_name="news_items")
    op.drop_index("ix_news_items_isin", table_name="news_items")
    op.drop_index("ix_news_items_t212_ticker", table_name="news_items")
    op.drop_index("ix_news_items_feed_key", table_name="news_items")
    op.drop_table("news_items")
    op.drop_index("ix_raw_news_ts", table_name="raw_news")
    op.drop_index("ix_raw_news_feed_key", table_name="raw_news")
    op.drop_table("raw_news")
