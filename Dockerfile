# syntax=docker/dockerfile:1
# Build context must be the parent directory containing both
# SocketDaim/ and MockImages/ (see docker-compose.yml: context: ..)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# -- Install gw_proto shared library from SocketDaim ----------------------
COPY SocketDaim/libs/gw_proto /tmp/gw_proto
RUN pip install /tmp/gw_proto && rm -rf /tmp/gw_proto

# -- Install runtime dependencies ------------------------------------------
# opencv-python-headless: TRANSCODE 모드용 (RAW 모드만 쓸 때도 import만 됨)
# numpy: opencv 의존
RUN pip install \
    "asyncpg>=0.30" \
    "pydantic-settings>=2.3" \
    "aiohttp>=3.9" \
    "opencv-python-headless>=4.10" \
    "numpy>=1.26"

# -- Copy mock_images package ---------------------------------------------
COPY MockImages/pyproject.toml /app/pyproject.toml
COPY MockImages/src/mock_images /app/mock_images

# -- Media mount point -----------------------------------------------------
RUN mkdir -p /media

EXPOSE 8081

CMD ["python", "-m", "mock_images"]
