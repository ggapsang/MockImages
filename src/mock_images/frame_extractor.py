"""OpenCV-backed frame extraction with fps/resize sampling.

Used by encoder.py when MODE=transcode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import cv2  # type: ignore[import-untyped]
import numpy as np


def open_capture(path: Path) -> "cv2.VideoCapture":
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open: {path}")
    return cap


def native_fps(cap) -> float:
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps is None or fps <= 0:
        return 30.0
    return float(fps)


def iter_frames_resampled(
    path: Path,
    *,
    target_fps: int,
    resize_w: int | None,
    resize_h: int | None,
) -> Iterator[np.ndarray]:
    """Yield BGR frames re-sampled to ``target_fps`` and optionally resized.

    Down-sampling only — if the source fps is lower than target, each
    frame is yielded once (we don't fabricate frames).
    """
    cap = open_capture(path)
    try:
        src_fps = native_fps(cap)
        ratio = max(1.0, src_fps / max(1, target_fps))
        accum = 0.0
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if frame_idx == 0 or accum >= 1.0:
                if resize_w and resize_h:
                    frame = cv2.resize(
                        frame, (resize_w, resize_h), interpolation=cv2.INTER_AREA,
                    )
                yield frame
                accum -= 1.0
            accum += 1.0 / ratio
            frame_idx += 1
    finally:
        cap.release()


def read_image_bgr(path: Path, resize_w: int | None, resize_h: int | None) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"failed to decode image: {path}")
    if resize_w and resize_h:
        img = cv2.resize(img, (resize_w, resize_h), interpolation=cv2.INTER_AREA)
    return img
