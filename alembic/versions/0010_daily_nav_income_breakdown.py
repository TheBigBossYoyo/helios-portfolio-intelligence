"""daily_nav: dividends, interest and fees per day

A day's change in value has two unrelated causes -- money moved in or out, and what the
investments earned -- and the dashboard now shows them apart for every period. External flows
were already stored; these three columns let the investment result itself be broken down
(market movement is the residual). Existing rows default to zero until the next replay rewrites
them, which every sync now triggers.

Revision ID: 0010_daily_nav_income_breakdown
Revises: 0009_index_instrument_mapping_status
Create Date: 2026-09-27 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_daily_nav_income_breakdown"
down_revision = "0009_index_instrument_mapping_status"
branch_labels = None
depends_on = None

COLUMNS = ("dividend_eur", "interest_eur", "fee_eur")


def upgrade() -> None:
    with op.batch_alter_table("daily_nav") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Text(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("daily_nav") as batch:
        for name in COLUMNS:
            batch.drop_column(name)
