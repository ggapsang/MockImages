# MockImages 구현 계획서

> 작성일: 2026-05-07
> 위치: `c:\MockImages\` (신규 레포)
> 자매 프로젝트: [`c:\MockSensor\`](../MockSensor/) (분진 센서 mock)

---

## 1. 목적

AMR이 촬영하여 Ingestion Gateway로 송신하는 영상/이미지 데이터를 흉내내는 송신기. 실제 AMR 측 TCP 프로토콜 구현이 완료되기 전, Ingestion Gateway의 `VIDEO_CHUNK` / `VIDEO_COMPLETE` 수신 경로 및 후속 컨슈머(이상감지, YOLO)의 동작을 검증하는 데 사용한다.

`MockSensor`가 시계열 분진 농도를 생성-송신하는 시뮬레이터인 것과 달리, `MockImages`는 사용자가 사전 준비한 영상·이미지 파일을 그대로 또는 재인코딩하여 프로토콜에 맞게 청크 분할 송신하는 **에뮬레이터**에 가깝다.

### 1.1 일방 송신 원칙

MockImages는 **수신측의 상태를 참조하지 않는다.** 실제 AMR이 자기가 보유한 `station_id`로 SocketDaim에 일방 송신하는 것과 동일하게, MockImages도 자기 메타 DB의 `station_id`를 그대로 헤더에 담아 쏜다.

이 원칙은 두 시나리오를 모두 검증 가능하게 한다:

- **정상 시나리오**: 사용자가 SocketDaim에 등록한 UUID를 MockImages에도 동일하게 등록 → 정상 수신
- **거부 시나리오**: SocketDaim에 미등록된 UUID로 송신 → SocketDaim의 `Unknown station` 에러 응답 동작 검증

수신측 거부 동작 자체가 검증 대상의 일부이므로, MockImages가 사전에 SocketDaim DB를 조회해 자체 검열하는 것은 부적절하다.

### 1.2 MockSensor와의 차이

- 다중 station에 라운드로빈 송신 (체류형, 실제 AMR 순찰 패턴 모사)
- station 등록·파일 매핑이 admin UI에서 직접 관리 (DB SQL 직접 조작 불요)
- 자체 PostgreSQL 메타 DB 보유 (등록 station, 파일 매핑 메타데이터)

---

## 2. 책임 범위

### 2.1 책임

- Admin UI에서 station 등록·삭제·관리 (`station_id` UUID 포함)
- Admin UI에서 station별 영상·이미지 파일 업로드·삭제 (드래그앤드롭 지원)
- 등록된 station을 라운드로빈(체류형)으로 순회하며 각 station의 파일 큐를 송신
- `gw_proto` 라이브러리 client 측 사용 (Ingestion Gateway와 동일 프로토콜 스택)
- 출력 fps·해상도·압축 품질·청크 크기 등 송신 파라미터 제어
- `VIDEO_CHUNK` JSON 헤더에 확장 메타데이터(amr_position 등) 추가 가능

### 2.2 비책임 (명시적 제외)

- **SocketDaim Gateway DB의 station 등록은 본 송신기의 책임이 아님.** SocketDaim 측에 별도 등록 UI가 만들어지며, 사용자가 양쪽(MockImages + SocketDaim)에 동일 `station_id`(UUID)로 등록할 책임을 짐.
- **SocketDaim DB의 station 목록을 읽지 않음.** 송신 전 검증 없이 메타 DB의 UUID를 그대로 송신. 등록 여부는 수신측이 검사.
- 영상 콘텐츠 자체의 합성·생성. 입력은 사용자가 직접 촬영·수집한 파일을 그대로 사용
- 분석 결과 검증 (Gateway 수신 후 처리는 본 송신기의 관심사 밖)

### 2.3 SocketDaim과의 책임 경계

```
[MockImages 메타 DB]                   [SocketDaim gateway_db]
  station 테이블                         station 테이블
  - 등록 UI: MockImages admin            - 등록 UI: SocketDaim 측 별도 구현
  - 용도: 송신 라우팅 + 파일 매핑         - 용도: 수신 검증

  ┌──────────────────────────────────────────┐
  │ 사용자가 양쪽에 동일 station_id(UUID) 등록   │
  │ (정상 시나리오)                              │
  │                                              │
  │ 또는 의도적으로 한쪽만 등록                   │
  │ (거부 동작 검증 시나리오)                     │
  └──────────────────────────────────────────┘

           송신 시 (일방 송신)
  ┌─────────────────┐                ┌─────────────────┐
  │ MockImages       │   TCP 송신      │ Ingestion        │
  │ (메타 DB의       │ ──────────────▶ │ Gateway          │
  │  station_id를    │                │ (수신 후 자체 DB │
  │  그대로 헤더에)  │  ◀── ACK/ERROR │  로 등록 여부 검사)│
  └─────────────────┘                └─────────────────┘

  MockImages는 gateway_db에 접근하지 않음.
```

---

## 3. 입력 미디어 및 메타데이터

### 3.1 디렉토리 구조

station 폴더를 1depth로 둔다. videos/·images/ 분리는 폐기.

```
media/                              (host volume mount)
├── FL-A01-NORTH/
│   ├── patrol_001.mp4
│   ├── patrol_002.mp4
│   └── snapshot_a.jpg
├── FL-A02-SOUTH/
│   └── patrol_001.mp4
├── FL-B01-EAST/
└── FL-C01-WEST/
```

- 영상·이미지 구분은 확장자로 판별
- 빈 폴더의 station은 송신 사이클에서 자동 스킵
- Admin UI 업로드 → 파일을 host의 해당 station 폴더에 저장 + 메타 DB에 record INSERT
- Admin UI 삭제 → 파일 + record 모두 제거
- 외부에서 파일을 직접 폴더에 떨어뜨려도 됨. Admin UI의 `Rescan` 버튼으로 메타 DB와 동기화

폴더명은 `station_name`을 사용. `station_id`(UUID)는 별도로 메타 DB에 보관.

### 3.2 메타 DB 스키마

```sql
-- station 테이블
CREATE TABLE station (
    station_name  VARCHAR(64)   PRIMARY KEY,                -- 디렉토리명, 사용자 친화 식별자
    station_id    UUID          NOT NULL UNIQUE,             -- 송신 헤더에 박힐 UUID
    label         VARCHAR(128),                              -- 사용자 친화 레이블
    status        VARCHAR(16)   NOT NULL DEFAULT 'active',   -- active / paused
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- station별 미디어 파일 매핑
CREATE TABLE media_file (
    id            SERIAL        PRIMARY KEY,
    station_name  VARCHAR(64)   NOT NULL
                  REFERENCES station(station_name) ON DELETE CASCADE,
    filename      VARCHAR(255)  NOT NULL,                -- station 폴더 내 파일명
    file_type     VARCHAR(16)   NOT NULL,                -- 'video' / 'image'
    size_bytes    BIGINT,
    uploaded_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_sent_at  TIMESTAMPTZ,
    send_count    INTEGER       NOT NULL DEFAULT 0,
    UNIQUE (station_name, filename)
);

CREATE INDEX idx_media_file_station ON media_file(station_name);
```

`station.status='paused'`인 station은 사이클에서 스킵.

### 3.3 station 등록 시 station_id 결정

Admin UI의 station 등록 폼에서 `station_id` 입력은 두 가지 방식 지원:

| 방식 | 동작 | 용도 |
|---|---|---|
| **수동 입력** | 사용자가 UUID 직접 입력 (포맷 검증) | SocketDaim 측에서 등록한 UUID를 복사 → 정상 송신 시나리오 |
| **자동 생성** | 입력 비우면 UUID v4 자동 생성 | SocketDaim에 미등록된 UUID로 쏨 → 거부 동작 검증 시나리오 |

자동 생성된 UUID도 사용자가 등록 후 SocketDaim에 동일하게 옮겨 등록하면 정상 시나리오로 전환 가능.

---

## 4. 송신 모드

### 4.1 RAW 모드 (기본값)

파일을 디스크에 있는 그대로 읽어 청크 분할만 수행. fps·해상도·품질 변경 불가 (원본 그대로).

### 4.2 TRANSCODE 모드

OpenCV로 디코딩 → 출력 파라미터 적용 → 재인코딩 후 청크 분할.

| 적용 파라미터 | 영상 | 이미지 |
|---|---|---|
| `OUT_FPS` | 출력 프레임레이트 | N/A |
| `RESIZE_W`, `RESIZE_H` | 리사이즈 | 리사이즈 |
| `JPEG_QUALITY` | JPEG 시퀀스 인코딩 시 | JPEG 재인코딩 시 |
| `OUTPUT_FORMAT` | `mp4` / `jpeg_seq` | `jpeg` |

`OUTPUT_FORMAT=jpeg_seq` + `JPEG_SEQ_MODE=per_frame` 조합 시 영상의 각 프레임을 별도 video_id로 분리 송신.

---

## 5. 전송 프로토콜

### 5.1 공용 라이브러리 사용

`gw_proto` 라이브러리의 client 측(`transport/client.py`, `codec/standard.py`)을 그대로 사용. `MockSensor`와 동일 정책.

### 5.2 사용 메시지 타입

| 메시지 | 코드 | 사용 시점 |
|---|---|---|
| `VIDEO_CHUNK` | `0x0001` | 파일을 청크로 분할한 각 조각 송신 |
| `VIDEO_COMPLETE` | `0x0002` | 한 파일의 모든 청크 송신 완료 시 1회 |
| `HEARTBEAT` | `0x0F00` | 30초 간격 keep-alive |

상세 페이로드 스펙은 `gw_protocol_spec.md` §4.2~§4.4 참조.

### 5.3 VIDEO_CHUNK 헤더 필드

필수:

```json
{
  "video_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "chunk_seq": 0,
  "total_chunks": 5,
  "station_id": "2595693b-6142-49d7-9f13-0bb72d897ca6",
  "captured_at": "2026-05-07T10:00:00+00:00"
}
```

`station_id`는 메타 DB의 `station.station_id` 값을 그대로 사용. **송신 전 검증 없음**.

확장 (payload extension plug-in으로 주입):

```json
{
  "amr_id": "mock-amr-01",
  "amr_position": {"x": 12.5, "y": 8.3, "heading": 90.0},
  "source_file": "FL-A01-NORTH/patrol_001.mp4",
  "encoding": {"mode": "transcode", "out_fps": 15, "jpeg_quality": 85}
}
```

### 5.4 ACK / ERROR 처리

송신 후 수신측 응답에 따른 동작:

| 응답 | MockImages 동작 |
|---|---|
| `ACK` | 정상. 다음 청크 또는 다음 파일 송신 |
| `ERROR` (예: `Unknown station`) | 로그 기록 + 통계 카운터 증가. 현재 파일 송신 중단 후 다음 파일로 진행. station은 사이클에서 스킵하지 않음 (다음 사이클에 다시 시도) |

거부 시나리오 검증 중인 station도 정상 station과 동일한 사이클로 송신을 계속 시도한다. Admin UI에서 거부 횟수가 통계로 노출됨.

---

## 6. 라운드로빈 동작 (체류형)

### 6.1 사이클 정의

1 사이클 = 등록된 모든 active station을 한 번씩 순회하는 단위.

```
[Cycle N]
  Station A에 체류 → A의 파일 큐를 모두 1회씩 송신 → Station B로 이동
  Station B에 체류 → B의 파일 큐를 모두 1회씩 송신 → Station C로 이동
  ...
  마지막 station 끝 → 1 cycle 완료
[Cycle N+1]
  station 목록·파일 큐 재로드 → 처음부터 반복
```

`LOOP=true`인 한 무한 반복.

### 6.2 station 내 파일 송신 순서

기본값: `media_file` 테이블에서 `last_sent_at ASC NULLS FIRST, id ASC`로 정렬. 가장 오래 보낸 파일부터 보내고 한 번도 안 보낸 파일은 우선 송신.

옵션: `FILE_ORDER=alphabetical | uploaded | random`

### 6.3 체류 정책 옵션

| 옵션 | 동작 |
|---|---|
| `STAY_MODE=all_files` (기본) | 해당 station의 모든 파일을 1회씩 송신 후 이동 |
| `STAY_MODE=fixed_count` + `FILES_PER_STAY=N` | N개만 송신 후 이동 (큐가 짧으면 그만큼만) |
| `STAY_MODE=time_based` + `STAY_SECONDS=T` | T초 동안 송신, 시간 끝나면 현재 파일 송신 완료 후 이동 |

기본값(`all_files`)은 실제 AMR 동작에 가장 가까움.

### 6.4 사이클 간 휴지

`CYCLE_INTERVAL_SEC` 만큼 대기 후 다음 사이클 시작 (기본 0).

---

## 7. 동작 흐름

```mermaid
flowchart TD
    Start["MockImages 시작"]
    InitMeta["메타 DB 마이그레이션<br/>(station, media_file)"]
    LoadStations["메타 DB에서 active station 목록 로드<br/>(station_name + station_id)"]
    Connect["Ingestion Gateway TCP connect"]

    subgraph CycleLoop["Cycle (LOOP=true)"]
        subgraph StationLoop["등록 station 체류 순회"]
            LoadFiles["해당 station의 file 큐 로드<br/>(last_sent_at 기준 정렬)"]
            EmptyCheck{"파일 있음?"}
            Skip["스킵"]

            subgraph FileLoop["station 체류: 파일 큐 송신"]
                EncMode{"송신 모드"}
                Raw["RAW: 파일 바이트 그대로"]
                Trans["TRANSCODE: OpenCV 재인코딩"]
                Chunk["청크 분할"]
                Header["VIDEO_CHUNK 헤더 조립<br/>(메타 DB의 station_id 그대로 사용)"]
                Send["청크 순차 송신"]
                Resp{"응답?"}
                AckPath["ACK: 다음 청크"]
                ErrPath["ERROR: 로그 + 통계,<br/>현재 파일 중단"]
                Complete["VIDEO_COMPLETE + ACK"]
                Update["media_file.last_sent_at,<br/>send_count 갱신"]
                Wait["INTERVAL_SEC 대기"]
                StayCheck{"체류 종료 조건?<br/>(STAY_MODE에 따라)"}
            end

            Move["다음 station으로 이동"]
        end
        CycleEnd["사이클 종료"]
        CyclePause["CYCLE_INTERVAL_SEC 대기"]
    end

    Start --> InitMeta --> LoadStations --> Connect
    Connect --> CycleLoop
    CycleLoop --> StationLoop
    StationLoop --> LoadFiles --> EmptyCheck
    EmptyCheck -->|No| Skip --> Move
    EmptyCheck -->|Yes| FileLoop
    FileLoop --> EncMode
    EncMode -->|raw| Raw --> Chunk
    EncMode -->|transcode| Trans --> Chunk
    Chunk --> Header --> Send --> Resp
    Resp -->|ACK| AckPath --> Complete
    Resp -->|ERROR| ErrPath --> Wait
    Complete --> Update --> Wait --> StayCheck
    StayCheck -->|계속| FileLoop
    StayCheck -->|종료| Move
    Move -->|남은 station 존재| StationLoop
    Move -->|모든 station 완료| CycleEnd
    CycleEnd --> CyclePause --> CycleLoop
```

---

## 8. 컨테이너 구성

### 8.1 컨테이너 목록

| 컨테이너 | 역할 | 호스트 포트 |
|---|---|---|
| `mock-images` | 송신기 + Admin UI | `8081` (admin) |
| `mock-images-postgres` | 메타 DB (station, media_file) | `2347` |

`socketdaim_gw-net` external network에 join하여 Ingestion Gateway에 TCP 송신. **`gateway_db` 컨테이너에는 접근하지 않음.**

### 8.2 디렉토리 구조

```
MockImages/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── README.md
├── init_db.sql                     # 메타 DB 스키마
├── media/                          # host volume mount, station별 폴더
│   ├── FL-A01-NORTH/
│   ├── FL-A02-SOUTH/
│   └── ...
├── src/mock_images/
│   ├── __init__.py
│   ├── config.py                   # pydantic-settings, 환경변수 + 런타임 변경
│   ├── meta_db.py                  # asyncpg, station/media_file CRUD
│   ├── file_queue.py               # station별 파일 큐 (last_sent_at 정렬)
│   ├── frame_extractor.py          # OpenCV 영상 디코딩, fps 샘플링, resize
│   ├── encoder.py                  # RAW / TRANSCODE 분기
│   ├── chunker.py                  # 바이트스트림 → 청크
│   ├── payload_builder.py          # VIDEO_CHUNK 헤더 dict 조립 (extension plug-in)
│   ├── sender.py                   # gw_proto client, VIDEO_CHUNK + COMPLETE
│   ├── runtime.py                  # 진행 상태, PauseGate
│   ├── admin/
│   │   ├── server.py               # aiohttp HTTP 서버
│   │   ├── api/
│   │   │   ├── stations.py         # station CRUD endpoint
│   │   │   ├── media.py            # 파일 업로드/삭제 endpoint
│   │   │   └── runtime.py          # pause/resume/skip 등
│   │   ├── static/
│   │   └── templates/
│   │       ├── index.html          # 대시보드
│   │       ├── stations.html       # station 관리
│   │       └── media.html          # station별 파일 관리
│   └── main.py                     # asyncio entrypoint
└── tests/
    ├── test_meta_db.py
    ├── test_file_queue.py
    ├── test_chunker.py
    ├── test_payload_builder.py
    └── test_encoder.py
```

`station_resolver.py`는 더 이상 존재하지 않음 (gateway_db 조회 폐기).

### 8.3 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `INGESTION_HOST` | `ingestion-gw` | Gateway 호스트명 |
| `INGESTION_PORT` | `9000` | Gateway TCP 포트 |
| `PROTOCOL` | `standard` | 코덱 선택 (gw_proto) |
| `META_DB_URL` | `postgresql://mock:mockpw@mock-images-postgres:5432/mock_images` | 자체 메타 DB |
| `MEDIA_DIR` | `/media` | 컨테이너 내 미디어 루트 (host volume mount 지점) |
| `AMR_ID` | `mock-amr-01` | 확장 payload용 AMR 식별자 |
| `LOOP` | `true` | 사이클 무한 반복 |
| `MODE` | `raw` | `raw` / `transcode` |
| `OUT_FPS` | `15` | transcode 시 출력 fps |
| `RESIZE_W`, `RESIZE_H` | `null` | transcode 시 리사이즈 (null=원본) |
| `JPEG_QUALITY` | `85` | 1~100 |
| `OUTPUT_FORMAT` | `mp4` | `mp4` / `jpeg_seq` (transcode 시) |
| `JPEG_SEQ_MODE` | `single_video` | `single_video` / `per_frame` |
| `CHUNK_SIZE_KB` | `512` | 청크 크기 (4~4096 권장) |
| `INTERVAL_SEC` | `5` | 파일 한 개 송신 후 다음 파일까지 대기 |
| `CYCLE_INTERVAL_SEC` | `0` | 사이클 종료 후 다음 사이클까지 대기 |
| `STAY_MODE` | `all_files` | `all_files` / `fixed_count` / `time_based` |
| `FILES_PER_STAY` | `null` | `fixed_count` 모드일 때 사용 |
| `STAY_SECONDS` | `null` | `time_based` 모드일 때 사용 |
| `FILE_ORDER` | `last_sent_asc` | `last_sent_asc` / `alphabetical` / `uploaded` / `random` |
| `STARTUP_DELAY_SEC` | `2` | 컨테이너 기동 직후 Gateway 대기 |
| `ADMIN_PORT` | `8081` | Admin UI 포트 |

`STATION_NAME`, `GATEWAY_DB_URL` 환경변수는 폐기.

### 8.4 로그 출력

```
[META_DB] migrated: station, media_file
[STATIONS] loaded 3 active stations from meta DB
[CYCLE 1] start (3 stations)
[STAY] FL-A01-NORTH (station_id=2595693b-...) 5 files, mode=all_files
[FILE 1/5] patrol_001.mp4 (8.3 MB) raw chunks=17 sent ACK=1.24s
[FILE 2/5] patrol_002.mp4 ...
[STAY] FL-A01-NORTH done, moving to next station
[STAY] FL-A02-SOUTH (station_id=8de27f12-...) 1 file
[FILE 1/1] patrol_001.mp4 ...
[STAY] FL-B01-EAST (station_id=99999999-...) 2 files
[FILE 1/2] sample.jpg ERROR: Unknown station: 99999999-...
[FILE 1/2] aborted, stats: rejected=1
[FILE 2/2] sample2.jpg ERROR: Unknown station: 99999999-...
[STAY] FL-B01-EAST done (2 rejected)
[CYCLE 1] complete in 3m21s, total_sent=6 rejected=2
[CYCLE 2] start
```

---

## 9. Admin UI

포트 `8081`. `MockSensor` admin (8080)과 동일 톤(흰 바탕·1px 테두리·시스템 폰트).

### 9.1 페이지 구성

#### `/` 대시보드
- 현재 사이클 번호, 진행 station, 파일 진행도(`3/5`), 청크 진행도(`12/17`)
- 전송 통계 (누적 파일, 누적 청크, **수신측 ERROR 수**, 평균 latency)
- 연결 상태, Pause / Running 상태
- `[Pause/Resume]`, `[Skip Current File]`, `[Skip Current Station]`, `[Restart Cycle]` 버튼
- 런타임 파라미터 폼 (`MODE`, `OUT_FPS`, `JPEG_QUALITY`, `CHUNK_SIZE_KB`, `INTERVAL_SEC`, `STAY_MODE` 등)

#### `/stations` station 관리
- 등록된 station 목록 (이름, **station_id (UUID)**, 레이블, status, 파일 수, 마지막 송신, 누적 ERROR 수)
- `[Add Station]` 폼: `station_name`, `station_id` (선택, 비우면 자동생성), `label`
- 행별 액션: `[Edit]`, `[Pause / Activate]`, `[Delete]`
- 삭제 시 cascade로 `media_file` 및 폴더 내 파일도 삭제 (확인 다이얼로그)
- 누적 ERROR 수가 0이 아닌 station은 노란 표시 (수신측에 미등록 가능성 시사)

#### `/stations/<station_name>` 파일 관리
- 해당 station의 파일 목록 (이름, 타입, 크기, 업로드일, 마지막 송신, 송신 횟수)
- 드래그앤드롭 영역 + `[Upload]` 버튼 (multiple files)
- `[Rescan Folder]` 버튼 (디렉토리 ↔ DB 동기화)
- 행별 `[Delete]`, `[Reset send count]`

### 9.2 API endpoint

| 메서드 | 경로 | 용도 |
|---|---|---|
| `GET` | `/api/stations` | 등록 station 목록 |
| `POST` | `/api/stations` | station 등록 (`station_id` 미입력 시 자동 생성) |
| `PATCH` | `/api/stations/{name}` | station 수정 (label, status, station_id) |
| `DELETE` | `/api/stations/{name}` | station 삭제 (cascade) |
| `GET` | `/api/stations/{name}/files` | 파일 목록 |
| `POST` | `/api/stations/{name}/files` | 파일 업로드 (multipart) |
| `DELETE` | `/api/stations/{name}/files/{filename}` | 파일 삭제 |
| `POST` | `/api/stations/{name}/rescan` | 디렉토리 ↔ DB 동기화 |
| `POST` | `/api/runtime/pause` | 송신 일시정지 |
| `POST` | `/api/runtime/resume` | 송신 재개 |
| `POST` | `/api/runtime/skip-file` | 현재 파일 스킵 |
| `POST` | `/api/runtime/skip-station` | 현재 station 스킵 |
| `POST` | `/api/runtime/restart-cycle` | 사이클 처음부터 |
| `PATCH` | `/api/runtime/config` | 런타임 파라미터 변경 |

런타임 변경된 파라미터는 다음 파일 송신부터 적용 (현재 송신 중인 파일은 그대로 완료).

`POST /api/runtime/resolve-stations` 엔드포인트는 폐기 (gateway_db 조회 안 함).

---

## 10. payload extension 메커니즘

`payload_builder.py`에 plug-in 구조. 기본 enable 목록은 `config.py`의 `DEFAULT_EXTENSIONS`.

```python
DEFAULT_EXTENSIONS = ["amr_id", "encoding", "source_file"]
```

새 확장 필드 추가 시 `Extension` 클래스 1개 + 등록만으로 완료.

---

## 11. 구현 체크리스트

### Phase I-1. 메타 DB

- [ ] `init_db.sql` 작성 (station + station_id UUID 컬럼, media_file, index)
- [ ] PostgreSQL 컨테이너 docker-compose 정의 (`mock-images-postgres`, host port 2347)
- [ ] `meta_db.py`: asyncpg connection pool, station/media_file CRUD
- [ ] station 등록 시 `station_id` 미입력이면 UUID v4 자동 생성
- [ ] 디렉토리 ↔ DB 동기화 함수 (`rescan_station_folder`)
- [ ] 단위 테스트 (CRUD, cascade 삭제, rescan 정합, UUID 자동/수동 입력)

### Phase I-2. 미디어 큐 + 인코딩

- [ ] `file_queue.py`: station별 파일 큐 로드 (`last_sent_at` 정렬), `FILE_ORDER` 옵션
- [ ] `frame_extractor.py`: OpenCV 영상 디코딩 + fps 샘플링 + resize
- [ ] `encoder.py`: RAW / TRANSCODE 분기, JPEG / MP4 재인코딩
- [ ] 단위 테스트 (큐 정렬, RAW 바이트 일치, TRANSCODE 출력 검증)

### Phase I-3. 청크 분할 + 헤더 조립

- [ ] `chunker.py`: 바이트스트림 → `CHUNK_SIZE_KB` 단위 분할
- [ ] `payload_builder.py`: 필수 필드 + extension plug-in (메타 DB의 station_id를 그대로 사용)
- [ ] 기본 extension 3종 (`amr_id`, `encoding`, `source_file`)
- [ ] 단위 테스트

### Phase I-4. 송신 루프

- [ ] `gw_proto.transport.client`로 Ingestion Gateway 접속
- [ ] **체류형 라운드로빈 루프** (`STAY_MODE` 분기, station 순회, file 순회)
- [ ] 파일 송신: VIDEO_CHUNK N회 + VIDEO_COMPLETE 1회 + `last_sent_at`/`send_count` 갱신
- [ ] ACK / ERROR 분기 처리: ERROR는 통계 카운터 증가 + 현재 파일 중단 + 다음 파일
- [ ] Heartbeat / 재연결 (gw_proto 기본 정책)
- [ ] `INTERVAL_SEC`, `CYCLE_INTERVAL_SEC`, `LOOP` 처리

### Phase I-5. Admin UI

- [ ] aiohttp HTTP 서버 (`8081`)
- [ ] 대시보드 페이지 (`/`)
- [ ] station 관리 페이지 (`/stations`) + CRUD API
- [ ] station 등록 폼: `station_id` 수동 입력 또는 자동 생성
- [ ] 파일 관리 페이지 (`/stations/<name>`) + 업로드/삭제 API
- [ ] 드래그앤드롭 업로드 (multipart, multiple files)
- [ ] 런타임 액션 (pause, skip, restart-cycle)
- [ ] 런타임 파라미터 변경 API (다음 파일부터 반영)
- [ ] station별 ERROR 누적 카운터 표시

### Phase I-6. 컨테이너화 및 통합 테스트

- [ ] `Dockerfile` (gw_proto, opencv-python-headless, asyncpg, aiohttp 설치)
- [ ] `docker-compose.yml` (mock-images + mock-images-postgres, `socketdaim_gw-net` external join)
- [ ] SocketDaim compose 기동 후 MockImages 기동
- [ ] **정상 시나리오**: SocketDaim에 등록된 UUID로 station 2개 등록 → 송신 → `video` 테이블 INSERT 검증
- [ ] **거부 시나리오**: SocketDaim에 미등록된 UUID(자동 생성)로 station 1개 등록 → 송신 → ERROR 응답 + Admin UI에 ERROR 카운터 증가 확인
- [ ] **혼합 시나리오**: 정상 station 2개 + 미등록 station 1개 → 사이클 진행 중 정상 송신과 ERROR 응답이 공존하는지 확인
- [ ] 메타 DB에서 `last_sent_at`, `send_count` 갱신 확인 (성공한 파일만 갱신)
- [ ] RAW / TRANSCODE 모드 전환 동작 확인
- [ ] `STAY_MODE` 3가지 옵션 동작 확인
- [ ] 확장 payload 필드(`source_file` 등)가 Gateway 통과 확인 (`ingestion_log` 검사)

---

## 12. 향후 확장 (out of scope)

- 시나리오 파일(YAML) 기반 송신: 시각·간격·메시지 시퀀스를 외부 정의
- AMR 순찰 경로 재현: `amr_position` extension에서 사전 정의된 waypoint 기반 좌표 발행
- 이상 상황 영상 주입 모드: 정상 영상 N개당 이상 영상 1개를 사이클에 끼워넣어 컨슈머 검증
- 다중 AMR 동시 시뮬레이션: 여러 송신 인스턴스 병렬 운영
- 의도적 결함 주입: malformed JSON 헤더, chunk_seq 누락, station_id 형식 오류 등을 의도적으로 송신해 Gateway의 에러 핸들링 검증

---

## 13. 변경 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| 0.1 | 2026-05-07 | 초안. 단일 station 환경변수, videos/images 분리 디렉토리 |
| 0.2 | 2026-05-07 | 다중 station(메타 DB 관리), 체류형 라운드로빈, station 폴더 1depth, PostgreSQL 메타 DB, Admin UI 확장(station/파일 CRUD) |
| 0.3 | 2026-05-07 | **일방 송신 원칙 명시.** gateway_db 조회 로직 폐기. `station_id`(UUID)를 메타 DB에 직접 보관(수동 입력 또는 자동 생성). 거부 시나리오를 명시적 검증 대상으로 포함. ACK/ERROR 분기 처리, ERROR 통계 카운터 추가 |