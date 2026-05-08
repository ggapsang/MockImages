"""Build VIDEO_CHUNK headers with extension fields.

Wraps gw_proto.VideoChunkMeta + build_video_chunk_payload, plus a
small extension plug-in registry so callers can attach amr_id /
amr_position / source_format / source_file in a controlled way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# gw_proto helpers are still available; we inline header construction
# here to merge plug-in extensions in one pass.


@dataclass(slots=True)
class ChunkContext:
    """Runtime context passed to extension plug-ins to enrich the header.

    The wire identifier is ``station_name`` only — UUIDs are private to
    each module's database and never travel over the wire.
    """

    video_id: str
    station_name: str
    captured_at: str | None
    chunk_seq: int
    total_chunks: int
    source_filename: str
    source_format: str | None
    amr_id: str | None
    encoding: dict[str, Any] | None      # transcode parameters etc.
    amr_position: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Extension registry
# ---------------------------------------------------------------------------

ExtensionFn = Callable[[ChunkContext, dict[str, Any]], None]
_EXTENSIONS: dict[str, ExtensionFn] = {}


def register_extension(name: str, fn: ExtensionFn) -> None:
    _EXTENSIONS[name] = fn


def _ext_amr_id(ctx: ChunkContext, header: dict[str, Any]) -> None:
    if ctx.amr_id is not None:
        header["amr_id"] = ctx.amr_id


def _ext_source_file(ctx: ChunkContext, header: dict[str, Any]) -> None:
    header["source_file"] = f"{ctx.station_name}/{ctx.source_filename}"


def _ext_encoding(ctx: ChunkContext, header: dict[str, Any]) -> None:
    if ctx.encoding is not None:
        header["encoding"] = ctx.encoding


def _ext_amr_position(ctx: ChunkContext, header: dict[str, Any]) -> None:
    if ctx.amr_position is not None:
        header["amr_position"] = ctx.amr_position


register_extension("amr_id", _ext_amr_id)
register_extension("source_file", _ext_source_file)
register_extension("encoding", _ext_encoding)
register_extension("amr_position", _ext_amr_position)


DEFAULT_EXTENSIONS: tuple[str, ...] = ("amr_id", "encoding", "source_file")


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_chunk_payload(
    ctx: ChunkContext,
    binary_body: bytes,
    *,
    enabled_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> bytes:
    """Build a VIDEO_CHUNK payload.

    Required fields + Gateway-recognized optional fields are inlined.
    Extra plug-in extensions are merged into the JSON header.
    """
    import json as _json

    header: dict[str, Any] = {
        "video_id": ctx.video_id,
        "chunk_seq": ctx.chunk_seq,
        "total_chunks": ctx.total_chunks,
        "station_name": ctx.station_name,
    }
    if ctx.captured_at is not None:
        header["captured_at"] = ctx.captured_at
    if ctx.amr_id is not None:
        header["amr_id"] = ctx.amr_id
    if ctx.amr_position is not None:
        header["amr_position"] = ctx.amr_position
    if ctx.source_format is not None:
        header["source_format"] = ctx.source_format

    for name in enabled_extensions:
        fn = _EXTENSIONS.get(name)
        if fn is None:
            continue
        fn(ctx, header)

    return _json.dumps(header).encode() + b"\n" + binary_body
