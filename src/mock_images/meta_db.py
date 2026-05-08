"""Meta DB layer (asyncpg) — station + media_file CRUD.

The Admin UI is the only writer.  The send loop only reads station list
and updates last_sent_at / send_count after a successful send.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterable

import asyncpg


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _classify(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "unknown"


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
    assert pool is not None
    return pool


# ---------------------------------------------------------------------------
# station CRUD
# ---------------------------------------------------------------------------


async def list_stations(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT station_name, station_id, label, status, created_at, updated_at
          FROM station
      ORDER BY station_name
        """
    )
    return [dict(r) for r in rows]


async def get_station(pool: asyncpg.Pool, name: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """
        SELECT station_name, station_id, label, status, created_at, updated_at
          FROM station
         WHERE station_name = $1
        """,
        name,
    )
    return dict(row) if row else None


async def insert_station(
    pool: asyncpg.Pool,
    *,
    station_name: str,
    station_id: uuid.UUID | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Insert a new station.  station_id None → DB default (gen_random_uuid)."""
    if station_id is None:
        row = await pool.fetchrow(
            """
            INSERT INTO station (station_name, label)
            VALUES ($1, $2)
         RETURNING station_name, station_id, label, status, created_at, updated_at
            """,
            station_name, label,
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO station (station_name, station_id, label)
            VALUES ($1, $2, $3)
         RETURNING station_name, station_id, label, status, created_at, updated_at
            """,
            station_name, station_id, label,
        )
    assert row is not None
    return dict(row)


async def update_station(
    pool: asyncpg.Pool,
    name: str,
    *,
    label: str | None = None,
    status: str | None = None,
    station_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Patch label / status / station_id.  station_name PK는 변경 불가."""
    row = await pool.fetchrow(
        """
        UPDATE station
           SET label      = COALESCE($2, label),
               status     = COALESCE($3, status),
               station_id = COALESCE($4, station_id),
               updated_at = NOW()
         WHERE station_name = $1
     RETURNING station_name, station_id, label, status, created_at, updated_at
        """,
        name, label, status, station_id,
    )
    return dict(row) if row else None


async def delete_station(pool: asyncpg.Pool, name: str) -> bool:
    """Cascade-delete station + its media_file rows.
    Caller is responsible for also deleting the on-disk folder."""
    result = await pool.execute(
        "DELETE FROM station WHERE station_name = $1", name,
    )
    return result.endswith(" 1")


# ---------------------------------------------------------------------------
# media_file CRUD + folder rescan
# ---------------------------------------------------------------------------


async def list_media(pool: asyncpg.Pool, station_name: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, station_name, filename, file_type, size_bytes,
               uploaded_at, last_sent_at, send_count
          FROM media_file
         WHERE station_name = $1
      ORDER BY uploaded_at DESC
        """,
        station_name,
    )
    return [dict(r) for r in rows]


async def upsert_media(
    pool: asyncpg.Pool,
    *,
    station_name: str,
    filename: str,
    size_bytes: int | None,
) -> dict[str, Any]:
    file_type = _classify(filename)
    row = await pool.fetchrow(
        """
        INSERT INTO media_file (station_name, filename, file_type, size_bytes)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (station_name, filename) DO UPDATE
            SET size_bytes = EXCLUDED.size_bytes
     RETURNING id, station_name, filename, file_type, size_bytes,
               uploaded_at, last_sent_at, send_count
        """,
        station_name, filename, file_type, size_bytes,
    )
    assert row is not None
    return dict(row)


async def delete_media(pool: asyncpg.Pool, station_name: str, filename: str) -> bool:
    result = await pool.execute(
        "DELETE FROM media_file WHERE station_name = $1 AND filename = $2",
        station_name, filename,
    )
    return result.endswith(" 1")


async def reset_send_count(
    pool: asyncpg.Pool, station_name: str, filename: str
) -> bool:
    result = await pool.execute(
        """
        UPDATE media_file
           SET send_count = 0, last_sent_at = NULL
         WHERE station_name = $1 AND filename = $2
        """,
        station_name, filename,
    )
    return result.endswith(" 1")


async def mark_sent(
    pool: asyncpg.Pool, station_name: str, filename: str
) -> None:
    """Called by the send loop after a successful VIDEO_COMPLETE ACK."""
    await pool.execute(
        """
        UPDATE media_file
           SET last_sent_at = NOW(),
               send_count   = send_count + 1
         WHERE station_name = $1 AND filename = $2
        """,
        station_name, filename,
    )


async def rescan_station_folder(
    pool: asyncpg.Pool,
    station_name: str,
    media_root: Path,
) -> dict[str, Any]:
    """Sync the meta DB with the on-disk folder.

    - New files on disk → INSERT into media_file
    - Deleted from disk → DELETE from media_file
    - Existing → update size_bytes if changed
    """
    folder = media_root / station_name
    inserted = 0
    deleted = 0
    updated = 0

    on_disk: dict[str, int] = {}
    if folder.is_dir():
        for entry in folder.iterdir():
            if not entry.is_file():
                continue
            on_disk[entry.name] = entry.stat().st_size

    rows = await pool.fetch(
        "SELECT filename, size_bytes FROM media_file WHERE station_name = $1",
        station_name,
    )
    in_db = {r["filename"]: r["size_bytes"] for r in rows}

    async with pool.acquire() as conn:
        async with conn.transaction():
            for fname, size in on_disk.items():
                if fname not in in_db:
                    await conn.execute(
                        """
                        INSERT INTO media_file
                            (station_name, filename, file_type, size_bytes)
                        VALUES ($1, $2, $3, $4)
                        """,
                        station_name, fname, _classify(fname), size,
                    )
                    inserted += 1
                elif in_db[fname] != size:
                    await conn.execute(
                        """
                        UPDATE media_file
                           SET size_bytes = $3
                         WHERE station_name = $1 AND filename = $2
                        """,
                        station_name, fname, size,
                    )
                    updated += 1
            for fname in in_db:
                if fname not in on_disk:
                    await conn.execute(
                        """
                        DELETE FROM media_file
                         WHERE station_name = $1 AND filename = $2
                        """,
                        station_name, fname,
                    )
                    deleted += 1

    return {"inserted": inserted, "updated": updated, "deleted": deleted}


# ---------------------------------------------------------------------------
# send-loop accessors
# ---------------------------------------------------------------------------


_FILE_ORDER_SQL = {
    "last_sent_asc":
        "ORDER BY last_sent_at ASC NULLS FIRST, id ASC",
    "alphabetical":
        "ORDER BY filename ASC, id ASC",
    "uploaded":
        "ORDER BY uploaded_at ASC, id ASC",
    "random":
        "ORDER BY random()",
}


async def fetch_active_stations(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT station_name, station_id, label, status
          FROM station
         WHERE status = 'active'
      ORDER BY station_name
        """
    )
    return [dict(r) for r in rows]


async def fetch_files_for_send(
    pool: asyncpg.Pool, station_name: str, *, file_order: str = "last_sent_asc"
) -> list[dict[str, Any]]:
    order_clause = _FILE_ORDER_SQL.get(file_order, _FILE_ORDER_SQL["last_sent_asc"])
    rows = await pool.fetch(
        f"""
        SELECT id, station_name, filename, file_type, size_bytes,
               uploaded_at, last_sent_at, send_count
          FROM media_file
         WHERE station_name = $1
        {order_clause}
        """,
        station_name,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# safe FS helpers (called from admin upload/delete handlers)
# ---------------------------------------------------------------------------


def safe_station_dir(media_root: Path, station_name: str) -> Path:
    """Return media_root / station_name with a name-safety check."""
    if "/" in station_name or "\\" in station_name or ".." in station_name:
        raise ValueError(f"unsafe station_name: {station_name!r}")
    target = (media_root / station_name).resolve()
    if not str(target).startswith(str(media_root.resolve())):
        raise ValueError(f"path escape: {station_name!r}")
    return target


def safe_file_path(media_root: Path, station_name: str, filename: str) -> Path:
    """Return media_root / station_name / filename with safety checks."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"unsafe filename: {filename!r}")
    folder = safe_station_dir(media_root, station_name)
    return folder / filename


def remove_station_folder(media_root: Path, station_name: str) -> None:
    """Best-effort recursive remove of the station folder."""
    folder = safe_station_dir(media_root, station_name)
    if not folder.exists():
        return
    for entry in folder.iterdir():
        if entry.is_file():
            entry.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass  # non-empty (e.g. nested) — leave it
