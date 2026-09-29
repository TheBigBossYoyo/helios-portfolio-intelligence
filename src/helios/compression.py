"""Lossless compression for the raw columns: Trading 212 responses, feed bodies, exports.

Raw-first storage keeps every response exactly as served, which is most of the database by size
and nearly all of its growth. Nothing is ever dropped to save space; it is compressed instead.

Two encodings, both zstd with a checksum on every frame so corruption is caught on read:

* **Standalone** (``s2:``): one zstd frame, level 19. Used for exports and for the first row of
  every raw stream.
* **Delta** (``d2:``): a zstd frame compressed against the *previous row of the same stream* (the
  same feed URL, the same Trading 212 endpoint) used as a raw-content dictionary. Consecutive
  fetches of one feed share nearly everything, so a delta is typically 1-3% of the body -- about
  15x smaller again than compressing each row on its own. Which row it is based on is stored in
  the row's ``delta_base_id``; every ``KEYFRAME_INTERVAL`` rows a stream starts again from a
  standalone frame, so reading any row needs at most that many small decompressions and a
  damaged row cannot affect more than the rows after it up to the next keyframe.

Older formats still read: ``z1:`` (zlib, migration 0017) and uncompressed text.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from typing import Any

import zstandard
from sqlalchemy import LargeBinary
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

#: zlib, written by migration 0017 and read for compatibility.
MAGIC = b"z1:"
STANDALONE = b"s2:"
DELTA = b"d2:"
LEVEL = 19
#: A stream starts again from a standalone frame after this many deltas.
KEYFRAME_INTERVAL = 32


class RawDataCorruptError(ValueError):
    """A stored frame failed its checksum or its delta base is missing."""


def _compressor(dictionary: bytes | None = None) -> zstandard.ZstdCompressor:
    data = (
        None
        if dictionary is None
        else zstandard.ZstdCompressionDict(dictionary, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    )
    return zstandard.ZstdCompressor(
        level=LEVEL, dict_data=data, write_checksum=True, write_content_size=True
    )


def _decompressor(dictionary: bytes | None = None) -> zstandard.ZstdDecompressor:
    if dictionary is None:
        return zstandard.ZstdDecompressor()
    data = zstandard.ZstdCompressionDict(dictionary, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    return zstandard.ZstdDecompressor(dict_data=data)


def encode_standalone(content: bytes) -> bytes:
    return STANDALONE + _compressor().compress(content)


def encode_delta(content: bytes, base: bytes) -> bytes:
    return DELTA + _compressor(base).compress(content)


def is_delta(blob: bytes) -> bool:
    return blob.startswith(DELTA)


def decode_bytes(blob: bytes | str, base: bytes | None = None) -> bytes:
    """The original bytes. ``base`` is the decoded base row, required for a delta frame."""

    if isinstance(blob, str):
        return blob.encode("utf-8")
    try:
        if blob.startswith(STANDALONE):
            return _decompressor().decompress(blob[len(STANDALONE) :])
        if blob.startswith(DELTA):
            if base is None:
                raise RawDataCorruptError("delta frame without its base row")
            return _decompressor(base).decompress(blob[len(DELTA) :])
        if blob.startswith(MAGIC):
            return zlib.decompress(blob[len(MAGIC) :])
    except zstandard.ZstdError as exc:
        raise RawDataCorruptError(str(exc)) from exc
    return blob


def stream_key(kind: str, *parts: str) -> str:
    """Which rows a delta may be based on: the same kind of data from the same source."""

    joined = "\n".join((kind, *parts)).encode("utf-8")
    return hashlib.sha1(joined, usedforsecurity=False).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --- standalone column types (exports) --------------------------------------------------------


def compress_text(value: str) -> bytes:
    return encode_standalone(value.encode("utf-8"))


def decompress_text(value: bytes | str) -> str:
    return decode_bytes(value).decode("utf-8")


class CompressedText(TypeDecorator[str]):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        del dialect
        return None if value is None else compress_text(value)

    def process_result_value(self, value: bytes | str | None, dialect: Dialect) -> str | None:
        del dialect
        return None if value is None else decompress_text(value)
