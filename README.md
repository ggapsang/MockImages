# MockImages

AMR이 촬영한 영상·이미지를 SocketDaim Ingestion Gateway로 송신하는 동작을 흉내내는 mock 송신기. `MockSensor`(분진 센서 mock)의 자매 프로젝트입니다.

설계 문서: [refs/MockImages_dev_plan.md](refs/MockImages_dev_plan.md) v0.3
구현 플랜: [`socketdaim-adminui-shiny-patterson.md`](../) (`MockImages 구현 + SocketDaim Gateway 확장 플랜`)

---

## 동작 요약

- 메타 DB(자체 PostgreSQL)에 등록된 active station을 라운드로빈(체류형)으로 순회
- 각 station에 머무는 동안 station 폴더 안의 영상·이미지 파일을 큐 정렬에 따라 송신
- TCP `VIDEO_CHUNK` + `VIDEO_COMPLETE` (gw_proto) 사용
- **일방 송신 원칙**: SocketDaim의 `gateway_db`를 조회하지 않음. 자체 메타 DB의 `station_id`(UUID)를 헤더에 그대로 박아 보냄. 등록 여부 검증은 수신측이 담당
- Admin UI(`http://localhost:8081/`)에서 station/파일 CRUD, 런타임 파라미터 조정, 일시정지/스킵 제어

## 동봉된 컨테이너

| 서비스 | 컨테이너 | 호스트 포트 | 비고 |
|--------|---------|-------------|------|
| `mock-images-postgres` | `mi-postgres` | **2347** → 5432 | 메타 DB (`mock_images`, user `mock`/`mockpw`) |
| `mock-images` | `mock-images` | **8081** | 송신기 + Admin UI |

## 전제 조건

SocketDaim compose가 먼저 올라와 있어야 합니다 (`socketdaim_gw-net` 네트워크 + `ingestion-gw` 컨테이너):

```bash
cd ~/projects/SocketDaim && docker compose up -d
```

또한 SocketDaim Gateway는 이번 변경(`video.source_format` 컬럼 + 확장 헤더 처리)이 적용된 빌드여야 합니다. 기존 dev DB라면 한 번:

```bash
docker exec -i sd-postgres psql -U postgres -d gateway_db \
    < ~/projects/SocketDaim/scripts/migrate_002_video_source_format.sql
```

## 실행

```bash
cd ~/projects/MockImages
docker compose up --build -d        # 백그라운드
docker compose up --build           # 포어그라운드 (로그 직접 봄)
docker compose down                 # 정지 (메타 DB 보존)
docker compose down -v              # 메타 DB 볼륨까지 삭제
```

Admin UI: <http://localhost:8081/>

## 시나리오 — UUID 동기화 워크플로 3가지

송신측(MockImages)과 수신측(SocketDaim)에 동일 `station_id`(UUID)를 두기 위한 흐름.

### A. MockImages 자동 생성 → SocketDaim 승인 (가장 자연스러움, 권장)

1. Admin UI → `[+ 개소 등록]`. `station_id` 칸 비우면 UUID v4 자동 생성
2. station 폴더(예: `media/FL-A01-NORTH/`)에 mp4/jpg 떨어뜨림 → `[폴더 재스캔]` 또는 Admin UI 업로드
3. MockImages가 다음 사이클에서 송신 시도 → SocketDaim이 unknown station으로 거부, 동시에 SocketDaim의 `station_request` 테이블에 `pending` 행 생성
4. SocketDaim Admin UI(<http://localhost:9108/>) `[대기 중]` 탭에서 그 UUID가 자동 등장
5. `[승인]` 버튼으로 station_name/location/amr_id/capture_cycle 입력 → 같은 UUID로 station 등록
6. 다음 사이클부터 정상 송신 (gateway `video` 테이블에 `amr_id`/`source_format` 채워짐)

### B. SocketDaim 먼저 등록 → UUID 복사

1. SocketDaim Admin UI에서 station 등록 → 생성된 UUID 복사
2. MockImages Admin UI에서 등록 시 그 UUID 수동 입력

### C. 거부 시나리오 (의도적 검증)

1. MockImages에 등록 후 SocketDaim에는 등록하지 않음
2. 송신 → 영구히 ERROR. 통계에 `rejected` 카운터 증가. SocketDaim의 `station_request` 행은 `attempts`만 누적
3. SocketDaim Admin UI의 `[기각]`으로 마킹 가능

## 송신 모드

| MODE | 동작 |
|------|------|
| `raw` (기본) | 디스크 파일을 그대로 청크 분할해서 송신 |
| `transcode` | OpenCV로 디코딩 후 재인코딩(fps/resize/JPEG quality 적용) |

전환은 Admin UI 대시보드의 런타임 설정에서. 변경값은 **다음 파일**부터 적용됩니다.

> TRANSCODE 모드는 OpenCV가 디코딩할 수 있는 정상 영상/이미지 파일을 전제로 합니다. 깨진 파일이나 수동으로 만든 더미 mp4는 디코딩 실패로 send_one_file이 예외를 일으킬 수 있습니다.

## STAY_MODE

| 값 | 동작 |
|----|------|
| `all_files` (기본) | station의 모든 파일 1회씩 송신 후 다음 station |
| `fixed_count` + `FILES_PER_STAY=N` | N개만 송신 |
| `time_based` + `STAY_SECONDS=T` | T초 동안 송신 (현재 파일 끝나면 이동) |

## 환경 변수 (compose에 모두 박혀 있음)

[docker-compose.yml](docker-compose.yml) `mock-images` 서비스 environment 블록 참조. 자세한 의미는 [refs/MockImages_dev_plan.md](refs/MockImages_dev_plan.md) §8.3.

## 디렉토리 / 파일

```
MockImages/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── init_db.sql                       # 메타 DB 스키마
├── media/                            # 호스트 bind mount, station 폴더 1depth
│   └── <station_name>/<filename>
└── src/mock_images/
    ├── config.py                     # 환경변수 (pydantic-settings)
    ├── meta_db.py                    # asyncpg, station/media_file CRUD + rescan
    ├── chunker.py                    # 바이트 → 청크
    ├── payload_builder.py            # VIDEO_CHUNK 헤더 + extension plug-in
    ├── frame_extractor.py            # OpenCV 디코딩 + fps/resize 샘플링
    ├── encoder.py                    # RAW / TRANSCODE 분기
    ├── sender.py                     # gw_proto.TcpClient 송신 + recv_loop
    ├── runtime.py                    # AppState/RuntimeConfig/Stats/Status/PauseGate
    ├── loop.py                       # 라운드로빈(체류형) 메인 루프
    ├── __main__.py                   # asyncio entrypoint
    └── admin/
        ├── server.py                 # aiohttp routes
        ├── templates/                # index.html / stations.html / media.html
        └── static/{css,js}/          # admin.css(공통) + mockimages.css(추가)
```

## 검증 (수동 e2e)

```bash
# 1. SocketDaim + MockImages 부팅 (전제)
cd ~/projects/SocketDaim && docker compose up -d
cd ~/projects/MockImages && docker compose up -d --build

# 2. station 등록 (자동 UUID)
curl -X POST http://localhost:8081/api/stations \
  -H 'Content-Type: application/json' \
  -d '{"station_name":"FL-A01-NORTH","label":"Fab A 북측"}'

# 3. 파일 업로드 (multipart) — 호스트의 임의 mp4를 사용
curl -X POST -F "files=@/tmp/test.mp4" \
  http://localhost:8081/api/stations/FL-A01-NORTH/files

# 4. 한 사이클 대기, 거부 확인
sleep 10
curl -s 'http://localhost:9108/admin/api/requests?status=pending' | jq

# 5. SocketDaim Admin에서 승인 (그 UUID 그대로)
PENDING=$(curl -s http://localhost:8081/api/stations | jq -r '.[0].station_id')
curl -X POST http://localhost:9108/admin/api/requests/$PENDING/approve \
  -H 'Content-Type: application/json' \
  -d '{"station_name":"FL-A01-NORTH","location_info":"Fab A 북측","amr_id":"mock-amr-01","capture_cycle":60}'

# 6. 다음 사이클에서 정상 송신 확인
sleep 12
docker exec sd-postgres psql -U postgres -d gateway_db \
  -c "SELECT video_id, amr_id, source_format FROM video ORDER BY created_at DESC LIMIT 3;"
```

기대 결과: `video` 테이블 행에 `amr_id='mock-amr-01'`, `source_format='mp4'` 채워짐.

## Out of scope (Phase 2)

- AMR 순찰 좌표 시뮬레이션 (`amr_position` extension)
- 시나리오 파일(YAML) 기반 송신
- 다중 AMR 동시 시뮬레이션 (현재는 단일 인스턴스 = 단일 TCP)
- 의도적 결함 주입 (malformed JSON 헤더, chunk_seq 누락 등)
- station_name PK rename UI

## 라이선스 / 메모

내부 mock·검증용. SocketDaim 플랜 v0.3 기준으로 만들어졌으며 수신측 프로토콜 변경 시 `gw_proto` 패키지 갱신 후 컨테이너 재빌드만 하면 됩니다.
