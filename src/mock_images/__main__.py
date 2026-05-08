"""Module entry: `python -m mock_images`.

Boots:
- meta DB pool
- aiohttp admin server (port 8081)
- send loop (round-robin, TCP to ingestion-gw)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .admin.server import start_admin_server
from .config import get_settings
from .loop import run_main_loop
from .meta_db import create_pool
from .runtime import (
    AppState,
    PauseGate,
    RuntimeConfig,
    RuntimeStats,
    RuntimeStatus,
    SkipFlags,
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


async def _main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("mock_images")
    logger.info(
        "starting MockImages target=%s:%d media_dir=%s",
        settings.ingestion_host, settings.ingestion_port, settings.media_dir,
    )

    pool = await create_pool(settings.meta_db_url)

    state = AppState(
        settings=settings,
        cfg=RuntimeConfig.from_settings(settings),
        stats=RuntimeStats(),
        status=RuntimeStatus(),
        gate=PauseGate(paused=False),
        skip=SkipFlags(),
        pool=pool,
        media_root=Path(settings.media_dir),
    )

    runner = await start_admin_server(
        host=settings.admin_host,
        port=settings.admin_port,
        state=state,
    )
    logger.info("admin server listening on %s:%d", settings.admin_host, settings.admin_port)

    try:
        await run_main_loop(state)
    finally:
        await runner.cleanup()
        await pool.close()


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logging.getLogger("mock_images").info("shutdown")


if __name__ == "__main__":
    main()
