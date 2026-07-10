# 모바일 복약 포스터 구현·시각 QA

- 검증일: 2026-07-10
- 브라우저: Playwright Chromium, Android Chrome과 같은 모바일 viewport
- 서버: `http://127.0.0.1:8600`
- 정본: `server/templates/patient.html`

## 시각 반복 결과

### 기준선

[기준선 360×800](../output/playwright/baseline/patient-hi-360x800.png)은 첫 화면의 약국 메타데이터와 약×시간 매트릭스가 관리용 대시보드처럼 읽혔다. 작은 셀 안에서 식전·식후 의미가 약했고, 모든 약 카드와 실제로 동작하지 않는 음성·영상 placeholder가 핵심 복약 행동보다 강했다.

### 선택·구현

`아침 → 점심 → 저녁 → 밤`을 한 방향으로 고정한 하루 흐름 포스터를 선택했다. 제목 바로 아래에는 첫 비어 있지 않은 시간대의 실제 약명·봉투 번호·용량을 요약해 320px 첫 화면에서도 "언제 무엇을" 바로 찾게 했다. 각 시간대 안에서 봉투의 색+큰 번호, 전체 약명, 자체 용량 SVG, 숫자·단위, 식사 순서, 기간을 한 행동 묶음으로 반복한다. 주 1회·PRN 등은 고정 일과 아래의 별도 구역, 약별 전체 정보와 가족 공유는 그 아래에 둔다.

1차 브라우저 검토에서 6약과 긴 약명이 정상 래핑됐지만, ml의 숟가락 그림이 가정용 티스푼으로 오해될 수 있고 320×640 첫 화면에는 실제 약명이 아직 안 보였다. 최종 반복에서 눈금 계량컵 SVG와 `눈금 있는 컵 또는 경구용 주사기로 재기` 문구를 추가하고, 첫 시간대 실약명 요약·힌디 하위 라벨 행간 1.6 이상·PRN 1회량 필수를 함께 보강했다.

최종 안전성 감사에서 PRN은 1회량·일일 최대·최소 간격 세 수치를 모두 신규 발급 필수로 강화했다. 기존 DB 행에 제한이 빠졌다면 환자 포스터가 약국 확인 경고를 명시한다.

## 최종 캡처

| 조건 | 캡처 | 결과 |
| --- | --- | --- |
| 1약, 힌디, 320×640 | [simple-hi-320x640-final.png](../output/playwright/final/simple-hi-320x640-final.png) | 첫 화면에 실제 `Dolo 650`, 봉투 1, `1 गोली`가 보임 |
| 3약, 힌디, 360×800 | [general-hi-360x800-final.png](../output/playwright/final/general-hi-360x800-final.png) | 첫 아침 복용 약 2종의 번호·약명·용량이 첫눈 요약에 보임 |
| 3약, 영어, 360×800 | [general-en-360x800-final.png](../output/playwright/final/general-en-360x800-final.png) | 긴 영어 제목과 라벨이 잘리지 않음 |
| 6약+긴 약명, 힌디, 390×844 | [complex-hi-390x844-final.png](../output/playwright/final/complex-hi-390x844-final.png) · [전체 페이지](../output/playwright/final/complex-hi-390x844-full-final.png) | ½정·5ml·긴 점안액·주 1회 아침·PRN 매회 1정·밤 파우치와 6개 봉투 번호 유지 |
| JavaScript 비활성, 6약 | [complex-hi-360x800-js-off-final.png](../output/playwright/final/complex-hi-360x800-js-off-final.png) | 첫눈 요약·6약·PRN 1회량을 포함한 핵심 정보가 서버 HTML로 그대로 노출 |
| 실제 발급 QR | [issued-qr-390x844-final.png](../output/playwright/final/issued-qr-390x844-final.png) | SVG QR, 백업 코드, 유효기간, 봉투 번호 표시 |
| 미동기화 대기 | [pending-360x800-final.png](../output/playwright/final/pending-360x800-final.png) | 30초 자동 재시도 + 52px 수동 재시도 |
| 만료 | [expired-360x800-final.png](../output/playwright/final/expired-360x800-final.png) | 410, 약국 재발급 안내와 일반 안전 문구 |
| 폐기 | [revoked-360x800-final.png](../output/playwright/final/revoked-360x800-final.png) | 410, 폐기 사유·약국·약 정보 비노출 |

## 계측 QA

| viewport·언어 | 문서 폭 | 가로 넘침 | 본문 행간 | 48px 미만 터치 대상 | 약명 너비 넘침 |
| --- | ---: | --- | --- | ---: | ---: |
| 320×640 · hi | 320/320px | 없음 | 27.52/16px = 1.72 | 0 | 0 |
| 360×800 · en | 360/360px | 없음 | 28.05/17px = 1.65 | 0 | 0 |
| 390×844 · hi · 6약 | 390/390px | 없음 | 29.24/17px = 1.72 | 0 | 0 |

- 활성 환자 화면과 새 QR 페이지의 콘솔: 오류 0, 경고 0.
- 320px의 소형 오렌지 텍스트 대비는 4.81:1, 봉투·시간 라벨은 최소 12px로 보강했고, 포커스 링은 주요 어두운/밝은 배경 모두에서 3:1 이상이다.
- 만료·폐기 문서는 계약대로 HTTP 410이므로 Chromium이 문서 요청 자체를 `Failed to load resource: 410`으로 기록한다. 페이지 JavaScript 오류나 누락 자산은 아니다.
- QR 페이지 요청: 문서, `rx.css`, `qrcode.js`, `rx.js`, 처방 API가 모두 200. 발급 직후에는 재표시 API를 부르지 않고, 새로고침 때만 200으로 호출한다.
- 복잡 환자 HTML: 원문 61,213 bytes, Python gzip level 9 기준 9,586 bytes(24KB 테스트 예산의 40% 미만).
- JavaScript 비활성 접근성 스냅샷에서 첫눈 요약, 6개 약, M/N/E/H, ½, ml, 주 1회 아침, PRN 매회 1정·최대 3회·최소 6시간이 모두 확인됐다.
- WhatsApp 링크를 decode해 일반 안내 문구와 불투명 토큰 URL만 있고 약명·용법·`patient_label`이 없음을 확인했다.

## 실제 관통 시연

브라우저에서 `/rx/new`를 열어 `Dolo` 자동완성 후보 중 `Dolo 650`을 선택하고, BD·식후·7일을 입력했다. 확인 시트에서 봉투 1, `1-0-1-0`, 총량 14정을 확인한 뒤 발급했다.

- 발급 ID: `70cec558-f62c-478b-a283-481a67185651`
- QR 백업 코드: `HS8Q-KD3N`
- QR 환자 URL: `http://127.0.0.1:8600/p/Y1dugGoBnf3HwWjbu8PfiA`
- 발급 직후 `?from=issue` 표지가 주소에서 제거되고 `qr.redisplayed=0`을 유지했으며, 새로고침 후에만 정확히 1건이 기록됐다. 환자 포스터의 영어 전환과 링크 복사 성공 상태도 확인했다.

## 회귀 검증

```bash
cd server
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 38 passed, 1 existing StarletteDeprecationWarning
```

테스트는 기존 발급·수정·재발급·토큰 상태·계측 계약에 더해 포스터 DOM, 첫눈 실약명, M/N/E/H 순서, 구 `OD_NIGHT` E→H 멱등 마이그레이션, 6약·긴 약명, ½/ml/캡슐/점안액/PRN 1회량·일일 최대·최소 간격, 기존 PRN 누락 경고, 고유 단위 SVG, 민감 메타데이터 비노출, 개인정보·대기·코드 재입력, gzip 예산, 3종 데모를 검증한다.

## 현지 검증 필요

- 북인도 저문해 사용자에게 시간대 용어와 식전·식후 순서 SVG가 설명 후에도 정확히 이해되는지
- 봉투 번호를 약사가 실제 운영에서 일관되게 쓰고 환자가 3초 안에 해당 복용 묶음을 찾는지
- PRN 1회량(`extra_params.dose_per_use`)·일일 최대·최소 간격을 현지 약사가 어떤 절차로 이중 확인할지
- 실제 계량컵·경구용 주사기 제공 여부와 ml 문구의 약국 운영 적합성
- 저가 Android 실기기 글꼴, 직사광, 네트워크 지연, WhatsApp 인앱 브라우저에서의 최종 확인
