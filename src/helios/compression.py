"""Transparent compression for the raw columns: Trading 212 responses, feed bodies, exports.

Raw-first storage keeps every response exactly as served, which is most of the database by
size and nearly all of its growth. These column types store that data zlib-compressed (about
5-6x smaller for JSON and feed XML) and hand back exactly what was written, so nothing that
reads a raw row -- the reparse commands, the export parser -- changes.

A compressed value is ``MAGIC + zlib(utf-8 bytes)``. Anything without the prefix is a row
written before compression existed and is returned as it was, so an old database reads
correctly before and after its migration.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

from sqlalchemy import LargeBinary
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

MAGIC = b"z1:"
LEVEL = 6


def compress_text(value: str) -> bytes:
    return MAGIC + zlib.compress(value.encode("utf-8"), LEVEL)


def decompress_text(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    if value.startswith(MAGIC):
        return zlib.decompress(value[len(MAGIC) :]).decode("utf-8")
    return value.decode("utf-8")


class CompressedText(TypeDecorator[str]):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        del dialect
        return None if value is None else compress_text(value)

    def process_result_value(self, value: bytes | str | None, dialect: Dialect) -> str | None:
        del dialect
        return None if value is None else decompress_text(value)


class CompressedJSON(TypeDecorator[Any]):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> bytes | None:
        del dialect
        if value is None:
            return None
        return compress_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    def process_result_value(self, value: bytes | str | None, dialect: Dialect) -> Any:
        del dialect
        if value is None:
            return None
        return json.loads(decompress_text(value))
