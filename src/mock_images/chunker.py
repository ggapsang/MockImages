"""Bytestream → chunk splitting.

Independent of how the bytes were produced (raw read, transcode output).
"""

from __future__ import annotations

from typing import Iterator


def split_chunks(data: bytes, chunk_size_bytes: int) -> Iterator[bytes]:
    """Yield consecutive chunks of `data` up to `chunk_size_bytes` each.

    Last chunk may be smaller.  Empty `data` yields nothing.
    """
    if chunk_size_bytes < 1:
        raise ValueError(f"chunk_size_bytes must be >= 1, got {chunk_size_bytes}")
    n = len(data)
    if n == 0:
        return
    offset = 0
    while offset < n:
        yield data[offset : offset + chunk_size_bytes]
        offset += chunk_size_bytes


def chunk_count(total_size: int, chunk_size_bytes: int) -> int:
    if chunk_size_bytes < 1:
        raise ValueError(f"chunk_size_bytes must be >= 1, got {chunk_size_bytes}")
    if total_size <= 0:
        return 0
    return (total_size + chunk_size_bytes - 1) // chunk_size_bytes
