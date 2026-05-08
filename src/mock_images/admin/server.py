"""aiohttp HTTP server for the MockImages admin UI.

Routes:
- HTML pages:  /, /stations, /stations/<name>
- JSON API:    /api/stations, /api/stations/<name>, /api/stations/<name>/files,
               /api/stations/<name>/files/<filename>, /api/stations/<name>/rescan,
               /api/runtime/{status, pause, resume, skip-file, skip-station,
                             restart-cycle, config}
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from string import Template
from typing import Any

from aiohttp import web

from .. import meta_db
from ..runtime import AppState

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _state(request: web.Request) -> AppState:
    return request.app["state"]


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _render_template(filename: str, **substitutions: str) -> str:
    raw = (_TEMPLATES / filename).read_text(encoding="utf-8")
    return Template(raw).safe_substitute(**substitutions)


def _bad_request(message: str) -> web.Response:
    return web.json_response({"detail": message}, status=400)


def _not_found(message: str) -> web.Response:
    return web.json_response({"detail": message}, status=404)


def _conflict(message: str) -> web.Response:
    return web.json_response({"detail": message}, status=409)


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


async def page_index(request: web.Request) -> web.Response:
    return web.Response(
        text=_render_template("index.html", title="MockImages 관리"),
        content_type="text/html",
    )


async def page_stations(request: web.Request) -> web.Response:
    return web.Response(
        text=_render_template("stations.html", title="MockImages — 개소 관리"),
        content_type="text/html",
    )


async def page_media(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.Response(
        text=_render_template(
            "media.html",
            title=f"MockImages — {name} 파일",
            station_name=name,
        ),
        content_type="text/html",
    )


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------


async def api_stations_list(request: web.Request) -> web.Response:
    s = _state(request)
    rows = await meta_db.list_stations(s.pool)
    rejected = s.stats.rejected_per_station
    enriched = []
    for r in rows:
        d = _json_safe(r)
        d["rejected_count"] = rejected.get(r["station_name"], 0)
        # file count
        files = await meta_db.list_media(s.pool, r["station_name"])
        d["file_count"] = len(files)
        last = max((f.get("last_sent_at") for f in files if f.get("last_sent_at")), default=None)
        d["last_sent_at"] = last.isoformat() if last else None
        enriched.append(d)
    return web.json_response(enriched)


async def api_stations_create(request: web.Request) -> web.Response:
    s = _state(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _bad_request("invalid JSON")
    name = (body.get("station_name") or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return _bad_request("invalid station_name")
    label = body.get("label")

    try:
        row = await meta_db.insert_station(
            s.pool, station_name=name, label=label,
        )
    except Exception as exc:
        # asyncpg.UniqueViolationError, etc.
        return _conflict(str(exc))

    # ensure folder exists
    try:
        meta_db.safe_station_dir(s.media_root, name).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("failed to create station folder: %s", exc)

    return web.json_response(_json_safe(row), status=201)


async def api_stations_patch(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _bad_request("invalid JSON")
    label = body.get("label")
    status = body.get("status")
    if status is not None and status not in ("active", "paused"):
        return _bad_request("status must be active|paused")
    row = await meta_db.update_station(
        s.pool, name, label=label, status=status,
    )
    if row is None:
        return _not_found(f"station not found: {name}")
    return web.json_response(_json_safe(row))


async def api_stations_delete(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    ok = await meta_db.delete_station(s.pool, name)
    if not ok:
        return _not_found(f"station not found: {name}")
    try:
        meta_db.remove_station_folder(s.media_root, name)
    except Exception as exc:
        logger.warning("failed to remove station folder: %s", exc)
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# Media files
# ---------------------------------------------------------------------------


async def api_files_list(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    rows = await meta_db.list_media(s.pool, name)
    return web.json_response([_json_safe(r) for r in rows])


async def api_files_upload(request: web.Request) -> web.Response:
    """Multipart upload — supports multiple files in a single request."""
    s = _state(request)
    name = request.match_info["name"]
    if not await meta_db.get_station(s.pool, name):
        return _not_found(f"station not found: {name}")

    try:
        folder = meta_db.safe_station_dir(s.media_root, name)
    except ValueError as exc:
        return _bad_request(str(exc))
    folder.mkdir(parents=True, exist_ok=True)

    reader = await request.multipart()
    saved: list[str] = []
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name not in ("file", "files", "files[]"):
            await part.read(decode=False)
            continue
        filename = part.filename
        if not filename:
            await part.read(decode=False)
            continue
        try:
            target = meta_db.safe_file_path(s.media_root, name, filename)
        except ValueError as exc:
            await part.read(decode=False)
            return _bad_request(str(exc))
        size = 0
        with open(target, "wb") as out:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        await meta_db.upsert_media(
            s.pool, station_name=name, filename=filename, size_bytes=size,
        )
        saved.append(filename)

    return web.json_response({"saved": saved, "count": len(saved)})


async def api_files_delete(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    filename = request.match_info["filename"]
    try:
        path = meta_db.safe_file_path(s.media_root, name, filename)
    except ValueError as exc:
        return _bad_request(str(exc))
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("failed to unlink %s: %s", path, exc)
    deleted = await meta_db.delete_media(s.pool, name, filename)
    if not deleted:
        return _not_found(f"media not found: {name}/{filename}")
    return web.json_response({"ok": True})


async def api_files_reset(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    filename = request.match_info["filename"]
    ok = await meta_db.reset_send_count(s.pool, name, filename)
    if not ok:
        return _not_found(f"media not found: {name}/{filename}")
    return web.json_response({"ok": True})


async def api_rescan(request: web.Request) -> web.Response:
    s = _state(request)
    name = request.match_info["name"]
    if not await meta_db.get_station(s.pool, name):
        return _not_found(f"station not found: {name}")
    result = await meta_db.rescan_station_folder(s.pool, name, s.media_root)
    return web.json_response(result)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


async def api_runtime_status(request: web.Request) -> web.Response:
    s = _state(request)
    return web.json_response({
        "started_at": s.status.started_at_iso,
        "cycle": s.status.cycle,
        "station_name": s.status.station_name,
        "station_idx": s.status.station_idx,
        "station_count": s.status.station_count,
        "file_idx_in_station": s.status.file_idx_in_station,
        "files_in_current_stay": s.status.files_in_current_stay,
        "current_filename": s.status.current_filename,
        "current_chunk": s.status.current_chunk,
        "current_chunk_total": s.status.current_chunk_total,
        "last_file_at": s.status.last_file_at_iso,
        "is_paused": s.gate.paused,

        "stats": {
            "cycles_completed": s.stats.cycles_completed,
            "files_sent": s.stats.files_sent,
            "files_rejected": s.stats.files_rejected,
            "chunks_sent": s.stats.chunks_sent,
            "ack_count": s.stats.ack_count,
            "error_count": s.stats.error_count,
            "last_error": s.stats.last_error,
            "rejected_per_station": s.stats.rejected_per_station,
        },

        "config": {
            "mode": s.cfg.mode,
            "out_fps": s.cfg.out_fps,
            "resize_w": s.cfg.resize_w,
            "resize_h": s.cfg.resize_h,
            "jpeg_quality": s.cfg.jpeg_quality,
            "output_format": s.cfg.output_format,
            "chunk_size_kb": s.cfg.chunk_size_kb,
            "interval_sec": s.cfg.interval_sec,
            "cycle_interval_sec": s.cfg.cycle_interval_sec,
            "loop": s.cfg.loop,
            "stay_mode": s.cfg.stay_mode,
            "files_per_stay": s.cfg.files_per_stay,
            "stay_seconds": s.cfg.stay_seconds,
            "file_order": s.cfg.file_order,
            "amr_id": s.cfg.amr_id,
        },
    })


async def api_runtime_pause(request: web.Request) -> web.Response:
    _state(request).gate.pause()
    return web.json_response({"ok": True, "paused": True})


async def api_runtime_resume(request: web.Request) -> web.Response:
    _state(request).gate.resume()
    return web.json_response({"ok": True, "paused": False})


async def api_runtime_skip_file(request: web.Request) -> web.Response:
    _state(request).skip.skip_file = True
    return web.json_response({"ok": True})


async def api_runtime_skip_station(request: web.Request) -> web.Response:
    _state(request).skip.skip_station = True
    return web.json_response({"ok": True})


async def api_runtime_restart_cycle(request: web.Request) -> web.Response:
    _state(request).skip.restart_cycle = True
    return web.json_response({"ok": True})


async def api_runtime_config(request: web.Request) -> web.Response:
    s = _state(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _bad_request("invalid JSON")

    cfg = s.cfg
    # Validate + apply each field individually
    try:
        if "mode" in body:
            v = body["mode"]
            if v not in ("raw", "transcode"):
                raise ValueError("mode must be raw|transcode")
            cfg.mode = v
        if "out_fps" in body:
            cfg.out_fps = max(1, int(body["out_fps"]))
        if "resize_w" in body:
            cfg.resize_w = int(body["resize_w"]) if body["resize_w"] else None
        if "resize_h" in body:
            cfg.resize_h = int(body["resize_h"]) if body["resize_h"] else None
        if "jpeg_quality" in body:
            q = int(body["jpeg_quality"])
            if not 1 <= q <= 100:
                raise ValueError("jpeg_quality must be 1..100")
            cfg.jpeg_quality = q
        if "output_format" in body:
            v = body["output_format"]
            if v not in ("mp4", "jpeg_seq", "jpeg"):
                raise ValueError("output_format must be mp4|jpeg_seq|jpeg")
            cfg.output_format = v
        if "chunk_size_kb" in body:
            v = int(body["chunk_size_kb"])
            if not 4 <= v <= 4096:
                raise ValueError("chunk_size_kb must be 4..4096")
            cfg.chunk_size_kb = v
        if "interval_sec" in body:
            cfg.interval_sec = max(0.0, float(body["interval_sec"]))
        if "cycle_interval_sec" in body:
            cfg.cycle_interval_sec = max(0.0, float(body["cycle_interval_sec"]))
        if "loop" in body:
            cfg.loop = bool(body["loop"])
        if "stay_mode" in body:
            v = body["stay_mode"]
            if v not in ("all_files", "fixed_count", "time_based"):
                raise ValueError("stay_mode must be all_files|fixed_count|time_based")
            cfg.stay_mode = v
        if "files_per_stay" in body:
            cfg.files_per_stay = (
                int(body["files_per_stay"]) if body["files_per_stay"] else None
            )
        if "stay_seconds" in body:
            cfg.stay_seconds = (
                float(body["stay_seconds"]) if body["stay_seconds"] else None
            )
        if "file_order" in body:
            v = body["file_order"]
            if v not in ("last_sent_asc", "alphabetical", "uploaded", "random"):
                raise ValueError("file_order invalid")
            cfg.file_order = v
        if "amr_id" in body:
            cfg.amr_id = str(body["amr_id"])
    except (ValueError, TypeError) as exc:
        return _bad_request(str(exc))

    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(state: AppState) -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024 * 1024)  # 8 GiB upload max
    app["state"] = state

    app.router.add_static("/static/", path=str(_STATIC), name="static")

    app.router.add_get("/", page_index)
    app.router.add_get("/stations", page_stations)
    app.router.add_get(r"/stations/{name}", page_media)

    app.router.add_get("/api/stations", api_stations_list)
    app.router.add_post("/api/stations", api_stations_create)
    app.router.add_patch(r"/api/stations/{name}", api_stations_patch)
    app.router.add_delete(r"/api/stations/{name}", api_stations_delete)

    app.router.add_get(r"/api/stations/{name}/files", api_files_list)
    app.router.add_post(r"/api/stations/{name}/files", api_files_upload)
    app.router.add_delete(r"/api/stations/{name}/files/{filename}", api_files_delete)
    app.router.add_post(r"/api/stations/{name}/files/{filename}/reset", api_files_reset)
    app.router.add_post(r"/api/stations/{name}/rescan", api_rescan)

    app.router.add_get("/api/runtime/status", api_runtime_status)
    app.router.add_post("/api/runtime/pause", api_runtime_pause)
    app.router.add_post("/api/runtime/resume", api_runtime_resume)
    app.router.add_post("/api/runtime/skip-file", api_runtime_skip_file)
    app.router.add_post("/api/runtime/skip-station", api_runtime_skip_station)
    app.router.add_post("/api/runtime/restart-cycle", api_runtime_restart_cycle)
    app.router.add_patch("/api/runtime/config", api_runtime_config)

    return app


async def start_admin_server(
    *, host: str, port: int, state: AppState
) -> web.AppRunner:
    app = build_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
