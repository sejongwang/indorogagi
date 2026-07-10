# 의약품 카탈로그 운영 가이드

- 대상: indoro 개발자, 데이터 운영자, 현지 약사 검수자
- 기준일: 2026-07-10
- 관련 설계: [`08-india-drug-data-foundation.md`](08-india-drug-data-foundation.md)

## 운영 원칙

1. 소스 라이선스 승인과 의료 내용 검수는 서로 다른 게이트다. 하나를 통과해도 다른 하나를 대체하지 않는다.
2. 원본 레코드는 수정하지 않는다. 새 버전은 새 `raw_sha256`의 source record로 추가한다.
3. canonical 레코드가 바뀌어도 기존 처방의 발급 스냅샷은 바꾸지 않는다.
4. 삭제보다 `inactive`, 불확실한 병합보다 `needs_review`를 우선한다.
5. dry-run 보고서를 검토하기 전 운영 DB에 apply하지 않는다.
6. Tier 3 자료와 승인되지 않은 Tier 2 자료는 운영 scope로 임포트하지 않는다.
7. source 계산 상태와 사람 운영 결정을 한 status에 섞지 않는다. `review_status/lifecycle_status`는 원본 해석, `workflow_review_status/operational_lifecycle_status`는 사람 승인과 검색 정책이다.
8. 사람의 상태 변경은 서비스 함수가 presentation update와 append-only audit insert를 같은 transaction에서 수행할 때만 허용한다. 수동 status SQL은 표준 운영 절차가 아니다.
9. full snapshot 누락은 retirement 후보일 뿐 삭제·비활성화 명령이 아니다. 후보 결정, batch 승인, 별도 apply가 모두 끝나야 `retired`가 된다.
10. 현재 적용된 projection evidence와 가장 최근 import evidence를 구분한다. `current_import_run_record_id`는 성공적으로 적용된 run-record만 가리키며, 동일 raw의 후속 실패·격리 run은 이 포인터를 덮어쓰지 않는다.

### 검토·retirement 정보구조 결정

| 기준 | A. 단일 audit table | B. 검토 결정과 retirement batch 분리 |
| --- | --- | --- |
| 잘못된 비활성화 방지 | 개별 review와 snapshot 누락의 의미가 같은 action 열에 섞인다 | 후보 결정, batch 승인, apply를 서로 다른 상태와 version으로 막는다 |
| 결정 복원 | 시간순 조회는 단순하지만 batch 경계와 unresolved 후보를 재구성해야 한다 | presentation 결정 원장과 batch 원장을 각각 직접 조회할 수 있다 |
| 처방 snapshot 보호 | 별도 제약을 추가해야 한다 | catalog projection만 바꾸고 발급 snapshot에는 쓰지 않는 경계가 분명하다 |
| SQLite 운영 | 테이블 수가 적다 | 관계가 네 개 늘지만 단순 FK·CHECK·transaction으로 운영 가능하다 |
| 테스트 가능성 | nullable action별 필드와 암묵적 규칙이 많아진다 | candidate → approve → apply 단계와 stale version을 독립 검증할 수 있다 |
| Postgres 이전 | 범용 event 해석 코드를 함께 옮겨야 한다 | 명시적 관계·version·ledger를 그대로 옮길 수 있다 |
| 구현 복잡도 | 낮음 | 중간이며 범용 workflow engine보다는 작다 |

**선택: B.** `drug_review_decisions`는 presentation의 사람 결정을, `drug_retirement_batches`와 `drug_retirement_candidates`는 full snapshot 누락 검토를, `drug_retirement_batch_events`는 batch 상태 이력을 담당한다. 이 분리가 자동 retirement를 구조적으로 어렵게 하고, 후보가 해결되지 않았거나 evidence version이 바뀐 batch의 승인을 차단한다. 별도 범용 승인 엔진이나 의약품 추천 로직은 도입하지 않는다.

## 1. 최초 설치와 데모 카탈로그

먼저 공식 NPPA 원본과 사람이 검토한 전사본이 현재 package에 정확히 대응하는지 확인한다. 수집기는 NPPA HTTPS 호스트만 허용하고 시스템 trust store를 사용하는 `curl`로 원본을 받으며, 크기·PDF signature·SHA-256가 pinned manifest와 다르면 실패한다. 상업 사이트는 이 경로에 추가하지 않는다.

```bash
cd server
uv run python scripts/fetch_catalog_source.py \
  ../data/catalog/nppa-anti-diabetes-2026-03.source-manifest.json \
  --output-dir var/catalog-sources
uv run python scripts/nppa_package.py --check
```

원본 PDF는 git에 넣지 않고 `server/var/catalog-sources/`에 캐시한다. 정본 provenance는 source manifest의 URL, ETag, Last-Modified, byte 수와 SHA-256이다. CSV는 PDF 11행을 사람이 대조한 전사본이며 `nppa_package.py --check`는 CSV+source metadata로 생성한 결과가 committed package와 semantic exact match인지 확인한다. layout/OCR 추정만으로 CSV나 package를 자동 갱신하지 않는다.

```bash
cd server
uv sync
uv run python scripts/seed_import.py --dry-run --report-dir ../data/reports/production-dry
uv run python scripts/seed_import.py --apply --report-dir ../data/reports/production
uv run python scripts/seed_import.py --apply --report-dir ../data/reports/production-replay
uv run pytest -q
```

production 기본 명령은 NPPA만 `server/var/indoro.db`에 적재한다. 합성 fixture는 반드시 별도 demo DB에 넣는다.

```bash
uv run python scripts/seed_import.py --catalog-scope demo --dry-run \
  --report-dir ../data/reports/demo-dry
uv run python scripts/seed_import.py --catalog-scope demo --apply \
  --db-path var/indoro-demo.db --report-dir ../data/reports/demo
uv run python scripts/seed_import.py --catalog-scope demo --apply \
  --db-path var/indoro-demo.db --report-dir ../data/reports/demo-replay
INDORO_DB_PATH=var/indoro-demo.db uv run uvicorn app.main:app --port 8600
```

`data/catalog/indoro-synthetic-demo-v1.json`은 가상 상품·가상 성분으로만 구성되고 `ph-demo-001`에서만 검색된다. 상업 사이트 참고분이 섞였던 기존 `data/drugs-seed.json`은 재사용 권리가 확인되지 않아 저장소와 importer에서 제거했다.

demo apply는 `scripts/seed_import.py`와 `scripts/catalog_import.py` 모두 명시적 비기본 `--db-path`를 요구한다. production 기본 DB 경로를 넘기거나 경로를 생략하면 중단한다. dry-run은 대상 DB와 무관한 메모리 DB를 사용하므로 `--db-path` 없이도 실행할 수 있다.

모든 apply는 `catalog_database_meta`의 singleton 행에 SQLite 파일의 역할을 `production` 또는 `demo`로 기록하고 이후 실행마다 이를 검증한다. 이미 production으로 표시된 DB에는 demo package를, demo로 표시된 DB에는 production mode apply를 실행할 수 없다. 단일 production package를 격리 demo DB의 공식 비교 데이터로 넣는 고급 경로에서는 `scripts/catalog_import.py ... --database-mode demo --db-path <non-default.db>`를 명시하며, 일반 데모 구성은 위의 `seed_import.py --catalog-scope demo`가 이 역할을 내부적으로 처리한다.

예상 산출물:

```text
data/reports/
  production-dry/drug-catalog-quality.{json,md}
  production/drug-catalog-quality.{json,md}
  production-replay/drug-catalog-quality.{json,md}
  demo-dry/drug-catalog-quality.{json,md}
  demo/drug-catalog-quality.{json,md}
  demo-replay/drug-catalog-quality.{json,md}
```

dry-run은 원본 수, 제외·격리, `needs_review`, 중복 후보와 누락 필드를 계산한다. 요청한 SQLite를 열거나 생성하지 않고 메모리 DB에서 교차 레코드 품질 검사까지 수행하므로 canonical 테이블, import-run, 약국 데이터는 운영 DB에 남지 않는다. 보고서 파일만 지정한 디렉터리에 쓴다.

## 2. 새 출처 등록 전 체크리스트

다음 항목이 하나라도 비어 있으면 운영 apply를 중단한다.

- 운영 기관과 담당 연락처
- 원본 페이지 URL과 실제 파일 URL
- 자료 제목, 버전, 발행일, 출처 갱신일
- 접근일
- 라이선스 원문 URL과 파일별 라이선스 표시
- 상업적 서비스 사용 가능 여부
- 변형, 캐시, 재배포, 출처 표시 의무
- 제3자 저작권·상표·시장조사 데이터 포함 여부
- API/다운로드 사용 조건과 자동 수집 허용 여부
- 원본 ID의 안정성
- 증분 갱신 또는 철회 공지 방식
- 현지 법무 승인자와 승인일
- 현지 약사 검수 범위와 검수자
- 원본 artifact manifest의 byte 수·SHA-256와 결정적 변환/수동 검토 경계

GODL이나 기관 저작권 정책이 있더라도 **해당 파일에 적용되는지** 확인한다. 웹 페이지에 보인다는 이유만으로 다운로드·스크래핑 권한이 생기지 않는다.

## 3. 임포트 입력 계약

소스 메타데이터의 최소 필드:

```json
{
  "slug": "example-official-source",
  "name": "Human-readable source name",
  "operator": "Publishing authority",
  "tier": 1,
  "usage_scope": "production",
  "reuse_status": "approved",
  "license_name": "GODL-India or file-specific terms",
  "license_url": "https://example.gov.in/license",
  "attribution_text": "Source: publishing authority",
  "version": "2026-07",
  "published_at": "2026-07-01",
  "source_updated_at": "2026-07-01",
  "accessed_at": "2026-07-10",
  "legal_review_required": false,
  "input_uri": "https://example.gov.in/file.csv",
  "snapshot_mode": "delta",
  "package_schema_version": "2"
}
```

production source에는 유효한 `accessed_at`이 필수이고 `published_at`, `source_updated_at`, `declared_as_of` 중 제공된 날짜가 접근일보다 미래일 수 없다. `source_metadata_sha256()`는 slug, 운영 기관, scope, reuse·license·attribution, input/source URL, 법무 상태, version, 날짜, `snapshot_mode`, `package_schema_version`을 함께 고정한다. 따라서 production 패키지의 delta/full 의미를 바꾸면 기존 승인 해시를 재사용할 수 없다.

NPPA package는 여기에 `source_artifact_manifest`, `source_artifact_sha256`, `source_artifact_bytes`, `transformation_method`도 포함하며 이 필드도 승인 해시에 들어간다. 원본이 변경되면 다음 순서로만 갱신한다: 새 artifact를 별도 버전으로 pin → PDF와 reviewed CSV 수동 대조 → `nppa_package.py --write` → `--check` → records/source metadata 해시 재계산 → 현지 법무·약사/데이터 거버넌스 승인 → `approved_packages` 갱신 → dry-run. 기존 버전의 원본·CSV·package를 덮어쓰지 않는다.

레코드의 최소 형태:

```json
{
  "source_record_id": "stable-source-id",
  "brand_name": "Example brand or official product name",
  "generic_name": "Ingredient A + Ingredient B",
  "ingredients": [
    {"name": "Ingredient A", "strength": "500 mg", "ordinal": 1},
    {"name": "Ingredient B", "strength": "125 mg", "ordinal": 2}
  ],
  "strength": "500 mg + 125 mg",
  "form": "tablet",
  "route": "oral",
  "release_modifier": null,
  "manufacturer": null,
  "marketer": null,
  "package": null,
  "aliases": [],
  "status": "active"
}
```

원본에 구조화 성분별 함량이 없다면 `/`의 위치만 보고 성분에 강도를 자동 배정하지 않는다. `generic_name`과 `strength` 원문은 보존하고 레코드를 `needs_review`로 분류한다.

### production exact-hash 승인 레지스트리

패키지의 `source.reuse_status="approved"`는 자기 선언일 뿐 충분하지 않다. [`../data/drug-sources.json`](../data/drug-sources.json)의 독립된 `approved_packages`에 다음 항목이 있어야 production dry-run과 apply가 모두 진행된다.

```json
{
  "approved_packages": {
    "example-official-source": {
      "approval_status": "approved",
      "approved_at": "2026-07-10",
      "approved_by_role": "indoro data governance review",
      "records_sha256": "<canonical records hash>",
      "source_metadata_sha256": "<source metadata hash>"
    }
  }
}
```

`source_registry_slug`와 `scope_note`는 승인 근거와 허용 필드 범위를 사람이 추적하기 위한 권장 메타데이터다. importer의 필수 판정 필드는 위 최소 entry와 package slug/version 매칭이다.

해시는 importer와 같은 canonical JSON 함수로 계산한다.

```bash
cd server
uv run python -c 'import json; from pathlib import Path; from app.drug_catalog import records_sha256, source_metadata_sha256; p=json.loads(Path("../data/catalog/PACKAGE.json").read_text()); print(records_sha256(p["records"])); print(source_metadata_sha256(p["source"]))'
```

1. 파일별 라이선스, 제3자 권리, 자동 수집 조건과 `accessed_at`을 확인한다.
2. 레코드와 source metadata를 약사·법무 검토 범위에 맞게 고정한다.
3. 두 해시, 승인일, 승인 역할, 허용 필드 범위를 승인 레지스트리에 기록한다.
4. dry-run을 실행하고 보고서와 해시가 일치하는지 확인한다.
5. 파일 또는 source metadata가 바뀌면 기존 승인을 재사용하지 않고 새 해시로 다시 검토한다.

현재 NPPA 패키지의 고정값은 [`../data/drug-sources.json`](../data/drug-sources.json)이 정본이다. 승인 entry와 전체 레지스트리 해시, source/license snapshot은 apply 당시 `drug_import_runs`에 복사된다.

## 4. dry-run 검토

exact-hash 승인 레지스트리에 등록된 입력을 실제 DB에 넣기 전에 `scripts/catalog_import.py <package.json> --dry-run` 또는 임포터의 `dry_run=True` 경로를 실행한다. 두 경로는 같은 정규화·승인 계약을 사용한다. dry-run은 법무·의료 승인을 대신하지 않는다.

```bash
cd server
uv run python scripts/catalog_import.py ../data/catalog/nppa-anti-diabetes-2026-03.json \
  --dry-run --report-dir ../data/reports/production-dry
```

```python
from scripts.catalog_import import load_package, run_catalog_import

source, records = load_package("../data/catalog/nppa-anti-diabetes-2026-03.json")
report = run_catalog_import(source, records, dry_run=True)
```

검토 순서:

1. `raw_total == imported + excluded` 관계를 확인한다. `quarantined`는 `excluded`의 부분집합이고 `needs_review`는 imported의 부분집합이다.
2. 상품명·성분·함량·제형 누락 건을 원본 ID로 샘플링한다.
3. unknown form/unit, 동일 상품명·상이한 성분, 유사 이름 후보를 전수 검토한다.
4. SR/ER/CR/XR/MR, tablet/capsule, eye/ear/oral drops가 병합되지 않았는지 확인한다.
5. 입력·records·source metadata·approval registry SHA-256, 소스 버전, 발행/갱신/접근일, 라이선스 URL을 운영 기록에 남긴다.
6. 현지 약사와 법무 승인 결과를 첨부한다.

dry-run은 대상 DB를 열지 않는다. 실행 전 없던 `--db-path` 파일이 생기거나 대상 DB의 테이블·행 수가 바뀌었다면 apply하지 말고 버그로 처리한다.

## 5. apply와 멱등성 확인

운영 반영 전에 SQLite 백업을 만든다.

```bash
cd server
mkdir -p var/backups
sqlite3 var/indoro.db ".backup 'var/backups/indoro-before-drug-import.db'"
```

apply 후 **같은 source, version, package content, importer/normalization/package schema/snapshot mode**로 한 번 더 실행한다. 두 번째 실행에서 presentation, source record, alias, audit가 늘어나면 멱등성 결함이다.

검증 SQL 예시:

```sql
SELECT slug, tier, usage_scope, reuse_status, license_url
FROM drug_sources
ORDER BY slug;

SELECT ingest_status, COUNT(*)
FROM drug_import_run_records
GROUP BY ingest_status;

SELECT source_record_id, normalized_projection_sha256,
       previous_normalized_projection_sha256, normalized_diff_json,
       review_required
FROM drug_import_run_records
ORDER BY rowid DESC;

SELECT workflow_review_status, operational_lifecycle_status, COUNT(*)
FROM drug_presentations
GROUP BY workflow_review_status, operational_lifecycle_status;

SELECT version, importer_version, normalization_version,
       package_schema_version, snapshot_mode, package_content_sha256, code_revision
FROM drug_import_runs
ORDER BY started_at DESC;
```

apply 완료 기준:

- import run이 `completed`
- source, license, approval registry entry와 각 해시의 실행 시점 snapshot이 import run에 존재
- 모든 원본이 `drug_import_run_records`에서 이번 실행과 연결되고 imported/needs_review/excluded/quarantined 상태가 구분됨
- 각 정상화 실행에 현재/이전 projection SHA-256, changed-field JSON, review 필요 여부가 남음
- 실패·격리 레코드와 오류 코드가 보고서와 실행별 run-record에 존재
- source record와 원본 JSON의 수량이 보고서와 일치
- `source_id + source_record_id + raw_sha256` 중복 없음
- `inactive`가 기본 검색 결과에 나오지 않음
- production 검색에 demo source가 섞이지 않음

DB unique key의 컬럼 이름은 하위 호환 때문에 `(source_id, version, input_sha256, mode)`를 유지하지만, 현재 `input_sha256` 값은 순수 파일 해시가 아니다. package content SHA-256, importer semantic version, normalization rules version, package schema version, snapshot mode를 canonical JSON으로 합친 **processing/replay hash**다. 순수 source+records 입력 해시는 `package_content_sha256`에 별도 저장한다.

같은 package라도 importer 또는 normalization version이 바뀌면 별도 import run으로 처리된다. importer는 검색·표시·성분·별칭·포장까지 포함한 deterministic normalized projection JSON과 SHA-256을 이전 projection과 비교하고, changed field diff와 review 필요 여부를 run-record와 JSON/Markdown 보고서에 남긴다. projection이 같으면 source evidence `record_version`은 증가해 오래된 retirement 후보를 stale로 만들지만 기존 `approved/rejected` 결정은 보존한다. projection이 달라졌을 때만 기존 `approved/rejected`를 `needs_review`로 되돌린다. 이는 변경 결과가 자동 승인된다는 뜻이 아니다. 성공한 write 뒤에만 presentation의 `current_import_run_record_id`를 새 run-record로 옮긴다. 동일 raw를 재사용한 후속 실행이 persistence 단계에서 실패하면 실패 run-record는 최신 evidence로 보이지만, 사람 결정과 처방 provenance는 계속 마지막 적용 run을 참조한다. `code_revision`은 실행 commit SHA를 기록할 수 있는 선택 필드이며, 비어 있어도 semantic processing versions와 결정 당시 normalized snapshot은 필수다.

## 6. 검색 확인

서버를 실행한다.

```bash
cd server
uv run uvicorn app.main:app --port 8600
```

API 스모크:

```bash
curl -sS -H 'X-Pharmacy-Id: ph-demo-001' \
  'http://127.0.0.1:8600/api/drugs?q=500%20mg&limit=8'
```

브라우저에서 `http://127.0.0.1:8600/rx/new`를 열고 다음을 확인한다.

- 상품 exact와 prefix가 성분·substring보다 먼저 나오는가
- `500mg`, `500 mg`, `500-mg`가 같은 후보를 찾는가
- 같은 상품의 다른 함량·제형·방출 특성이 별도 행인가
- eye/ear/oral drops의 경로가 구분되는가
- 긴 상품명과 성분 조합이 잘리지 않는가
- 키보드의 위/아래, Enter, Escape로 조작 가능한가
- `needs_review`와 불완전 항목의 경고가 보이는가
- 결과가 없을 때 자유 입력을 계속할 수 있는가
- 선택해도 M/N/E/H, 용량, 단위, 식전/식후, 기간이 자동 확정되지 않는가
- 선택 후 직접 수정하면 `selected_then_modified`로 보존되는가

검색 요청·로그에 환자명, 전체 처방, 환자 토큰을 포함하지 않는다.

## 7. 갱신 절차

1. 원본의 새 버전을 별도 파일로 보관하고 해시를 계산한다.
2. 기존 source row의 라이선스 상태가 여전히 유효한지 확인한다.
3. 새 `version`, `published_at`, `source_updated_at`, `accessed_at`, `input_uri`를 기록한다.
4. 추가·변경·누락 후보를 원본 ID 단위로 diff한다.
5. 이름, 함량, 제형, route, release 변경은 현지 약사가 검토하고 파일별 권리 범위는 법무가 다시 확인한다.
6. 새 records/source metadata 해시와 승인일·역할을 `approved_packages`에 등록한다.
7. dry-run 보고서를 검토한 뒤 apply한다.
8. 자동완성과 기존 발급 처방의 스냅샷 불변성을 회귀 테스트한다.
9. 품질 JSON/Markdown, 입력 패키지, 승인 레지스트리 상태를 함께 보존한다.

출처의 업데이트 주기를 추측해 고정하지 않는다. 공식 공지와 파일 메타데이터를 기준으로 확인한다.

### delta와 full snapshot

- `snapshot_mode=delta`: 파일에 포함된 레코드만 insert/update 후보가 된다. 기존 레코드가 파일에 없더라도 retirement batch를 만들지 않는다.
- 첫 `snapshot_mode=full`: import는 성공하지만 비교할 이전 성공 full run이 없음을 `retirement_baseline_missing=true`로 보고한다. 후보를 만들지 않는다.
- 후속 full: 같은 source의 이전 성공 full에 한 번이라도 포함됐고 현재 retired가 아닌 presentation 가운데 새 파일에 없는 `source_record_id`를 찾는다. 새 누락은 `proposed` batch의 `pending` candidate가 되며 presentation은 계속 active이고 검색에도 남는다. 직전 full에도 이미 빠졌다는 이유만으로 누락을 영구히 잊지 않는다.
- 같은 presentation이 아직 `proposed`, `under_review`, `approved` batch에 있으면 후속 full에서 중복 후보를 만들지 않는다. 취소된 batch 또는 apply된 `keep_active` 결정 뒤에도 이후 full에서 계속 빠지면 새로운 source evidence로 다시 후보가 될 수 있다.
- 명시적 source ID가 새 package에 있으나 excluded/quarantine된 경우는 “누락”으로 간주하지 않는다. 마지막 정상 검색 필드는 보존하고, 기존 approval은 `needs_review`로 되돌리되 사람의 `rejected` 결정은 유지해 검색 차단을 해제하지 않는다. 실패 raw/run 링크와 importer audit를 남겨 데이터 오류와 source 철회를 혼동하지 않는다.

production full은 exact-hash 승인 package에만 허용한다. apply 전에 기존 DB를 읽기 전용으로 여는 retirement preview를 실행할 수 있다.

```bash
cd server
uv run python scripts/catalog_import.py ../data/catalog/FULL-PACKAGE.json \
  --dry-run \
  --retirement-preview-db var/indoro.db \
  --report-dir ../data/reports/full-preview
```

preview는 baseline DB를 SQLite `mode=ro`로 열고 JSON/Markdown 보고서에 누락 ID를 기록하지만 batch를 만들거나 lifecycle을 바꾸지 않는다.

## 8. 실패 레코드 검토

실패를 조용히 버리지 않는다. 현재 구현은 다음 증거를 보존한다.

- source slug, version, source record ID
- raw SHA-256와 raw JSON
- 오류 문자열과 정규화 또는 persistence 격리 단계
- 실행별 record ordinal과 imported/needs_review/excluded/quarantined 상태
- import run의 source/license/approval snapshot

searchable presentation으로 생성된 불완전 record는 내부 review queue에서 검토한다. quarantine되어 presentation이 없는 원본은 기존 품질 보고서와 `drug_import_run_records` 증거로 별도 조사한다. 검토 UI가 원본 레코드를 억지로 searchable presentation으로 승격하지 않는다.

presentation 결정은 `expected_record_version`이 현재 `record_version`과 일치할 때만 적용된다. 성공 transaction은 presentation 상태 변경과 `drug_review_decisions` append를 함께 commit한다. 다른 검토자가 먼저 바꿨으면 HTTP 409를 반환하고 어떤 상태도 덮어쓰지 않는다.

처리 원칙:

- 필수 상품/제품 이름 없음 -> `excluded`
- 성분·함량·제형 일부 누락 -> 임의 보완 없이 `needs_review`
- 알 수 없는 form/route/release -> 원문 보존 + `needs_review`
- 같은 상품명·다른 성분/제형 -> 별도 presentation, 자동 병합 금지
- 원본 ID 재사용 + 내용 변경 -> 새 source record version, 이전 원본 유지
- 라이선스 철회/출처 오류 -> 신규 검색은 `inactive`, 기존 처방 스냅샷은 별도 법무 판단 전 보존

## 9. 비활성화와 롤백

### 개별 레코드 비활성화와 복원

개별 비활성화는 review 상세 화면의 `lifecycle_inactivated` 전이를 사용한다. reviewer ID·역할, reason code, 필수 note, expected version이 모두 필요하고 `drug_review_decisions`에 남는다. 복원은 새 `lifecycle_reactivated` 결정을 append한다. 과거 audit를 수정하거나 삭제하지 않는다.

`active/inactive -> retired`는 일반 상세 UI에서 금지하고 승인된 retirement batch apply만 허용한다. `retired -> active`는 현재 일반 UI에서 지원하지 않는다. 잘못 적용한 retirement 복원은 production 인증·권한·규제 승인 절차가 마련될 때까지 통제된 유지보수와 새 보상 audit 설계가 필요한 운영 예외다. 특정 batch NSQ/recall을 상품 전체 비활성화로 확대하지 않는다.

### 임포트 롤백

1. 서버 쓰기를 중지한다.
2. 실패한 import run, 실행별 run-record, 해당 불변 source record를 식별해 영향 범위를 기록한다.
3. `drug_review_decisions`와 `drug_retirement_batch_events`에서 영향 presentation, actor, expected/result version, batch를 확인한다. 이력 행 자체를 수정하지 않는다.
4. import 전 SQLite 백업을 권위 있는 복구 지점으로 사용한다.
5. 서버를 재시작하고 SQLite integrity/FK 검사, 전체 테스트, 검색 스모크, 발급-QR-환자 화면 회귀를 실행한다.

```bash
cd server
cp var/indoro.db "var/indoro-failed-import-$(date +%Y%m%d-%H%M%S).db"
cp var/backups/indoro-before-drug-import.db var/indoro.db
uv run pytest -q
```

복원은 백업 이후 생성된 처방까지 잃을 수 있는 운영 작업이다. 상태 전이의 잘못은 가능한 경우 새 보상 결정을 append하고, DB 전체 복구는 migration/import 자체가 손상된 경우에만 사용한다. 현재 가이드는 자동 DB rollback 기능을 제공하지 않는다. 실제 서비스에서는 유지보수 창, 쓰기 차단, 최신 처방 별도 보존·재적용, 담당자 승인과 복구 훈련을 확정해야 한다.

## 10. 검토 queue와 retirement 운영 UI

현재 UI는 인증 시스템을 흉내 내지 않는 **loopback 개발 prototype**이다. 기본값은 비활성이고 다음 두 조건을 만족해야 열린다.

```bash
cd server
uv run python scripts/catalog_governance_demo.py \
  --db-path var/catalog-governance-demo.db
INDORO_DB_PATH=var/catalog-governance-demo.db \
INDORO_CATALOG_OPS_ENABLED=1 \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8610
```

- review queue: `http://127.0.0.1:8610/catalog/review`
- retirement batches: `http://127.0.0.1:8610/catalog/retirements`
- 기존 처방 회귀: `http://127.0.0.1:8610/rx/new`

`catalog_governance_demo.py`는 Tier 3 synthetic full v1, full v2, delta fixture와 가상 약국만 명시적 비기본 DB에 넣는다. production 기본 DB 경로를 거부하고 NPPA package를 변조하지 않는다.

### review queue 운영

1. source, workflow review, operational lifecycle, 누락 필드, import run, 검색어로 필터한다.
2. 상세 화면에서 **현재 projection의 원본 JSON/provenance**, **정규화 결과**, **가장 최근 import evidence**를 분리해 비교한다. 현재 영역은 `current_import_run_record_id`, 최신 evidence 영역은 시간순 마지막 run-record를 사용한다. 최신 excluded/quarantined evidence는 현재 정상 projection을 덮어쓰지 않으며 별도 경고·raw hash·run ID로 표시된다.
3. action, reason code, 필수 note, reviewer ID·역할을 입력한다.
4. 제출 시 hidden expected version을 검사한다. 409이면 최신 상태와 audit를 다시 읽고 새 결정을 내린다.
5. 성공은 303 PRG로 돌아오므로 새로고침이 mutation을 반복하지 않는다.
6. audit의 projection SHA-256과 접힌 normalized snapshot으로 결정 당시 검토한 정규화 내용을 복원한다. 이후 import가 현재 presentation을 바꿔도 이 snapshot은 append-only로 유지된다.
7. v6 이전 migration record처럼 projection JSON/hash가 없는 레코드는 importer로 재처리하기 전 승인·반려할 수 없다. UI와 service가 모두 이를 차단하며 lifecycle 결정만 별도로 허용한다.

workflow review와 catalog lifecycle은 독립적이다. 예를 들어 `approved + inactive`, `needs_review + active`가 모두 가능하다. 기본 자동완성은 operational active이며 workflow rejected가 아닌 결과만 보여준다.

### retirement batch 운영

1. `pending` candidate마다 `keep_active`, `retire`, `needs_investigation`을 기록한다.
2. 후보 결정은 presentation의 **후보 생성 시점 version**, 화면에 표시된 version, 현재 version이 모두 같을 때만 허용한다. 후속 full에서 품목이 재등장하거나 다른 검토가 들어오면 기존 후보는 stale이며 batch를 취소하고 새 evidence를 조사한다.
3. 각 후보 결정은 presentation audit와 `candidate_decided` batch event를 같은 transaction에 append하고 batch version을 증가시킨다. batch ledger의 expected/result version chain은 생성부터 현재 version까지 연속이어야 한다.
4. pending 또는 needs_investigation이 하나라도 있거나 stale candidate가 있으면 batch 승인 버튼과 서비스 전이가 거부된다.
5. 모든 후보가 해결되고 presentation version이 그대로인 batch만 별도 approver가 승인한다.
6. 승인된 batch를 다시 명시적으로 apply한다. `retire` 후보만 `retired`가 되고 `keep_active`는 유지된다.
7. apply는 한 transaction에서 모든 candidate version을 확인한다. 하나라도 stale이거나 audit insert가 실패하면 lifecycle, candidate, batch, audit 전체를 rollback한다.
8. 이미 applied인 batch의 재시도는 presentation version과 audit를 늘리지 않는다.

운영 UI와 mutation route는 `INDORO_CATALOG_OPS_ENABLED`가 없으면 404다. 활성화해도 socket peer와 HTTP Host가 모두 loopback이어야 하고 browser cross-origin POST는 거부한다. Chromium의 opaque `Origin: null` form POST는 같은 origin Referer가 있을 때만 허용한다. 운영 경로는 OpenAPI schema에서 숨기며 non-loopback 환경변수 우회, URL query secret, GET mutation, audit UPDATE/DELETE API는 제공하지 않는다.

이 loopback 검사는 reverse proxy의 인증 대체물이 아니다. 로컬 proxy가 외부 요청을 전달하면서 peer와 Host를 loopback으로 재작성할 수 있으므로 이 prototype을 reverse proxy, tunnel, port-forward에 연결하지 않는다. 실행 예시처럼 `127.0.0.1`에 직접 bind한 개발 환경에서만 사용한다.

production 배포 전에는 실제 사용자 인증, reviewer/approver 역할 분리, 권한 검사, CSRF token, session 수명, 보안 로그·alert, rate limit, 운영자 계정 수명주기와 break-glass 복구를 별도 설계·검토해야 한다. 현지 약사·법무 검증 없이 이 UI의 결정 절차를 실제 인도 운영 정책으로 확정하지 않는다.

## 11. 출시 전 책임 분리

| 판단 | 책임자 |
| --- | --- |
| 파일 라이선스와 상업적 재사용 | 인도 현지 법무 |
| 성분·함량·제형·경로 검수 | 등록 약사/의약품 데이터 전문가 |
| import code, 멱등성, 백업 | 개발·운영 |
| 환자 표시명과 번역 | 약사 + 현지 언어 검수자 |
| recall/NSQ/banned 적용 범위 | 규제·약사 운영 담당 |
| 배포 승인 | 제품 책임자 + 의료·법무 승인자 |

이 저장소의 demo 데이터와 자동화 테스트는 위 승인을 대신하지 않는다.
