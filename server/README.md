# indoro server (v0 관통 프로토타입)

시드 DB 약 검색 → P1 처방 입력 → 발급(토큰+QR) → 환자 폰 스캔 → S1 인포그래픽 열람.
정본 스펙: `docs/01-dataflow.md` · 렌더 정본: `wireframes/patient/s1-landing.html` · DB: `server/var/indoro.db`(gitignore).

## 명령 4개

```bash
cd server && uv run uvicorn app.main:app --port 8600      # 실행 (첫 실행 전 uv sync)
cd server && uv run python scripts/seed_import.py         # 시드 임포트 (627건 + ph-demo-001, 재실행 멱등)
cd server && uv run python scripts/demo.py                # 데모 처방 1건(FX-A 3항목) 생성 → 토큰 URL 출력
cd server && uv run pytest -q                             # 테스트 (18 passed)
```

데모 흐름: `demo.py`가 출력한 patient URL을 폰/브라우저로 열면 S1 인포그래픽,
pharmacist QR URL을 열면 P3 QR 화면. 새 처방 입력은 `http://127.0.0.1:8600/rx/new`.

## 라우트 지도

| 경로 | 설명 |
|---|---|
| `POST /api/prescriptions` | 발급 (멱등 D10 — 재전송 200 + replayed) |
| `POST /api/prescriptions/{id}/reissue` | 폐기 후 재발급 (D5 — 구토큰 410) |
| `GET /api/prescriptions/{id}` | P3용 요약 |
| `GET /api/drugs?q=` | 자동완성 (항상 200 + 배열) |
| `POST /api/events` | 계측 비콘 (항상 204, D16) |
| `GET /p/{token}` | S1 환자 인포그래픽 (active 200 / 미존재 200 대기 D8 / expired·revoked 410) |
| `GET /c/{code}` | 가족 공유 짧은 코드 → 302 `/p/{token}?src=code` |
| `GET /rx/new` | P1 처방 입력 |
| `GET /rx/{id}/qr` | P3 QR 표시·인쇄 |

## 80% 범위표

| 상태 | 항목 |
|---|---|
| **구현** | 발급 API(멱등·검증 422·오프라인 토큰 409)·reissue(D5)·drugs 검색·이벤트 비콘(화이트리스트·dedup)·S1 렌더(매트릭스·픽토그램·식전후 스트립·날짜 점·hi/en D12)·대기 페이지(D8)·410 페이지·짧은 코드 /c(Crockford 정규화)·view.first 원자 선점·ivid 쿠키·P1 입력(자동완성·사전검증·P2 시트·계측)·P3 QR(클라 SVG 렌더·인쇄 뷰·전체화면)·시드 임포트 627건·테스트 18건 |
| **스텁** (코드 주석 명기) | 음성 안내·프리렌더 영상(placeholder UI만)·오프라인 제출(자동완성 비활성+인라인 재시도만, outbox 완전판 아님)·If-None-Match 304(ETag 발급만)·홈 `/` 코드 입력 폼(/c 자체는 동작)·`/privacy`(S6) |
| **제외** | P0/P4/P6 화면·Postgres·배포·rate limit/tarpit·purge 스크립트·IDEMPOTENCY_CONFLICT 409(항상 replay)·media.video_complete 발행(실재생 없음) |

## 메모

- 약국 식별: `X-Pharmacy-Id` 헤더 (화면은 localStorage `indoro.pharmacy`, 기본 `ph-demo-001`).
- 렌더 예산: S1 gzip 약 9.5KB (예산 80KB).
- 설정 단일 소스: `config/patterns.yaml` · `config/i18n.yaml` (fixtures.js META에서 추출) — UI 문구·패턴 추가는 YAML에.
