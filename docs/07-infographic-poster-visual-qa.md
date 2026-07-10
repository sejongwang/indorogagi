# 인포그래픽 행동 띠 환자 화면 구현·시각 QA

- 검증일: 2026-07-10
- 브라우저: Playwright Chrome, 모바일 viewport 320×640 / 360×800 / 390×844
- 정본: `server/templates/patient.html` + `server/static/patient-poster.css`
- A/B 프로토타입: `http://127.0.0.1:8600/static/ux-infographic-poster-en.html`
- 선행 근거: [`05-india-medication-poster-research.md`](05-india-medication-poster-research.md)

## 기존 포스터와 이번 버전의 차이

이전 버전은 하루 흐름을 세로로 정리했지만, 시간대 헤더와 약 카드가 서로 다른 시각 단위였고 첫눈 요약을 별도 카드로 중복했다. 이번 버전은 첨부된 `MEDICINE SAFETY GUIDE`의 **반복되는 가로 행동 패널** 문법만 참고해, 실제 복약 순서 자체를 모든 약에 같은 구조로 고정했다.

`시간대 → 봉투 색+큰 번호 → 약명 → 그림+숫자 용량 → 1·2번 식사 순서 → 달력+복용 일수`

- Morning → Afternoon → Evening → Night 순서를 빈 슬롯까지 고정한다.
- 한 시간대에 여러 약이 있으면 각 약을 점선으로 분리한 독립 행동 띠로 반복한다.
- 식전은 `1 약 → 2 식사`, 식후는 `1 식사 → 2 약` SVG와 텍스트를 함께 쓴다.
- PRN은 `1회량 → 식사 순서` 다음에 `하루 최대`와 `최소 간격`을 서로 다른 큰 규칙 타일로 분리한다.
- 약별 상세와 가족 공유는 전체 행동 흐름 아래로 내린다.
- 진단은 추정·표시하지 않고, 데모는 1약·3약·6약·반알·5 ml·점안액·주 1회·PRN 같은 **처방 패턴**으로 구성한다.

정부·병원 로고, 캐릭터, 상용 포스터 문구나 삽화는 복제하지 않았다. 시간·봉투·용량·식사·기간 그림은 모두 자체 SVG다.

## 구현 URL

서버 실행:

```bash
cd server
uv run python scripts/seed_import.py
uv run python scripts/demo.py
uv run uvicorn app.main:app --host 127.0.0.1 --port 8600
```

이번 검증에서 생성한 데모 URL:

| 사례 | URL |
| --- | --- |
| 정적 A/B | `http://127.0.0.1:8600/static/ux-infographic-poster-en.html` |
| 1약 | `http://127.0.0.1:8600/p/YE0sAFMwWwgt-dygm1trVg?lang=en` |
| 3약 | `http://127.0.0.1:8600/p/HgySkHdZPvrG3M1mNPy-pQ?lang=en` |
| 6약 영어 | `http://127.0.0.1:8600/p/u4voqQHm0xKI5n1yAtu9iQ?lang=en` |
| 6약 힌디 | `http://127.0.0.1:8600/p/u4voqQHm0xKI5n1yAtu9iQ?lang=hi` |
| 실제 QR | `http://127.0.0.1:8600/rx/62b05577-91ff-4ff4-aba7-60ec2bec2cc9/qr` |

`scripts/demo.py`를 다시 실행하면 새 토큰과 QR URL이 발급되므로 출력된 최신 URL을 우선한다.

## 최종 캡처

| 조건 | 캡처 | 확인 결과 |
| --- | --- | --- |
| A/B 전체 | [prototype-en-390x844-full-final2.png](../output/playwright/infographic-final/prototype-en-390x844-full-final2.png) | 네 시간대 모두 동일 행동 띠, 문장형 반복 요약 없음 |
| 1약 영어 320×640 | [simple-en-320x640-final2.png](../output/playwright/infographic-final/simple-en-320x640-final2.png) | 첫 화면 안에 Morning·봉투 1·약명·1 tab·식후 순서·3일 노출 |
| 3약 영어 360×800 | [general-en-360x800-final2.png](../output/playwright/infographic-final/general-en-360x800-final2.png) | 첫 행동 전체와 다음 봉투 시작까지 노출 |
| 6약 영어 390×844 | [complex-en-390x844-final2.png](../output/playwright/infographic-final/complex-en-390x844-final2.png) · [전체](../output/playwright/infographic-final/complex-en-390x844-full-final2.png) | 반알·5 ml·점안액·긴 약명·밤 파우치 유지 |
| 6약 힌디 390×844 | [complex-hi-390x844-final2.png](../output/playwright/infographic-final/complex-hi-390x844-final2.png) · [전체](../output/playwright/infographic-final/complex-hi-390x844-full-final2.png) | 데바나가리 클리핑 없이 동일 순서 유지 |
| PRN 구간 | [prn-en-390x844-final2.png](../output/playwright/infographic-final/prn-en-390x844-final2.png) | 매회 1정·식후·최대 3회/일·최소 6시간을 분리 표시 |
| JavaScript 비활성 | [complex-hi-360x800-js-off-final.png](../output/playwright/infographic-final/complex-hi-360x800-js-off-final.png) | SSR 포스터와 반알·ml·점안액·PRN 규칙 유지 |
| 실제 QR | [issued-qr-390x844-final.png](../output/playwright/infographic-final/issued-qr-390x844-final.png) | 로딩 종료 후 실제 SVG QR 생성 |
| 대기 / 만료 / 폐기 | [pending](../output/playwright/infographic-final/pending-360x800-final.png) · [expired](../output/playwright/infographic-final/expired-360x800-final.png) · [revoked](../output/playwright/infographic-final/revoked-360x800-final.png) | 200 대기, 410 만료, 410 폐기 계약 유지 |

## 자동·수동 브라우저 QA

| 화면 | 문서 폭 | 가로 스크롤 | 48px 미만 터치 대상 | 누락 SVG/이미지 | 시간 순서 |
| --- | --- | --- | ---: | ---: | --- |
| 1약 영어 320×640 | 320 / 320 | 없음 | 0 | 0 | M → N → E → H |
| 3약 영어 360×800 | 360 / 360 | 없음 | 0 | 0 | M → N → E → H |
| 6약 영어 390×844 | 390 / 390 | 없음 | 0 | 0 | M → N → E → H |
| 6약 힌디 390×844 | 390 / 390 | 없음 | 0 | 0 | M → N → E → H |

- 힌디 `[lang=hi]`의 최소 계산 행간: 부동소수점 오차를 제외하면 `1.6`.
- 활성 환자 화면, 정적 A/B, QR 화면 콘솔: 오류 0, 경고 0.
- 만료·폐기 문서는 의도한 HTTP 410 때문에 Chromium이 문서 요청을 410 오류로 기록한다. 누락 자산이나 페이지 JavaScript 오류는 아니다.
- JavaScript 비활성 스냅샷에서 `½`, `5`+`ml`, 점안액 단위, 긴 약명, PRN 항목과 두 제한 규칙을 확인했다.
- WhatsApp 링크와 복사 URL에는 일반 안내 문구와 불투명 토큰만 있고 약명은 없으며, 복사 성공 상태가 실제로 전환됐다.
- `/rx/new` 실제 브라우저에서 `Dolo` 검색 후보 3종을 확인하고 `Dolo 650` → BD → After food → 7일을 선택했다. 발급 전 확인 시트는 `봉투 1 · 1-0-1-0 tablet · After food · 7 d · Qty 14 tablet`을 표시했고 Issue 버튼은 활성 상태였다.
- 복잡 힌디 HTML: 원문 `71,726 bytes`, gzip-9 `10,275 bytes`; 새 CSS gzip-9 `2,757 bytes`.
- 최종 visual-verdict 동등 감사: 3차 반복 `96/100`, 통과. 320·360 첫 화면 행동 노출, PRN 분리, 정적/동적 구조 일치를 수정 후 재검증했다.

## 회귀 테스트

```text
uv run pytest -q
39 passed, 1 existing StarletteDeprecationWarning in 0.65s
```

발급·재발급·토큰·QR·계측 계약과 함께 새 인포그래픽 DOM, M/N/E/H, Afternoon 라벨, 7개 단위 SVG, 긴 약명, ½·ml·캡슐·점안액, PRN 두 제한 타일, 데모 A/B의 반복 행동 띠, JavaScript 이전 SSR 정보, gzip 예산, 민감 메타데이터 비노출을 검증한다.

## 문서·구현 불일치 정리

- `docs/03-wireframe-patient.md`의 표·카드·미디어 placeholder는 초기 탐색 기록이며 현재 서버 정본이 아니다.
- `docs/06-mobile-poster-visual-qa.md`는 첫 번째 하루 흐름 포스터 반복의 기록이다. 이번 문서와 `patient.html`·`patient-poster.css`가 최신 인포그래픽 행동 띠 정본이다.
- 시간대 N의 영어 표기는 환자 구현과 테스트에서 `Noon`을 `Afternoon`으로 통일했다. 데이터 키 M/N/E/H 자체는 바꾸지 않았다. 약사 입력 화면의 기존 `Noon` 표기는 이번 환자 화면 범위 밖이라 유지했다.
- 환자 화면은 약사 전용 `rx.css`·`rx.js`에 의존하지 않는다. QR·약사 입력 화면의 기존 자산과 API는 수정하지 않았다.

## 현지 검증 필요

- 북인도 저문해 사용자에게 `1 약 → 2 식사`, `1 식사 → 2 약` 그림이 설명 없이도 반대로 읽히지 않는지
- 접시·식사 자체 SVG가 알약·점안액과 혼동되지 않는지, `with food`와 `empty stomach`가 현지 표현에 맞는지
- `Packet/पुड़िया`, `Afternoon/दोपहर`, `Evening/शाम`, `Night/रात` 어휘가 약사 봉투 표기와 환자 일과에 맞는지
- PRN의 `하루 최대`와 `최소 간격`을 환자가 서로 다른 제한으로 회상하는지
- 실제 약 봉투에 색+번호를 적는 운영 절차가 1·3·6약 모두에서 일관되게 유지되는지
- 저가 Android 실기기, WhatsApp 인앱 브라우저, 직사광선, 확대 글꼴에서의 최종 확인
