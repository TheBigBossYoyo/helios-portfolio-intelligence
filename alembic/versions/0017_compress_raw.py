"""Compress the raw columns already stored (raw_snapshots, raw_news, t212_exports)

New rows are written compressed by the column types in helios.compression; this rewrites the
rows written before, in batches, in the same format. Reads accept both, so the migration is
safe to interrupt and re-run. The space freed is returned to the disk by the next VACUUM
(helios.storage runs one when enough of the file is free).

Revision ID: 0017_compress_raw
Revises: 0016_ai_run_kind
Create Date: 2026-09-29 20:00:00
"""

from __future__ import annotations

import zlib

import sqlalchemy as sa

from alembic import op

revision = "0017_compress_raw"
down_revision = "0016_ai_run_kind"
branch_labels = None
depends_on = None

MAGIC = b"z1:"
BATCH = 200
COLUMNS = (("raw_snapshots", "payload_json"), ("raw_news", "body"), ("t212_exports", "body"))


def _compress(value: object) -> bytes | None:
    if value is None:
        return None
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    if raw.startswith(MAGIC):
        return None  # already compressed
    return MAGIC + zlib.compress(raw, 6)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in COLUMNS:
        last_id = 0
        while True:
            rows = bind.execute(
                sa.text(
                    f"SELECT id, {column} FROM {table} WHERE id > :last ORDER BY id LIMIT :batch"
                ),
                {"last": last_id, "batch": BATCH},
            ).all()
            if not rows:
                break
            for row_id, value in rows:
                packed = _compress(value)
                if packed is not None:
                    bind.execute(
                        sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                        {"value": packed, "id": row_id},
                    )
                last_id = row_id


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in COLUMNS:
        for row_id, value in bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).all():
            if isinstance(value, bytes) and value.startswith(MAGIC):
                bind.execute(
                    sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                    {"value": zlib.decompress(value[len(MAGIC) :]).decode("utf-8"), "id": row_id},
                )
