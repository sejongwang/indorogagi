# indoro 안전 명세 v1 — 상태기계·불변식 (구현 독립)

- 도출 근거: [docs/01-dataflow.md](../docs/01-dataflow.md), [docs/08-india-drug-data-foundation.md](../docs/08-india-drug-data-foundation.md), [docs/09-drug-catalog-operations.md](../docs/09-drug-catalog-operations.md)만 사용. 코드는 참조하지 않았다.
- 목적: 이 명세가 검증 기준(what)이고, 구현(how)은 이 명세에 수렴해야 한다. 반례 탐색은 [model/](model/)의 명시적 상태 탐색기가 수행한다.
- 표기: 상태·필드는 코드 예상 리터럴(영문), 설명은 한국어. `⊥`=NULL, `now`=요청 처리 시각.

## 0. 기계 목록과 공유 상태

| 기계 | 주 상태 변수 | 근거 |
|---|---|---|
| M1 처방+토큰 (1:1 단일 기계) | `rx.status ∈ {active, revoked}`(저장), `token.revoked_at`, `token.expires_at`, `token.first_viewed_at`, `rx.version`, `purged` | 01 §5 |
| M2 오프라인 발급·outbox·동기화 | 클라 `outbox[]`, 서버 `events[view.pending]`, `access_tokens` | 01 §2.3 |
| M3 카탈로그 원본·임포트 실행 | `drug_source_records`(불변), `drug_import_runs`, `drug_import_run_records`, `presentation.normalized_projection_sha256`, `presentation.current_import_run_record_id` | 08 §4, 09 §5 |
| M4 사람 검토·운영 생명주기 | `presentation.workflow_review_status`, `presentation.operational_lifecycle_status`, `presentation.record_version`, `drug_review_decisions`(append-only) | 08 §4, 09 §8–9 |
| M5 full-snapshot retirement | `batch.status`, `batch.version`, `candidate.decision`, `candidate.presentation_version_at_creation`, `drug_retirement_batch_events`(append-only) | 08 §4, 09 §7·10 |
| 교차 | `prescription_items.drug_catalog_snapshot_json`(발급 시 1회 기록), `catalog_database_meta.catalog_mode`(싱글턴), `events`(append-only, FK 없음) | 01 §3, 08 §4 |

## 1. M1 — 처방·토큰 전이표

저장 상태는 `active | revoked` 2종뿐. `expired`는 `now > expires_at` 파생, `viewed`는 `first_viewed_at ≠ ⊥` 파생, `purged`는 PII 널링 완료 파생. (01 §5.2)

| # | 트리거 | 가드 | 효과 | 원자성 요구 |
|---|---|---|---|---|
| T1 issue | `(pharmacy_id, client_input_id)` 미존재 | rx(active, version=1) INSERT + token INSERT(UNIQUE, `expires_at = clamp(max(30d, max(duration_days)+7d), ≤90d)`) + `events[rx.created]` | **전부 단일 트랜잭션** (01 §2.2 [A], §6.2) |
| T1r issue-replay | 동일 `(pharmacy_id, client_input_id)` + 동일 본문 | 쓰기 0, 기존 token 반환(`replayed=true`) | 새 토큰 생성 금지 (01 §4.3) |
| T1c issue-conflict | 동일 멱등키 + 다른 본문 해시 | 409 IDEMPOTENCY_CONFLICT, 쓰기 0 | |
| T1t token-collision | 신규 멱등키 + 클라 제공 token이 이미 존재 | 409 TOKEN_COLLISION, 쓰기 0, **조용한 재생성 금지** | 01 §2.3 |
| T2 edit | rx.status=active (미만료; 만료·revoked 편집은 명세상 미정의→거부가 안전) | items 전체 교체, `version+1`, 구버전 JSON을 revisions에 append, `events[rx.edited]`; **token·URL·expires_at 불변** | 단일 트랜잭션 |
| T3 reissue | rx 존재 (만료돼도 허용) ∧ rx.status≠revoked? (01 §4.4는 만료 허용만 명시) | 구 token `revoked_at=now` + rx.status=revoked + 신규 rx/token 생성(`reissue_of`) + `events[rx.revoked, rx.created]` | **구건 종결과 신건 생성이 함께 성립**해야 함 — 부분 실패 시 어느 쪽도 남으면 안 됨 |
| T4 view | token 존재 ∧ revoked_at=⊥ ∧ now≤expires_at | 200 렌더 + `UPDATE ... SET first_viewed_at=now WHERE token=? AND first_viewed_at IS NULL`(원자 선점) + events | view.first는 최대 1회 |
| T4x view-410 | revoked_at≠⊥ ∨ now>expires_at | 410, 쓰기는 events만 | |
| T4p view-pending | token 미존재 | 200 대기 페이지 + `events[view.pending]` | token 열거 신호 은닉 |
| T5 purge | 만료/폐기 +30일, 수동 스크립트 | `patient_label, note, item.note, extra_params.instructions, revisions.payload_json` 널링. **행 삭제 금지, drug_name_raw 보존** | 01 §8.3 |

**금지 전이(안전 핵심)**: `revoked → active`, `expired → active`(expires_at 연장 포함), token 재할당(`token.prescription_id` 변경), revoked_at 널링, 삭제된 행 없음.

## 2. M2 — 오프라인 발급·동기화 전이표

| # | 트리거 | 효과 | 비고 |
|---|---|---|---|
| O1 offline-issue | 클라: token=CSPRNG 128bit 생성, outbox append, QR 즉시 렌더 | short_code 사전 생성 금지 |
| O2 pre-sync scan | 서버: T4p와 동일 (`view.pending` 기록 — token은 자유 TEXT) | |
| O3 flush | 클라: 오래된 순 **단건 순차** POST(T1 계열로 수렴) | 재전송·중복은 T1r로 흡수 |
| O4 sync-commit | 서버: T1 커밋 시 동일 token의 과거 `view.pending` 존재하면 `view.first(src=pre_sync)` 소급 | 커밋과 소급 인정의 원자성 |
| O5 crash | 클라 탭 종료 = outbox 유실(감수 리스크), 서버 측 불변식에는 영향 없어야 함 | 01 §7.4-3 |

## 3. M3 — 카탈로그 원본·임포트 실행 전이표

| # | 트리거 | 가드 | 효과 |
|---|---|---|---|
| C1 dry-run | — | **대상 DB 접근 0**(메모리 DB), 보고서 파일만. run row·presentation 잔존 금지 (09 §1·§4) |
| C2 apply | production이면: approved_packages에 slug exact + records_sha256 + source_metadata_sha256 + approval_status/일자/역할 일치; `catalog_database_meta.catalog_mode`와 scope 일치 | source_records upsert(불변, `(source_id, source_record_id, raw_sha256)` UNIQUE), run INSERT(`(source_id, version, input_sha256, mode)` UNIQUE), run_records INSERT, presentation projection 갱신 |
| C2i apply-replay | 동일 (source, version, package content, 처리 버전들) | **presentation·source record·alias·audit 증가 0** (09 §5) |
| C3 projection-same | 재처리 시 projection SHA 동일 | 새 run+evidence는 남기되 **사람 승인·반려 유지**, record_version 증가(→기존 retirement 후보 stale화) |
| C4 projection-changed | projection SHA 상이 | changed-field diff 기록 + 승인·반려 → `needs_review` 리셋 + record_version 증가 |
| C5 persist-fail | 동일 raw 재사용 run이 persistence 실패 | 실패 run-record는 최신 evidence로 남되 **`current_import_run_record_id` 불변**(마지막 성공 적용 run 유지) |
| C6 excluded-reappear | 새 package에 ID는 있으나 excluded/quarantined | "누락" 아님(후보 생성 금지). 마지막 정상 검색 필드 보존, 기존 approval→needs_review, **사람 rejected는 유지** (09 §7) |

> **명세 긴장점 S1**: C4(08 §4)는 "승인·반려를 needs_review로 되돌린다", C6(09 §7)는 "rejected 유지". C4가 rejected까지 리셋하면 사람이 차단한 레코드가 상류 변경만으로 검색에 재유입된다(INV-8 잠재 위반). 검증 대상.

## 4. M4 — 사람 검토·운영 생명주기 전이표

`workflow_review_status`(검토): `needs_review | unverified? | approved | rejected`. `operational_lifecycle_status`(운영): `active | inactive | retired`. 두 축은 독립(예: `approved+inactive`, `needs_review+active` 모두 가능 — 09 §10).

| # | 트리거 | 가드 | 효과 |
|---|---|---|---|
| R1 approve/reject/re-review | `expected_record_version == record_version` ∧ reviewer·역할·reason·note 존재 ∧ projection JSON/hash 존재(v6 이전 레코드 차단) | 상태 변경 + `drug_review_decisions` append **같은 트랜잭션**, record_version+1 |
| R1s stale | expected ≠ current | 409, **어떤 상태도 덮어쓰지 않음** |
| L1 inactivate / L2 reactivate | R1과 동일 가드(단 projection 부재여도 lifecycle 결정은 허용 — 09 §10.7) | active↔inactive + audit append, version+1 |
| L3 retire | **일반 UI 금지** — 승인된 batch apply(M5)만 | |
| L4 un-retire | 미지원(운영 예외) | `retired → active` 전이 부재가 명세 |

**감사 원장**: `drug_review_decisions`·`drug_retirement_batch_events`는 append-only, UPDATE/DELETE는 DB 트리거로 거부. (08 §4, 09 §8)

## 5. M5 — retirement batch 전이표

batch: `proposed → under_review? → approved → applied`, 어느 시점이든 `→ cancelled`. candidate: `pending → {keep_active, retire, needs_investigation}`.

| # | 트리거 | 가드 | 효과 |
|---|---|---|---|
| B1 propose | 후속 full snapshot ∧ 이전 성공 full에 포함됐던 비-retired presentation이 새 파일에 없음 ∧ 해당 presentation이 열린 batch(proposed/under_review/approved)에 없음 | batch(proposed) + candidates(pending) 생성. **presentation lifecycle·검색 불변** |
| B1x no-propose | delta mode ∨ 첫 full(baseline 부재) ∨ excluded/quarantined 재등장 | 후보 생성 금지 (`retirement_baseline_missing=true` 보고) |
| B2 decide | 후보 생성 시점 version == 화면 표시 version == 현재 version | candidate 결정 + presentation audit + `candidate_decided` batch event **같은 트랜잭션**, batch version+1 |
| B3 approve | pending=0 ∧ needs_investigation=0 ∧ stale candidate=0 ∧ presentation version 불변 | batch→approved (별도 approver) |
| B4 apply | batch=approved, **한 트랜잭션에서 모든 candidate version 재확인** | `retire` 후보만 `operational_lifecycle_status=retired`, keep_active 불변. 하나라도 stale ∨ audit 실패 → **전체 rollback** |
| B4i apply-retry | 이미 applied | presentation version·audit 증가 0 |
| B5 cancel | — | batch→cancelled; 이후 full에서 재제안 가능 |

batch ledger의 expected/result version 체인은 생성부터 현재까지 **연속**이어야 한다. (09 §10.3)

## 6. 교차 기계 불변식 (검증 대상 형식화)

`∀` 도달 가능한 전 상태(초기 상태에서 임의 인터리빙·재시도·크래시로 도달)에서:

| ID | 형식화 | 근거 |
|---|---|---|
| **INV-1** QR 불변 | (a) `token.prescription_id`는 최초 기록 후 불변. (b) 렌더 내용 변경은 반드시 `version+1 ∧ revisions append ∧ rx.edited` 동반(조용한 변경 부재). (c) 이미 존재하는 token으로의 신규 바인딩 시도는 409(무효과). | 01 D4·D5·§2.3 |
| **INV-2** 재활성 금지 | `revoked_at ≠ ⊥`인 token이 이후 상태에서 `revoked_at = ⊥`가 되지 않음 ∧ `rx.status=revoked → 이후에도 revoked` ∧ `expires_at`은 발급 후 불변(연장 = expired→active 재활성) ∧ 어떤 전이도 410 판정 집합을 축소하지 않음. | 01 §5.2 |
| **INV-3** 스냅샷 불변 | 카탈로그 기계(M3–M5)의 어떤 전이도 `prescription_items.*`(특히 `drug_catalog_snapshot_json`, `drug_name_raw`)를 변경하지 않음. 환자 렌더 함수는 live presentation을 읽지 않음. | 08 §4, 09 원칙3 |
| **INV-4** 자동 retire 금지 | `operational_lifecycle_status=retired`로의 전이는 B4(approved batch apply)에서만 발생. B1(후보 생성)·C2(임포트)·C6 직후 상태에서 retired 증가 없음. | 08 결정10, 09 §7 |
| **INV-5** 중복 발급 수렴 | 동일 `(pharmacy_id, client_input_id)`의 T1 계열 임의 횟수·임의 시점 재시도(크래시 포함) 후: rx 행 ≤1, token 행 ≤1, `rx.created` 이벤트 ≤1, 반환 token 동일. | 01 D10·§4.3 |
| **INV-6** 원자성 | (a) T1 커밋 ↔ `rx.created` 존재 동치. (b) R1/B2/B4 커밋 ↔ 대응 audit 행 존재 동치. 크래시 지점 어디서도 "상태만 변경 & audit 없음" 또는 "audit만 & 상태 없음" 불가. | 01 §6.2, 08 결정9, 09 §8·§10 |
| **INV-7** stale 검토자 | expected version 불일치 결정은 어떤 쓰기도 하지 않음(409). 두 검토자가 같은 version에서 동시 제출 시 최대 1건 성공. batch approve/apply는 결정 이후 presentation 변경이 있으면 거부. | 08 결정9, 09 §8·§10 |
| **INV-8** 검색 격리 | production 검색 결과에 `usage_scope=demo` ∨ `workflow_review_status=rejected` ∨ `operational_lifecycle_status≠active` presentation이 포함되지 않음. demo는 명시적 demo 컨텍스트에서만. 레거시 `drugs` fail-open 금지. **rejected의 리셋 경로(S1) 포함 검증.** | 01 §4.5, 08 §7, 09 §10 |
| **INV-9** 부분 상태 금지 | 임의 전이의 임의 크래시 지점에서 재기동 후 상태는 "전이 전" 또는 "전이 후"와 동일(중간 상태 관측 불가). dry-run은 대상 DB 파일을 생성·변경하지 않음. | 09 §4·§10.7 |
| **INV-10** 불변 provenance | 커밋된 audit/ledger/이벤트 행은 이후 어떤 전이로도 수정·삭제되지 않음(purge의 명시 널링 대상 제외). import run은 당시 승인 스냅샷을 보존. `current_import_run_record_id`는 성공 적용 run만 가리킴(C5). | 08 §4, 09 원칙10 |

## 7. 환경(적대) 모델

탐색기가 주입하는 비결정성:

1. **동시성**: 서로 다른 액터(약사 A/B, 검토자 A/B, approver, 임포터, 환자, purge 스크립트)의 임의 인터리빙. SQLite 직렬화 전제라 트랜잭션은 원자 단위로 모델링하되, **트랜잭션 경계 밖의 read-then-write 시퀀스**는 분리 스텝으로 모델링(TOCTOU 노출).
2. **재시도·중복**: 모든 클라이언트 요청은 응답 유실 후 동일 payload 재전송 가능(멱등키 동일). outbox flush는 임의 횟수 반복.
3. **재정렬**: outbox 순차 전송 실패 시 부분 전송, pre-sync 스캔이 발급보다 선행.
4. **크래시**: 모델의 각 다중 트랜잭션 오퍼레이션 사이 지점에서 프로세스 중단 → 재기동 후 클라이언트 재시도.
5. **시간**: expires_at 경계 전/후, purge 시점 도달.

## 8. 코드로 증명 불가한 가정 (검증 범위 밖 — 최종 보고서에 이관)

- SQLite WAL 자체의 내구성·fsync 계약, 파일시스템 손상.
- 클라 `crypto.getRandomValues`의 품질(Math.random 폴백 금지는 코드리뷰 기준 — 서버에서 관측 불가).
- loopback 운영 UI 앞에 reverse proxy를 두지 않는다는 운영 수칙 (09 §10).
- 라이선스·법무·약사 검수의 타당성 (09 §11 책임 분리).
- 봉투 번호 기입 등 현장 절차.
