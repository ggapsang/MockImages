-- =====================================================================
-- MockImages 메타 DB 스키마
-- =====================================================================
-- mock-images-postgres 컨테이너가 첫 부팅 시 자동 실행한다.
-- 본 DB는 SocketDaim의 gateway_db와 무관하다 (일방 송신 원칙).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- station -------------------------------------------------------------
-- PK = station_name (디렉토리명, 사용자 친화 식별자)
-- station_id (UUID) = SocketDaim Ingestion Gateway에 송신할 station_id.
--                     수동 입력 또는 자동 생성 (Admin UI POST /api/stations).
CREATE TABLE IF NOT EXISTS station (
    station_name  VARCHAR(64)   PRIMARY KEY,
    station_id    UUID          NOT NULL UNIQUE  DEFAULT gen_random_uuid(),
    label         VARCHAR(128),
    status        VARCHAR(16)   NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'paused')),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- media_file ----------------------------------------------------------
-- station 폴더 내 영상/이미지 파일 매핑.  Admin UI 업로드 또는 호스트 폴더에
-- 직접 떨어뜨린 후 [Rescan]으로 동기화.
CREATE TABLE IF NOT EXISTS media_file (
    id            SERIAL        PRIMARY KEY,
    station_name  VARCHAR(64)   NOT NULL
                  REFERENCES station(station_name) ON DELETE CASCADE,
    filename      VARCHAR(255)  NOT NULL,
    file_type     VARCHAR(16)   NOT NULL,            -- 'video' / 'image'
    size_bytes    BIGINT,
    uploaded_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_sent_at  TIMESTAMPTZ,
    send_count    INTEGER       NOT NULL DEFAULT 0,
    UNIQUE (station_name, filename)
);

CREATE INDEX IF NOT EXISTS idx_media_file_station ON media_file(station_name);
