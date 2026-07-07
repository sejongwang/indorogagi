# indoro 인도 의약품 마스터 시드 (drugs-seed.json)

- **수집일**: 2026-07-07 (원격 수집 트랙, 인도 방문 전)
- **총 건수**: 629건 (브랜드 379건 + NLEM 성분 백본 250건)
- **스키마 버전**: 1
- **용도**: v0 자동완성(`GET /api/drugs?q=`) 및 P1 약사 입력 자동완성 목업 데이터. 25만 SKU 풀 덤프가 아닌 top 브랜드 + NLEM 중심 큐레이션.

## 소스별 기여

| 소스 | 기여 | URL |
|---|---|---|
| NLEM 2022 (CDSCO, 인도 필수의약품 목록) | 성분 백본 250건 (`is_generic: true`) | https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadConsumer/nlem2022.pdf |
| Pharmarack PharmaTrac 보도 (top 브랜드 랭킹) | 77건이 근거로 인용, 그중 75건에 `priority_rank` 부여. 주 근거: PharmaTrac MAT Mar-2026 Top 40 | Medical Dialogues PDF: https://medicaldialogues.in/pdf_upload/2026/04/10/ipm-performance-pharmatrac-mat-mar-2026-1-340944.pdf · BioSpectrum(May 2026): https://www.biospectrumindia.com/news/73/27970/mounjaro-retains-no-1-position-as-25-leading-pharma-brands-register-double-digit-growth-in-may-2026-pharmarack.html · Business Standard(FY25): https://www.business-standard.com/industry/news/robust-chronic-performance-drives-8-4-growth-for-indian-pharma-mkt-in-fy25-125040801060_1.html · MedicinMan(Apr 2025): https://medicinman.net/2025/05/indian-pharma-market-performance-april-2025/ |
| 온라인 약국 목록 페이지 (1mg · Netmeds · PharmEasy, 보조: Apollo Pharmacy · Truemeds · MedPlusMart) | 353건이 강도·성분·제형 확인 근거로 인용. 랭킹 근거 없는 브랜드 276건은 치료군별 다빈도 큐레이션 | 1mg 예: https://www.1mg.com/search/all?name=Thyronorm · Netmeds 예: https://www.netmeds.com/prescriptions/naxdom-500-tablet-15s · PharmEasy 예: https://pharmeasy.in/online-medicine-order/zerodol-sp-tablet-14794 |
| IQVIA 보도 | 0건 — 최종 시드 근거로는 Pharmarack 계열 보도만 사용됨 | — |
| Jan Aushadhi (PMBJP 제네릭 스토어 목록) | 0건 — 이번 원격 시드에 미편입. 저가 제네릭 표기 수요 확인 후 현지 트랙에서 편입 후보 | http://janaushadhi.gov.in/ |

항목별 세부 근거는 각 entry의 `source` 배열(수집 페이지 URL 또는 문서명+URL)에 있다. Schedule H1 통제약 중 Alprax(alprazolam)·Zolfresh(zolpidem)는 약사 입력 실수요 기준으로 포함했고, Spasmo-Proxyvon Plus(tramadol 복합)는 정책 확정 전 보류했다.

## 스키마

| 필드 | 타입 | 설명 | 예시 |
|---|---|---|---|
| `id` | string | 임시 ID(`seed-일련번호`). 병합/임포트 단계에서 재부여 | `"seed-0001"` |
| `brand_name` | string | 표시명. NLEM 성분 항목은 INN명 그대로 | `"Dolo 650"`, `"Paracetamol"` |
| `generic_name` | string | 성분 문자열. 복합제는 `" + "` 연결 | `"Amoxicillin + Clavulanic Acid"` |
| `molecules` | string[] | 구조화 성분 배열(복합제는 성분별 원소) | `["Amoxicillin", "Clavulanic Acid"]` |
| `strength` | string | `"650 mg"` \| `"500/125 mg"` \| `"per 5 ml"` \| `"60000 IU"` 표기 규칙 | `"500/125 mg"` |
| `form` | string | tablet·capsule·syrup·suspension·drops·injection·cream·ointment·inhaler·sachet·gel·other | `"tablet"` |
| `is_generic` | boolean | `true` = NLEM 성분 백본 항목(브랜드 아님) | `false` |
| `priority_rank` | int \| null | top 판매 근거(Pharmarack 보도) 있는 브랜드만 정수(1이 최상), 그 외 null | `1` |
| `category` | string | 치료군 16종(analgesic_antipyretic, antibiotic, cardiovascular …) | `"antibiotic"` |
| `aliases` | string[] | 실존 표기 변형만(하이픈·시장 통용 축약·성분명). 발명 금지 | `["Augmentin", "Augmentin 625"]` |
| `source` | {label,url}[] | 근거 필수 — 수집 페이지 URL 또는 문서명+URL | `[{"label": "1mg", "url": "https://…"}]` |

같은 브랜드의 다른 강도는 별도 항목(예: Dolo 500 vs Dolo 650, 대표 강도 1~3개). 외형 필드(색·크기·모양·사진)와 복용법/용량 기본값은 **의도적으로 배제**했다(아래 한계 참조).

### docs/01 drugs 테이블 매핑

| 시드 필드 | drugs 테이블 컬럼 | 비고 |
|---|---|---|
| `molecules` | `generic_name` | `" + "` 연결 문자열로 평탄화 |
| `aliases` | `aliases_json` | JSON 배열 그대로 |
| `source` | `source` (TEXT) | 직렬화 저장 |
| `brand_name` / `strength` / `form` | 동명 컬럼 | 그대로 |
| `priority_rank` · `is_generic` · `category` | (컬럼 없음) | **시드 전용** — 자동완성 정렬·큐레이션 관리용 |
| — | `verified` | 임포트 시 일괄 `0` (현지/약사 검증 전) |

## 라이선스 / 법적 메모

- **NLEM 2022**는 인도 정부(CDSCO) 공공 문서로, 성분 목록의 사용에 제약이 없다.
- 브랜드명·성분·강도는 **사실 정보(facts)의 소량 큐레이션**이다. 특정 상업 DB(1mg, PharmEasy 등)의 실질적 복제가 아니며, 소스당 소수의 목록·보도 페이지만 참조해 629건을 선별했다.
- 판매 랭킹(`priority_rank`)은 Pharmarack PharmaTrac **보도 인용**에 근거하며, 출처(매체명+URL)를 각 entry의 `source`에 명시했다. 원 데이터셋 자체를 재배포하지 않는다.

## 검증 요약

- 스팟체크 30건, 오류율 **3.3%** (1건).
- 오류 발견 치료군(cat_acute)은 **재수집** 완료. 갭 보강 41건은 2026-07-07 웹 재확인 완료.
- 임포트 시 전 항목 `verified=0`으로 들어가며, 현지 파트너 약국 검증을 통과한 항목만 `verified=1`로 승격한다.

## 한계

- **전국 top 판매 기준의 큐레이션이므로, 파트너 약국의 실제 처방 믹스로 현지 보정이 반드시 필요하다.** 지역·약국별 취급 브랜드는 전국 랭킹과 다를 수 있다.
- **외형 필드(색·크기·모양·사진) 미포함**: 같은 브랜드도 제조사·배치별로 제각각이라 원격 수집 값은 오답 위험이 있다. 현지 검증 트랙의 몫이다.
- **복용법/용량 기본값(패턴·식전후·1일 횟수) 미포함**: 용법은 약사 입력 영역이므로 시드가 기본값을 제안하면 안 된다. `meta.pattern_candidates`는 UI 픽스처 대조용 후보일 뿐 복약 지시가 아니다.
- **지역 언어 별칭(힌디어·타밀어 등) 미수집**: 실존 표기 확인이 원격으로 어려워 현지 트랙으로 미뤘다. 현재 `aliases`는 영문 표기 변형만 담는다.

## 갱신 절차

파일럿 운영 중 약사 입력 화면에서 자동완성에 매칭되지 않은 원문 입력을 `drug_name_raw`로 로그에 남긴다. 주기적으로(주 1회 권장) 미매칭 로그를 빈도순으로 집계해 상위 항목을 검토하고, 실존 브랜드/강도로 확인된 것만 소스 URL과 함께 시드에 편입한다(신규 항목도 `verified=0`으로 시작). 이 루프가 전국 top 기준 시드를 파트너 약국의 실제 처방 믹스에 수렴시키는 핵심 보정 장치다.
