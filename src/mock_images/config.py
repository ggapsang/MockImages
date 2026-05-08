"""MockImages settings loaded from environment variables.

Prefix: none (variable names are flat to match dev_plan §8.3 verbatim).
Compose injects all of these explicitly.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -- TCP target --------------------------------------------------------
    ingestion_host: str = "ingestion-gw"
    ingestion_port: int = 9000
    protocol: str = "standard"

    # -- Meta DB -----------------------------------------------------------
    meta_db_url: str = (
        "postgresql://mock:mockpw@mock-images-postgres:5432/mock_images"
    )

    # -- Media root (host bind-mount) -------------------------------------
    media_dir: str = "/media"

    # -- Identity ----------------------------------------------------------
    amr_id: str = "mock-amr-01"

    # -- Cycle / loop ------------------------------------------------------
    loop: bool = True
    interval_sec: float = 5.0
    cycle_interval_sec: float = 0.0
    startup_delay_sec: float = 2.0

    # -- Stay mode ---------------------------------------------------------
    stay_mode: str = "all_files"        # all_files | fixed_count | time_based
    files_per_stay: int | None = None
    stay_seconds: float | None = None
    file_order: str = "last_sent_asc"   # last_sent_asc | alphabetical | uploaded | random

    # -- Encoding / chunking ----------------------------------------------
    mode: str = "raw"                    # raw | transcode
    out_fps: int = 15
    resize_w: int | None = None
    resize_h: int | None = None
    jpeg_quality: int = 85
    output_format: str = "mp4"           # mp4 | jpeg_seq | jpeg
    jpeg_seq_mode: str = "single_video"  # single_video | per_frame
    chunk_size_kb: int = 512

    # -- Admin UI ----------------------------------------------------------
    admin_host: str = "0.0.0.0"
    admin_port: int = 8081

    # -- Logging -----------------------------------------------------------
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
