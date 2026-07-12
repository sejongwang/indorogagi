# 반례 목록 (심각도 랭킹) — 2026-07-11 검증 실행

각 반례는 세 겹으로 검증됐다: ① 정적 추적(소스 라인), ② 명시적 상태 탐색기의 최소 트레이스([model/](model/)), ③ 실서버 회귀 테스트(red 재현 → fix → green). **4건 전부 이번 실행에서 수정 완료.**

## CX-1 — 만료된 처방이 수정으로 부활한다 (INV-2) · HIGH · FIXED

- **위반 불변식**: 만료·폐기된 처방은 어떤 전이로도 다시 active가 되지 않는다 (docs/01 §5.2 "구 처방은 어떤 전이로도 active로 돌아오지 않는다").
- **최소 트레이스** (`model/m1_prescription.py`, 5스텝): `create(short TTL)` → `tick×3`(만료 경과) → `edit(long duration)` → 파생 상태 expired→**active**.
- **HTTP 재현**: `POST /api/prescriptions`(duration 5d) → 만료 경과 → 환자 링크 410 → `PUT /api/prescriptions/{id}`(duration 90d) → **200** → 같은 링크가 200으로 부활. 환자에게 이미 죽었다고 안내된 QR(공유본 포함)이 편집된 내용으로 되살아난다.
- **원인**: 구 `db.edit_prescription`이 revoked만 거부하고 만료를 거부하지 않으면서, 4단계에서 `expires_at`을 무조건 재산정(`UPDATE access_tokens SET expires_at=?`)했다.
- **수정** (최소): [db.py:1536](../server/app/db.py) `edit_prescription` — ① `token_status==expired` 가드(`PrescriptionExpiredError`→라우터 410 `LINK_EXPIRED`), ② 경계 레이스 백스톱: `UPDATE ... SET expires_at=? WHERE ... AND expires_at > now` + rowcount 검사(살아 있는 토큰만 재산정), ③ `UPDATE prescriptions ... WHERE status='active'` + rowcount(동시 reissue 레이스에서 폐기 구건 내용 변경 차단).
- **회귀 테스트**: [test_lifecycle_invariants.py](../server/tests/test_lifecycle_invariants.py) `test_edit_expired_prescription_does_not_resurrect_token`, `test_edit_active_prescription_still_works`(정상 경로 보존).

## CX-2 — 상류 변경이 사람의 검색 차단을 자동 해제한다 (INV-8) · HIGH · FIXED

- **위반 불변식**: rejected 레코드는 production 검색에 새어들지 않는다 — 취지: 사람이 차단한 것은 사람만 푼다 (docs/09 §7 근거).
- **최소 트레이스** (`model/m2_governance.py --buggy`, 2스텝): `review_rejected(p0)` → `import_changed(p0)` → p0 = needs_review = **검색 노출**(human_blocked 상태로).
- **실행 재현**: 약사가 위험 유사명 레코드를 reject(검색 차단) → 출처가 표시 필드 하나 변경 → 후속 임포트 → `record_source_refresh`가 rejected를 needs_review로 리셋 → needs_review는 검색 가능 상태(검색 필터가 `<>'rejected'`만 거름, [drug_catalog.py:1562](../server/app/drug_catalog.py)) → 사람 개입 없이 재유입, 처방 선택 가능.
- **원인**: 문서 모순의 코드 유입 — docs/08 §4(승인·반려 리셋)와 docs/09 §7(rejected sticky)이 충돌했고, 코드가 경로별로 각각을 구현했다(quarantine 경로만 sticky).
- **수정** (최소): [catalog_governance.py:324](../server/app/catalog_governance.py) `record_source_refresh` — projection 변경 시 **approved만** needs_review로 리셋, rejected는 유지(quarantine 경로와 동일 원칙). 재개는 기존 `review_requested`(rejected→needs_review) 사람 전이로만. 문서 2곳 동기화(docs/08 §4, docs/09 §5).
- **회귀 테스트**: [test_catalog_governance.py](../server/tests/test_catalog_governance.py) `test_projection_change_preserves_human_rejection_and_search_block`, `test_projection_change_still_reopens_human_approval`(approved 리셋 방향 보존).
- **모델 확인**: BUGGY 모드 2스텝 검출 / FIXED 모드 120만 상태 위반 0.

## CX-3 — 멱등 replay가 다른 본문을 조용히 삼킨다 (T1c/INV-1b) · MEDIUM · FIXED

- **위반 계약**: 동일 멱등키 + 다른 본문 해시 = 409 `IDEMPOTENCY_CONFLICT` (docs/01 §4.1). 위반 시 "타임아웃 → 같은 폼에서 오타 수정 → 재제출" 시나리오에서 수정본이 조용히 유실되고 약사는 성공으로 인식한다(환자는 구 내용 열람).
- **최소 트레이스** (`model/m1_prescription.py`, 2스텝): `create(c1,short)` → `create(c1,long)` → 200 replay(본문 불일치 무시).
- **원인**: `create_prescription`의 replay가 본문 비교 없이 기존 결과를 반환. 참고: server/README.md 80% 범위표가 이를 명시적 스코프 컷("항상 replay")으로 기록하고 있었다 — 조용한 결함이 아니라 설계 문서(01 §4.1)와 프로토타입 범위의 문서화된 모순이었고, 이번 수정으로 설계 문서 계약을 구현했다(README 동기화 완료).
- **수정** (최소): [db.py:99](../server/app/db.py) `client_request_sha256`(내용 결정 필드만 — `client_metrics`·`issued_at_client` 제외: outbox 재전송은 retry_count만 달라도 replay돼야 함, §4.3; `token` 포함: 같은 키+다른 토큰 = 클라 재생성 신호 §2.3) + `prescriptions.client_request_sha256` additive 컬럼 + `_replay_or_conflict` 비교(해시 없는 구 행은 호환 replay). 라우터 409 매핑 [api.py:308](../server/app/routers/api.py).
- **회귀 테스트**: `test_same_idempotency_key_with_different_items_is_conflict`, `test_same_key_same_body_replays_even_with_different_metrics`, `test_offline_replay_with_same_client_token_replays`.

## CX-4 — 이중 reissue가 폐기 시각·감사를 덮어쓰고 대체본을 분기시킨다 (INV-10) · MEDIUM · FIXED

- **위반 불변식**: 되돌릴 수 없는 결정은 불변 provenance를 유지한다. 구 `reissue`는 가드 없는 `UPDATE prescriptions SET status='revoked'` / `UPDATE access_tokens SET revoked_at=?`로 ① 원 폐기 시각을 덮어쓰고 ② `rx.revoked` 감사를 중복 발생시키고 ③ 한 구건에서 두 개의 활성 대체본(reissue_of 동일)을 분기시켰다(이중탭·상이 cid 재전송).
- **최소 트레이스** (`model/m1_prescription.py`, 3스텝): `create(c1)` → `reissue(rx0,c3)` → `reissue(rx0,c2)` → `ev_revoked=[2,…]`, 대체본 2개.
- **수정** (최소): [db.py:1466](../server/app/db.py) `reissue` — ① 사전 가드: 이미 revoked면 `ValueError`(라우터 410 `LINK_REVOKED` — "활성 대체본을 재발급하라"), ② 레이스 백스톱: `UPDATE ... WHERE status != 'revoked'` + rowcount, `UPDATE ... WHERE revoked_at IS NULL`(같은 커밋 단위라 부분 효과 없음). 만료 건 reissue는 §4.4대로 계속 허용.
- **회귀 테스트**: `test_second_reissue_of_revoked_prescription_is_rejected`, `test_reissue_retry_with_same_cid_still_replays`(멱등 재시도 보존), `test_reissue_of_expired_prescription_still_allowed`.

## CX-5 — 발급된 항목이 live 카탈로그 재조회로 조용히 바뀐다 (INV-1·INV-3) · MEDIUM · FIXED

- **위반 불변식**: 이미 발급한 안내가 카탈로그 갱신으로 조용히 변하지 않는다 (docs/01 §4.6 "환자 렌더는 live drug_presentations를 다시 조회하지 않는다", docs/08 §4).
- **발견**: 적대적 헌트의 patient-surface·snapshot-boundary 두 에이전트가 [db.py:1386](../server/app/db.py)에 독립 수렴.
- **트레이스**: `prescription_items.drug_id`는 있으나 `drug_catalog_snapshot_json`이 NULL인 행(스냅샷 컬럼 도입 이전의 legacy 발급 행이 정확히 이 형태) → `_build_bundle`이 `get_presentation_snapshot(conn, drug_id)`로 **현재** presentation을 읽음(심지어 `snapshot_at`을 매 호출 `_now()`로 스탬프) → 이후 카탈로그 편집·retirement가 이미 발급된 QR의 표시명·제형·route 라벨(eye/ear drop 등)을 버전 업·revision·ETag 변경 없이 소급 변경.
- **수정** (최소): [db.py:1380 부근](../server/app/db.py) `_build_bundle` — 발급 항목의 live 카탈로그 재조회 fallback(`get_presentation_snapshot` + legacy `drugs` 조회) 제거. 스냅샷이 저장된 항목만 그 스냅샷을 렌더하고, 스냅샷 없는 legacy 행은 행에 동결된 `drug_name_raw` 등으로만 렌더(enrichment 생략). 신규 발급은 `_insert_items_tx`가 이미 drug_id와 스냅샷을 함께 기록하므로 정상 경로 무영향.
- **회귀 테스트**: [test_snapshot_immutability.py](../server/tests/test_snapshot_immutability.py) `test_issued_item_without_stored_snapshot_never_rerenders_from_live_catalog`, `test_issued_item_with_stored_snapshot_is_already_immutable`(정상 경로 보존).

## CX-6 — 읽기 전용 의도의 retirement 프리뷰가 apply를 커밋한다 (INV-9) · MEDIUM · FIXED

- **위반 불변식**: 실패한 트랜잭션/명령은 부분 상태를 남기지 않는다; dry-run은 대상 DB를 생성·변경하지 않는다 (docs/09 §4·§8).
- **발견**: import-gate 헌터 실증 재현(rc=SystemExit(2)이지만 대상 DB에 `drug_import_runs`(completed)·presentation·`catalog_database_meta`(demo 브랜딩) 커밋됨).
- **트레이스**: 운영자가 읽기 전용 프리뷰를 의도하되 `--dry-run`을 빠뜨림(apply가 문서화된 기본) → [catalog_import.py](../server/scripts/catalog_import.py) `run_catalog_import`가 `import_catalog(dry_run=False)`를 커밋한 **뒤에야** `--retirement-preview-db requires --dry-run` 검증에 도달 → ValueError → 보고서 없이 실패. 운영자는 실패로 인식하나 production/demo 상태가 바뀌었고, 승인된 full-snapshot production 패키지면 retirement batch까지 제안됨.
- **수정** (최소): 플래그 조합 검증 3종(`not dry_run` / `snapshot_mode != full` / `preview_path` 부재)을 `init_db`·`import_catalog` 앞으로 끌어올림. read-only 프리뷰 실행부만 기존 위치 유지.
- **회귀 테스트**: [test_catalog_import_scripts.py](../server/tests/test_catalog_import_scripts.py) `test_retirement_preview_flag_without_dry_run_leaves_no_committed_state`.

## CX-7 — meta 행 없는 production DB가 demo로 브랜딩된다 (INV-8) · MEDIUM · FIXED

- **위반 불변식**: demo 레코드가 production 검색·저장 scope에 섞이지 않는다 (docs/08 결정1·§7, docs/09 §1).
- **발견**: import-gate 헌터 실증 재현.
- **트레이스**: `catalog_database_meta` 행이 없고 production source가 이미 있는 DB(meta 테이블 도입 커밋 1ac5bf0 이전에 seed된 DB의 상태 — v4 마이그레이션이 테이블은 추가하나 행을 백필하지 않음) → demo first-apply가 [drug_catalog.py:1127](../server/app/drug_catalog.py) `_ensure_database_mode`의 missing-row 분기에 진입 → 이 분기가 production 방향만 보호(demo source 존재 시 production 거부)하고 demo 방향은 무방비 → DB를 demo로 브랜딩, demo 레코드가 production 레코드 옆에 앉고 이후 production refresh는 영구 거부. 노출: `X-Pharmacy-Id: ph-demo-001` 헤더(무인증 문자열 비교)면 production 서버 검색에 demo 레코드 노출.
- **수정** (최소): 대칭 가드 추가 — missing-row 분기에서 production source가 이미 있으면 demo 브랜딩 거부(기존 demo→production 가드의 거울).
- **회귀 테스트**: `test_metaless_db_with_production_sources_refuses_demo_first_apply`.

## 문서-코드 편차 (수정하지 않음 — 판단 필요 항목)

- **D1 (KPI, MEDIUM-LOW)**: docs/01 §2.3 O4 — 오프라인 동기화 커밋 시 과거 `view.pending`을 `view.first(src=pre_sync)`로 소급 인정하는 로직이 **미구현**(`pre_sync` 문자열이 코드에 없음). 환자가 동기화 전 스캔 후 재방문하지 않으면 도달률이 과소집계된다. 안전 불변식 위반은 아니고 KPI 정확성 문제. 발급 트랜잭션에 ~10줄 추가로 구현 가능하나 발급 경로 변경이라 별도 결정 권장.
- **D2 (설계 확인, LOW)**: active 처방의 편집이 `expires_at`을 연장할 수 있다(코드 주석은 D6 인용, 문서는 발급 시 1회 산정만 명시). 부활은 아니므로(active→active) 이번 수정에서 유지했다. 의도 여부 팀 확인 권장.

- **D4 (/c 열거 오라클, HIGH — 보안, 문서화된 이연)**: patient-surface 헌터 확정. `/c/{short_code}`가 유효 코드는 302→`/p/{token}`, 무효는 404로 갈려 40-bit short_code 공간의 **무제한 열거 오라클**이며 적중 시 실제 환자 포스터가 렌더된다. docs §4.9/§5.1은 short_code를 `(IP,코드) 10회/10분` 실패 제한과 "한 몸"으로 규정하지만, server/README.md 80% 범위표가 rate limit/tarpit을 명시적 제외로 기록(조용한 결함 아님). 본토큰(128-bit)이 실제 보안 경계라 파일럿 2곳 규모에서는 감내 가능하나, **정식 출시 전 §4.9 rate limit 구현이 short_code 사용의 전제**다. rate limit 인프라 신설은 스코프 확장이라 이번 자율 실행에서 구현하지 않았다 — 잔여 리스크로 이관(verdict.md).

- **D5 (OD_NIGHT 기동 마이그레이션, LOW — 설계상 승인됨)**: snapshot-boundary 헌터 지적. init_db가 기동 시 발급된 `prescription_items`의 OD_NIGHT dose를 E→H로 옮기는 비버전 데이터 마이그레이션을 수행(버전 업·revision·감사 없음 → 기술적으로 INV-1 접촉). 그러나 docs §3.4가 이를 명시적으로 승인한 멱등 1회 교정으로 규정하고(H=0∧E>0 명백 행만, 모호 행은 건드리지 않음), 프로토타입 실발급 이전 교정이라 실무 위험이 없다. 설계 결정으로 수용 — 정식 발급 개시 후에는 이런 소급 교정을 금지하고 보상 마이그레이션+감사로 전환할 것.
- **D3 (문서화된 이연)**: docs/01 §5.2·§8.3의 purge 수동 스크립트는 미구현이며 server/README.md 80% 범위표에 명시적 제외로 기록돼 있다(조용한 누락 아님). docs/01 §9.2의 D+50 백로그 시한이 구속 조건 — 첫 만료 코호트 도래 전 구현 필요. PII 널링 범위는 §8.3 명세 그대로 구현할 것(revisions.payload_json 포함).
