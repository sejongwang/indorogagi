# indoro server (v0 관통 프로토타입)

출처 추적 의약품 검색 → P1 처방 입력 → 발급(토큰+실제 SVG QR) → 환자 폰 스캔 → 하루 흐름 복약 포스터 열람.
정본 스펙: `docs/01-dataflow.md` · 환자 렌더 정본: `server/templates/patient.html` · DB: `server/var/indoro.db`(gitignore).

## 주요 명령

```bash
cd server && uv run uvicorn app.main:app --port 8600      # 실행 (첫 실행 전 uv sync)
cd server && uv run python scripts/seed_import.py --dry-run --report-dir ../data/reports
cd server && uv run python scripts/seed_import.py --report-dir ../data/reports
cd server && uv run python scripts/seed_import.py --catalog-scope demo --db-path var/indoro-demo.db --report-dir ../data/reports/demo
cd server && INDORO_DB_PATH=var/indoro-demo.db uv run uvicorn app.main:app --port 8600
cd server && uv run python scripts/demo.py                # 1약·3약·6약 + 상태 데모 URL 출력
cd server && uv run pytest -q                             # 전체 회귀 테스트
```

데모 흐름: `demo.py`가 출력한 patient URL을 폰/브라우저로 열면 S1 복약 포스터,
pharmacist QR URL을 열면 P3 QR 화면. 새 처방 입력은 `http://127.0.0.1:8600/rx/new`.

## 라우트 지도

| 경로 | 설명 |
|---|---|
| `POST /api/prescriptions` | 발급 (멱등 D10 — 재전송 200 + replayed) |
| `POST /api/prescriptions/{id}/reissue` | 폐기 후 재발급 (D5 — 구토큰 410) |
| `GET /api/prescriptions/{id}` | P3용 요약 |
| `GET /api/prescriptions/{id}/qr` | 같은 QR URL 재표시 + `qr.redisplayed` 계측(발급 직후 첫 화면은 제외) |
| `GET /api/drugs?q=` | 출처 추적 자동완성 (항상 200 + 배열, 자유 입력 fallback 유지) |
| `POST /api/events` | 계측 비콘 (항상 204, D16) |
| `GET /p/{token}` | S1 환자 복약 포스터 (active 200 / 미존재 200 대기 D8 / expired·revoked 410) |
| `GET /c/{code}` | 가족 공유 짧은 코드 → 302 `/p/{token}?src=code` |
| `GET /rx/new` | P1 처방 입력 |
| `GET /rx/{id}/qr` | P3 QR 표시·인쇄 |
| `GET /privacy` | 환자 링크의 최소 개인정보 안내 |

## 80% 범위표

| 상태 | 항목 |
|---|---|
| **구현** | 발급 API(멱등·검증 422·오프라인 토큰 409)·reissue(D5)·source/version/raw/review 상태가 있는 의약품 검색·자유 입력·이벤트 비콘·S1 하루 시간 흐름 포스터(첫눈 실약명·봉투 색+번호·단위별 자체 SVG·PRN 1회량/일일최대/최소간격·식전후 순서·기간·hi/en D12)·대기/410/코드 재입력/개인정보 화면·view.first 원자 선점·ivid 쿠키·P1 자동완성/확인·P3 실제 SVG QR/인쇄/전체화면·3종 데모 |
| **스텁** (코드 주석 명기) | 완전한 오프라인 제출(outbox)·If-None-Match 304(ETag 발급만)·현지 검증 전 음성/영상. 미구현 미디어 placeholder는 환자 화면에서 제거 |
| **제외** | P0/P4/P6 화면·Postgres·배포·rate limit/tarpit·purge 스크립트·IDEMPOTENCY_CONFLICT 409(항상 replay)·media.video_complete 발행(실재생 없음) |

## 메모

- 약국 식별: `X-Pharmacy-Id` 헤더 (화면은 localStorage `indoro.pharmacy`, 기본 `ph-demo-001`).
- 렌더 예산: 6약·긴 약명 S1 실측 Python gzip-9 9,586 bytes (테스트 예산 24KB).
- 설정 단일 소스: `config/patterns.yaml` · `config/i18n.yaml` (fixtures.js META에서 추출) — UI 문구·패턴 추가는 YAML에.
- 의약품 데이터: [`../docs/08-india-drug-data-foundation.md`](../docs/08-india-drug-data-foundation.md) · 운영 절차: [`../docs/09-drug-catalog-operations.md`](../docs/09-drug-catalog-operations.md). NPPA 11건만 production DB에 적재하며 합성 fixture 16건은 별도 demo DB 전용이다. 상업 사이트 참고분이 섞인 기존 627건 파일은 제거했다.
