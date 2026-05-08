"""Source bytes → bytes-to-send dispatch.

RAW mode: file as-is.
TRANSCODE mode: re-encode via OpenCV.

Returned bytes are then handed to the chunker.  For OUTPUT_FORMAT='jpeg_seq'
with JPEG_SEQ_MODE='per_frame' we'd need to send a separate VIDEO_CHUNK
sequence per frame — Phase 2.  For now `jpeg_seq` collapses all frames
into a single concatenated MJPEG-ish bytestream (downstream parses it
out-of-band; or you can use 'jpeg' for a single still image).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np

from .frame_extractor import iter_frames_resampled, read_image_bgr


def encode_for_send(
    path: Path,
    *,
    file_type: str,
    mode: str,
    output_format: str,
    out_fps: int,
    resize_w: int | None,
    resize_h: int | None,
    jpeg_quality: int,
) -> bytes:
    """Return the bytestream that the sender should chunk and transmit.

    `file_type` is 'video' / 'image' / 'unknown' from media_file row.
    """
    if mode == "raw":
        return path.read_bytes()

    # TRANSCODE
    if file_type == "image":
        img = read_image_bgr(path, resize_w, resize_h)
        return _encode_jpeg(img, jpeg_quality)

    if file_type == "video":
        if output_format == "jpeg" or output_format == "jpeg_seq":
            return _encode_video_to_jpeg_concat(
                path, out_fps=out_fps,
                resize_w=resize_w, resize_h=resize_h,
                jpeg_quality=jpeg_quality,
            )
        if output_format == "mp4":
            return _encode_video_to_mp4(
                path, out_fps=out_fps,
                resize_w=resize_w, resize_h=resize_h,
            )

    # Unknown file type or output_format → fall back to raw bytes
    return path.read_bytes()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _encode_jpeg(frame: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("cv2.imencode (.jpg) failed")
    return buf.tobytes()


def _encode_video_to_jpeg_concat(
    path: Path,
    *,
    out_fps: int,
    resize_w: int | None,
    resize_h: int | None,
    jpeg_quality: int,
) -> bytes:
    """Naive: concatenate JPEG-encoded frames.  Useful as a deterministic
    transcode payload; not a valid video container but mirrors the
    OUTPUT_FORMAT=jpeg_seq behaviour described in dev_plan §4.2."""
    parts: list[bytes] = []
    for frame in iter_frames_resampled(
        path, target_fps=out_fps, resize_w=resize_w, resize_h=resize_h,
    ):
        parts.append(_encode_jpeg(frame, jpeg_quality))
    return b"".join(parts)


def _encode_video_to_mp4(
    path: Path,
    *,
    out_fps: int,
    resize_w: int | None,
    resize_h: int | None,
) -> bytes:
    """Re-encode to mp4 with the given fps/resize.  Uses a temp file
    because cv2.VideoWriter only writes to disk; we read it back as bytes."""
    import tempfile

    # Probe first frame to get the size if no resize was requested
    cap = cv2.VideoCapture(str(path))
    try:
        ok, first = cap.read()
        if not ok or first is None:
            return b""
        if resize_w and resize_h:
            target_size = (int(resize_w), int(resize_h))
        else:
            h, w = first.shape[:2]
            target_size = (w, h)
    finally:
        cap.release()

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(tmp_path), fourcc, float(out_fps), target_size,
        )
        try:
            for frame in iter_frames_resampled(
                path, target_fps=out_fps,
                resize_w=resize_w, resize_h=resize_h,
            ):
                writer.write(frame)
        finally:
            writer.release()
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
