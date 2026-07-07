/* ============================================================
   indoro 약사 화면 JS (rx.js) — P1 /rx/new + P3 /rx/{id}/qr 공용
   렌더 정본: wireframes/pharmacist/p1-input.html · p3-qr.html의 JS 빌더 이식(실연동판).
   부트스트랩: 템플릿이 window.INDORO = { page:"new", meta:{...} } 또는
              { page:"qr", prescription_id:"..." } 를 심는다.
   메타 단일 소스: meta = 서버 config(patterns.yaml + i18n.yaml)를 라우터가 임베드 —
   클라 하드코딩 없음(§4.8의 의도). 약국 식별: localStorage 'indoro.pharmacy'
   (데모 기본 "ph-demo-001") → X-Pharmacy-Id 헤더.
   ============================================================ */
"use strict";
(function () {
  var BOOT = window.INDORO || {};

  /* ---------- 공용 유틸 ---------- */
  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtN(v) {              /* 숫자 ASCII, 0.5 → ½ */
    if (v == null) return "0";
    var i = Math.floor(v), f = v - i;
    if (Math.abs(f - 0.5) < 1e-9) return (i === 0 ? "" : String(i)) + "½";
    return String(v);
  }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function pharmacyId() {
    try { return localStorage.getItem("indoro.pharmacy") || "ph-demo-001"; }
    catch (e) { return "ph-demo-001"; }
  }
  function apiHeaders() {
    return { "Content-Type": "application/json", "X-Pharmacy-Id": pharmacyId() };
  }
  function istNow() {             /* issued_at_client — +05:30 허용(§4.1) */
    return new Date(Date.now() + 330 * 60000).toISOString().replace(/\.\d+Z$/, "+05:30");
  }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtDate(iso) {         /* "2026-08-05T..." → "5 Aug 2026" */
    var p = String(iso).slice(0, 10).split("-");
    return (+p[2]) + " " + MONTHS[+p[1] - 1] + " " + p[0];
  }
  function fmtTimeIST(iso) {
    var d = new Date(Date.parse(iso) + 198e5); /* +05:30 */
    var h = d.getUTCHours(), m = d.getUTCMinutes();
    var ap = h >= 12 ? "pm" : "am"; h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" : "") + m + " " + ap;
  }
  /* UUIDv4 — crypto 필수(H4: Math.random 폴백 금지). 미지원 브라우저는 발급 비활성. */
  function uuid4() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    if (window.crypto && crypto.getRandomValues) {
      var b = new Uint8Array(16);
      crypto.getRandomValues(b);
      b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
      var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
      return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
    }
    return null;
  }

  /* G2 연결 점 — navigator.onLine 기반 (전 페이지 공통) */
  function bindConnDots(onChange) {
    function apply() {
      var off = !navigator.onLine;
      Array.prototype.forEach.call(document.querySelectorAll(".conn-dot"), function (el) {
        el.classList.toggle("is-off", off);
      });
      if (onChange) onChange(off);
    }
    window.addEventListener("online", apply);
    window.addEventListener("offline", apply);
    apply();
  }

  /* ==========================================================
     P1 — 새 처방 입력 (/rx/new)
     ========================================================== */
  function initNew() {
    var META = BOOT.meta;                 /* {patterns, pattern_order, slot_order, i18n} */
    var I18N = META.i18n;

    /* 라벨 접근자 — 약사측 UI는 영어 고정(병기 없음) */
    function unitEn(u) { return (I18N.dose_units[u] || { en: u }).en; }
    function timingEn(t) { return t ? (I18N.timing_food[t] || { en: t }).en : "Not set"; }
    function dayEn(k) { return (I18N.days_of_week[k] || { en: k }).en; }
    function patOf(it) { return META.patterns[it.pattern_key] || null; }

    /* 약사 관용 축약 라벨 — [알려진 한계] 설정 카탈로그(abbr_en)로 이전 예정(와이어프레임 주석 승계).
       미등록 pattern_key는 name.en 폴백 — 카탈로그 추가 시 undefined 렌더 방지. */
    var PATTERN_ABBR = { OD_MORNING: "OD morning", OD_NIGHT: "OD night", BD: "BD", TDS: "TDS",
      QID: "QID", WEEKLY_ONCE: "Weekly", STAT_SINGLE: "STAT", PRN: "PRN", CUSTOM: "Custom" };
    function patternLabel(k) {
      var p = META.patterns[k];
      return PATTERN_ABBR[k] || (p && p.name && p.name.en) || k;
    }
    function patternSub(k) {
      var p = META.patterns[k];
      return p.schedule_type === "weekly" ? "1/wk" : p.digits;   /* WEEKLY digits는 환자용 데바나가리 — 영어 대체 */
    }

    function sumDoses(it) { return (it.doses.M || 0) + (it.doses.N || 0) + (it.doses.E || 0) + (it.doses.H || 0); }
    function digits4(it) { return ["M", "N", "E", "H"].map(function (s) { return fmtN(it.doses[s] || 0); }).join("-"); }
    function digitsShort(it) {
      var d = it.doses;
      var arr = (d.H || 0) > 0 ? ["M", "N", "E", "H"] : ["M", "N", "E"];
      return arr.map(function (s) { return fmtN(d[s] || 0); }).join("-");
    }
    function patShort(it) {
      var p = patOf(it);
      if (!p) return "?";
      if (p.schedule_type === "prn") return "SOS";
      if (p.schedule_type === "custom") return "Custom";
      if (p.schedule_type === "weekly") {
        var dw = it.extra_params && it.extra_params.day_of_week;
        return "1/wk" + (dw ? " (" + dayEn(dw).slice(0, 3) + ")" : "");
      }
      if (p.schedule_type === "once") return "1×";
      return digitsShort(it) + (it.dose_unit === "ml" ? " ml" : "");
    }
    function autoTotal(it) {            /* Σ슬롯×일수 — prn/custom은 수동 */
      var p = patOf(it);
      if (!p) return null;
      var s = sumDoses(it);
      if (p.schedule_type === "daily") return s * (it.duration_days || 0);
      if (p.schedule_type === "weekly") return s * Math.max(1, Math.round((it.duration_days || 0) / 7));
      if (p.schedule_type === "once") return s;
      return null;
    }
    function recalc(it) {
      var a = autoTotal(it);
      if (a != null && !it._totalEdited) it.total_quantity = a;
    }

    /* ---------- 계측 (docs/02 §11) ---------- */
    /* [T0] 폼 오픈: client_input_id 생성(D10) + gross 타이머(performance.now 단조시계) */
    var CID = uuid4();
    var MET = { t0: performance.now(), active: 0, last: null, gaps: 0, auto: 0, retry: 0 };
    document.addEventListener("pointerdown", metBump, true);
    document.addEventListener("keydown", metBump, true);
    function metBump() {
      var now = performance.now();
      if (MET.last != null) {
        var gap = now - MET.last;
        if (gap < 30000) { MET.active += gap; } else { MET.gaps++; }  /* 30초+ 갭 제외 */
      }
      MET.last = now;
    }

    /* ---------- 폼 모델 ---------- */
    var M = null;
    var submitting = false;
    function freshItem(pos, inheritFrom) {
      return { position: pos, drug_name_raw: "", drug_id: null, drug: null, pattern_key: null,
        doses: { M: 0, N: 0, E: 0, H: 0 }, dose_unit: "tablet", timing_food: null,
        duration_days: inheritFrom ? inheritFrom.duration_days : null, total_quantity: 0,
        prn_reason_key: null, prn_max_per_day: null, prn_min_gap_hours: null,
        extra_params: null, note: null,
        _inheritFrom: inheritFrom ? inheritFrom.position : null, _totalEdited: false, _noteOpen: false };
    }
    function initModel() {
      /* lang 프리필: 정식으론 pharmacies.default_patient_lang — 페이지 GET엔 약국 헤더가 없어
         서버 조회 생략, 데모 약국(ph-demo-001) 기본 hi로 시작(칩으로 즉시 변경 가능). */
      M = { lang: "hi", alias: null, aliasOpen: false, items: [freshItem(1, null)], expanded: 0, errors: [] };
    }
    function errsFor(i) {
      return M.errors.filter(function (e) { return e.path.indexOf("items." + i + ".") === 0; });
    }
    function errFor(i, field) {
      for (var k = 0; k < M.errors.length; k++) {
        if (M.errors[k].path === "items." + i + "." + field) return M.errors[k];
      }
      return null;
    }

    /* ---------- 렌더 ---------- */
    function renderHeader() {
      $("#hdrLang").innerHTML = [["hi", "हिन्दी"], ["en", "English"]].map(function (L) {
        return '<button class="chip" type="button" data-act="lang" data-l="' + L[0] + '" aria-pressed="' + (M.lang === L[0]) + '">' + L[1] + "</button>";
      }).join("");
      var z = $("#aliasZone");
      if (M.aliasOpen) {
        z.innerHTML = '<label class="field"><span class="field-label">Patient alias (optional)</span>' +
          '<input class="input" type="text" maxlength="20" data-in="alias" value="' + esc(M.alias || "") + '" placeholder="e.g. R.K. / Amma">' +
          '<span class="field-help">Do not write real names</span></label>';
      } else {
        z.innerHTML = '<button class="btn btn-ghost" type="button" data-act="alias-open" style="justify-content:flex-start;padding-left:0;">' +
          (M.alias ? "Alias: " + esc(M.alias) + ' <span class="t-supporting t-secondary">tap to edit</span>' : "+ Patient alias (optional)") + "</button>";
      }
    }

    function svgCaret() {
      return '<svg class="sum-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 6l6 6-6 6"/></svg>';
    }
    function svgErr() {
      return '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.5"/><path d="M8 4.5V9M8 11.2v.3"/></svg>';
    }

    function cardCollapsed(it, i) {
      var meta = patShort(it) + " · " + timingEn(it.timing_food) + " · " + (it.duration_days != null ? it.duration_days + " d" : "—");
      return '<button type="button" class="card item-sum" data-act="expand" data-i="' + i + '" aria-expanded="false">' +
        '<span class="badge-item hue-' + Math.min(it.position, 10) + '">' + it.position + "</span>" +
        '<span class="sum-body"><b class="sum-name">' + (esc(it.drug_name_raw) || '<span class="t-secondary">(no name)</span>') + "</b>" +
        '<span class="sum-meta t-secondary">' + meta + "</span></span>" + svgCaret() + "</button>";
    }

    function doseGrid(it) {
      var p = patOf(it);
      var slotNames = { M: "Morning", N: "Noon", E: "Evening", H: "Night" };
      var allOff = !p || p.schedule_type === "prn" || p.schedule_type === "custom";
      var cells = META.slot_order.map(function (s) {
        var on = !allOff && p.slots.indexOf(s) >= 0;   /* slots 밖 슬롯 = 0 강제 비활성(§4.3 검증의 거울) */
        return '<div class="dose-slot"><span class="slot-name">' + slotNames[s] + "</span>" +
          '<div class="stepper' + (on ? "" : " is-off") + '">' +
          '<button class="stepper-btn" type="button" data-act="dose" data-slot="' + s + '" data-d="-1" aria-label="decrease ' + slotNames[s] + '"' + (on ? "" : " disabled") + ">−</button>" +
          '<output class="stepper-value">' + fmtN(it.doses[s] || 0) + "</output>" +
          '<button class="stepper-btn" type="button" data-act="dose" data-slot="' + s + '" data-d="1" aria-label="increase ' + slotNames[s] + '"' + (on ? "" : " disabled") + ">+</button>" +
          "</div></div>";
      }).join("");
      var help = !p ? '<span class="inline-note">Pick a pattern first — slots follow the pattern</span>'
        : (allOff ? '<span class="inline-note">' + (p.schedule_type === "prn" ? "PRN: no fixed slots — set limits below" : "Custom: write instructions below") + "</span>" : "");
      return '<div class="dose-grid">' + cells + "</div>" + help;
    }

    function patternZone(it, i) {
      var p = patOf(it);
      if (!p) return "";
      var h = "";
      if (p.schedule_type === "weekly") {
        var dw = it.extra_params && it.extra_params.day_of_week;
        var dwErr = errFor(i, "extra_params.day_of_week");
        h += '<div class="zone"><span class="field-label">Day of week <span class="t-supporting t-secondary">required</span></span><div class="chip-row">' +
          Object.keys(I18N.days_of_week).map(function (k) {
            return '<button class="chip" type="button" data-act="day" data-dw="' + k + '" aria-pressed="' + (dw === k) + '">' + dayEn(k).slice(0, 3) + "</button>";
          }).join("") + "</div>" +
          (dwErr ? '<span class="field-error">' + svgErr() + esc(dwErr.msg) + "</span>" : "") + "</div>";
      }
      if (p.schedule_type === "prn") {
        h += '<div class="zone"><span class="field-label">PRN (only when needed)</span>' +
          '<label class="field"><span class="field-help" style="margin:0 0 4px;">Reason</span>' +
          '<select class="input" data-in="prnreason"><option value=""' + (it.prn_reason_key ? "" : " selected") + ">Select reason…</option>" +
          Object.keys(I18N.prn_reasons).map(function (k) {
            return '<option value="' + k + '"' + (it.prn_reason_key === k ? " selected" : "") + ">" + I18N.prn_reasons[k].en + "</option>";
          }).join("") + "</select></label>" +
          '<div class="qty-row" style="margin-top:10px;"><span class="t-supporting t-secondary">Max per day</span>' +
          '<div class="stepper"><button class="stepper-btn" type="button" data-act="prnmax" data-d="-1" aria-label="decrease max per day">−</button><output class="stepper-value">' + fmtN(it.prn_max_per_day || 0) + '</output><button class="stepper-btn" type="button" data-act="prnmax" data-d="1" aria-label="increase max per day">+</button></div>' +
          '<span class="t-supporting t-secondary">Min gap</span>' +
          '<div class="stepper"><button class="stepper-btn" type="button" data-act="prngap" data-d="-1" aria-label="decrease gap hours">−</button><output class="stepper-value">' + fmtN(it.prn_min_gap_hours || 0) + ' h</output><button class="stepper-btn" type="button" data-act="prngap" data-d="1" aria-label="increase gap hours">+</button></div></div></div>';
      }
      if (it.pattern_key === "CUSTOM") {
        var xp = it.extra_params || {};
        var insErr = errFor(i, "extra_params.instructions");
        var vbErr = errFor(i, "extra_params.verbal_counseling_given");
        h += '<div class="zone"><label class="field"><span class="field-label">Special instructions <span class="t-supporting t-secondary">required</span></span>' +
          '<textarea class="input' + (insErr ? " is-invalid" : "") + '" rows="3" data-in="custom-ins" placeholder="e.g. 2 tab for 3 days, then 1 tab for 3 days…">' + esc(xp.instructions || "") + "</textarea>" +
          (insErr ? '<span class="field-error">' + svgErr() + esc(insErr.msg) + "</span>" : "") + "</label>" +
          '<label class="check"><input type="checkbox" data-in="custom-verbal"' + (xp.verbal_counseling_given ? " checked" : "") + "><span>I explained this verbally to the patient <b>(required)</b></span></label>" +
          (vbErr ? '<span class="field-error">' + svgErr() + esc(vbErr.msg) + "</span>" : "") +
          '<p class="notice">Patient screen shows a caution card instead of a video for custom instructions.</p></div>';
      }
      return h;
    }

    function cardExpanded(it, i) {
      var err = errFor(i, "drug_name_raw");
      var p = patOf(it);
      var isStat = p && p.schedule_type === "once";
      var manualQty = !p || p.schedule_type === "prn" || p.schedule_type === "custom" || it._totalEdited;
      var qtyUnit = unitEn(it.dose_unit);

      /* 필드 인라인로 못 붙인 잔여 에러(서버 422의 임의 path 포함) — 카드 상단에 나열 */
      var known = ["drug_name_raw", "extra_params.day_of_week", "extra_params.instructions", "extra_params.verbal_counseling_given"];
      var rest = errsFor(i).filter(function (e) {
        return known.indexOf(e.path.slice(("items." + i + ".").length)) < 0;
      });

      var h = '<section class="card card--emphasized" data-card="' + i + '">';
      h += '<div class="item-head"><button class="item-fold" type="button" data-act="fold" aria-expanded="true">' +
        '<span class="badge-item hue-' + Math.min(it.position, 10) + '">' + it.position + "</span>" +
        '<span class="item-title">Item ' + it.position + "</span></button>" +
        (M.items.length > 1 ? '<button class="btn btn-ghost" type="button" data-act="remove" data-i="' + i + '">Remove</button>' : "") + "</div>";

      if (rest.length) {
        h += rest.map(function (e) {
          return '<span class="field-error">' + svgErr() + esc(e.path.slice(("items." + i + ".").length) + ": " + e.msg) + "</span>";
        }).join("");
      }

      /* 약명 — 유일한 타이핑 필드. 2자부터 자동완성(fetch /api/drugs, 300ms 디바운스),
         미스·미채택도 동일 경로 발급(DB-optional §4.5) */
      h += '<div class="fgroup ac-wrap"><label class="field"><span class="field-label">Drug name</span>' +
        '<input class="input' + (err ? " is-invalid" : "") + '" type="text" data-in="name" data-i="' + i + '" value="' + esc(it.drug_name_raw) + '" placeholder="Type brand or generic name" autocomplete="off">' +
        (err ? '<span class="field-error">' + svgErr() + esc(err.msg) + "</span>" : "") +
        (!navigator.onLine ? '<span class="field-help">Autocomplete off while offline — type the full name</span>' : "") +
        "</label>" +
        (it.drug_id && it.drug ? '<span class="inline-note">Matched: ' + esc(it.drug.generic_name || "") + " " + esc(it.drug.strength || "") + " · defaults applied</span>" : "") +
        '<div class="ac-pop" id="acPop" hidden></div></div>';

      /* 최근 약 레일: 80% 범위 외(로컬 캐시 설계 미확정) — 자동완성·칩·승계만으로 20~40초 동선 유지 */

      /* 패턴 칩 그리드 — 서버 config 임베드(pattern_order · sort_order 순), 칩 탭 = 용량 기본값 채움 */
      h += '<div class="fgroup"><span class="field-label">Dose pattern</span><div class="chip-row">' +
        META.pattern_order.map(function (k) {
          return '<button class="chip" type="button" data-act="pat" data-k="' + k + '" aria-pressed="' + (it.pattern_key === k) + '">' + patternLabel(k) + ' <span class="chip-sub">' + patternSub(k) + "</span></button>";
        }).join("") + "</div></div>";

      h += '<div class="fgroup"><span class="field-label">Dose per slot</span>' + doseGrid(it) + "</div>";

      /* 단위 7종 칩 — 기본 tablet, 항상 노출(숨은 기본값 금지 — 시럽 사고 방지 §3.3) */
      h += '<div class="fgroup"><span class="field-label">Unit</span><div class="chip-row">' +
        Object.keys(I18N.dose_units).map(function (u) {
          return '<button class="chip" type="button" data-act="unit" data-u="' + u + '" aria-pressed="' + (it.dose_unit === u) + '">' + I18N.dose_units[u].en + "</button>";
        }).join("") + "</div></div>";

      /* 식전후 — 패턴과 독립 축(D14), 4종 + Not set(NULL) */
      h += '<div class="fgroup"><span class="field-label">Food timing</span><div class="chip-row">' +
        Object.keys(I18N.timing_food).map(function (t) {
          return '<button class="chip" type="button" data-act="timing" data-t="' + t + '" aria-pressed="' + (it.timing_food === t) + '">' + I18N.timing_food[t].en + "</button>";
        }).join("") +
        '<button class="chip" type="button" data-act="timing" data-t="" aria-pressed="' + (it.timing_food == null) + '">Not set</button>' +
        "</div></div>";

      /* 기간 — 프리셋 칩 + 스테퍼. 첫 항목 기본값 없음, 2번째부터 직전 값 승계. STAT은 비활성 */
      h += '<div class="fgroup"><span class="field-label">Duration (days)' + (isStat ? ' <span class="t-supporting t-secondary">single dose — off</span>' : "") + "</span>" +
        '<div class="duration-row' + (isStat ? " dim" : "") + '">' +
        I18N.duration_presets.map(function (d) {
          return '<button class="chip" type="button" data-act="dur" data-d="' + d + '" aria-pressed="' + (it.duration_days === d) + '">' + d + "d</button>";
        }).join("") +
        '<div class="stepper"><button class="stepper-btn" type="button" data-act="durstep" data-d="-1" aria-label="decrease days">−</button><output class="stepper-value">' + (it.duration_days != null ? it.duration_days : "—") + '</output><button class="stepper-btn" type="button" data-act="durstep" data-d="1" aria-label="increase days">+</button></div></div>' +
        (it._inheritFrom && it.duration_days != null ? '<span class="inline-note">Inherited from item ' + it._inheritFrom + " — tap to change</span>" : "") +
        "</div>";

      /* 총량 — Σ자동 산출 + 탭 수정(서버는 경고만 §3.3) */
      h += '<div class="fgroup"><span class="field-label">Total quantity</span><div class="qty-row">' +
        (manualQty
          ? '<div class="stepper"><button class="stepper-btn" type="button" data-act="qtystep" data-d="-1" aria-label="decrease total">−</button><output class="stepper-value">' + fmtN(it.total_quantity) + '</output><button class="stepper-btn" type="button" data-act="qtystep" data-d="1" aria-label="increase total">+</button></div><span class="t-supporting t-secondary">' + qtyUnit + (it._totalEdited ? " · edited — server warns only" : " · manual") + "</span>"
          : '<span class="qty-num">' + fmtN(it.total_quantity) + "</span><span>" + qtyUnit + '</span><span class="t-supporting t-secondary">auto Σ slots × days</span><button class="btn btn-ghost" type="button" data-act="qty-edit">Edit</button>') +
        "</div></div>";

      h += patternZone(it, i);

      h += '<div class="fgroup">' + (it._noteOpen || it.note
        ? '<label class="field"><span class="field-label">Note (optional)</span><textarea class="input" rows="2" data-in="note">' + esc(it.note || "") + "</textarea></label>"
        : '<button class="btn btn-ghost" type="button" data-act="note-open" style="justify-content:flex-start;padding-left:0;">+ Note (optional)</button>') + "</div>";

      h += "</section>";
      return h;
    }

    function renderItems() {
      $("#itemList").innerHTML = M.items.map(function (it, i) {
        return i === M.expanded ? cardExpanded(it, i) : cardCollapsed(it, i);
      }).join("");
    }

    function renderBottom() {
      $("#itemCount").textContent = M.items.length + (M.items.length === 1 ? " item" : " items");
      var full = M.items.length >= 10;                 /* items 1..10 상한(§4.3) */
      $("#addBtn").disabled = full;
      $("#addBtn").textContent = "+ Add drug (item " + Math.min(M.items.length + 1, 10) + ")";
      $("#addNote").textContent = full ? "Max 10 items per prescription" : "";
    }

    function renderBanner() {
      var b = $("#errBanner");
      if (!M.bannerMsg) { b.hidden = true; return; }
      $("#errBannerTxt").innerHTML = M.bannerMsg;      /* 내부 생성 문자열만(사용자 입력은 esc 처리 후 조립) */
      $("#errRetry").hidden = !M.bannerRetry;
      b.hidden = false;
    }

    function renderAll() { renderHeader(); renderItems(); renderBottom(); renderBanner(); }

    /* ---------- 자동완성 — GET /api/drugs?q= (300ms 디바운스 · AbortController) ---------- */
    var AC = { timer: null, ctrl: null, list: [] };
    function scheduleAC(i) {
      if (AC.timer) clearTimeout(AC.timer);
      AC.timer = setTimeout(function () { fetchAC(i); }, 300);
    }
    function fetchAC(i) {
      var pop = $("#acPop");
      if (!pop || M.expanded !== i) return;
      if (!navigator.onLine) { pop.hidden = true; return; }          /* 오프라인: 조용히 비활성 */
      var q = (M.items[i].drug_name_raw || "").trim();
      if (q.length < 2) { pop.hidden = true; return; }               /* 2자부터(§4.5) */
      if (AC.ctrl) AC.ctrl.abort();
      AC.ctrl = new AbortController();
      fetch("/api/drugs?q=" + encodeURIComponent(q) + "&limit=8", { signal: AC.ctrl.signal })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (hits) { AC.list = hits || []; renderAC(i, q); })
        .catch(function (e) {                                        /* 실패도 흐름 미차단(DB-optional) */
          if (e && e.name === "AbortError") return;
          AC.list = [];
          renderAC(i, q);
        });
    }
    function renderAC(i, q) {
      var pop = $("#acPop");
      if (!pop || M.expanded !== i) return;
      var cur = (M.items[i].drug_name_raw || "").trim();
      if (cur !== q || q.length < 2) { pop.hidden = true; return; }  /* 응답 도착 전 입력 변경 무시 */
      var rows = AC.list.map(function (d) {
        return '<button class="ac-row" type="button" data-act="adopt" data-di="' + esc(d.id) + '">' +
          '<span class="ac-main">' + esc(d.brand_name) + "</span>" +
          '<span class="ac-sub">' + esc(d.generic_name || "") + " · " + esc(d.strength || "") + " · " + esc(d.form || "") + "</span></button>";
      });
      /* 자유입력 확정 행 상시 — "등록 안 된 약" 차단 문구 금지(DB-optional §4.5) */
      rows.push('<button class="ac-row ac-new" type="button" data-act="acnew">' +
        '<span class="ac-main">Use “' + esc(q) + '” as new drug name</span>' +
        '<span class="ac-sub">' + (AC.list.length ? "Not the one above?" : "Not in list") + " — issuing works the same</span></button>");
      pop.innerHTML = rows.join("");
      pop.hidden = false;
    }
    function closeAC() { var pop = $("#acPop"); if (pop) pop.hidden = true; }

    /* ---------- 항목 조작 ---------- */
    function applyPattern(it, k) {
      it.pattern_key = k;
      var p = META.patterns[k];
      it.doses = { M: 0, N: 0, E: 0, H: 0 };
      (p.slots || []).forEach(function (s) { it.doses[s] = 1; });    /* 칩 탭 = 기본값 완성 */
      if (p.schedule_type === "once") it.duration_days = 1;
      if (p.schedule_type === "weekly") {
        if (!(it.extra_params && "day_of_week" in it.extra_params)) it.extra_params = { day_of_week: null };
      } else if (k === "CUSTOM") {
        if (!(it.extra_params && "instructions" in it.extra_params)) it.extra_params = { instructions: "", verbal_counseling_given: false };
      } else {
        it.extra_params = null;
      }
      if (p.schedule_type === "prn") {
        if (it.prn_max_per_day == null) it.prn_max_per_day = 2;
        if (it.prn_min_gap_hours == null) it.prn_min_gap_hours = 6;
      } else { it.prn_reason_key = null; it.prn_max_per_day = null; it.prn_min_gap_hours = null; }
      recalc(it);
    }
    function adoptSeed(it, d) {
      it.drug_name_raw = d.brand_name;
      it.drug_id = d.id;
      it.drug = { brand_name: d.brand_name, generic_name: d.generic_name, strength: d.strength, form: d.form };
      /* 시드 default_* 3종은 전부 null(용법 제안 금지) — 값이 오면 프리필 */
      if (d.default_pattern_key) applyPattern(it, d.default_pattern_key);
      if (d.default_timing_food) it.timing_food = d.default_timing_food;
      if (d.default_dose_unit) it.dose_unit = d.default_dose_unit;
      recalc(it);
      MET.auto++;                                                    /* used_autocomplete_count++ */
    }

    /* ---------- 클라 사전 검증 (§4.3 미러 — 서버 422가 정본) ---------- */
    function validateAll() {
      var errs = [];
      M.items.forEach(function (it, i) {
        var pre = "items." + i + ".";
        if (!(it.drug_name_raw || "").trim()) errs.push({ path: pre + "drug_name_raw", msg: "Drug name is required" });
        var p = patOf(it);
        if (!p) { errs.push({ path: pre + "pattern_key", msg: "Pick a dose pattern" }); return; }
        if (p.schedule_type === "daily") {
          if (sumDoses(it) <= 0) errs.push({ path: pre + "doses", msg: "Set at least one dose" });
          if (it.duration_days == null) errs.push({ path: pre + "duration_days", msg: "Set duration" });
        }
        if (p.schedule_type === "weekly") {
          if (!(it.extra_params && it.extra_params.day_of_week)) errs.push({ path: pre + "extra_params.day_of_week", msg: "Pick a day of week" });
          if (it.duration_days == null) errs.push({ path: pre + "duration_days", msg: "Set duration" });
        }
        if (it.pattern_key === "CUSTOM") {
          var xp = it.extra_params || {};
          if (!(xp.instructions || "").trim()) errs.push({ path: pre + "extra_params.instructions", msg: "Instructions are required" });
          if (!xp.verbal_counseling_given) errs.push({ path: pre + "extra_params.verbal_counseling_given", msg: "Confirm you explained verbally" });
        }
      });
      return errs;
    }
    function showErrors(errs, bannerMsg) {
      M.errors = errs;
      M.bannerMsg = bannerMsg;
      M.bannerRetry = false;
      var first = errs.length ? parseInt(errs[0].path.split(".")[1], 10) : -1;
      if (first >= 0) M.expanded = first;                            /* 해당 카드 자동 확장 */
      closeSheet();
      renderAll();
      var el = document.querySelector('[data-card="' + first + '"]');
      if (el) el.scrollIntoView({ block: "start" });
    }

    /* ---------- P2 확인 시트 ---------- */
    function rowLead(it) {
      var p = patOf(it);
      if (!p) return "?";
      if (p.schedule_type === "prn") return "SOS";
      if (p.schedule_type === "custom") return "Custom";
      return digits4(it);
    }
    function rowMeta(it) {
      var p = patOf(it) || {};
      var u = unitEn(it.dose_unit);
      var parts = [rowLead(it) + " " + u];
      if (p.schedule_type === "weekly") {
        var dw = it.extra_params && it.extra_params.day_of_week;
        parts[0] += " · 1/wk" + (dw ? " (" + dayEn(dw).slice(0, 3) + ")" : " (day?)");
      }
      if (p.schedule_type === "once") parts[0] += " · single dose";
      if (p.schedule_type === "prn") {
        parts[0] = "SOS" + (it.prn_reason_key ? " (" + I18N.prn_reasons[it.prn_reason_key].en + ")" : "") +
          (it.prn_max_per_day ? " · max " + fmtN(it.prn_max_per_day) + "/day" : "") +
          (it.prn_min_gap_hours ? " · gap " + fmtN(it.prn_min_gap_hours) + " h" : "");
      }
      parts.push(timingEn(it.timing_food));
      parts.push(it.duration_days != null ? it.duration_days + " d" : "—");
      parts.push("Qty " + fmtN(it.total_quantity) + " " + u);
      return parts.join(" · ");
    }
    function renderSheet() {
      $("#sheetSub").textContent = "Guide language: " + (M.lang === "hi" ? "हिन्दी" : "English") +
        (M.alias ? " · Alias: " + M.alias : " · (no alias)");
      $("#sheetRows").innerHTML = M.items.map(function (it, i) {     /* position 순 고정 — 처방전 대조용 */
        var xp = it.extra_params || {};
        var custom = it.pattern_key === "CUSTOM"
          ? '<span class="sum-quote">' + esc(xp.instructions || "(instructions missing)") + "</span>" +
            '<span class="t-supporting ' + (xp.verbal_counseling_given ? "t-secondary" : "") + '"' + (xp.verbal_counseling_given ? "" : ' style="color:var(--color-error);"') + ">Verbal counseling: " + (xp.verbal_counseling_given ? "given ✓" : "NOT confirmed") + "</span>"
          : "";
        return '<button class="sum-row" type="button" data-act="sheet-row" data-i="' + i + '">' +
          '<span class="badge-item badge-item--sm hue-' + Math.min(it.position, 10) + '">' + it.position + "</span>" +
          '<span class="sum-row-body"><b>' + (esc(it.drug_name_raw) || "(no name)") + "</b>" +
          '<span class="t-supporting t-secondary">' + rowMeta(it) + "</span>" + custom + "</span></button>";
      }).join("");
      var warns = [];
      M.items.forEach(function (it) {
        if (it.dose_unit === "ml") warns.push("Item " + it.position + " · " + it.drug_name_raw + ": total " + fmtN(it.total_quantity) + " ml — ml, not bottles");
        var a = autoTotal(it);
        if (a != null && it._totalEdited && a !== it.total_quantity) warns.push("Item " + it.position + ": total " + fmtN(it.total_quantity) + " differs from auto Σ " + fmtN(a) + " (allowed — server warns only)");
      });
      var wz = $("#sheetWarn");
      wz.hidden = warns.length === 0;
      wz.innerHTML = warns.length ? "<b>Check units &amp; totals</b>" + warns.map(function (w) { return "<span>" + esc(w) + "</span>"; }).join("") : "";
    }
    function openSheet() { renderSheet(); $("#sheetBack").classList.add("is-open"); }
    function closeSheet() { $("#sheetBack").classList.remove("is-open"); }

    /* ---------- 제출 — POST /api/prescriptions (멱등 D10) ---------- */
    function buildPayload() {
      return {
        client_input_id: CID,
        issued_at_client: istNow(),
        lang: M.lang,
        patient_label: M.alias,
        items: M.items.map(function (it) {
          return {
            position: it.position,
            drug_name_raw: (it.drug_name_raw || "").trim(),
            drug_id: it.drug_id,
            pattern_key: it.pattern_key,
            doses: it.doses,
            dose_unit: it.dose_unit,
            timing_food: it.timing_food,
            duration_days: it.duration_days,
            total_quantity: it.total_quantity,
            prn_reason_key: it.prn_reason_key,
            prn_max_per_day: it.prn_max_per_day,
            prn_min_gap_hours: it.prn_min_gap_hours,
            extra_params: it.extra_params,
            note: it.note
          };
        }),
        client_metrics: {                                            /* [T1] 확정(§6.3) */
          input_duration_ms: Math.round(performance.now() - MET.t0),
          active_input_ms: Math.round(MET.active),
          idle_gaps_count: MET.gaps,
          used_autocomplete_count: MET.auto,
          retry_count: MET.retry,
          app_version: "v0-web"
        }
      };
    }
    function setIssuing(on) {
      submitting = on;
      var b = $("#issueBtn");
      b.disabled = on;
      b.textContent = on ? "Issuing…" : "Issue";
    }
    function submit() {
      if (submitting) return;                                        /* 더블탭 억제(D10이 최종 방어) */
      if (!CID) {
        showErrors([], "This browser lacks secure random (crypto) — cannot issue. Use a modern browser.");
        return;
      }
      setIssuing(true);
      var ctrl = new AbortController();
      var tmo = setTimeout(function () { ctrl.abort(); }, 15000);
      fetch("/api/prescriptions", {
        method: "POST", headers: apiHeaders(),
        body: JSON.stringify(buildPayload()), signal: ctrl.signal
      }).then(function (r) {
        clearTimeout(tmo);
        return r.json().then(function (j) { return { status: r.status, body: j }; });
      }).then(function (res) {
        if (res.status === 201 || res.status === 200) {              /* 200 = 멱등 replay(동일 token) */
          location.href = "/rx/" + encodeURIComponent(res.body.id) + "/qr";
          return;
        }
        setIssuing(false);
        var err = (res.body && res.body.error) || {};
        if (res.status === 422 && err.fields) {                      /* fields[].path → 카드·필드 인라인 매핑 */
          showErrors(err.fields.map(function (f) {
            return { path: f.path, msg: f.issue || "invalid" };
          }), "Server rejected (422). Fix the highlighted field below. " +
            '<span class="t-supporting">' + esc(err.code || "VALIDATION_ERROR") + " · " + esc(err.message || "") + "</span>");
          return;
        }
        /* 409 IDEMPOTENCY_CONFLICT·500 등 — 인라인 에러 + 재시도 */
        failBanner(esc(err.code || "HTTP " + res.status) + (err.message ? " — " + esc(err.message) : ""));
      }).catch(function () {
        clearTimeout(tmo);
        setIssuing(false);
        /* 네트워크 실패·타임아웃: 동일 client_input_id 재시도 안내.
           완전한 오프라인 outbox(영속 큐·백오프·클라 사전생성 token)는 80% 범위 외 —
           여기서는 인라인 에러 + 수동 재시도 버튼만(재시도 시 retry_count++, 멱등키로 중복 발급 없음). */
        failBanner("Could not reach the server" + (!navigator.onLine ? " (offline)" : "") + ". Nothing was lost — tap Retry.");
      });
    }
    function failBanner(msg) {
      setIssuing(false);
      closeSheet();
      M.errors = [];
      M.bannerMsg = "Issue failed: " + msg;
      M.bannerRetry = true;
      renderBanner();
    }

    /* ---------- 이벤트 위임 ---------- */
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act]");
      if (!btn) {
        if (!e.target.closest(".ac-wrap")) closeAC();
        return;
      }
      var act = btn.getAttribute("data-act");
      var i = M.expanded;
      var it = M.items[i];
      var v;
      switch (act) {
        case "lang": M.lang = btn.getAttribute("data-l"); renderHeader(); break;
        case "alias-open": M.aliasOpen = true; renderHeader(); break;
        case "expand":
          M.expanded = parseInt(btn.getAttribute("data-i"), 10);
          renderItems();
          var el = document.querySelector('[data-card="' + M.expanded + '"]');
          if (el) el.scrollIntoView({ block: "nearest" });
          break;
        case "fold": M.expanded = -1; renderItems(); break;
        case "remove":                                               /* position 재부여 — P3 번호 나열이 안전망 */
          M.items.splice(parseInt(btn.getAttribute("data-i"), 10), 1);
          M.items.forEach(function (x, n) { x.position = n + 1; });
          M.expanded = Math.min(M.expanded, M.items.length - 1);
          renderAll();
          break;
        case "adopt":
          v = btn.getAttribute("data-di");
          var seed = null;
          AC.list.forEach(function (d) { if (String(d.id) === v) seed = d; });
          if (seed && it) { adoptSeed(it, seed); closeAC(); renderItems(); }
          break;
        case "acnew": closeAC(); break;                              /* drug_id 없음 — raw가 정본 */
        case "pat": if (it) { applyPattern(it, btn.getAttribute("data-k")); renderItems(); } break;
        case "dose":
          if (it) {
            var s = btn.getAttribute("data-slot");
            it.doses[s] = Math.max(0, Math.min(10, (it.doses[s] || 0) + 0.5 * parseInt(btn.getAttribute("data-d"), 10)));
            recalc(it); renderItems();
          }
          break;
        case "unit": if (it) { it.dose_unit = btn.getAttribute("data-u"); renderItems(); } break;
        case "timing": if (it) { it.timing_food = btn.getAttribute("data-t") || null; renderItems(); } break;
        case "dur":
          if (it) { it.duration_days = parseInt(btn.getAttribute("data-d"), 10); it._inheritFrom = null; recalc(it); renderItems(); }
          break;
        case "durstep":
          if (it) {
            it.duration_days = Math.max(1, Math.min(90, (it.duration_days || 0) + parseInt(btn.getAttribute("data-d"), 10)));
            it._inheritFrom = null; recalc(it); renderItems();
          }
          break;
        case "qty-edit": if (it) { it._totalEdited = true; renderItems(); } break;
        case "qtystep":
          if (it) {
            var step = it.dose_unit === "ml" ? 5 : 1;
            it.total_quantity = Math.max(0, (it.total_quantity || 0) + step * parseInt(btn.getAttribute("data-d"), 10));
            renderItems();
          }
          break;
        case "day":
          if (it) { it.extra_params = it.extra_params || {}; it.extra_params.day_of_week = btn.getAttribute("data-dw"); renderItems(); }
          break;
        case "prnmax": if (it) { it.prn_max_per_day = Math.max(0, Math.min(12, (it.prn_max_per_day || 0) + parseInt(btn.getAttribute("data-d"), 10))); renderItems(); } break;
        case "prngap": if (it) { it.prn_min_gap_hours = Math.max(0, Math.min(24, (it.prn_min_gap_hours || 0) + parseInt(btn.getAttribute("data-d"), 10))); renderItems(); } break;
        case "note-open": if (it) { it._noteOpen = true; renderItems(); } break;
        case "add":                                                  /* 직전 카드 접힘 + duration 승계 */
          if (M.items.length < 10) {
            var prev = M.items[M.items.length - 1] || null;
            M.items.push(freshItem(M.items.length + 1, prev));
            M.expanded = M.items.length - 1;
            renderAll();
            var nc = document.querySelector('[data-card="' + M.expanded + '"]');
            if (nc) nc.scrollIntoView({ block: "start" });
          }
          break;
        case "review":                                               /* P1→P2 시트(라우트 아님) */
          v = validateAll();
          if (v.length) { showErrors(v, "Fix the highlighted field below before issuing."); break; }
          M.errors = []; M.bannerMsg = null; renderAll();
          openSheet();
          break;
        case "sheet-close": closeSheet(); break;
        case "sheet-row":
          v = parseInt(btn.getAttribute("data-i"), 10);
          closeSheet();
          M.expanded = v;
          renderItems();
          var rc = document.querySelector('[data-card="' + v + '"]');
          if (rc) rc.scrollIntoView({ block: "start" });
          break;
        case "issue": submit(); break;                               /* [T1] */
        case "retry":                                                /* 동일 client_input_id 재전송(D10) */
          MET.retry++;
          M.bannerMsg = null; M.bannerRetry = false; renderBanner();
          submit();
          break;
      }
    });

    $("#sheetBack").addEventListener("click", function (e) { if (e.target === this) closeSheet(); });

    /* 타이핑 필드 — 입력 중 재렌더 금지(포커스 유지), 모델만 갱신 */
    document.addEventListener("input", function (e) {
      var el = e.target.closest("[data-in]");
      if (!el) return;
      var kind = el.getAttribute("data-in");
      var it = M.items[M.expanded];
      if (kind === "alias") { M.alias = el.value.slice(0, 20) || null; return; }
      if (!it) return;
      if (kind === "name") {
        it.drug_name_raw = el.value;
        it.drug_id = null; it.drug = null;                           /* 타이핑 재개 = 채택 해제 */
        scheduleAC(M.expanded);
      }
      if (kind === "note") it.note = el.value || null;
      if (kind === "custom-ins") { it.extra_params = it.extra_params || {}; it.extra_params.instructions = el.value; }
    });
    document.addEventListener("change", function (e) {
      var el = e.target.closest("[data-in]");
      if (!el) return;
      var it = M.items[M.expanded];
      if (!it) return;
      if (el.getAttribute("data-in") === "prnreason") it.prn_reason_key = el.value || null;
      if (el.getAttribute("data-in") === "custom-verbal") { it.extra_params = it.extra_params || {}; it.extra_params.verbal_counseling_given = el.checked; }
    });

    /* 오프라인 배너 + 자동완성 헬프 갱신 */
    bindConnDots(function (off) {
      $("#offlineBanner").hidden = !off;
      if (M) renderItems();                                          /* 필드 헬프 문구 갱신 */
    });

    initModel();
    renderAll();
    if (!CID) {
      M.bannerMsg = "This browser lacks secure random (crypto) — issuing is disabled (H4: no Math.random fallback).";
      renderBanner();
      $("#reviewBtn").disabled = true;
    }
  }

  /* ==========================================================
     P3 — 발급 완료·QR 표시 (/rx/{id}/qr)
     데이터: GET /api/prescriptions/{id} (bundle 계약: prescription·pharmacy·access·items·token_status)
     QR: 클라 JS 렌더 단일 경로(D18) — static/vendor/qrcode.js (MIT, qrcode-generator 1.4.4)
     ========================================================== */
  function initQr() {
    var pid = BOOT.prescription_id;

    function qrSvg(url) {
      /* ECC Q(인쇄 스펙 §2.1 — 봉투 부착 후 접힘·오염 내성) 단일 렌더 · quiet zone 4모듈 · 타입 자동 */
      var qr = window.qrcode(0, "Q");
      qr.addData(url);
      qr.make();
      return qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
    }

    function show(sel) { Array.prototype.forEach.call(document.querySelectorAll(sel), function (el) { el.hidden = false; }); }

    function render(b) {
      /* 응답 형태 허용 2종: bundle 계약({prescription:{...}}) · 현행 API(처방 필드 톱레벨 평탄화) */
      var rx = b.prescription || b, acc = b.access, items = b.items || [];
      var url = location.origin + "/p/" + acc.token;                 /* QR 페이로드 = /p/{token} 그대로(D2) */
      var svg = qrSvg(url);
      ["#qrMain", "#qrFs", "#qrPrint"].forEach(function (s) { $(s).innerHTML = svg; });

      /* 상태 칩 — token_status: active | revoked | expired */
      var chip = $("#stChip");
      if (b.token_status === "revoked") { chip.className = "status-chip st-revoked"; chip.textContent = "Revoked"; }
      else if (b.token_status === "expired") { chip.className = "status-chip st-expired"; chip.textContent = "Expired"; }
      else { chip.className = "status-chip st-active"; chip.textContent = "Issued"; }

      var label = rx.patient_label || "(no label)";
      $("#issueMeta").textContent = fmtTimeIST(rx.created_at) + " IST · " + label + " · " +
        items.length + (items.length === 1 ? " item" : " items");

      /* 짧은 코드 병기 (D3) — 오프라인 기원 미동기화 등으로 없으면 안내만 */
      var code = acc.short_code_display;
      if (code) {
        $("#shortCode").textContent = code;
        $("#ppCode").textContent = code;
        $("#fsCode").textContent = code;
      } else {
        $("#shortCode").textContent = "—";
        $("#codeHint").textContent = "Code arrives after sync";
      }

      var validTxt = "Valid till " + fmtDate(acc.expires_at);
      $("#validTill").textContent = validTxt;
      $("#ppValid").textContent = validTxt;
      var daysLeft = Math.round((Date.parse(String(acc.expires_at).slice(0, 10)) - Date.now()) / 864e5);
      if (daysLeft <= 3) {                                           /* D-3 "곧 만료" */
        $("#expFlag").textContent = "Expires in " + daysLeft + (daysLeft === 1 ? " day" : " days");
        $("#expFlag").hidden = false;
      }

      /* 항목 번호 배지 — position=hue 고정(봉투 번호 안전망 §2.1 ③) */
      $("#badgeRow").innerHTML = items.map(function (it) {
        return '<span class="badge-item hue-' + Math.min(it.position, 10) + '">' + it.position + "</span>";
      }).join("");

      /* 인쇄 카드 — 약국명·지역 (인쇄가 기본 경로 D21 · v0는 브라우저 인쇄 다이얼로그) */
      $("#ppName").textContent = (b.pharmacy && b.pharmacy.name) || "";
      $("#ppArea").textContent = (b.pharmacy && b.pharmacy.area) || "";

      $("#qrLoad").hidden = true;
      show("[data-loaded]");
    }

    function fail(msg) {
      $("#qrLoad").innerHTML = '<span style="color:var(--color-error);font-weight:600;">' + esc(msg) + "</span>" +
        '<button class="btn btn-secondary" type="button" id="qrRetry">Retry</button>';
      $("#qrRetry").addEventListener("click", load);
    }

    function load() {
      $("#qrLoad").hidden = false;
      $("#qrLoad").textContent = "Loading…";
      fetch("/api/prescriptions/" + encodeURIComponent(pid), { headers: { "X-Pharmacy-Id": pharmacyId() } })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status === 404 ? "Prescription not found (or another pharmacy's)." : "Server error (" + r.status + ").");
          return r.json();
        })
        .then(render)
        .catch(function (e) { fail(e.message || "Could not load."); });
    }

    /* 전체화면 크게 보기 (스캔 실패 사다리 2단 §2.5 · v0 미계측) */
    $("#btnFullscreen").addEventListener("click", function () { $("#fsOverlay").classList.add("is-open"); });
    $("#fsOverlay").addEventListener("click", function () { $("#fsOverlay").classList.remove("is-open"); });

    /* 화면/인쇄 모드 전환 + 인쇄(재출력 = 로컬 재인쇄 — 서버 호출 없음) */
    $("#btnPrint").addEventListener("click", function () { window.print(); });
    $("#btnGoPrint").addEventListener("click", function () { document.body.setAttribute("data-mode", "print"); });
    $("#btnGoScreen").addEventListener("click", function () { document.body.setAttribute("data-mode", "screen"); });

    bindConnDots(null);
    load();
  }

  /* ---------- 부팅 ---------- */
  if (BOOT.page === "new") initNew();
  else if (BOOT.page === "qr") initQr();
})();
