"""Runtime state — RuntimeConfig (mutable, snapshotted at file boundaries),
RuntimeStats (counters), PauseGate (asyncio Event wrapper), control flags.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from .config import Settings


# ---------------------------------------------------------------------------
# Runtime config (mutable, but Read at file boundaries to keep send loop
# consistent within a single file).  Admin UI updates this; main loop
# snapshots fields at the start of each file.
# ---------------------------------------------------------------------------


@dataclass
class RuntimeConfig:
    mode: str
    out_fps: int
    resize_w: int | None
    resize_h: int | None
    jpeg_quality: int
    output_format: str
    jpeg_seq_mode: str
    chunk_size_kb: int

    interval_sec: float
    cycle_interval_sec: float
    loop: bool

    stay_mode: str
    files_per_stay: int | None
    stay_seconds: float | None
    file_order: str

    amr_id: str

    @classmethod
    def from_settings(cls, s: Settings) -> RuntimeConfig:
        return cls(
            mode=s.mode,
            out_fps=s.out_fps,
            resize_w=s.resize_w,
            resize_h=s.resize_h,
            jpeg_quality=s.jpeg_quality,
            output_format=s.output_format,
            jpeg_seq_mode=s.jpeg_seq_mode,
            chunk_size_kb=s.chunk_size_kb,
            interval_sec=s.interval_sec,
            cycle_interval_sec=s.cycle_interval_sec,
            loop=s.loop,
            stay_mode=s.stay_mode,
            files_per_stay=s.files_per_stay,
            stay_seconds=s.stay_seconds,
            file_order=s.file_order,
            amr_id=s.amr_id,
        )

    def chunk_size_bytes(self) -> int:
        return max(1024, int(self.chunk_size_kb) * 1024)

    def encoding_dict(self) -> dict:
        d: dict = {"mode": self.mode}
        if self.mode == "transcode":
            d["out_fps"] = self.out_fps
            if self.resize_w and self.resize_h:
                d["resize"] = [self.resize_w, self.resize_h]
            d["jpeg_quality"] = self.jpeg_quality
            d["output_format"] = self.output_format
        return d


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class RuntimeStats:
    cycles_completed: int = 0
    files_sent: int = 0
    files_rejected: int = 0
    chunks_sent: int = 0
    ack_count: int = 0
    error_count: int = 0
    last_error: str | None = None

    # Per-station error tally — UI hint that the station may not be
    # registered on the gateway side.
    rejected_per_station: dict[str, int] = field(default_factory=dict)

    def bump_rejected_for(self, station_name: str) -> None:
        self.rejected_per_station[station_name] = (
            self.rejected_per_station.get(station_name, 0) + 1
        )


# ---------------------------------------------------------------------------
# Status (current cycle / station / file progress, surfaced via /api/status)
# ---------------------------------------------------------------------------


@dataclass
class RuntimeStatus:
    started_at_iso: str = ""
    cycle: int = 0
    station_name: str | None = None
    station_idx: int = 0
    station_count: int = 0
    file_idx_in_station: int = 0
    files_in_current_stay: int = 0
    current_filename: str | None = None
    current_chunk: int = 0
    current_chunk_total: int = 0
    last_file_at_iso: str | None = None


# ---------------------------------------------------------------------------
# PauseGate (re-entrant — callers always `await gate.wait()` between actions)
# ---------------------------------------------------------------------------


class PauseGate:
    def __init__(self, paused: bool = False) -> None:
        self._event = asyncio.Event()
        if not paused:
            self._event.set()

    @property
    def paused(self) -> bool:
        return not self._event.is_set()

    def pause(self) -> None:
        self._event.clear()

    def resume(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


# ---------------------------------------------------------------------------
# Skip flags (admin actions: skip-file / skip-station / restart-cycle)
# ---------------------------------------------------------------------------


@dataclass
class SkipFlags:
    skip_file: bool = False
    skip_station: bool = False
    restart_cycle: bool = False

    def consume_skip_file(self) -> bool:
        v = self.skip_file
        self.skip_file = False
        return v

    def consume_skip_station(self) -> bool:
        v = self.skip_station
        self.skip_station = False
        return v

    def consume_restart_cycle(self) -> bool:
        v = self.restart_cycle
        self.restart_cycle = False
        return v


# ---------------------------------------------------------------------------
# AppState (single bag passed around)
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    settings: Settings
    cfg: RuntimeConfig
    stats: RuntimeStats
    status: RuntimeStatus
    gate: PauseGate
    skip: SkipFlags
    pool: object        # asyncpg.Pool (forward ref to avoid import cycle)
    media_root: object  # pathlib.Path (forward ref)
