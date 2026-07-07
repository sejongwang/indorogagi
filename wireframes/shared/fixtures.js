/* ============================================================
   indoro 와이어프레임 공용 픽스처 (fixtures.js)
   - 정본 스펙: docs/01-dataflow.md §3(스키마)·§4.3(POST payload)·docs/04 §2.1(FX 정의)
   - 필드명은 docs/01 스키마 그대로: drug_name_raw, pattern_key, doses{M,N,E,H},
     dose_unit, timing_food, duration_days, total_quantity, extra_params, prn_* ...
   - 화면 하드코딩 금지 — 모든 화면은 window.INDORO_FX만 읽는다.
     (예외: S1은 제품 동형 제약상 FX-A를 정적 마크업으로 두고, 인라인 사본으로 전환 처리)
   - 순수 데이터(함수 없음) → JSON.stringify로 인라인 사본 추출 가능.
   - 데모 기준일: 2026-07-06 (FX-C의 "D-3 곧 만료" 판정 기준)
   ============================================================ */
(function () {
  "use strict";

  var PHARMACY = {
    name: "Sharma Medical Store",
    area: "Karol Bagh, Delhi",
    pincode: "110005",
    has_printer: 1,
    ui_lang: "en",
    default_patient_lang: "hi"
  };

  /* ---------- FX-A · 일상 3항목 (전부 자동완성 히트, daily만) ---------- */
  var FX_A = {
    fx: "a",
    label: "FX-A 일상 3항목 (자동완성 히트)",
    pharmacy: PHARMACY,
    prescription: {
      id: "01981fa0-5b2c-7c3e-8a11-4d9f2b7c6e10",
      client_input_id: "9f1c6b2e-8a44-4c1f-b1d2-3e5a7c90d412",
      patient_label: "Mr. S",
      lang: "hi",
      note: null,
      status: "active",            /* 저장 상태. 계산 status는 viewed(§5.2) */
      version: 1,
      entry_method: "manual",
      origin: "online",
      created_at: "2026-07-06T09:12:40Z"
    },
    access: {
      token: "hV8s3kQxWnA9cLd3Ye7Rk2",
      url: "https://indoro.example/p/hV8s3kQxWnA9cLd3Ye7Rk2",
      short_code: "K7F39Q2M",
      short_code_display: "K7F3-9Q2M",
      expires_at: "2026-08-05T09:12:40Z",
      first_viewed_at: "2026-07-06T09:41:05Z",
      scan_count: 2,
      revoked_at: null
    },
    items: [
      {
        position: 1,
        drug_name_raw: "Dolo 650",
        drug_id: "drug-seed-01",
        drug: { brand_name: "Dolo 650", generic_name: "Paracetamol", strength: "650 mg", form: "tablet", caution_keys: [] },
        pattern_key: "TDS",
        doses: { M: 1, N: 1, E: 1, H: 0 },
        dose_unit: "tablet",
        timing_food: "after_food",
        duration_days: 5,
        total_quantity: 15,
        extra_params: null,
        note: null
      },
      {
        position: 2,
        drug_name_raw: "Azithral 500",
        drug_id: "drug-seed-02",
        drug: { brand_name: "Azithral 500", generic_name: "Azithromycin", strength: "500 mg", form: "tablet", caution_keys: [] },
        pattern_key: "OD_MORNING",
        doses: { M: 1, N: 0, E: 0, H: 0 },
        dose_unit: "tablet",
        timing_food: "after_food",
        duration_days: 3,
        total_quantity: 3,
        extra_params: null,
        note: null
      },
      {
        position: 3,
        drug_name_raw: "Pantocid 40",
        drug_id: "drug-seed-03",
        drug: { brand_name: "Pantocid 40", generic_name: "Pantoprazole", strength: "40 mg", form: "tablet", caution_keys: [] },
        pattern_key: "OD_MORNING",
        doses: { M: 1, N: 0, E: 0, H: 0 },
        dose_unit: "tablet",
        timing_food: "empty_stomach",
        duration_days: 10,
        total_quantity: 10,
        extra_params: null,
        note: null
      }
    ]
  };

  /* ---------- FX-B · 코어 8패턴 전부 + CUSTOM 포함 10항목 (상한) ---------- */
  var FX_B = {
    fx: "b",
    label: "FX-B 전 패턴 10항목 (코어 8 + CUSTOM + daily 1)",
    pharmacy: PHARMACY,
    prescription: {
      id: "01981fb2-7d0a-7e41-9c22-5e0a3c8d7f21",
      client_input_id: "b3d90a1c-2f57-4e8b-a6c4-7d1e5f92ab30",
      patient_label: "R.K.",
      lang: "hi",
      note: null,
      status: "active",
      version: 1,
      entry_method: "manual",
      origin: "online",
      created_at: "2026-07-06T11:02:10Z"
    },
    access: {
      token: "Tg5vB2mNqXr7Jd4Wcy9KpL",
      url: "https://indoro.example/p/Tg5vB2mNqXr7Jd4Wcy9KpL",
      short_code: "M2ZD71XQ",
      short_code_display: "M2ZD-71XQ",
      expires_at: "2026-09-07T11:02:10Z",   /* max(30d, 56+7d)=63d (D6) */
      first_viewed_at: null,                 /* 아직 미열람 — P4/P5 상태 다양성 */
      scan_count: 0,
      revoked_at: null
    },
    items: [
      { position: 1, drug_name_raw: "Amlokind 5", drug_id: "drug-seed-04",
        drug: { brand_name: "Amlokind 5", generic_name: "Amlodipine", strength: "5 mg", form: "tablet", caution_keys: [] },
        pattern_key: "OD_MORNING", doses: { M: 1, N: 0, E: 0, H: 0 }, dose_unit: "tablet",
        timing_food: null, duration_days: 30, total_quantity: 30, extra_params: null, note: null },
      { position: 2, drug_name_raw: "Atorva 10", drug_id: "drug-seed-05",
        drug: { brand_name: "Atorva 10", generic_name: "Atorvastatin", strength: "10 mg", form: "tablet", caution_keys: [] },
        pattern_key: "OD_NIGHT", doses: { M: 0, N: 0, E: 1, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 30, total_quantity: 30, extra_params: null, note: null },
      { position: 3, drug_name_raw: "Augmentin 625", drug_id: "drug-seed-06",
        drug: { brand_name: "Augmentin 625 Duo", generic_name: "Amoxicillin + Clavulanic acid", strength: "500/125 mg", form: "tablet", caution_keys: [] },
        pattern_key: "BD", doses: { M: 1, N: 0, E: 1, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 5, total_quantity: 10, extra_params: null, note: null },
      { position: 4, drug_name_raw: "Dolo 650", drug_id: "drug-seed-01",
        drug: { brand_name: "Dolo 650", generic_name: "Paracetamol", strength: "650 mg", form: "tablet", caution_keys: [] },
        pattern_key: "TDS", doses: { M: 1, N: 1, E: 1, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 5, total_quantity: 15, extra_params: null, note: null },
      { position: 5, drug_name_raw: "Ascoril LS Syrup", drug_id: "drug-seed-07",
        drug: { brand_name: "Ascoril LS", generic_name: "Levosalbutamol + Ambroxol + Guaifenesin", strength: "per 5 ml", form: "syrup", caution_keys: [] },
        pattern_key: "QID", doses: { M: 5, N: 5, E: 5, H: 5 }, dose_unit: "ml",
        timing_food: null, duration_days: 4, total_quantity: 80,   /* 총량 단위 = ml (병 아님 — §3.3) */
        extra_params: null, note: null },
      { position: 6, drug_name_raw: "Uprise-D3 60K", drug_id: "drug-seed-08",
        drug: { brand_name: "Uprise-D3 60K", generic_name: "Cholecalciferol", strength: "60000 IU", form: "capsule", caution_keys: [] },
        pattern_key: "WEEKLY_ONCE", doses: { M: 1, N: 0, E: 0, H: 0 }, dose_unit: "capsule",
        timing_food: "with_food", duration_days: 56, total_quantity: 8,
        extra_params: { day_of_week: "sun" }, note: null },
      { position: 7, drug_name_raw: "Zentel 400", drug_id: "drug-seed-09",
        drug: { brand_name: "Zentel 400", generic_name: "Albendazole", strength: "400 mg", form: "tablet", caution_keys: [] },
        pattern_key: "STAT_SINGLE", doses: { M: 1, N: 0, E: 0, H: 0 }, dose_unit: "tablet",
        timing_food: "with_food", duration_days: 1, total_quantity: 1, extra_params: null, note: null },
      { position: 8, drug_name_raw: "Cyclopam", drug_id: "drug-seed-10",
        drug: { brand_name: "Cyclopam", generic_name: "Dicyclomine + Paracetamol", strength: "20/500 mg", form: "tablet", caution_keys: [] },
        pattern_key: "PRN", doses: { M: 0, N: 0, E: 0, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 5, total_quantity: 10,
        prn_reason_key: "pain", prn_max_per_day: 3, prn_min_gap_hours: 6,
        extra_params: null, note: null },
      { position: 9, drug_name_raw: "Wysolone 10", drug_id: null, drug: null,   /* CUSTOM — 시드 밖 */
        pattern_key: "CUSTOM", doses: { M: 0, N: 0, E: 0, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 9, total_quantity: 11,
        extra_params: {
          instructions: "2 tab for 3 days, then 1 tab for 3 days, then ½ tab for 3 days (taper)",
          verbal_counseling_given: true
        }, note: null },
      { position: 10, drug_name_raw: "Glycomet 500", drug_id: "drug-seed-11",
        drug: { brand_name: "Glycomet 500", generic_name: "Metformin", strength: "500 mg", form: "tablet", caution_keys: [] },
        pattern_key: "BD", doses: { M: 1, N: 0, E: 1, H: 0 }, dose_unit: "tablet",
        timing_food: "with_food", duration_days: 30, total_quantity: 60, extra_params: null, note: null }
    ]
  };

  /* ---------- FX-C · 엣지 케이스 ----------
     시드 밖 약명(자동완성 미스) · ml 시럽 · 0.5정(½) · PRN ·
     별칭 유(Cremaffin alias)/무 · patient_label 없음 → P4 "(별칭 없음)" ·
     version 2 "수정됨" · D-3 곧 만료(기준일 2026-07-06) */
  var FX_C = {
    fx: "c",
    label: "FX-C 엣지 (시드 밖 · ml · ½정 · PRN · v2 수정됨 · D-3 만료임박)",
    pharmacy: PHARMACY,
    prescription: {
      id: "01981c44-1a9e-7b02-8d33-6f1b4d9e8a55",
      client_input_id: "e7a21f04-6c3d-4b9a-91d5-0c8f2e6a7b18",
      patient_label: null,               /* 별칭 없음 — P4 "(별칭 없음)" 행 검증 */
      lang: "hi",
      note: "3 दिन बाद डॉक्टर को दिखाएं", /* 자리표시(데바나가리 실문자): "3일 후 의사에게 보이세요" */
      status: "active",
      version: 2,                        /* 수정됨 배지 */
      entry_method: "manual",
      origin: "online",
      created_at: "2026-06-10T09:00:00Z",
      revised_at: "2026-07-05T10:20:00Z" /* 최신 revision 시각 — "7월 5일 수정됨" */
    },
    access: {
      token: "pQ3wLx8ZnR5tYv1KfHs6Ua",
      url: "https://indoro.example/p/pQ3wLx8ZnR5tYv1KfHs6Ua",
      short_code: "9XW4KT2H",
      short_code_display: "9XW4-KT2H",
      expires_at: "2026-07-09T09:00:00Z",  /* 기준일 2026-07-06 → D-3 "곧 만료" */
      first_viewed_at: "2026-06-10T09:25:00Z",
      scan_count: 5,
      revoked_at: null
    },
    items: [
      { position: 1, drug_name_raw: "Zerodol-SP", drug_id: null, drug: null,   /* 시드 밖 — 자동완성 미스, 발급은 동일 경로 */
        pattern_key: "TDS", doses: { M: 1, N: 1, E: 1, H: 0 }, dose_unit: "tablet",
        timing_food: "after_food", duration_days: 3, total_quantity: 9,
        extra_params: null, note: "Take with warm water" },
      { position: 2, drug_name_raw: "Cremaffin Plus Syrup", drug_id: "drug-seed-12",
        drug: { brand_name: "Cremaffin Plus", generic_name: "Liquid Paraffin + Milk of Magnesia", strength: "per 10 ml", form: "syrup",
                aliases: ["Cremaffin"], caution_keys: [] },                     /* 별칭 있는 약 */
        pattern_key: "OD_NIGHT", doses: { M: 0, N: 0, E: 10, H: 0 }, dose_unit: "ml",
        timing_food: null, duration_days: 10, total_quantity: 100,             /* ml 총량(병 아님) */
        extra_params: null, note: null },
      { position: 3, drug_name_raw: "Nebicard 2.5", drug_id: "drug-seed-13",
        drug: { brand_name: "Nebicard 2.5", generic_name: "Nebivolol", strength: "2.5 mg", form: "tablet", aliases: [], caution_keys: [] },
        pattern_key: "OD_MORNING", doses: { M: 0.5, N: 0, E: 0, H: 0 }, dose_unit: "tablet",  /* 0.5 → 표기 ½ */
        timing_food: null, duration_days: 30, total_quantity: 15, extra_params: null, note: null },
      { position: 4, drug_name_raw: "Ondem 4", drug_id: "drug-seed-14",
        drug: { brand_name: "Ondem 4", generic_name: "Ondansetron", strength: "4 mg", form: "tablet", aliases: [], caution_keys: [] },
        pattern_key: "PRN", doses: { M: 0, N: 0, E: 0, H: 0 }, dose_unit: "tablet",
        timing_food: null, duration_days: 3, total_quantity: 5,
        prn_reason_key: "vomiting", prn_max_per_day: 3, prn_min_gap_hours: 8,
        extra_params: null, note: null }
    ]
  };

  /* ---------- 메타: patterns.yaml·i18n 카탈로그의 와이어프레임 대역 ---------- */
  var META = {
    demo_today: "2026-07-06",
    /* 코어 8 + CUSTOM = 9항목 (docs/01 §3.4). digits = 파생 표기 */
    pattern_order: ["OD_MORNING", "OD_NIGHT", "BD", "TDS", "QID", "WEEKLY_ONCE", "STAT_SINGLE", "PRN", "CUSTOM"],
    patterns: {
      OD_MORNING:  { schedule_type: "daily",  slots: ["M"],                digits: "1-0-0",   sort_order: 1,
                     name_hi: "दिन में 1 बार — सुबह", name_en: "Once a day — morning" },
      OD_NIGHT:    { schedule_type: "daily",  slots: ["E"],                digits: "0-0-1",   sort_order: 2,
                     name_hi: "दिन में 1 बार — रात", name_en: "Once a day — night" },
      BD:          { schedule_type: "daily",  slots: ["M", "E"],           digits: "1-0-1",   sort_order: 3,
                     name_hi: "दिन में 2 बार", name_en: "Twice a day" },
      TDS:         { schedule_type: "daily",  slots: ["M", "N", "E"],      digits: "1-1-1",   sort_order: 4,
                     name_hi: "दिन में 3 बार", name_en: "3 times a day" },
      QID:         { schedule_type: "daily",  slots: ["M", "N", "E", "H"], digits: "1-1-1-1", sort_order: 5,
                     name_hi: "दिन में 4 बार", name_en: "4 times a day" },
      WEEKLY_ONCE: { schedule_type: "weekly", slots: ["M"],                digits: "→ हफ़्ते में 1", sort_order: 6,
                     name_hi: "हफ़्ते में 1 बार", name_en: "Once a week" },
      STAT_SINGLE: { schedule_type: "once",   slots: ["M"],                digits: "1×", sort_order: 7,
                     name_hi: "सिर्फ़ 1 बार", name_en: "One time only" },
      PRN:         { schedule_type: "prn",    slots: [],                   digits: "SOS",     sort_order: 8,
                     name_hi: "ज़रूरत पड़ने पर", name_en: "Only when needed" },
      CUSTOM:      { schedule_type: "custom", slots: [],                   digits: "…",  sort_order: 9,
                     name_hi: "विशेष निर्देश", name_en: "Special instructions" }
    },
    /* 슬롯 사전 — 순서는 항상 M→N→E→H (docs/03 §2.1) */
    slot_order: ["M", "N", "E", "H"],
    slots: {
      M: { hi: "सुबह",       en: "Morning" },
      N: { hi: "दोपहर", en: "Noon" },
      E: { hi: "शाम",             en: "Evening" },
      H: { hi: "रात",             en: "Night" }
    },
    timing_food: {
      before_food:   { hi: "खाने से पहले", en: "Before food" },
      after_food:    { hi: "खाने के बाद",       en: "After food" },
      with_food:     { hi: "खाने के साथ",       en: "With food" },
      empty_stomach: { hi: "खाली पेट",                    en: "Empty stomach" }
    },
    /* dose_unit enum 7종 (§3.3) */
    dose_units: {
      tablet:      { hi: "गोली",             en: "tablet" },
      capsule:     { hi: "कैप्सूल", en: "capsule" },
      ml:          { hi: "ml",                                   en: "ml" },
      drop:        { hi: "बूंद",             en: "drop" },
      puff:        { hi: "पफ़",                   en: "puff" },
      sachet:      { hi: "पाउच",             en: "sachet" },
      application: { hi: "लगाएं",       en: "apply" }
    },
    prn_reasons: {
      fever:    { hi: "बुख़ार", en: "Fever" },
      pain:     { hi: "दर्द",             en: "Pain" },
      vomiting: { hi: "उल्टी",       en: "Vomiting" },
      acidity:  { hi: "एसिडिटी", en: "Acidity" }
    },
    days_of_week: {
      sun: { hi: "रविवार",             en: "Sunday" },
      mon: { hi: "सोमवार",             en: "Monday" },
      tue: { hi: "मंगलवार",       en: "Tuesday" },
      wed: { hi: "बुधवार",             en: "Wednesday" },
      thu: { hi: "गुरुवार",       en: "Thursday" },
      fri: { hi: "शुक्रवार", en: "Friday" },
      sat: { hi: "शनिवार",             en: "Saturday" }
    },
    duration_presets: [3, 5, 7, 10, 15, 30],   /* P1 기간 빈도 칩 */
    /* 배지 색 규칙: position n = .hue-n (1..10 고정, ?color=0이면 회색) */
    hue_note: "position 1..10 = .badge-item .hue-1..hue-10"
  };

  /* ---------- P1 자동완성용 drugs 시드 (docs/01 §3.3 — 시드 5~10종) ---------- */
  var DRUGS_SEED = [
    { id: "drug-seed-01", brand_name: "Dolo 650",        generic_name: "Paracetamol",                  strength: "650 mg",   form: "tablet",  default_pattern_key: "TDS",         default_timing_food: "after_food",    default_dose_unit: "tablet",  aliases: [] },
    { id: "drug-seed-02", brand_name: "Azithral 500",    generic_name: "Azithromycin",                 strength: "500 mg",   form: "tablet",  default_pattern_key: "OD_MORNING",  default_timing_food: "after_food",    default_dose_unit: "tablet",  aliases: ["Azithromycin"] },
    { id: "drug-seed-03", brand_name: "Pantocid 40",     generic_name: "Pantoprazole",                 strength: "40 mg",    form: "tablet",  default_pattern_key: "OD_MORNING",  default_timing_food: "empty_stomach", default_dose_unit: "tablet",  aliases: ["Pantoprazole"] },
    { id: "drug-seed-04", brand_name: "Amlokind 5",      generic_name: "Amlodipine",                   strength: "5 mg",     form: "tablet",  default_pattern_key: "OD_MORNING",  default_timing_food: null,            default_dose_unit: "tablet",  aliases: [] },
    { id: "drug-seed-05", brand_name: "Atorva 10",       generic_name: "Atorvastatin",                 strength: "10 mg",    form: "tablet",  default_pattern_key: "OD_NIGHT",    default_timing_food: "after_food",    default_dose_unit: "tablet",  aliases: [] },
    { id: "drug-seed-06", brand_name: "Augmentin 625 Duo", generic_name: "Amoxicillin + Clavulanic acid", strength: "500/125 mg", form: "tablet", default_pattern_key: "BD",      default_timing_food: "after_food",    default_dose_unit: "tablet",  aliases: ["Augmentin"] },
    { id: "drug-seed-07", brand_name: "Ascoril LS",      generic_name: "Levosalbutamol + Ambroxol",    strength: "per 5 ml", form: "syrup",   default_pattern_key: "TDS",         default_timing_food: null,            default_dose_unit: "ml",      aliases: ["Ascoril"] },
    { id: "drug-seed-08", brand_name: "Uprise-D3 60K",   generic_name: "Cholecalciferol",              strength: "60000 IU", form: "capsule", default_pattern_key: "WEEKLY_ONCE", default_timing_food: "with_food",     default_dose_unit: "capsule", aliases: ["D3 60K"] },
    { id: "drug-seed-11", brand_name: "Glycomet 500",    generic_name: "Metformin",                    strength: "500 mg",   form: "tablet",  default_pattern_key: "BD",          default_timing_food: "with_food",     default_dose_unit: "tablet",  aliases: ["Metformin"] },
    { id: "drug-seed-12", brand_name: "Cremaffin Plus",  generic_name: "Liquid Paraffin + MoM",        strength: "per 10 ml", form: "syrup",  default_pattern_key: "OD_NIGHT",    default_timing_food: null,            default_dose_unit: "ml",      aliases: ["Cremaffin"] },
    /* ---- drugs-seed.json priority_rank 상위 확장 (13~14는 FX_C drug_id 정합 고정: Nebicard·Ondem) ----
       default_pattern_key·default_timing_food는 null 고정 — 용법 제안 금지 원칙 */
    { id: "drug-seed-13", brand_name: "Nebicard 2.5", generic_name: "Nebivolol", strength: "2.5 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Nebicard", "Nebivolol"] },
    { id: "drug-seed-14", brand_name: "Ondem 4", generic_name: "Ondansetron", strength: "4 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Ondem", "Ondansetron"] },
    { id: "drug-seed-15", brand_name: "Mounjaro", generic_name: "Tirzepatide", strength: "2.5 mg", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Tirzepatide"] },
    { id: "drug-seed-16", brand_name: "Mounjaro", generic_name: "Tirzepatide", strength: "5 mg", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Tirzepatide"] },
    { id: "drug-seed-17", brand_name: "Augmentin 1000 Duo", generic_name: "Amoxicillin + Clavulanic Acid", strength: "875/125 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Augmentin", "Augmentin 1g", "Amoxicillin-Clavulanate", "Augmentin 1000"] },
    { id: "drug-seed-18", brand_name: "Augmentin DDS", generic_name: "Amoxicillin + Clavulanic Acid", strength: "200/28.5 mg/5 ml", form: "suspension", default_pattern_key: null, default_timing_food: null, default_dose_unit: "ml", aliases: ["Augmentin", "Augmentin DDS Suspension"] },
    { id: "drug-seed-19", brand_name: "Glycomet-GP 2", generic_name: "Glimepiride + Metformin", strength: "2/500 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Glycomet GP2", "Glycomet GP", "Glycomet GP 2"] },
    { id: "drug-seed-20", brand_name: "Glycomet-GP 0.5", generic_name: "Glimepiride + Metformin", strength: "0.5/500 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Glycomet GP 0.5", "Glycomet GP"] },
    { id: "drug-seed-21", brand_name: "Glycomet-GP 1", generic_name: "Glimepiride + Metformin", strength: "1/500 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Glycomet GP1", "Glycomet GP", "Glimepiride + Metformin", "Glycomet GP 1"] },
    { id: "drug-seed-22", brand_name: "Pan 40", generic_name: "Pantoprazole", strength: "40 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Pan", "Pan-40", "Pantoprazole"] },
    { id: "drug-seed-23", brand_name: "Rybelsus 7", generic_name: "Semaglutide", strength: "7 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Rybelsus", "Semaglutide"] },
    { id: "drug-seed-24", brand_name: "Foracort 200", generic_name: "Budesonide + Formoterol", strength: "200/6 mcg", form: "inhaler", default_pattern_key: null, default_timing_food: null, default_dose_unit: "puff", aliases: ["Foracort", "Foracort Inhaler"] },
    { id: "drug-seed-25", brand_name: "Foracort 400", generic_name: "Budesonide + Formoterol", strength: "400/6 mcg", form: "inhaler", default_pattern_key: null, default_timing_food: null, default_dose_unit: "puff", aliases: ["Foracort"] },
    { id: "drug-seed-26", brand_name: "Mixtard 30 HM", generic_name: "Human Insulin (30/70 Biphasic Isophane)", strength: "100 IU/ml", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Mixtard", "Human Mixtard"] },
    { id: "drug-seed-27", brand_name: "Pan 20", generic_name: "Pantoprazole", strength: "20 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Pan", "Pan-20", "Pantoprazole"] },
    { id: "drug-seed-28", brand_name: "Mixtard 30 HM", generic_name: "Human Insulin (30/70 Biphasic)", strength: "40 IU/ml", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Human Mixtard", "Mixtard", "Human Mixtard 30/70"] },
    { id: "drug-seed-29", brand_name: "Thyronorm", generic_name: "Thyroxine Sodium", strength: "50 mcg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Thyroxine", "Levothyroxine", "Thyronorm 50"] },
    { id: "drug-seed-30", brand_name: "Telma 40", generic_name: "Telmisartan", strength: "40 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Telma", "Telmisartan"] },
    { id: "drug-seed-31", brand_name: "Liv.52", generic_name: "Herbal hepatoprotective combination (Ayurvedic)", strength: "per tablet", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Liv 52", "Liv-52"] },
    { id: "drug-seed-32", brand_name: "Liv.52 DS", generic_name: "Herbal hepatoprotective combination (Ayurvedic)", strength: "per tablet", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Liv 52 DS"] },
    { id: "drug-seed-33", brand_name: "Zerodol-SP", generic_name: "Aceclofenac + Paracetamol + Serratiopeptidase", strength: "100/325/15 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Zerodol SP", "Aceclofenac"] },
    { id: "drug-seed-34", brand_name: "Ecosprin-AV 75", generic_name: "Aspirin + Atorvastatin", strength: "75/10 mg", form: "capsule", default_pattern_key: null, default_timing_food: null, default_dose_unit: "capsule", aliases: ["Ecosprin AV", "Aspirin + Atorvastatin", "Ecosprin-AV", "Ecosprin AV 75"] },
    { id: "drug-seed-35", brand_name: "Clavam 375", generic_name: "Amoxicillin + Clavulanic Acid", strength: "250/125 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Clavam"] },
    { id: "drug-seed-36", brand_name: "Clavam 625", generic_name: "Amoxicillin + Clavulanic Acid", strength: "500/125 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Clavam", "Amoxicillin-Clavulanate"] },
    { id: "drug-seed-37", brand_name: "Cilacar 10", generic_name: "Cilnidipine", strength: "10 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Cilacar", "Cilnidipine"] },
    { id: "drug-seed-38", brand_name: "Lantus", generic_name: "Insulin Glargine", strength: "100 IU/ml", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Lantus Solostar", "Insulin Glargine"] },
    { id: "drug-seed-39", brand_name: "Pan-D", generic_name: "Pantoprazole + Domperidone", strength: "40/30 mg", form: "capsule", default_pattern_key: null, default_timing_food: null, default_dose_unit: "capsule", aliases: ["Pan D", "Pantoprazole + Domperidone"] },
    { id: "drug-seed-40", brand_name: "Monocef", generic_name: "Ceftriaxone", strength: "1 g", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Ceftriaxone", "Monocef Injection", "Monocef 1g Injection"] },
    { id: "drug-seed-41", brand_name: "Ryzodeg", generic_name: "Insulin Degludec + Insulin Aspart", strength: "100 IU/ml", form: "injection", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Ryzodeg FlexTouch", "Insulin Degludec"] },
    { id: "drug-seed-42", brand_name: "Udiliv 150", generic_name: "Ursodeoxycholic Acid", strength: "150 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Udiliv"] },
    { id: "drug-seed-43", brand_name: "Udiliv 300", generic_name: "Ursodeoxycholic Acid", strength: "300 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Udiliv", "UDCA", "Ursodeoxycholic Acid"] },
    { id: "drug-seed-44", brand_name: "Thyronorm", generic_name: "Thyroxine Sodium", strength: "100 mcg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Thyroxine", "Levothyroxine", "Thyronorm 100"] },
    { id: "drug-seed-45", brand_name: "Thyronorm", generic_name: "Thyroxine Sodium", strength: "25 mcg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Thyroxine", "Levothyroxine", "Thyronorm 25"] },
    { id: "drug-seed-46", brand_name: "Duolin Inhaler", generic_name: "Levosalbutamol + Ipratropium Bromide", strength: "50/20 mcg", form: "inhaler", default_pattern_key: null, default_timing_food: null, default_dose_unit: "puff", aliases: ["Duolin"] },
    { id: "drug-seed-47", brand_name: "Duolin Respules", generic_name: "Levosalbutamol + Ipratropium Bromide", strength: "1.25 mg/500 mcg per 2.5 ml", form: "other", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Duolin", "Duolin Respule"] },
    { id: "drug-seed-48", brand_name: "Cilacar 20", generic_name: "Cilnidipine", strength: "20 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Cilacar", "Cilnidipine"] },
    { id: "drug-seed-49", brand_name: "Cilacar 5", generic_name: "Cilnidipine", strength: "5 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Cilacar", "Cilnidipine"] },
    { id: "drug-seed-50", brand_name: "Betadine Gargle", generic_name: "Povidone Iodine", strength: "2% w/v", form: "other", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Betadine"] },
    { id: "drug-seed-51", brand_name: "Betadine Ointment", generic_name: "Povidone Iodine", strength: "10% w/w", form: "ointment", default_pattern_key: null, default_timing_food: null, default_dose_unit: "apply", aliases: ["Betadine"] },
    { id: "drug-seed-52", brand_name: "Telma 20", generic_name: "Telmisartan", strength: "20 mg", form: "tablet", default_pattern_key: null, default_timing_food: null, default_dose_unit: "tablet", aliases: ["Telma", "Telmisartan"] }
  ];

  /* ---------- P4/P8용 outbox 샘플 (4상태: 대기·백오프 재시도·24h 경고·409 확인) ---------- */
  var OUTBOX = [
    { client_input_id: "ob-9a01", state: "waiting",     issued_at_client: "2026-07-06T15:41:22+05:30",
      patient_label: null, n_items: 2, token: "aF7dQ2xLm9RtY4vKzB8shW", retry_count: 0 },
    { client_input_id: "ob-9a02", state: "retrying",    issued_at_client: "2026-07-06T15:12:05+05:30",
      patient_label: "Amma", n_items: 1, token: "cN4jP7yTb2WqX9dLfV5rGm", retry_count: 2, next_retry_in_s: 4 },
    { client_input_id: "ob-9a03", state: "stale_24h",   issued_at_client: "2026-07-05T09:10:00+05:30",
      patient_label: null, n_items: 3, token: "eK2mV8sHc5ZxQ7nRjW3tYd", retry_count: 9 },
    { client_input_id: "ob-9a04", state: "conflict_409", issued_at_client: "2026-07-06T14:02:44+05:30",
      patient_label: "Mr. B", n_items: 1, token: "hV8s3kQxWnA9cLd3Ye7Rk2", retry_count: 1 } /* TOKEN_COLLISION 데모 — 기존 토큰과 동일 값 */
  ];

  window.INDORO_FX = {
    a: FX_A,
    b: FX_B,
    c: FX_C,
    meta: META,
    drugs_seed: DRUGS_SEED,
    outbox: OUTBOX
  };
})();
