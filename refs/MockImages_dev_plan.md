# MockImages 구현 계획서

> 작성일: 2026-05-07
> 위치: `c:\MockImages\` (신규 레포)
> 자매 프로젝트: [`c:\MockSensor\`](../MockSensor/) (분진 센서 mock)

---

## 1. 목적

AMR이 촬영하여 Ingestion Gateway로 송신하는 영상/이미지 데이터를 흉내내는 송신기. 실제 AMR 측 TCP 프로토콜 구현이 완료되기 전, Ingestion Gateway의 `VIDEO_CHUNK` / `VIDEO_COMPLETE` 수신 경로 및 후속 컨슈머(이상감지, YOLO)의 동작을 검증하는 데 사용한다.

`MockSensor`가 시계열 분진 농도를 생성-송신하는 시뮬레이터인 것과 달리, `MockImages`는 사용자가 사전 준비한 영상·이미지 파일을 그대로 또는 재인코딩하여 프로토콜에 맞게 청크 분할 송신하는 **에뮬레이터**에 가깝다.

---

## 2. 책임 범위

### 2.1 책임

- 지정된 디렉토리의 영상(`.mp4`, `.avi`)·이미지(`.jpg`, `.png`) 파일을 라운드로빈으로 순회 송신
- 큐 끝에 도달하면 처음부터 재순회 (loop)
- `gw_proto` 라이브러리의 client 측 사용 (Ingestion Gateway와 동일 프로토콜 스택)
- 출력 fps·해상도·압축 품질·청크 크기 등 송신 파라미터 제어
- `VIDEO_CHUNK` JSON 헤더에 확장 메타데이터(amr_position 등) 추가 가능

### 2.2 비책임 (명시적 제외)

- 영상 콘텐츠 자체의 합성·생성 (분진 합성 등). 입력은 사용자가 직접 촬영·수집한 파일을 그대로 사용
- station 등록 (Gateway 측 DB에 사전 등록되어 있어야 함, MockSensor와 동일 정책)
- 분석 결과 검증 (Gateway 수신 후 처리는 본 송신기의 관심사 밖)

---

## 3. 입력 미디어 정책

### 3.1 디렉토리 구조

```
media/
├── videos/        # *.mp4, *.avi  (확장자별 lexicographic 정렬)
└── images/        # *.jpg, *.jpeg, *.png
```

볼륨 마운트로 컨테이너에 주입한다. 미디어 파일은 송신기 빌드와 분리되며, 사용자가 자유롭게 추가/삭제 가능.

### 3.2 큐 구성 정책

기동 시 두 디렉토리를 모두 스캔하여 단일 큐를 구성한다. 정렬 순서:

```
videos/* (이름순) → images/* (이름순)
```

큐 끝에 도달하면 처음으로 wrap. 한 사이클이 끝나는 시점에 디렉토리를 재스캔하여 새 파일이 있으면 다음 사이클부터 반영한다(중간 추가는 다음 사이클까지 대기).

### 3.3 영상·이미지의 차이

| 구분 | 영상 (mp4/avi) | 이미지 (jpg/png) |
|---|---|---|
| 프레임 수 | N개 (transcoding 시 OUT_FPS 적용) | 1개 |
| video_id | 파일당 1개 | 파일당 1개 |
| 전송 단위 | 1 파일 = 1 video_id, 다수 청크 | 1 파일 = 1 video_id, 1~N 청크 |
| `total_chunks` | 인코딩 결과 바이트수 / `CHUNK_SIZE` | 동일 |

이미지도 영상과 동일한 `VIDEO_CHUNK` 시퀀스로 송신한다. Gateway는 콘텐츠 형식을 모르므로 동일 경로로 처리된다.

---

## 4. 송신 모드

### 4.1 RAW 모드 (기본값)

파일을 디스크에 있는 그대로 읽어 청크 분할만 수행. 빠르고 단순하며 Gateway 수신·저장 동작 검증에 충분.

- 입력: `videos/sample.mp4` (10 MB)
- 출력: 10 MB / `CHUNK_SIZE_KB` 만큼의 청크
- fps·해상도·품질 변경 불가 (원본 그대로)

### 4.2 TRANSCODE 모드

OpenCV로 디코딩 → 출력 파라미터 적용 → 재인코딩 후 청크 분할.

| 적용 파라미터 | 영상 | 이미지 |
|---|---|---|
| `OUT_FPS` | 출력 프레임레이트 | N/A |
| `RESIZE_W`, `RESIZE_H` | 리사이즈 | 리사이즈 |
| `JPEG_QUALITY` | 프레임을 JPEG 시퀀스로 인코딩 시 적용 | JPEG 재인코딩 시 적용 |
| `OUTPUT_FORMAT` | `mp4` 또는 `jpeg_seq` | `jpeg` |

`OUTPUT_FORMAT=jpeg_seq`인 경우 영상의 매 프레임을 별도 video_id로 분리하여 송신할지, 단일 video_id 안에 모든 프레임을 합쳐 송신할지를 `JPEG_SEQ_MODE` 옵션으로 선택. 기본값은 단일 video_id.

### 4.3 모드 선택 가이드

- Ingestion Gateway 수신 동작 검증: `RAW`
- AMR이 송출할 실제 해상도/fps/대역폭 시뮬레이션: `TRANSCODE`
- YOLO 컨슈머의 프레임 단위 처리 검증: `TRANSCODE` + `OUTPUT_FORMAT=jpeg_seq` + `JPEG_SEQ_MODE=per_frame`

---

## 5. 전송 프로토콜

### 5.1 공용 라이브러리 사용

`MockSensor`와 동일하게 `gw_proto` 라이브러리의 client 측(`transport/client.py`, `codec/standard.py`)을 그대로 사용. 프로토콜 변경 시 라이브러리만 교체하면 송신기는 무영향.

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

확장 (선택, payload extension으로 plug-in):

```json
{
  "amr_id": "mock-amr-01",
  "amr_position": {"x": 12.5, "y": 8.3, "heading": 90.0},
  "source_file": "videos/sample.mp4",
  "encoding": {"mode": "transcode", "out_fps": 15, "jpeg_quality": 85}
}
```

Gateway는 첫 청크에서 `station_id`만 검증하고 그 외 필드는 통과시키므로(`gw_protocol_spec.md` §4.2 주의사항), 확장 필드 추가는 호환성에 영향 없음.

### 5.4 Station 사전 등록 (필수)

`MockSensor`와 동일 정책. Gateway는 등록된 `station_id`에 대해서만 데이터를 수신하므로, 송신기 실행 전에 station이 DB에 등록되어 있어야 한다.

기존 4개 테스트 station(`FL-A01-NORTH`, `FL-A02-SOUTH`, `FL-B01-EAST`, `FL-C01-WEST`)이 SocketDaim compose 기동 시 자동 등록되므로(`gw_protocol_spec.md` §8.1), 이 중 하나를 `STATION_NAME` 환경변수로 지정한다.

### 5.5 Mock의 station_id resolve

기동 시 공용 저장소 DB에 `gw_reader` 권한으로 접속하여 `STATION_NAME → station_id(UUID)`를 조회한다. `MockSensor`의 resolve 로직과 동일.

미등록 station 지정 시 기동 실패 + 에러 로그 + 종료.

---

## 6. 동작 흐름

```mermaid
flowchart TD
    Start["MockImages 시작"]
    Resolve["DB 조회: STATION_NAME → UUID"]
    Connect["Ingestion Gateway TCP connect"]
    Scan["media/ 디렉토리 스캔<br/>큐 구성"]

    subgraph LoopOuter["라운드로빈 (LOOP=true)"]
        subgraph FileLoop["큐의 각 파일"]
            Detect["파일 타입 판별<br/>(영상 / 이미지)"]
            Encode{"송신 모드"}
            Raw["RAW: 파일 바이트 그대로"]
            Trans["TRANSCODE: OpenCV 디코딩<br/>+ resize/quality/fps 적용<br/>+ 재인코딩"]
            Chunk["chunker: N개 청크 분할"]
            BuildHeader["payload_builder: 헤더 dict 조립<br/>(필수 + 확장 필드)"]
            SendChunks["VIDEO_CHUNK 순차 송신<br/>각 청크 ACK 대기"]
            Complete["VIDEO_COMPLETE 송신 + ACK"]
            Wait["INTERVAL_SEC 대기"]
        end
        Rescan["큐 끝 → 디렉토리 재스캔"]
    end

    Start --> Resolve --> Connect --> Scan
    Scan --> LoopOuter
    LoopOuter --> FileLoop
    FileLoop --> Detect --> Encode
    Encode -->|raw| Raw
    Encode -->|transcode| Trans
    Raw --> Chunk
    Trans --> Chunk
    Chunk --> BuildHeader --> SendChunks --> Complete --> Wait
    Wait --> FileLoop
    FileLoop -->|큐 소진| Rescan
    Rescan --> LoopOuter
```

---

## 7. 컨테이너 구성

### 7.1 디렉토리 구조

```
MockImages/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── README.md
├── media/                         # volume mount, 입력 미디어
│   ├── videos/
│   └── images/
├── src/mock_images/
│   ├── __init__.py
│   ├── config.py                  # pydantic-settings, 환경변수 + 런타임 변경
│   ├── media_queue.py             # 디렉토리 스캔 + 라운드로빈 iterator
│   ├── frame_extractor.py         # OpenCV 영상 디코딩, fps 샘플링, resize
│   ├── encoder.py                 # RAW / TRANSCODE 모드 분기, 인코딩 출력
│   ├── chunker.py                 # 바이트스트림 → 청크 분할
│   ├── payload_builder.py         # VIDEO_CHUNK 헤더 dict 조립 (확장 plug-in)
│   ├── sender.py                  # gw_proto client, VIDEO_CHUNK + COMPLETE
│   ├── admin/
│   │   ├── server.py              # aiohttp HTTP 서버
│   │   └── templates/
│   │       └── index.html
│   ├── runtime.py                 # 진행 상태, PauseGate
│   └── main.py                    # asyncio entrypoint
└── tests/
    ├── test_media_queue.py
    ├── test_chunker.py
    ├── test_payload_builder.py
    └── test_encoder.py
```

### 7.2 Docker 구성

- `gw-proto` 라이브러리는 빌드 시 설치 (Ingestion/MockSensor와 동일 방식)
- 별도 DB 불필요 (stateless)
- `socketdaim_gw-net` external network에 join (MockSensor와 동일)
- `media/` 디렉토리는 host volume mount
- OpenCV는 `opencv-python-headless` 사용 (GUI 의존성 제거)

### 7.3 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `INGESTION_HOST` | `ingestion-gw` | Gateway 호스트명 |
| `INGESTION_PORT` | `9000` | Gateway TCP 포트 |
| `PROTOCOL` | `standard` | 코덱 선택 (gw_proto) |
| `GATEWAY_DB_URL` | `postgresql://gw_reader:dev_reader_pw@postgres:5432/gateway_db` | station UUID resolve용 |
| `STATION_NAME` | `FL-A01-NORTH` | 송신 대상 station |
| `AMR_ID` | `mock-amr-01` | 확장 payload용 AMR 식별자 |
| `MEDIA_DIR` | `/media` | 미디어 루트 디렉토리 |
| `LOOP` | `true` | 큐 끝에서 처음부터 재순회 |
| `MODE` | `raw` | `raw` / `transcode` |
| `OUT_FPS` | `15` | transcode 시 출력 fps |
| `RESIZE_W` | `null` | transcode 시 리사이즈 너비 (null=원본) |
| `RESIZE_H` | `null` | transcode 시 리사이즈 높이 (null=원본) |
| `JPEG_QUALITY` | `85` | 1~100 |
| `OUTPUT_FORMAT` | `mp4` | `mp4` / `jpeg_seq` (transcode 시) |
| `JPEG_SEQ_MODE` | `single_video` | `single_video` / `per_frame` |
| `CHUNK_SIZE_KB` | `512` | 청크 크기 (4~4096 권장, 스펙: 4 KiB~4 MiB) |
| `INTERVAL_SEC` | `5` | 파일 한 개 송신 후 다음 파일까지 대기 |
| `STARTUP_DELAY_SEC` | `2` | 컨테이너 기동 직후 Gateway 대기 |
| `ADMIN_PORT` | `8081` | Admin UI 포트 (MockSensor 8080과 분리) |

### 7.4 로그 출력

```
[RESOLVE] station FL-A01-NORTH -> 2595693b-...
[QUEUE] scanned media/: 12 videos, 8 images
[CYCLE 1] starting round-robin (20 files)
[FILE 1/20] videos/amr_patrol_001.mp4 (8.3 MB) mode=raw chunks=17
[FILE 1/20] sent: 17 chunks, COMPLETE ACK in 1.24s
[FILE 2/20] images/dust_sample_a.jpg (240 KB) mode=raw chunks=1
...
[CYCLE 1] complete, rescanning media/...
[CYCLE 2] starting round-robin (20 files, no changes)
```

---

## 8. Admin UI

`MockSensor`의 admin (포트 8080)과 동일 톤의 미니멀 HTML UI. 포트는 `8081`.

### 8.1 표시 항목

- 현재 사이클 번호, 큐 위치(`5/20`)
- 현재 파일명, 파일 크기, 청크 진행도(`12/17`)
- 전송 통계: 누적 파일 수, 누적 청크 수, ACK 실패 수, 평균 latency
- 연결 상태 (connected / reconnecting)
- Pause / Running 상태

### 8.2 조작 항목

- `[Pause / Resume]` 버튼
- `[Skip Current File]` 버튼 (현재 파일 송신 중단, 다음 파일로)
- `[Restart Cycle]` 버튼 (큐를 처음부터)
- `[Rescan Media]` 버튼 (즉시 디렉토리 재스캔)
- 파라미터 폼: `MODE`, `OUT_FPS`, `JPEG_QUALITY`, `CHUNK_SIZE_KB`, `INTERVAL_SEC`, `RESIZE_W/H` 런타임 변경

런타임 변경된 파라미터는 다음 파일 송신부터 적용된다(현재 송신 중인 파일은 그대로 완료).

---

## 9. payload extension 메커니즘

`payload_builder.py`에 확장 인터페이스를 두어 헤더 dict에 추가 필드를 plug-in 형태로 끼워넣을 수 있다.

```python
# payload_builder.py
class PayloadExtension(Protocol):
    def contribute(self, base: dict, ctx: SendContext) -> dict: ...

class AmrIdExtension:
    def contribute(self, base, ctx):
        base["amr_id"] = ctx.amr_id
        return base

class AmrPositionExtension:
    """가상 순찰 경로를 따라 좌표 생성."""
    def contribute(self, base, ctx):
        base["amr_position"] = ctx.position_provider.next()
        return base

class EncodingMetaExtension:
    def contribute(self, base, ctx):
        base["encoding"] = ctx.encoding_meta
        return base
```

기본 enable 목록은 `config.py`에서 정의:

```python
DEFAULT_EXTENSIONS = ["amr_id", "encoding"]
```

새 확장 필드 추가 시 `Extension` 클래스 1개 추가 + `DEFAULT_EXTENSIONS` 등록만으로 완료.

---

## 10. 구현 체크리스트

### Phase I-1. 미디어 큐 + 인코딩

- [ ] 디렉토리 스캔 + 파일 타입 판별 (`media_queue.py`)
- [ ] 라운드로빈 iterator (큐 끝 → wrap, 사이클 종료 시 재스캔)
- [ ] RAW 모드 인코더 (파일 바이트 통과)
- [ ] TRANSCODE 모드: OpenCV 영상 디코딩 + fps 샘플링 + resize
- [ ] TRANSCODE 모드: JPEG / MP4 재인코딩
- [ ] 단위 테스트 (큐 순서, RAW 바이트 일치, TRANSCODE 출력 검증)

### Phase I-2. 청크 분할 + 헤더 조립

- [ ] `chunker.py`: 바이트스트림 → `CHUNK_SIZE_KB` 단위 분할
- [ ] `payload_builder.py`: 필수 필드 dict + extension plug-in
- [ ] 기본 extension 3종 구현 (`amr_id`, `encoding`, `amr_position`)
- [ ] 단위 테스트 (chunk_seq 연속성, total_chunks 정합, 확장 필드 주입)

### Phase I-3. 전송 루프

- [ ] 기동 시 DB에서 STATION_NAME → UUID resolve (실패 시 종료)
- [ ] `gw_proto.transport.client`로 Ingestion Gateway 접속
- [ ] 파일 1개당 VIDEO_CHUNK N회 + VIDEO_COMPLETE 1회 송신
- [ ] 각 메시지 ACK 대기 (실패 시 해당 파일 스킵 + 다음 파일)
- [ ] Heartbeat 송수신 (gw_proto 기본 정책)
- [ ] 재연결 백오프 (gw_proto 기본 정책)
- [ ] `INTERVAL_SEC` 대기 + `LOOP` 처리

### Phase I-4. Admin UI

- [ ] aiohttp HTTP 서버 (`8081`)
- [ ] HTML 템플릿 (MockSensor와 동일 톤)
- [ ] 진행 상태 표시 (사이클, 큐 위치, 청크 진행도, 통계)
- [ ] Pause / Resume 게이트
- [ ] Skip / Restart Cycle / Rescan Media 액션
- [ ] 런타임 파라미터 변경 (다음 파일부터 반영)

### Phase I-5. 컨테이너화 및 통합 테스트

- [ ] `Dockerfile` (gw_proto 설치, opencv-python-headless 설치)
- [ ] `docker-compose.yml` (`socketdaim_gw-net` external join)
- [ ] SocketDaim compose 기동 후 MockImages 기동
- [ ] 영상 1개 송신 → Gateway에 파일 저장 + `video` 테이블 INSERT 확인
- [ ] 이미지 1개 송신 → 동일 검증
- [ ] 큐 1사이클 완주 → 사이클 2 시작 확인
- [ ] RAW / TRANSCODE 모드 전환 동작 확인
- [ ] Admin UI에서 파라미터 변경 시 다음 파일부터 적용 확인
- [ ] 확장 payload 필드(`amr_position` 등)가 Gateway 통과 확인 (`ingestion_log` 검사)

---

## 11. 향후 확장 (out of scope, 메모)

본 v1 구현 이후 필요 시 검토.

- 시나리오 파일(YAML) 기반 송신 (`gateway_plan.md` §10.2 언급): 시각·간격·메시지 시퀀스를 외부 정의
- 다중 AMR 동시 시뮬레이션: 여러 STATION_NAME에 병렬 송신
- AMR 순찰 경로 재현: `amr_position` extension에서 사전 정의된 waypoint 기반 좌표 발행
- 이상 상황 영상 주입 모드: 정상 영상 N개당 이상 영상 1개를 큐에 끼워넣어 컨슈머 검증

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| 0.1 | 2026-05-07 | 초안 작성 |