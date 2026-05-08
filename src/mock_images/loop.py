"""Main round-robin send loop.

One cycle = visit every active station once, send its file queue per
the active stay-mode, then move on. Admin UI mutates state.cfg / state.skip
between files.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from gw_proto import StandardCodec, TcpClient

from . import meta_db
from .runtime import AppState
from .sender import recv_loop, send_one_file

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def run_main_loop(state: AppState) -> None:
    """Top-level send loop. Runs until cancelled."""
    state.status.started_at_iso = _iso_now()

    # TCP connect (with startup grace)
    await asyncio.sleep(state.settings.startup_delay_sec)
    client = TcpClient(
        state.settings.ingestion_host,
        state.settings.ingestion_port,
        StandardCodec(),
    )
    await client.connect()
    logger.info(
        "connected to %s:%d",
        state.settings.ingestion_host, state.settings.ingestion_port,
    )

    # Run recv loop concurrently to consume ACK / ERROR
    recv_task = asyncio.create_task(
        recv_loop(client, state.stats), name="mi_recv_loop",
    )

    try:
        cycle = 0
        while True:
            cycle += 1
            state.status.cycle = cycle
            await _run_one_cycle(client, state)
            state.stats.cycles_completed += 1

            if not state.cfg.loop:
                logger.info("LOOP=false, exiting after cycle %d", cycle)
                break

            if state.cfg.cycle_interval_sec > 0:
                logger.info(
                    "[CYCLE %d] complete, sleeping %.1fs before next",
                    cycle, state.cfg.cycle_interval_sec,
                )
                await asyncio.sleep(state.cfg.cycle_interval_sec)
            else:
                logger.info("[CYCLE %d] complete", cycle)
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass
        try:
            await client.close()
        except Exception:
            pass


async def _run_one_cycle(client: TcpClient, state: AppState) -> None:
    stations = await meta_db.fetch_active_stations(state.pool)
    state.status.station_count = len(stations)

    if not stations:
        logger.info("[CYCLE] no active stations; sleeping 5s before retry")
        state.status.station_name = None
        await asyncio.sleep(5.0)
        return

    sent_anything = False
    for idx, st in enumerate(stations):
        # restart-cycle requested → break out, top loop will start fresh
        if state.skip.consume_restart_cycle():
            logger.info("restart-cycle requested mid-cycle")
            return
        await state.gate.wait()

        state.status.station_idx = idx
        state.status.station_name = st["station_name"]

        files = await meta_db.fetch_files_for_send(
            state.pool, st["station_name"], file_order=state.cfg.file_order,
        )
        if not files:
            logger.info("[STAY] %s has no files, skipping", st["station_name"])
            continue

        await _stay_at_station(client, state, st, files)
        sent_anything = True

    if not sent_anything:
        # Every station's file queue was empty.  Without a sleep here the
        # cycle counter would race uncontrollably (DB polled in tight loop).
        # Use the 'no active stations' cadence as the sane idle fallback.
        await asyncio.sleep(5.0)


async def _stay_at_station(
    client: TcpClient,
    state: AppState,
    station: dict,
    files: list[dict],
) -> None:
    name = station["station_name"]

    cfg = state.cfg     # snapshot at station entry; still readable each file
    stay_mode = cfg.stay_mode
    files_per_stay = cfg.files_per_stay
    stay_seconds = cfg.stay_seconds

    if stay_mode == "fixed_count" and files_per_stay is not None:
        candidate_files = files[: max(1, files_per_stay)]
    else:
        candidate_files = files

    state.status.files_in_current_stay = len(candidate_files)
    state.status.file_idx_in_station = 0

    logger.info(
        "[STAY] %s %d files, mode=%s",
        name, len(candidate_files), stay_mode,
    )

    stay_started = time.monotonic()

    for f_idx, f in enumerate(candidate_files):
        if state.skip.consume_skip_station():
            logger.info("skip-station requested at %s", name)
            break
        if state.skip.consume_restart_cycle():
            logger.info("restart-cycle requested at %s", name)
            return
        await state.gate.wait()

        # time_based: stay until STAY_SECONDS elapsed
        if stay_mode == "time_based" and stay_seconds is not None:
            if (time.monotonic() - stay_started) >= stay_seconds:
                logger.info(
                    "[STAY] %s time-based stay limit reached (%.1fs)",
                    name, stay_seconds,
                )
                break

        # skip-file ?
        if state.skip.consume_skip_file():
            logger.info("skip-file requested at %s/%s", name, f["filename"])
            continue

        await _send_one(client, state, name, f)
        state.status.file_idx_in_station = f_idx + 1
        state.status.last_file_at_iso = _iso_now()

        if state.cfg.interval_sec > 0:
            await asyncio.sleep(state.cfg.interval_sec)

    state.status.current_filename = None
    state.status.current_chunk = 0
    state.status.current_chunk_total = 0


async def _send_one(
    client: TcpClient,
    state: AppState,
    station_name: str,
    f: dict,
) -> None:
    file_path = Path(state.media_root) / station_name / f["filename"]
    if not file_path.is_file():
        logger.warning("missing on disk, skipping: %s", file_path)
        return

    cfg = state.cfg
    state.status.current_filename = f["filename"]

    ok = await send_one_file(
        client,
        state.stats,
        file_path=file_path,
        station_name=station_name,
        source_filename=f["filename"],
        file_type=f.get("file_type", "unknown"),
        chunk_size_bytes=cfg.chunk_size_bytes(),
        amr_id=cfg.amr_id,
        source_format=_infer_source_format(f, cfg),
        encoding=cfg.encoding_dict(),
        amr_position=None,   # Phase 2: AMR position simulator
        mode=cfg.mode,
        output_format=cfg.output_format,
        out_fps=cfg.out_fps,
        resize_w=cfg.resize_w,
        resize_h=cfg.resize_h,
        jpeg_quality=cfg.jpeg_quality,
    )

    if ok:
        await meta_db.mark_sent(state.pool, station_name, f["filename"])
    else:
        state.stats.bump_rejected_for(station_name)


def _infer_source_format(f: dict, cfg) -> str | None:
    """Decide source_format header value.

    RAW + image  → 'jpeg'
    RAW + video  → 'mp4'  (best guess; downstream just uses the file extension)
    TRANSCODE    → cfg.output_format
    """
    if cfg.mode == "transcode":
        return cfg.output_format
    file_type = f.get("file_type")
    if file_type == "image":
        return "jpeg"
    if file_type == "video":
        ext = Path(f["filename"]).suffix.lower().lstrip(".")
        return ext or "mp4"
    return None
