"""TCP sender — VIDEO_CHUNK + VIDEO_COMPLETE for one media file.

Recv loop runs concurrently to consume ACK/ERROR messages and update
runtime statistics.  Errors do NOT raise out of `send_one_file` — they
are counted and the file is aborted, mirroring dev_plan §5.4.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gw_proto import Message, MessageType, TcpClient
from gw_proto.errors import GwProtoError

from .chunker import chunk_count, split_chunks
from .encoder import encode_for_send
from .payload_builder import ChunkContext, build_chunk_payload
from .runtime import RuntimeStats

logger = logging.getLogger(__name__)


async def recv_loop(client: TcpClient, stats: RuntimeStats) -> None:
    """Run forever — increment ACK / ERROR counters."""
    while True:
        try:
            msg = await client.receive()
        except (GwProtoError, ConnectionResetError, asyncio.IncompleteReadError):
            await asyncio.sleep(1.0)
            continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("recv loop error")
            await asyncio.sleep(1.0)
            continue

        if msg.msg_type == MessageType.ACK:
            stats.ack_count += 1
        elif msg.msg_type == MessageType.ERROR:
            try:
                body = json.loads(msg.payload.decode("utf-8"))
                reason = body.get("error", "<no reason>")
            except Exception:
                reason = msg.payload[:200].decode("utf-8", errors="replace")
            stats.error_count += 1
            stats.last_error = reason
            logger.warning("ERROR from gateway: %s", reason)
        else:
            logger.debug("unexpected msg_type=0x%04X", int(msg.msg_type))


async def send_one_file(
    client: TcpClient,
    stats: RuntimeStats,
    *,
    file_path: Path,
    station_id: str,
    station_name: str,
    source_filename: str,
    file_type: str,
    chunk_size_bytes: int,
    amr_id: str | None,
    source_format: str | None,
    encoding: dict[str, Any] | None,
    amr_position: dict[str, Any] | None,
    mode: str = "raw",
    output_format: str = "mp4",
    out_fps: int = 15,
    resize_w: int | None = None,
    resize_h: int | None = None,
    jpeg_quality: int = 85,
) -> bool:
    """Send a file as VIDEO_CHUNK[N] + VIDEO_COMPLETE.

    Returns True on apparent success (all chunks + COMPLETE flushed).
    Returns False if a connection or send error aborted mid-file.

    Note: ACK/ERROR are received asynchronously by `recv_loop`.  This
    function does NOT wait for ACK on each chunk — it pushes them out
    back-to-back and trusts the recv loop to count results.
    """
    data = await asyncio.to_thread(
        encode_for_send,
        file_path,
        file_type=file_type,
        mode=mode,
        output_format=output_format,
        out_fps=out_fps,
        resize_w=resize_w,
        resize_h=resize_h,
        jpeg_quality=jpeg_quality,
    )
    total_size = len(data)
    if total_size == 0:
        logger.warning("file is empty, skipping: %s", file_path)
        return False

    n_chunks = chunk_count(total_size, chunk_size_bytes)
    video_id = str(uuid.uuid4())
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    error_baseline = stats.error_count

    t_start = time.monotonic()
    for seq, body in enumerate(split_chunks(data, chunk_size_bytes)):
        ctx = ChunkContext(
            video_id=video_id,
            station_id=station_id,
            station_name=station_name,
            captured_at=captured_at,
            chunk_seq=seq,
            total_chunks=n_chunks,
            source_filename=source_filename,
            source_format=source_format,
            amr_id=amr_id,
            encoding=encoding,
            amr_position=amr_position,
        )
        payload = build_chunk_payload(ctx, body)
        try:
            await client.send(Message(msg_type=MessageType.VIDEO_CHUNK, payload=payload))
            stats.chunks_sent += 1
        except GwProtoError:
            logger.warning("send failed (disconnected) at chunk %d, reconnecting...", seq)
            try:
                await client.reconnect()
            except Exception:
                logger.exception("reconnect failed")
                await asyncio.sleep(1.0)
            return False

        # Tiny gap so the recv loop has a chance to process ACK/ERROR
        # before the next send. Not strictly required, but reduces TCP
        # buffer churn for very small chunks.
        if seq % 16 == 15:
            await asyncio.sleep(0)

    # Wait briefly for the gateway to process accumulated chunks before
    # sending COMPLETE (gives recv_loop a chance to see early ERRORs).
    await asyncio.sleep(0.05)

    # If we already saw new ERRORs (e.g. unknown station on first chunk),
    # we still send COMPLETE — Gateway's video_handler responds ERROR
    # ("No buffered video"), but counting is consistent.
    complete_payload = json.dumps({"video_id": video_id}).encode()
    try:
        await client.send(
            Message(msg_type=MessageType.VIDEO_COMPLETE, payload=complete_payload)
        )
    except GwProtoError:
        logger.warning("send VIDEO_COMPLETE failed, reconnecting...")
        try:
            await client.reconnect()
        except Exception:
            logger.exception("reconnect failed")
            await asyncio.sleep(1.0)
        return False

    elapsed = time.monotonic() - t_start

    # Brief grace for the COMPLETE response.
    await asyncio.sleep(0.1)
    new_errors = stats.error_count - error_baseline
    if new_errors > 0:
        logger.info(
            "[FILE] %s/%s rejected (errors=%d, %d chunks, %.2fs)",
            station_name, source_filename, new_errors, n_chunks, elapsed,
        )
        stats.files_rejected += 1
        return False

    logger.info(
        "[FILE] %s/%s sent (chunks=%d, size=%d, %.2fs)",
        station_name, source_filename, n_chunks, total_size, elapsed,
    )
    stats.files_sent += 1
    return True
