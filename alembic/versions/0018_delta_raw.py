"""Delta-compress the raw tables (raw_news, raw_snapshots) with zstd

Each row is re-encoded against the previous row of its stream (same feed URL, same Trading 212
endpoint) -- see helios.compression. Every row is decoded again and compared with the original
bytes before it is written, so the migration either keeps everything exactly or stops.
The file shrinks at the next compaction (VACUUM), which the worker runs shortly after start.

Revision ID: 0018_delta_raw
Revises: 0017_compress_raw
Create Date: 2026-09-29 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from helios.compression import (
    KEYFRAME_INTERVAL,
    decode_bytes,
    encode_delta,
    encode_standalone,
    stream_key,
)

revision = "0018_delta_raw"
down_revision = "0017_compress_raw"
branch_labels = None
depends_on = None

BATCH = 200
#: Keep a delta only when it is well under this share of the original.
DELTA_WORTH_IT = 0.125
TABLES = (
    ("raw_news", "body", "news", ("feed_key", "url")),
    ("raw_snapshots", "payload_json", "t212", ("endpoint",)),
)


def _encode(content: bytes, base: tuple[int, bytes, int] | None) -> tuple[bytes, int | None, int]:
    """(blob, base id, depth past the keyframe)."""

    if base is not None and base[2] + 1 < KEYFRAME_INTERVAL:
        delta = encode_delta(content, base[1])
        if len(delta) <= max(len(content), 1) * DELTA_WORTH_IT:
            return delta, base[0], base[2] + 1
        standalone = encode_standalone(content)
        if len(delta) < len(standalone):
            return delta, base[0], base[2] + 1
        return standalone, None, 0
    return encode_standalone(content), None, 0


def upgrade() -> None:
    for table, _column, _kind, _key_columns in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("stream_key", sa.String(40), nullable=True))
            batch.add_column(sa.Column("delta_base_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_stream", table, ["stream_key", "id"])

    bind = op.get_bind()
    for table, column, kind, key_columns in TABLES:
        latest: dict[str, tuple[int, bytes, int]] = {}
        last_id = 0
        selected = ", ".join(key_columns)
        while True:
            rows = bind.execute(
                sa.text(
                    f"SELECT id, {selected}, {column} FROM {table} "
                    "WHERE id > :last ORDER BY id LIMIT :batch"
                ),
                {"last": last_id, "batch": BATCH},
            ).all()
            if not rows:
                break
            for row in rows:
                row_id, *parts, value = row
                content = decode_bytes(value)
                key = stream_key(kind, *(str(part) for part in parts))
                blob, base_id, depth = _encode(content, latest.get(key))
                base_content = latest[key][1] if base_id is not None else None
                if decode_bytes(blob, base_content) != content:
                    raise RuntimeError(f"{table} row {row_id} did not round-trip; nothing written")
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = :blob, stream_key = :key, "
                        "delta_base_id = :base WHERE id = :id"
                    ),
                    {"blob": blob, "key": key, "base": base_id, "id": row_id},
                )
                latest[key] = (row_id, content, depth)
                last_id = row_id


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, _kind, _keys in TABLES:
        decoded: dict[int, bytes] = {}
        rows = bind.execute(
            sa.text(f"SELECT id, {column}, delta_base_id FROM {table} ORDER BY id")
        ).all()
        for row_id, blob, base_id in rows:
            content = decode_bytes(blob, decoded.get(base_id) if base_id is not None else None)
            decoded[row_id] = content
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                {"value": content.decode("utf-8"), "id": row_id},
            )
        op.drop_index(f"ix_{table}_stream", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("delta_base_id")
            batch.drop_column("stream_key")
