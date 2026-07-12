# indoro

인도 약국을 진입점으로 삼는 복약 안내 서비스. 약사가 수기 처방전을 앱에 입력하면(항목당 20~40초 목표), 서버가 토큰 URL과 QR을 발급하고, 환자는 앱 설치 없이 폰으로 QR을 스캔해 저문해 친화적인 **모바일 복약 포스터**(힌디/영어)를 본다.

2026 아이디어 해커톤 인도 해외연수 지원 프로젝트 — KAIST 팀(김나연 · 김준휘 · 정영민).

> **개발 단계 프로토타입입니다.** 실제 환자에게 배포된 의료 서비스가 아닙니다. 처방·약국·합성 상품 데이터는 데모용이며, NPPA 공식 formulation 11건은 출처 추적 임포트 경로 검증에만 사용합니다. 어느 데이터도 복약 권고나 전국 상품 마스터를 뜻하지 않습니다.

## 이게 뭘 푸는가

인도 외래 진료는 평균 약 2분이라 복약 설명이 생략되고, 수기 처방전은 라틴 약어("1-0-1", "TDS")로 쓰여 환자가 해독하기 어렵다. 문해율이 낮은 계층과 22개 공용어 환경에서 "언제 무엇을 얼마나 먹는가"가 환자에게 전달되지 못한다. indoro는 약사를 진입점으로 이 마지막 구간을 그림·숫자·색으로 메운다.

## 저장소 구조

| 경로 | 내용 |
|---|---|
| [`docs/`](docs/) | 데이터플로우, 화면 인벤토리, [인도 레퍼런스 조사](docs/05-india-medication-poster-research.md), [의약품 데이터 기반](docs/08-india-drug-data-foundation.md), [검토·감사·retirement 운영](docs/09-drug-catalog-operations.md), [최신 인포그래픽 시각 QA](docs/07-infographic-poster-visual-qa.md) |
| [`wireframes/`](wireframes/) | 초기 클릭형 와이어프레임. 환자 화면의 현재 정본은 `server/templates/patient.html` |
| [`server/`](server/) | FastAPI + SQLite 관통 프로토타입. 출처 추적 카탈로그 검색 → 발급 → 실제 QR → 모바일 복약 포스터가 동작 |
| [`data/`](data/) | NPPA 공식 formulation 11건과 프로젝트가 작성한 합성 demo fixture 16건. 라이선스가 불명확한 레거시 627건 파일은 제거. 출처·한계는 [`data/drugs-README.md`](data/drugs-README.md) |

처음 보는 사람은 **`docs/01-dataflow.md`**(무엇을·왜)부터, 바로 돌려보고 싶으면 아래 **빠른 시작**으로.

## 빠른 시작

### 서버 (관통 프로토타입)

[uv](https://docs.astral.sh/uv/)가 필요하다.

```bash
cd server
uv sync                              # 의존성 설치
uv run python scripts/seed_import.py --dry-run --report-dir ../data/reports/production-dry
uv run python scripts/seed_import.py --apply --report-dir ../data/reports/production
uv run python scripts/demo.py        # 1약·3약·6약 + 대기/만료/폐기 데모 URL 출력
uv run uvicorn app.main:app --port 8600
```

- 약사 처방 입력: http://127.0.0.1:8600/rx/new
- 환자 복약 포스터: `demo.py`가 출력한 `patient URL` (뒤에 `?lang=en`으로 영어)
- 영문 환자 UX 스토리보드: http://127.0.0.1:8600/static/ux-storyboard-en.html
- 테스트: `uv run pytest -q`

합성 상품명·제형·경로 자동완성까지 시연할 때만 별도 demo DB를 만든다. demo 패키지는 기본 production DB에 적용할 수 없다.

```bash
cd server
uv run python scripts/seed_import.py --catalog-scope demo --dry-run \
  --report-dir ../data/reports/demo-dry
uv run python scripts/seed_import.py --catalog-scope demo --apply \
  --db-path var/indoro-demo.db --report-dir ../data/reports/demo
INDORO_DB_PATH=var/indoro-demo.db uv run uvicorn app.main:app --port 8600
```

검토 queue와 full-snapshot retirement를 시연할 때는 별도의 synthetic 운영 DB를 만든다. 이 내부 UI는 인증·CSRF가 없는 loopback 전용 prototype이며 기본 비활성이다.

```bash
cd server
uv run python scripts/catalog_governance_demo.py \
  --db-path var/catalog-governance-demo.db
INDORO_DB_PATH=var/catalog-governance-demo.db \
INDORO_CATALOG_OPS_ENABLED=1 \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8610
# http://127.0.0.1:8610/catalog/review
# http://127.0.0.1:8610/catalog/retirements
```

운영 prototype은 `127.0.0.1`에 직접 bind할 때만 사용한다. 외부 요청을 loopback으로 재작성할 수 있는 reverse proxy, tunnel, port-forward에는 연결하지 않는다. 실제 배포에는 별도 인증·RBAC·CSRF가 필요하다.

> 폰으로 QR을 실제 스캔하려면 `--host 0.0.0.0`으로 띄우고 폰에서 `http://<이 PC의 LAN IP>:8600/rx/new`로 접속한다. QR은 요청 호스트 기준으로 URL을 인코딩하므로 `127.0.0.1`로 띄우면 폰에서 열리지 않는다.

### 와이어프레임 (정적 화면)

```bash
python3 -m http.server 8493 --directory wireframes
# http://127.0.0.1:8493/index.html — 화면 허브
```

## 현재 상태

인도 방문 전 원격으로 가능한 트랙을 완료한 상태다.

- **환자 화면**: 아침→점심→저녁→밤의 하루 흐름 포스터. 각 행동에 봉투 색+번호, 약명, 용량, 식전/식후, 기간을 함께 표시하고 약별 상세·공유는 아래에 둔다.
- **의약품 카탈로그**: 출처·버전·원본·처리 버전을 추적하고, 사람 review와 검색 lifecycle을 분리한다. optimistic locking, append-only audit, delta/full snapshot 구분, 사람 승인 뒤에만 적용되는 retirement batch를 제공한다. NPPA 공식 formulation 11건만 production DB에 두고 검색·운영 UX 합성 fixture는 별도 demo DB에 둔다.
- **서버 프로토타입**: 자동완성 → 확인 → 발급 → QR → 포스터 → 언어 전환·공유와 상태 화면까지 실동작하고 전체 pytest로 회귀 검증한다.
- **의도적 미구현(현지/후속 트랙)**: 전국 상품명 데이터 라이선스, 현지 약사·법무·언어 검증, 운영 UI의 실제 인증·RBAC·CSRF, 실제 음성/영상, 완전한 오프라인 큐, 일부 약국 운영 화면(온보딩·목록·수정 UI), 배포. 서버의 [`README.md`](server/README.md)에 범위표가 있다.

다음: 팀 리뷰 → 파일럿 지역(1차 언어) 확정 → 현지 사용자 테스트.
