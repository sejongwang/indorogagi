"""환자측 HTML 라우터 — /p/{token}, /c/{short_code}, /c?code= (docs/01 §4.6, §2.5).

규약: 어떤 경우에도 JSON을 반환하지 않는다(§4.1). 미존재 토큰은 200 대기 페이지(D8),
revoked/expired는 410 HTML. 언어는 ?lang= 우선, 없으면 prescriptions.lang(D12).

S1 렌더 정본은 wireframes/patient/s1-landing.html — 그 JS 빌더(buildTT·buildCard·
mxCell·pictos·dayDots)를 이 모듈의 뷰모델 빌더 + templates/patient.html로 이식했다.
UI 문구는 전부 config/i18n.yaml `ui:` 네임스페이스(Jinja 하드코딩 금지).
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import db

router = APIRouter()

IST = timezone(timedelta(hours=5, minutes=30))
LANGS = ("hi", "en")

# §6.6 봇 필터 — 버리지 않고 is_bot=1 플래그 저장(WhatsApp 링크 프리뷰 봇이 핵심)
_BOT_RE = re.compile(
    r"bot|crawler|spider|curl|wget|python|WhatsApp|facebookexternalhit"
    r"|TelegramBot|Googlebot|HeadlessChrome",
    re.IGNORECASE,
)

# 픽토그램 심볼 매핑 (s1-landing.html PICTO/SLOTICO/TFICO와 동형)
_PICTO = {"tablet": "p-tab", "capsule": "p-cap", "ml": "p-spoon", "drop": "i-drop"}
_SLOT_ICO = {"M": "i-slot-m", "N": "i-slot-n", "E": "i-slot-e", "H": "i-slot-h"}
_TF_ICO = {
    "before_food": "i-tf-before",
    "after_food": "i-tf-after",
    "with_food": "i-tf-with",
    "empty_stomach": "i-tf-empty",
}
_SPECIAL_ICO = {"weekly": "i-repeat", "once": "i-pill", "prn": "i-clock", "custom": "i-warn"}

_SEC_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}
_NO_STORE = {"Cache-Control": "no-store", **_SEC_HEADERS}


# ---------------------------------------------------------------- 공용 헬퍼

def _pick_lang(request: Request, presc_lang: str | None = None) -> str:
    """D12 협상: 1. ?lang= → 4. prescriptions.lang → 5. hi (2·3순위 토글/쿠키는
    ?lang= 링크 재렌더로 수렴 — 별도 클라 상태 없음)."""
    q = request.query_params.get("lang")
    if q in LANGS:
        return q
    if presc_lang in LANGS:
        return presc_lang
    return "hi"


def _src_of(request: Request) -> str:
    """§6.3 src ∈ qr/code/share/direct. QR 페이로드는 쿼리 없음(D2) → 무표기=qr."""
    s = request.query_params.get("src")
    return s if s in ("code", "share", "direct") else "qr"


def _ua_class(ua: str) -> str:
    if re.search(r"iPhone|iPad|iPod", ua, re.I):
        return "ios"
    if "Android" in ua:
        if "; wv" in ua or ("Version/" in ua and "Chrome" in ua):
            return "android_webview"
        if "Chrome" in ua:
            return "android_chrome"
    return "other"


def _parse_ts(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt_date(iso: str, months: dict[str, list[str]]) -> dict[str, str]:
    d = _parse_ts(iso).astimezone(IST).date()
    return {
        "hi": f"{d.day} {months['hi'][d.month - 1]} {d.year}",
        "en": f"{d.day} {months['en'][d.month - 1]} {d.year}",
    }


def _days_left(expires_iso: str) -> int:
    today = datetime.now(timezone.utc).astimezone(IST).date()
    return (_parse_ts(expires_iso).astimezone(IST).date() - today).days


def _qf(v: Any) -> str:
    """수량 표기: 숫자 ASCII, 0.5는 ½ 단일 글리프 (wireframe qf 이식)."""
    f = float(v or 0)
    i = int(f)
    if f - i == 0.5:
        return f"{i}½" if i else "½"
    if f == i:
        return str(i)
    return f"{f:g}"


def _pictos(unit: str, q: float) -> list[str]:
    """카드 스트립 픽토그램: 정수 1~4 반복 + ½ 글리프. ml·5개 이상은 글리프 1개."""
    gid = _PICTO.get(unit, "p-generic")
    n = int(q)
    half = q - n >= 0.5
    if unit == "ml" or q > 4.5:
        n = 1 if q > 0 else 0
        half = False
    ids = [gid] * n
    if half:
        ids.append("p-tab-half" if unit == "tablet" else gid)
    return ids


def _mx_cell(unit: str, q: float) -> dict[str, Any]:
    """매트릭스 셀: 글리프×수량(≤2), 3개 이상 ×N, ml은 스푼+숫자, 0은 – (mxCell 이식)."""
    if not q:
        return {"off": True}
    if unit == "ml":
        return {"off": False, "glyphs": ["p-spoon"], "sub": {"kind": "ml", "q": _qf(q)}}
    gid = "p-dot" if unit == "tablet" else _PICTO.get(unit, "p-generic")  # 매트릭스는 꽉 찬 점
    if q > 2.5:
        return {"off": False, "glyphs": [gid], "sub": {"kind": "x", "q": _qf(q)}}
    n = int(q)
    ids = [gid] * n
    if q - n >= 0.5:
        ids.append("p-tab-half" if unit == "tablet" else gid)
    return {"off": False, "glyphs": ids, "sub": None}


def _day_dots(n: Any) -> int:
    """하루=한 칸(≤15일만 — 그 이상은 숫자만)."""
    return int(n) if isinstance(n, (int, float)) and 1 <= n <= 15 else 0


def _fmt2(pair: dict[str, str], **kw: Any) -> dict[str, str]:
    return {lang: pair[lang].format(**{k: (v[lang] if isinstance(v, dict) else v) for k, v in kw.items()}) for lang in LANGS}


# ---------------------------------------------------------------- 뷰모델 빌더

def _item_views(bundle: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """buildTT·buildCard의 데이터 절반 — DOM은 patient.html 몫."""
    i18n = cfg["i18n"]
    ui = i18n["ui"]
    s1 = ui["s1"]
    views: list[dict[str, Any]] = []
    for it in bundle["items"]:
        key = it["pattern_key"]
        pat = cfg["patterns"].get(key) or {}
        st = pat.get("schedule_type", "daily")
        name = pat.get("name") or {"hi": key, "en": key}
        unit = it["dose_unit"] or "tablet"
        unit_pair = i18n["dose_units"].get(unit) or {"hi": unit, "en": unit}
        drug = it["drug"]
        doses = it["doses"]

        v: dict[str, Any] = {
            "position": it["position"],
            "hue": (it["position"] - 1) % 10 + 1,  # position=hue 고정(≤10), 초과는 순환
            "name_raw": it["drug_name_raw"],
            "enrich": None,
            "stype": st,
            "pattern_key": key,
            "pattern_name": name,
            "unit": unit,
            "unit_pair": unit_pair,
            "strip": None,
            "cells": None,
            "weekly_day": None,
            "every_week": None,
            "prn": None,
            "custom_instr": None,
            "food": None,
            "ml_per": None,
            "duration_days": it["duration_days"],
            "dots": _day_dots(it["duration_days"]),
            "total_q": _qf(it["total_quantity"]),
            "cautions": [],
            "note": it["note"],
            "has_media": st != "custom",  # CUSTOM은 음성·영상·스트립 비렌더(§3.4)
        }
        if drug and drug.get("generic_name"):
            v["enrich"] = " · ".join(x for x in (drug["generic_name"], drug.get("strength")) if x)

        if st == "daily":
            v["cells"] = [_mx_cell(unit, doses.get(s) or 0) for s in cfg["slot_order"]]
        if st in ("daily", "weekly", "once"):
            strip = []
            for s in cfg["slot_order"]:
                d = doses.get(s) or 0
                strip.append({
                    "on": d > 0,
                    "ico": _SLOT_ICO[s],
                    "glyphs": _pictos(unit, d) if d > 0 else [],
                    "q": _qf(d),
                    "label": i18n["slots"][s],
                })
            v["strip"] = strip
        if st == "weekly":
            dw = (it["extra_params"] or {}).get("day_of_week")
            day = i18n["days_of_week"].get(dw) or {"hi": "", "en": ""}
            v["weekly_day"] = day
            v["every_week"] = _fmt2(s1["every_week"], day=day)
        if st == "prn":
            v["prn"] = {  # 수치는 _qf로 ASCII 정수화(REAL 컬럼의 3.0 → 3)
                "reason": i18n["prn_reasons"].get(it["prn_reason_key"]),
                "max_per_day": _qf(it["prn_max_per_day"]) if it["prn_max_per_day"] else None,
                "min_gap_hours": _qf(it["prn_min_gap_hours"]) if it["prn_min_gap_hours"] else None,
            }
        if st == "custom":
            v["custom_instr"] = (it["extra_params"] or {}).get("instructions") or ""
        if it["timing_food"]:
            v["food"] = {
                "ico": _TF_ICO.get(it["timing_food"], "i-tf-with"),
                "label": i18n["timing_food"].get(it["timing_food"])
                or {"hi": it["timing_food"], "en": it["timing_food"]},
            }
        if unit == "ml":
            v["ml_per"] = _qf(max((doses.get(s) or 0) for s in cfg["slot_order"]))

        # 주의 배지: drug_id 매칭 시만. 카탈로그 결측 라벨은 키 노출 폴백(§3.4)
        for ck in (drug or {}).get("caution_keys") or []:
            label = (i18n.get("caution") or {}).get(ck)
            v["cautions"].append(label or {"hi": f"caution.{ck}", "en": f"caution.{ck}"})

        # 시간표 특수 행(비-daily) 사전 계산
        if st != "daily":
            if st in ("weekly", "once"):
                q = doses.get("M") or 1
                chip: dict[str, Any] = {
                    "kind": "qty",
                    "glyphs": _pictos(unit, q),
                    "q": _qf(q),
                    "unit_short": ui["dose_units_short"].get(unit) or unit_pair,
                }
            elif st == "prn":
                chip = {"kind": "sos"}
            else:
                chip = {"kind": "warn"}
            label = (
                _fmt2(s1["weekly_row"], day=v["weekly_day"]) if st == "weekly"
                else s1["custom_row"] if st == "custom"
                else name
            )
            v["special"] = {"icon": _SPECIAL_ICO.get(st, "i-warn"), "label": label, "chip": chip}
        views.append(v)
    return views


def _share_digits(v: dict[str, Any], it: dict[str, Any], s1: dict[str, Any]) -> dict[str, str]:
    """공유 메시지 숫자열(shareDigits 이식) — 카드에서는 제거된 숫자열이 여기에만 남는다."""
    if v["stype"] == "daily":
        s = "-".join(_qf(it["doses"].get(k) or 0) for k in ("M", "N", "E", "H"))
        if v["unit"] == "ml":
            s += " ml"
        return {"hi": s, "en": s}
    return s1["share_digits"].get(
        {"weekly": "weekly", "once": "once", "prn": "prn"}.get(v["stype"], "custom")
    )


def _build_ctx(request: Request, bundle: dict[str, Any], cfg: dict[str, Any],
               lang: str, token: str) -> dict[str, Any]:
    i18n = cfg["i18n"]
    ui = i18n["ui"]
    s1 = ui["s1"]
    months = ui["months"]
    presc = bundle["prescription"]
    pharmacy = bundle["pharmacy"] or {}
    access = bundle["access"]

    issued = _fmt_date(presc["created_at"], months)
    expires = _fmt_date(access["expires_at"], months)
    dl = _days_left(access["expires_at"])
    trust: dict[str, Any] = {
        "issued": _fmt2(s1["issued"], date=issued),
        "valid": _fmt2(s1["valid_till"], date=expires),
        "days_left": dl,
        "edited": None,
        "expiring": None,
    }
    if presc["version"] > 1:  # 수정 배지 — version>1 + 최신 revision ts(결측 시 created_at 폴백)
        rd = _fmt_date(presc["revised_at"] or presc["created_at"], months)
        trust["edited"] = _fmt2(s1["edited_flag"], date=rd)
    if 0 <= dl <= 3:  # 곧 만료 배지 — D-3(§4.6)
        trust["expiring"] = _fmt2(s1["expiring_flag"], n=dl)

    views = _item_views(bundle, cfg)

    # C2 공유 — 메시지 자체가 오프라인 사본(§7.3-②), patient_label 미포함(§8.1)
    base = f"{request.url.scheme}://{request.url.netloc}"
    share_lines = []
    for v, it in zip(views, bundle["items"]):
        share_lines.append({
            "position": v["position"],
            "name": v["name_raw"],
            "digits": _share_digits(v, it, s1),
            "days": v["duration_days"],
        })
    txt = [f"{s1['guide'][lang]} — {pharmacy.get('name', '')}"]
    for ln in share_lines:
        txt.append(
            f"{ln['position']}. {ln['name']} — {ln['digits'][lang]} — {ln['days']} {s1['days'][lang]}"
        )
    txt.append(f"{base}/p/{token}?lang={lang}&src=share")
    wa_href = "https://wa.me/?text=" + quote("\n".join(txt), safe="")

    # C1 토글 링크 — 서버 재렌더(JS 불요). src는 code/share만 보존(계측 연속성)
    src_q = request.query_params.get("src")
    keep = f"&src={src_q}" if src_q in ("code", "share") else ""
    return {
        "lang": lang,
        "token": token,
        "L": i18n,
        "UI": ui,
        "T": s1,
        "slot_order": cfg["slot_order"],
        "slot_ico": _SLOT_ICO,
        "pharmacy": pharmacy,
        "presc": presc,
        "trust": trust,
        "views": views,
        "matrix_rows": [v for v in views if v["stype"] == "daily"],
        "specials": [v for v in views if v["stype"] != "daily"],
        "share_lines": share_lines,
        "share_url_base": f"{base}/p/{token}",
        "wa_href": wa_href,
        "copy_url": f"{base}/p/{token}?lang={lang}&src=share",
        "lang_links": {lng: f"?lang={lng}{keep}" for lng in LANGS},
    }


# ---------------------------------------------------------------- 라우트

@router.get("/p/{token}")
def patient_view(token: str, request: Request) -> Response:
    templates = request.app.state.templates
    cfg = request.app.state.config
    ui = cfg["i18n"]["ui"]
    ua = request.headers.get("user-agent", "")
    is_bot = 1 if (not ua or _BOT_RE.search(ua)) else 0
    is_internal = 1 if request.cookies.get("indoro_staff") else 0  # staff 쿠키(§6.6-④)
    ua_class = _ua_class(ua)
    src = _src_of(request)

    conn = db.get_conn()
    try:
        status, bundle = db.get_bundle_by_token(conn, token)

        if status == "missing":  # D8: 미존재/동기화 대기 구분 없이 200 대기 페이지
            db.record_event(conn, "view.pending", token=token, src=src,
                            ua_class=ua_class, is_bot=is_bot, is_internal=is_internal)
            return templates.TemplateResponse(
                request, "pending.html", {"UI": ui}, headers=_NO_STORE)

        assert bundle is not None
        presc = bundle["prescription"]
        pharmacy = bundle["pharmacy"] or {}
        lang = _pick_lang(request, presc["lang"])

        if status in ("revoked", "expired"):  # §4.6: 410 HTML — revoked는 정보 0
            # §6.2: 410 조회 계열(revoked·expired 모두)은 view.expired.
            # revoked/expired 구분은 props.reason(=status)로만 — view.invalid는 /c 오입력 전용.
            db.record_event(
                conn, "view.expired",
                token=token, prescription_id=presc["id"], pharmacy_id=presc["pharmacy_id"],
                src=src, ua_class=ua_class, is_bot=is_bot, is_internal=is_internal,
                props={"status": status, "reason": status, "lang": lang},
            )
            return templates.TemplateResponse(
                request, "gone.html",
                {"UI": ui, "mode": status,
                 "pharmacy_name": pharmacy.get("name") if status == "expired" else None},
                status_code=410, headers=_NO_STORE)

        # ── active: 계측(서버 권위 §6.2·§6.3) — 단일 트랜잭션
        ivid = request.cookies.get("ivid") or secrets.token_urlsafe(16)  # D15
        now = db.now_utc()
        is_first = False
        if not is_bot and not is_internal:  # view.first는 봇·내부 판정 자체를 스킵
            is_first = db.claim_first_view(conn, token, ts=now, commit=False)
            if is_first:
                secs = int((_parse_ts(now) - _parse_ts(bundle["access"]["created_at"])).total_seconds())
                db.record_event(
                    conn, "view.first", token=token, prescription_id=presc["id"],
                    pharmacy_id=presc["pharmacy_id"], viewer_id=ivid, src=src,
                    ua_class=ua_class, ts=now, commit=False,
                    props={"lang": lang, "secs_since_issue": max(secs, 0)},
                )
        db.record_event(
            conn, "view.opened", token=token, prescription_id=presc["id"],
            pharmacy_id=presc["pharmacy_id"], viewer_id=ivid, src=src, ua_class=ua_class,
            is_bot=is_bot, is_internal=is_internal, ts=now, commit=False,
            props={"lang": lang, "is_first": is_first},
        )
        db.bump_scan_count(conn, token, commit=False)
        conn.commit()

        ctx = _build_ctx(request, bundle, cfg, lang, token)
        resp = templates.TemplateResponse(
            request, "patient.html", ctx,
            headers={"Cache-Control": "private, no-cache", **_SEC_HEADERS})
        # ETag — 재방문 재검증용(§4.6). If-None-Match 304 처리는 80% 범위 밖(항상 200도 유효)
        rev = presc["revised_at"] or ""
        resp.headers["ETag"] = f'W/"{presc["id"]}.{presc["version"]}.{rev}.{lang}"'
        resp.set_cookie("ivid", ivid, max_age=31536000, path="/",
                        httponly=True, samesite="lax")  # 1년(D15)
        return resp
    finally:
        conn.close()


def _normalize_code(raw: str) -> str:
    """Crockford Base32 관용 정규화 — 대문자화·하이픈/공백 제거·O→0·I/L→1 (§2.5)."""
    s = re.sub(r"[\s\-]", "", raw or "").upper()
    return s.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _code_lookup(request: Request, raw: str) -> Response:
    templates = request.app.state.templates
    ui = request.app.state.config["i18n"]["ui"]
    code = _normalize_code(raw)
    conn = db.get_conn()
    try:
        token = db.find_token_by_short_code(conn, code) if len(code) == 8 else None
        if token:
            return RedirectResponse(f"/p/{token}?src=code", status_code=302)
        # §6.2: 코드 오입력 = view.invalid (scan.failed는 약사 scan-failure API 전용).
        # H13: 오입력 코드 원문을 props에 복제 금지 — reason 라벨만 남긴다.
        db.record_event(conn, "view.invalid", src="code",
                        ua_class=_ua_class(request.headers.get("user-agent", "")),
                        props={"reason": "code_miss"})
        return templates.TemplateResponse(
            request, "gone.html", {"UI": ui, "mode": "code_miss", "pharmacy_name": None},
            status_code=404, headers=_NO_STORE)
    finally:
        conn.close()


@router.get("/")
def home(request: Request) -> Response:
    """§2.5·§4.2: 코드 입력 폼 홈. 가족 대행 경로 진입점(JS 불요, <form method=get action=/c>).
    /c 핸들러가 이 폼의 GET 제출을 받는다."""
    templates = request.app.state.templates
    ui = request.app.state.config["i18n"]["ui"]
    return templates.TemplateResponse(request, "home.html", {"UI": ui}, headers=_NO_STORE)


@router.get("/c/{code}")
def code_path(code: str, request: Request) -> Response:
    return _code_lookup(request, code)


@router.get("/c")
def code_query(request: Request, code: str = "") -> Response:
    """홈 폼의 GET 제출(<form method=get action=/c>) 쿼리 형태 수용(§2.5)."""
    return _code_lookup(request, code)
