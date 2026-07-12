# 최종 판정 — indoro 안전 검증 (agent/catalog-governance-operations)

- 실행일: 2026-07-11
- 방법: 구현 독립 명세([spec.md](spec.md)) → 명시적 상태 탐색기([model/](model/)) + 적대적 코드 헌트(4에이전트) → 반례별 회귀 테스트 선행 → 최소 수정 → 전체 스위트·모델 재실행.
- 근거 문서: docs/01·08·09. 검증 대상: server/app/**, server/scripts/**.

## 판정: **조건부 MERGE (CONDITIONAL MERGE)**

발견·수정된 7개 반례를 포함해 **코드로 증명 가능한 10개 안전 불변식은 현재 작업 트리에서 모두 성립**한다. 전체 테스트 **169개 통과**, 두 실행형 모델 모두 위반 0(수정 후). 이 브랜치는 아래 **머지 전 필수 조건 0건**, **정식 출시 전 조건 3건**을 전제로 머지 가능하다.

### 머지 전 필수 조건: 없음
7개 반례 전부 이번 브랜치에서 회귀 테스트와 함께 수정됐고, 파일럿(약국 2곳) 규모에서 즉시 차단해야 할 미해결 안전 결함은 남지 않았다.

### 정식 출시(파일럿 이후) 전 필수 조건
1. **`/c` short_code rate limit 구현** (D4, HIGH). docs §4.9의 `(IP,코드) 10회/10분` + tarpit + 실패율 알람. 현재 무제한 열거 오라클이며 short_code는 이 제한과 "한 몸"이다(§5.1). 본토큰 128-bit이 실제 보안 경계라 파일럿은 감내하나, 출시 전 구현이 short_code 사용의 전제.
2. **purge 스크립트 구현** (D3, docs §8.3). PII 널링(`patient_label`·`note`·`item.note`·CUSTOM `instructions`·`revisions.payload_json` 전체) 수동 SQL. docs §9.2의 **D+50 백로그**가 구속 시한 — 첫 만료 코호트 도래 전. DPDP 계약이므로 법무 검토 동반.
3. **거버넌스 UI 인증·RBAC·CSRF** (docs §10 명시). 현재 loopback 전용 prototype. reviewer/approver 역할 분리, session 수명, break-glass 복구 설계 필요.

## 수정 요약 (7 반례, 전부 테스트 선행)

| ID | 불변식 | 심각도 | 한 줄 |
|---|---|---|---|
| CX-1 | INV-2 | HIGH | 만료 처방 수정이 `expires_at` 재산정으로 죽은 QR 부활 → edit 만료 가드 + 조건부 UPDATE |
| CX-2 | INV-8 | HIGH | 상류 projection 변경이 사람의 rejected를 자동 해제 → rejected sticky(approved만 리셋) |
| CX-3 | INV-1 | MEDIUM | 멱등 replay가 다른 본문을 조용히 삼킴 → 본문 해시 대조 → 409 |
| CX-4 | INV-10 | MEDIUM | 이중 reissue가 폐기 시각·감사 덮어쓰고 대체본 분기 → revoked 재발급 410 |
| CX-5 | INV-1·3 | MEDIUM | 스냅샷 없는 발급 항목이 live 카탈로그 재조회 → 재조회 fallback 제거 |
| CX-6 | INV-9 | MEDIUM | 읽기 전용 의도 프리뷰가 apply 커밋 → 플래그 검증을 쓰기 앞으로 |
| CX-7 | INV-8 | MEDIUM | meta 없는 production DB가 demo로 브랜딩 → 대칭 scope 가드 |

수정 성격: 전부 **가드 추가·fallback 제거·검증 순서 이동**이며 정상 경로 동작을 바꾸지 않는다(정상 경로 보존 테스트 동반). 스키마 변경은 additive 1건(`prescriptions.client_request_sha256`, 기존 행은 호환 replay).

## 검증 커버리지와 한계

**증명된 것** (이 저장소 코드로):
- 10개 불변식이 명시된 상태 경계 안에서 성립(traceability.md 매트릭스).
- m1(발급/수정/재발급/열람/시간, 150만 상태)·m2(검토/임포트/retirement, 120만 상태) 모두 수정 후 위반 0.
- 트랜잭션 원자성은 BEGIN IMMEDIATE·단일 commit·rollback·조건부 UPDATE(rowcount)를 코드 판독으로 확인.

**증명하지 못한 것** — 코드 밖 잔여 리스크 (아래 §잔여 리스크).

## 잔여 리스크 (이 저장소 코드로 증명 불가)

### 검증 방법의 한계
- **모델은 유한 경계 내 전수**다: 처방≤3, presentation 2, 버전≤7, 정수 클록, 상태 150만/120만 상한(둘 다 경계 도달=TRUNCATED). 경계 밖 상태공간은 미탐색. 축약된 도메인(TTL 2단계 등)이 실제 값 다양성을 대표한다는 가정.
- **모델은 코드 판독을 신뢰**한다: 각 전이를 원자 액션으로 모델링했고, 이는 SQLite 직렬화·단일 커밋 전제에 의존한다. 모델과 실코드의 의미론 괴리는 회귀 테스트로만 방어된다.

### 인프라·런타임 (검증 범위 밖)
- SQLite WAL의 fsync·내구성 계약, 파일시스템 손상, 디스크 full 중 부분 쓰기.
- `PRAGMA foreign_keys=ON`·`journal_mode=WAL`이 모든 연결에 실제 적용되는지는 런타임 사안(코드엔 있음).
- 다중 프로세스 배포 시 `BEGIN IMMEDIATE` writer 직렬화의 실제 동작(현재 단일 프로세스 uvicorn 전제).

### 클라이언트 (서버에서 관측 불가)
- 오프라인 토큰의 `crypto.getRandomValues` 품질·`Math.random` 폴백 금지(docs §2.3 코드리뷰 기준) — 서버는 22자 형식만 검증하며 엔트로피는 검증 불가.
- outbox·QR 렌더·단조 시계 기반 입력 계측의 클라 정확성.

### 운영·거버넌스 (조직적 통제 필요)
- loopback UI 앞에 reverse proxy/tunnel을 두지 않는다는 운영 수칙(docs §10 — peer/Host 재작성으로 우회 가능).
- 무인증 `X-Pharmacy-Id` 헤더가 식별일 뿐 인증이 아니라는 전제(v0.5 `X-Pharmacy-Key` 승격 지점 예약됨).
- import 롤백은 자동화 없음(docs §9) — 백업 복원이 유일 복구 경로, 백업 이후 처방 유실 가능.

### 도메인 (약사·법무·규제 판단 필요 — 이 저장소가 대신할 수 없음)
- 카탈로그 데이터의 의학적 정확성, 라이선스·제3자 권리, `keep_active`/`retire`/`needs_investigation` 판단 기준, 환자 표시명·번역, recall/NSQ 적용 범위 (docs §11 책임 분리표).
- KPI 정확성 편차 1건(D1): 오프라인 `view.pending`→`view.first(src=pre_sync)` 소급 인정 미구현 — 안전 아닌 도달률 과소집계. 발급 트랜잭션 변경 수반이라 별도 결정 권장.

## 재현
```bash
cd server && .venv/bin/pytest -q                 # 169 passed
python3 formal/model/m1_prescription.py          # 수정 전 의미론: 반례 3건
python3 formal/model/m1_prescription.py --fixed  # 수정 후: 위반 0
python3 formal/model/m2_governance.py --buggy    # 수정 전: INV-8 2스텝
python3 formal/model/m2_governance.py            # 수정 후: 위반 0
```
