# indoro 와이어프레임 공용 기반 — 토큰·컴포넌트 참고 (팀용)

정본 토큰: `shared/vendor/astryx-theme-neutral.css` (Meta astryx, neutral 테마).
vendor 파일은 `@scope` + `light-dark()` 기반이라 직접 링크하지 않고, **`wf.css`의 `:root`에 라이트 모드 값만 증류**했다. 변수명은 astryx 원명 그대로 — Figma/Flutter 이관 시 이름으로 추적 가능.

## 1. 증류된 토큰 (wf.css `:root`)

| 그룹 | 변수 | 값(라이트) |
|---|---|---|
| 배경 | `--color-background-body` / `-surface` / `-card` / `-muted` / `-popover` | `#f1f1f1` / `#fff` / `#fff` / `#f1f1f1` / `#fff` |
| 텍스트 | `--color-text-primary` / `-secondary` / `-disabled` / `-accent` | `#171717` / `#737373` / `#a3a3a3` / `#262626` |
| 액센트 | `--color-accent` / `--color-accent-muted` / `--color-on-accent` | `#262626` / `#f1f1f1` / `#fff` |
| 시맨틱 | `--color-success` `-error` `-warning` (+`-muted` 3종) | `#007004` `#a50c25` `#745b00` / `#c5e5c0` `#facecb` `#f8da9d` |
| 보더 | `--color-border` / `--color-border-emphasized` / `--color-skeleton` | `#ebebeb` / `#d4d4d4` / `#ebebeb` |
| 오버레이 | `--color-overlay` / `-hover` / `-pressed` | `#00000080` / `#0000000D` / `#0000001A` |
| 10 hue | `--color-{background\|border\|icon\|text}-{red,orange,yellow,green,teal,cyan,blue,purple,pink,gray}` | background=파스텔, icon/text=진한 색(흰 글자 대비 확보) |
| 반경 | `--radius-inner` / `-element` / `-container` / `-full` | `0.375rem` / `0.625rem` / `0.75rem` / `9999px` |
| 그림자 | `--shadow-low` / `-med` / `-high` | vendor 라이트 값 |
| 타이포 롤 | `--text-{display-1..3, heading-1..6, body, label, large, supporting, code}-{size\|weight\|leading}` | vendor 그대로. `--font-weight-*`(400/500/600/700)는 wf.css가 보충 정의 |
| 폰트 | `--font-family-body` | `Figtree, -apple-system, …, sans-serif` — **웹폰트 로드 없음**(Figtree 미로드 → 시스템 폴백). 데바나가리는 OS 폴백(Android 내장 Noto Sans Devanagari)이 담당 |

라이트 고정(다크모드 없음). 애니메이션·트랜지션 토큰은 의도적으로 미증류(계획 §6 — 전부 정지 표현).

## 2. 레이아웃·베이스 규칙

- `body`: max-width **480px** 중앙 정렬(데스크톱 리뷰 프레임), 배경 `--color-background-body`. 설계 기준 360×640, 320px 가로 스크롤 0.
- 환자측 화면은 `<body class="patient">` — 본문 17px·행간 1.65, 굵기는 400/700 2단만 사용(데바나가리 합성 볼드 방지).
- 언어 토글: `html[data-lang="hi"] [lang="en"]{display:none}` (반대도 동일). `data-lang` 미세팅 화면(S3/S4/S5 등)은 hi/en **병기** 그대로 노출된다. wf.js가 `html.lang`도 함께 세팅.
- `?color=0` → `html[data-color="0"]` → `.badge-item` 전부 회색+숫자(그레이스케일 검증 모드). 배지 외 astryx 시맨틱 색은 유지.
- 터치 타깃 ≥48×48px, 주 행동 버튼 전폭·min-height 56px — 컴포넌트 클래스에 이미 반영.
- 이모지 금지 — 아이콘은 각 화면에서 인라인 SVG 단순 도형(stroke=currentColor).

## 3. 컴포넌트 클래스 (요약 — 상세 스니펫은 B1 카탈로그 참조)

| 분류 | 클래스 |
|---|---|
| 레이아웃 | `.screen` `.stack` `.hstack` `.spacer` `.divider` `.t-secondary` `.t-supporting` `.t-center` `.num-lg` |
| 앱바 | `.appbar` `.appbar-title` `.appbar-btn` |
| 카드 | `.card` `.card-title` `.card--emphasized` |
| 버튼 | `.btn` + `.btn-primary`(전폭 56px) / `.btn-secondary` / `.btn-ghost` / `.btn-danger` / `.btn-inline` |
| 칩 | `.chip-row` > `.chip`(+`.chip-sub`), 선택 = `.is-selected` 또는 `aria-pressed="true"` |
| 입력 | `.field` `.field-label` `.input`(+`.is-invalid`) `.field-help` `.field-error` |
| 스테퍼 | `.stepper` > `.stepper-btn`(48px) `.stepper-value`, slots 밖 = `.is-off` |
| 항목 배지 | `.badge-item` + `.hue-1..hue-10`(position 고정 매핑), `.badge-item--sm` |
| 슬롯 스트립 | `.slot-strip` > `.slot-cell`(`.is-off`) > `.slot-ico` `.slot-qty` `.slot-label`, 아래 `.dose-digits` |
| 상태 칩 5종 | `.status-chip` + `.st-active` `.st-viewed` `.st-pending` `.st-expired` `.st-revoked`; 보조 `.flag`(`.flag-edited` `.flag-expiring`), `.conn-dot` |
| 하단 바 | `.bottom-bar`(fixed, 엄지 존) |
| placeholder | `.ph` + `.ph--qr` `.ph--video` `.ph--poster` `.ph--audio`, 용량 라벨 `.ph-size` |
| 개발 툴바 | `.wf-toolbar`(wf.js가 렌더 — 직접 마크업 금지) |
| 상태 가시성 | `data-wf-show="stateA stateB"` / `data-wf-hide="..."` (wf.js가 `.wf-hidden` 토글) |

배지 hue 매핑(1..10): 파랑 blue · 주황 orange · 초록 green · 보라 purple · 적갈 red · 청록 teal · 마젠타 pink · 심청 cyan · 올리브 yellow · 암회 gray — astryx `--color-icon-{hue}`를 배경으로 사용(흰 숫자).

## 4. fixtures.js / wf.js 사용법

```html
<link rel="stylesheet" href="../shared/wf.css">
<!-- 본문 마크업 -->
<script src="../shared/fixtures.js"></script>
<script src="../shared/wf.js"></script>
<script>
  var ctx = WF.init({
    screen: "P1",
    states: ["default", "offline", "error422", "submitting"], // 인벤토리 상태 변형 전부 등록
    langs: null,              // 약사측 = null(영어 고정). 환자 S1 = ["hi","en"]
    fxList: ["a", "b", "c"],  // FX 스위처 (변경 시 리로드가 기본)
    onChange: function (ctx, key) { /* state/lang/color 인플레이스 전환 후 훅 */ }
  });
  var fx = WF.fixture();      // window.INDORO_FX[ctx.fx]
  WF.mark("T0 client_input_id 생성");        // 서버 권위 계측 자리 — 콘솔 데모
  WF.beacon("share.clicked", {method:"copy"}); // 클라 beacon 자리 — 콘솔 데모
</script>
```

`INDORO_FX` 구조: `a/b/c`(FX 3종 — pharmacy·prescription·access·items[]), `meta`(patterns 9종·slots M/N/E/H·timing_food·dose_units 7종·prn_reasons·days_of_week·duration_presets), `drugs_seed`(P1 자동완성 10종), `outbox`(P4/P8용 4상태 샘플). 필드명은 docs/01 스키마 그대로. 데모 기준일 2026-07-06(FX-C의 D-3 만료 판정).

계측 주석 규약(각 화면 HTML): `<!-- 계측: [T0] client_input_id 생성 -->` / `<!-- beacon: media.video_play -->` — 인벤토리 §11(약사)·§12(환자)와 1:1.
