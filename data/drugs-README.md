# indoro 의약품 카탈로그 데이터

- 기준일: 2026-07-10
- 설계·출처 평가: [`../docs/08-india-drug-data-foundation.md`](../docs/08-india-drug-data-foundation.md)
- 운영 절차: [`../docs/09-drug-catalog-operations.md`](../docs/09-drug-catalog-operations.md)
- 출처 레지스트리: [`drug-sources.json`](drug-sources.json)

## 데이터셋 구분

| 파일 | 범위 | 분류 | 사용 가능 범위 |
| --- | --- | --- | --- |
| [`catalog/nppa-anti-diabetes-2026-03.json`](catalog/nppa-anti-diabetes-2026-03.json) | NPPA가 2026년 3월 기준으로 게시한 11개 항당뇨 formulation과 단위·ceiling price 원문 사실 | Tier 1, production | NPPA 자체 작성 자료의 formulation 식별 보조. 브랜드·제조사·복용법을 추정하지 않음 |
| [`catalog/indoro-synthetic-demo-v1.json`](catalog/indoro-synthetic-demo-v1.json) | 프로젝트가 작성한 가상 상품·가상 성분 16건 | Tier 3, demo | `ph-demo-001`의 제형·경로·검색 UX·테스트 전용. 실제 의약품 또는 복약 권고가 아님 |
| [`drug-sources.json`](drug-sources.json) | 공식·참고 출처의 Tier, 라이선스, 자동 수집 정책 | 메타데이터 | source gate와 법무 검토 기록 |

**정부 사이트에 공개된 자료라는 사실은 자동으로 자유 재사용을 뜻하지 않는다.** 각 source는 파일별 라이선스, 버전, 입력 URL, 접근일을 추적한다. Tier 2는 현지 법무 승인 전 운영 import를 막고, Tier 3는 운영 데이터와 합치지 않는다.

production 패키지는 패키지 내부의 `reuse_status`만으로 승인되지 않는다. [`drug-sources.json`](drug-sources.json)의 별도 `approved_packages` 항목에 package slug, canonical records SHA-256, `accessed_at`을 포함한 source metadata SHA-256, `approved_at`, `approved_by_role`이 정확히 일치해야 한다. 현재 승인 범위는 `nppa-antidiabetes-formulations-2026-03`의 고정된 11건뿐이다. canonical record 값이나 승인 대상 source metadata가 바뀌면 새 해시와 별도 현지 법무·데이터 거버넌스 승인이 필요하다.

## 공식 NPPA 패키지

`catalog/nppa-anti-diabetes-2026-03.json`은 [NPPA 치료군별 ceiling price 목록](https://www.nppa.gov.in/en/therapeuticcategorywiseceilingpricelist)이 연결한 2026년 3월 PDF에서 NPPA 작성 formulation 11개를 전사한 작은 공식 패키지다. [NPPA 저작권 정책](https://www.nppa.gov.in/en/copyrightpolicy)은 별도 표시가 없는 NPPA 사이트 자료를 정확하고 비훼손적이며 오해를 일으키지 않는 방식으로, 출처를 명확히 표시해 재현할 수 있게 한다. 제3자 저작물은 이 허용에서 제외된다.

따라서 이 패키지는 다음으로 제한한다.

- NPPA가 작성한 formulation, 단위, 원문 price fact만 저장
- `source_url`, 버전, 접근일, 원본 행 번호와 attribution 보존
- 상품 브랜드, 제조사, 마케팅 회사, 진단, 용량, 대체약을 추정하지 않음
- ceiling price를 환자 화면에 표시하거나 복약 안내 생성에 사용하지 않음
- NPPA 전체 사이트나 제3자 시장·제조사 데이터를 같은 라이선스로 간주하지 않음

11건은 전국 상품 마스터가 아니다. 안전하게 재사용 가능한 공식 입력과 import/update 경로를 검증하는 최소 운영 패키지다.

원본과 변환 경계는 다음 파일로 고정한다.

- `catalog/nppa-anti-diabetes-2026-03.source-manifest.json`: 공식 PDF URL, ETag, Last-Modified, 401,697 bytes, SHA-256
- `catalog/nppa-anti-diabetes-2026-03.source.json`: package source metadata
- `catalog/nppa-anti-diabetes-2026-03.rows.csv`: PDF 11행을 사람이 대조한 reviewed transcription
- `catalog/nppa-anti-diabetes-2026-03.json`: 결정적으로 생성되는 importer package

```bash
cd server
uv run python scripts/fetch_catalog_source.py \
  ../data/catalog/nppa-anti-diabetes-2026-03.source-manifest.json \
  --output-dir var/catalog-sources
uv run python scripts/nppa_package.py --check
```

원본 PDF hash가 바뀌거나 reviewed CSV와 package가 다르면 명령은 실패한다. OCR/layout 추정을 자동 반영하지 말고, 새 version 파일에서 원문 대조·현지 약사/법무·데이터 거버넌스 검토와 승인 레지스트리 해시 갱신을 수행한다.

## 합성 demo fixture

`catalog/indoro-synthetic-demo-v1.json`은 외부 사이트나 실제 제품 카탈로그를 복제하지 않고 프로젝트가 직접 만든 식별 테스트 자료다. 모든 상품·성분명이 가상이며 source slug는 `indoro-synthetic-demo-v1`, usage scope는 `demo`다.

다음 회귀 사례를 포함한다.

- 같은 이름의 250 mg/500 mg 정제와 500 mg 캡슐
- 복합제의 성분 순서와 성분별 함량
- SR과 ER
- syrup과 suspension
- ophthalmic, otic, oral drops
- 긴 상품명·긴 성분 조합, 하이픈·공백·`500mg` 변형, Devanagari 별칭
- 위험한 유사 이름 후보, 불완전 검토 레코드, inactive 레코드

합성 fixture도 실제 처방 자료로 오해되지 않도록 UI에서 Demo·검토 상태를 표시하고, `ph-demo-001` 헤더 컨텍스트에서만 검색한다. production으로 자동 승격할 수 없다.

apply는 반드시 `--db-path`로 지정한 비기본 SQLite에만 허용된다. `scripts/seed_import.py`와 단일 패키지용 `scripts/catalog_import.py` 모두 demo 패키지를 기본 production DB에 적용하는 요청을 거부한다.

apply가 성공하면 `catalog_database_meta`가 해당 SQLite 파일의 물리적 역할을 `production` 또는 `demo`로 고정한다. 이후 반대 역할의 import는 경로 이름과 무관하게 거부되므로, 합성 fixture가 실수로 운영 DB에 섞이거나 운영 DB가 demo 데이터로 재분류되지 않는다.

## 레거시 627건의 격리

기존 `drugs-seed.json`은 NLEM 성분 백본 외에 상업 약국·보도에서 확인한 브랜드 큐레이션이 섞여 있었고, 전체 레코드의 데이터 공급 계약·상업적 재사용 라이선스가 없었다. 이 작업에서 파일을 저장소와 seed importer에서 제거했다.

금지 사항:

- 실제 환자 서비스, 자동완성 demo, 운영 master에서 사용
- 1mg, PharmEasy, Netmeds, Apollo 등 상업 사이트에서 추가 수집
- 상품 설명·가격·이미지·리뷰 복제
- 명시적 계약 없이 `demo`, `verified`, `production`으로 전환

이미 외부 저장소에 push된 Git 이력이 있다면 working tree 삭제만으로 과거 blob이 사라지지 않는다. 공개 이력 삭제 또는 보존의 법적 필요성은 현지 법무·데이터 거버넌스 책임자가 별도로 결정해야 하며, 파괴적인 history rewrite는 이 구현 범위에서 수행하지 않았다.

## 품질 보고서

```bash
cd server
uv run python scripts/seed_import.py --dry-run --report-dir ../data/reports/production-dry
uv run python scripts/seed_import.py --apply --report-dir ../data/reports/production
uv run python scripts/seed_import.py --apply --report-dir ../data/reports/production-replay

uv run python scripts/seed_import.py --catalog-scope demo --dry-run \
  --report-dir ../data/reports/demo-dry
uv run python scripts/seed_import.py --catalog-scope demo --apply \
  --db-path var/indoro-demo.db --report-dir ../data/reports/demo
uv run python scripts/seed_import.py --catalog-scope demo --apply \
  --db-path var/indoro-demo.db --report-dir ../data/reports/demo-replay
```

JSON과 Markdown 보고서에는 최소한 원본, 정상 import, 제외, 격리, 검토 필요, 중복 후보, 필드 누락, 미확인 form/unit, 위험한 유사 이름을 기록한다. dry-run은 요청한 DB를 열거나 생성하지 않고 메모리 DB에서 관계형 품질 검사까지 수행한다. 같은 입력을 다시 apply해도 presentation/source record 수가 늘어나지 않아야 한다.

## 검증의 의미

문자열 정규화·중복 후보 검사와 테스트 통과는 의료적 정확성, 현지 판매 상태, 규제 상태, 환자 사용 안전성을 증명하지 않는다. 운영 전 다음을 별도로 완료해야 한다.

- 현지 등록 약사의 상품명·성분·함량·제형·경로 검수
- 인도 현지 법무의 파일별 상업적 재사용 승인
- 공식 source의 갱신·철회·리콜 절차 확인
- 파트너 약국 실제 처방 원문의 미매칭 분석
- 지역어 별칭의 출처와 언어 검수

미매칭 원문을 수집할 때 환자명, 전체 처방, 토큰을 로그로 남기지 않는다. 빈도만으로 새 상품을 운영 카탈로그에 자동 추가하지 않는다.
