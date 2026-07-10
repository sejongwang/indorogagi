"""약사용 JSON API 라우터 — /api/* (docs/01 §4).

규약:
- X-Pharmacy-Id 헤더는 /api/prescriptions* 전체 필수(§4.1). 헤더 누락은 400,
  미존재 약국·타 약국 리소스는 404(존재 은닉 — 403 금지).
- 에러 envelope: {"error": {"code", "field"?, "message"}} + 422 (message는 개발자용 영문).
- POST /api/events는 어떤 경우에도 204(D16) — 계측 오류를 환자 웹뷰에 절대 되돌리지 않는다.
- GET /api/drugs는 어떤 실패로도 200 + 배열(§4.5) — 입력 흐름을 막지 않는다.
- 패턴 검증은 기동 시 로드된 patterns.yaml/i18n.yaml을 읽는다(§4.3)
  — 패턴 추가가 코드 수정이 되지 않게.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import db
from app.config import DOSE_UNITS, TIMING_FOOD

router = APIRouter(prefix="/api")

# D2: secrets.token_urlsafe(16) = 22자 [A-Za-z0-9_-] — 오프라인 클라 사전생성분도 동일 형식
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22}")

# §4.7 화이트리스트 (환자 웹뷰 클라 계층 — §6.2 이벤트 사전의 클라 이벤트)
EVENT_TYPE_WHITELIST = frozenset({
    "media.audio_play",
    "media.video_play",
    "media.video_complete",
    "ux.lang_switched",
    "share.clicked",
})
EVENTS_MAX_BODY_BYTES = 2048   # §4.7: 요청당 ≤2KB
EVENTS_MAX_COUNT = 20          # §4.7: 요청당 ≤20개

REISSUE_REASONS = ("wrong_patient", "input_error", "other")  # §4.4
ADMINISTRATION_ROUTES = (
    "oral", "ophthalmic", "otic", "nasal", "inhalation", "topical",
    "rectal", "vaginal", "transdermal", "intravenous", "intramuscular",
    "subcutaneous", "other",
)
# §6.2 scan.failed reason enum — 스캔 불가 단말 비율 실측(§2.5)
SCAN_FAILURE_REASONS = ("no_qr_camera", "camera_broken", "feature_phone", "refused")


# ---------------------------------------------------------------- 공통 헬퍼

def _err(status: int, code: str, message: str, field: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if field:
        body["error"]["field"] = field
    return JSONResponse(body, status_code=status)


def _verr(field: str, message: str) -> JSONResponse:
    return _err(422, "VALIDATION_ERROR", f"{field}: {message}", field=field)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _ua_class(ua: str | None) -> str:
    """§3.3 events 공통 컬럼 — 원본 UA 미저장, 4분류만."""
    u = (ua or "").lower()
    if "android" in u:
        return "android_webview" if ("; wv" in u or " wv)" in u) else "android_chrome"
    if "iphone" in u or "ipad" in u or "ios" in u:
        return "ios"
    return "other"


def _require_pharmacy(
    request: Request, conn: sqlite3.Connection
) -> tuple[str | None, JSONResponse | None]:
    """X-Pharmacy-Id 확인. 누락 400, 미존재 404(존재 은닉 — §4.1)."""
    pharmacy_id = request.headers.get("X-Pharmacy-Id")
    if not pharmacy_id:
        return None, _err(400, "MALFORMED_REQUEST", "X-Pharmacy-Id header required")
    if db.get_pharmacy(conn, pharmacy_id) is None:
        return None, _err(404, "NOT_FOUND", "not found")
    return pharmacy_id, None


async def _json_body(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        payload = await request.json()
    except Exception:
        return None, _err(400, "MALFORMED_REQUEST", "invalid JSON body")
    if not isinstance(payload, dict):
        return None, _err(400, "MALFORMED_REQUEST", "JSON object body required")
    return payload, None


# ---------------------------------------------------------------- 검증 (§4.3)

def _validate_items(cfg: dict[str, Any], items: Any, path: str = "items") -> JSONResponse | None:
    """§4.3 항목 검증 — 검증 로직 자체가 패턴 설정 파일을 읽는다.
    total_quantity 불일치는 경고만(§4.3)이므로 422 대상이 아니다."""
    patterns: dict[str, Any] = cfg["patterns"]
    slot_order: list[str] = cfg["slot_order"]
    days = set((cfg["i18n"].get("days_of_week") or {}).keys())

    if not isinstance(items, list) or not (1 <= len(items) <= 10):
        return _verr(path, "must be a list of 1..10 items")

    for i, item in enumerate(items):
        p = f"{path}.{i}"
        if not isinstance(item, dict):
            return _verr(p, "must be an object")

        name = item.get("drug_name_raw")
        if not isinstance(name, str) or not name.strip():
            return _verr(f"{p}.drug_name_raw", "required")
        input_raw = item.get("drug_input_raw")
        if input_raw is not None and not isinstance(input_raw, str):
            return _verr(f"{p}.drug_input_raw", "must be a string or null")
        match_state = item.get("drug_match_state")
        if match_state is not None and match_state not in (
            "free_text", "selected", "selected_then_modified"
        ):
            return _verr(
                f"{p}.drug_match_state",
                "must be free_text, selected, or selected_then_modified",
            )

        key = item.get("pattern_key")
        pat = patterns.get(key) if isinstance(key, str) else None
        if pat is None or not pat.get("is_active", True):
            return _verr(f"{p}.pattern_key", "unknown_pattern")

        doses = item.get("doses")
        if doses is None:
            doses = {}
        if not isinstance(doses, dict):
            return _verr(f"{p}.doses", "must be an object of slot: number")
        for slot in doses:
            if slot not in slot_order:
                return _verr(f"{p}.doses.{slot}", "unknown slot")
        pattern_slots = set(pat.get("slots") or [])
        total = 0.0
        for slot in slot_order:
            v = doses.get(slot)
            if v is None:
                v = 0
            if not _is_num(v) or v < 0:
                return _verr(f"{p}.doses.{slot}", "must be a number >= 0")
            # §4.3: slots 밖 슬롯 dose는 0
            if slot not in pattern_slots and v != 0:
                return _verr(f"{p}.doses.{slot}", f"slot outside pattern {key} must be 0")
            total += float(v)

        schedule_type = pat.get("schedule_type")
        extra = item.get("extra_params")
        if extra is not None and not isinstance(extra, dict):
            return _verr(f"{p}.extra_params", "must be an object or null")

        # §4.3: daily는 슬롯 합>0
        if schedule_type == "daily" and total <= 0:
            return _verr(f"{p}.doses", "daily pattern requires slot sum > 0")
        # §4.3: weekly는 extra_params 필수 키 (fixtures 정본: day_of_week.
        #  once=STAT_SINGLE은 fixtures에서 extra_params=null이라 요구하지 않음)
        if schedule_type == "weekly":
            if not extra or extra.get("day_of_week") not in days:
                return _verr(f"{p}.extra_params.day_of_week", "required for weekly pattern")
        if schedule_type == "prn":
            dose_per_use = (extra or {}).get("dose_per_use")
            if not _is_num(dose_per_use) or dose_per_use <= 0:
                return _verr(f"{p}.extra_params.dose_per_use", "required number > 0 for PRN")
            for prn_field in ("prn_max_per_day", "prn_min_gap_hours"):
                pv = item.get(prn_field)
                if not _is_num(pv) or pv <= 0:
                    return _verr(f"{p}.{prn_field}", "required number > 0 for PRN")
        # §4.3: CUSTOM은 instructions + verbal_counseling_given=true 필수
        if schedule_type == "custom":
            instructions = (extra or {}).get("instructions")
            if not isinstance(instructions, str) or not instructions.strip():
                return _verr(f"{p}.extra_params.instructions", "required for CUSTOM")
            if (extra or {}).get("verbal_counseling_given") is not True:
                return _verr(f"{p}.extra_params.verbal_counseling_given", "must be true for CUSTOM")
        # PRN 외 일정에 잔존 값이 있어도 잘못된 숫자는 받지 않는다.
        # PRN의 상한·최소 간격은 위에서 필수로 검증된다.
        for prn_field in ("prn_max_per_day", "prn_min_gap_hours"):
            pv = item.get(prn_field)
            if pv is not None and (not _is_num(pv) or pv <= 0):
                return _verr(f"{p}.{prn_field}", "must be a number > 0")

        unit = item.get("dose_unit")
        if not isinstance(unit, str) or unit not in DOSE_UNITS:
            return _verr(f"{p}.dose_unit", f"must be one of {list(DOSE_UNITS)}")
        route = item.get("administration_route")
        if route is not None and route not in ADMINISTRATION_ROUTES:
            return _verr(
                f"{p}.administration_route",
                f"must be one of {list(ADMINISTRATION_ROUTES)} or null",
            )
        if unit == "drop" and route not in ("oral", "ophthalmic", "otic", "nasal"):
            return _verr(
                f"{p}.administration_route",
                "oral, ophthalmic, otic, or nasal route is required for drops",
            )
        tf = item.get("timing_food")
        if tf is not None and tf not in TIMING_FOOD:
            return _verr(f"{p}.timing_food", f"must be one of {list(TIMING_FOOD)} or null")
        dur = item.get("duration_days")
        if dur is not None and (not isinstance(dur, int) or isinstance(dur, bool) or dur < 1):
            return _verr(f"{p}.duration_days", "must be an integer >= 1")
        tq = item.get("total_quantity")
        if tq is not None and (not _is_num(tq) or tq < 0):
            return _verr(f"{p}.total_quantity", "must be a number >= 0")
    return None


def _validate_create_payload(cfg: dict[str, Any], payload: dict[str, Any]) -> JSONResponse | None:
    cid = payload.get("client_input_id")
    if not isinstance(cid, str) or not cid.strip() or len(cid) > 64:
        return _verr("client_input_id", "required (D10 idempotency key)")

    label = payload.get("patient_label")
    if label is not None and (not isinstance(label, str) or len(label) > 20):
        return _verr("patient_label", "must be a string of <= 20 chars (D13)")

    lang = payload.get("lang")
    if lang is not None and not isinstance(lang, str):
        return _verr("lang", "must be a string")

    token = payload.get("token")
    if token is not None and (not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)):
        # D2: 오프라인 클라 사전생성 토큰은 22자 urlsafe — 형식 불일치는 엔트로피 전제 붕괴 신호
        return _verr("token", "must be a 22-char urlsafe token (offline pre-generated)")

    # 계측 원칙: client_metrics가 깨져 있어도 발급을 막지 않는다 — 조용히 제거
    metrics = payload.get("client_metrics")
    if metrics is not None and not isinstance(metrics, dict):
        payload["client_metrics"] = None

    return _validate_items(cfg, payload.get("items"))


# ---------------------------------------------------------------- 응답 조립

def _attach_url(result: dict[str, Any], base: str) -> dict[str, Any]:
    """§4.3 응답 완성: db 결과(url 제외)에 url + prescription_id 별칭 부여."""
    result["url"] = f"{base}/p/{result['token']}"
    result["prescription_id"] = result["id"]
    return result


def _issue_response_from_existing(
    conn: sqlite3.Connection, prescription_id: str, base: str
) -> dict[str, Any] | None:
    """멱등 replay(200)용 — 기존 처방에서 §4.3 응답 뼈대를 재구성."""
    bundle = db.get_prescription_bundle(conn, prescription_id)
    if bundle is None:
        return None
    p, a = bundle["prescription"], bundle["access"]
    return _attach_url(
        {
            "id": p["id"],
            "status": p["status"],
            "version": p["version"],
            "lang": p["lang"],
            "token": a["token"],
            "short_code": a["short_code"],
            "short_code_display": a["short_code_display"],
            "issued_at": p["created_at"],
            "expires_at": a["expires_at"],
            "reissue_of": p["reissue_of"],
            "replayed": True,
        },
        base,
    )


# ---------------------------------------------------------------- 라우트

@router.post("/prescriptions")
async def create_prescription(request: Request):
    """§4.3 처방 생성 + 토큰 발급. 멱등(D10): 기존 client_input_id면 200 + 동일 token.
    client_metrics는 db가 rx.created 이벤트 props로 기록(§6.2 — 별도 이벤트 불요)."""
    payload, err = await _json_body(request)
    if err:
        return err
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        cfg = request.app.state.config
        err = _validate_create_payload(cfg, payload)
        if err:
            return err
        try:
            result = db.create_prescription(conn, pharmacy_id, payload)
        except db.TokenCollisionError:
            # §2.3: 조용한 재생성 금지 — 클라가 약사 확인 목록에 올린다
            return _err(409, "TOKEN_COLLISION",
                        "pre-generated token already exists; do not regenerate silently")
        result = _attach_url(result, _base_url(request))
        return JSONResponse(result, status_code=200 if result["replayed"] else 201)
    finally:
        conn.close()


@router.get("/prescriptions")
def list_prescriptions(request: Request):
    """§4.2 최근 발급 목록 — 고정 ORDER BY created_at DESC LIMIT 20(페이징 없음).
    목록 화면에 QR 미노출(§8.2) — url만 담고 QR은 상세/재표시 경로에서 렌더."""
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        base = _base_url(request)
        items = db.list_prescriptions(conn, pharmacy_id, limit=20)
        for it in items:
            it["url"] = f"{base}/p/{it['token']}"
        return {"prescriptions": items}
    finally:
        conn.close()


@router.post("/prescriptions/{prescription_id}/reissue")
async def reissue_prescription(prescription_id: str, request: Request):
    """§4.4/D5: 구건 revoked(구 토큰 410) + 새 처방·새 토큰·새 short_code 201.
    만료된 처방에도 허용 — 구 처방은 어떤 경우에도 부활하지 않는다."""
    payload, err = await _json_body(request)
    if err:
        return err
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        cfg = request.app.state.config

        cid = payload.get("client_input_id")
        if not isinstance(cid, str) or not cid.strip() or len(cid) > 64:
            return _verr("client_input_id", "required (new UUID per reissue — D10)")
        reason = payload.get("reason")
        if reason is not None and reason not in REISSUE_REASONS:
            return _verr("reason", f"must be one of {list(REISSUE_REASONS)}")
        if payload.get("items") is not None:
            err = _validate_items(cfg, payload["items"])
            if err:
                return err

        base = _base_url(request)
        # 재전송 멱등: 같은 client_input_id의 reissue가 이미 처리됐으면 200 replay
        existing = db.find_by_client_input_id(conn, pharmacy_id, cid)
        if existing is not None:
            replay = _issue_response_from_existing(conn, existing["id"], base)
            if replay is not None:
                return JSONResponse(replay, status_code=200)

        try:
            result = db.reissue(conn, prescription_id, pharmacy_id, payload)
        except LookupError:
            return _err(404, "NOT_FOUND", "not found")  # 미존재·타 약국 — 존재 은닉
        except db.TokenCollisionError:
            return _err(409, "TOKEN_COLLISION",
                        "pre-generated token already exists; do not regenerate silently")
        except sqlite3.IntegrityError:
            # 동시 재전송 레이스 — 먼저 커밋된 쪽을 replay로 반환
            existing = db.find_by_client_input_id(conn, pharmacy_id, cid)
            if existing is not None:
                replay = _issue_response_from_existing(conn, existing["id"], base)
                if replay is not None:
                    return JSONResponse(replay, status_code=200)
            raise
        return JSONResponse(_attach_url(result, base), status_code=201)
    finally:
        conn.close()


@router.get("/prescriptions/{prescription_id}")
def get_prescription(prescription_id: str, request: Request):
    """P3 재조회용 요약 — 항목·토큰·상태(§4.2 상세+열람 현황).
    status는 §5.2 파생 계산(active|viewed|revoked|expired — purged는 80% 범위 밖)."""
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        bundle = db.get_prescription_bundle(conn, prescription_id)
        if bundle is None or bundle["prescription"]["pharmacy_id"] != pharmacy_id:
            return _err(404, "NOT_FOUND", "not found")  # 타 약국 리소스도 404(§4.1)

        p, a = bundle["prescription"], bundle["access"]
        status = bundle["token_status"]
        if status == db.TOKEN_STATUS_ACTIVE and a["first_viewed_at"]:
            status = "viewed"
        return {
            "id": p["id"],
            "prescription_id": p["id"],
            "status": status,
            "version": p["version"],
            "lang": p["lang"],
            "patient_label": p["patient_label"],
            "note": p["note"],
            "origin": p["origin"],
            "created_at": p["created_at"],
            "revised_at": p["revised_at"],
            "reissue_of": p["reissue_of"],
            "pharmacy": {
                "id": bundle["pharmacy"]["id"],
                "name": bundle["pharmacy"]["name"],
                "area": bundle["pharmacy"]["area"],
            } if bundle["pharmacy"] else None,
            "items": bundle["items"],
            "access": {
                "token": a["token"],
                "url": f"{_base_url(request)}/p/{a['token']}",
                "short_code": a["short_code"],
                "short_code_display": a["short_code_display"],
                "origin": a["origin"],
                "expires_at": a["expires_at"],
                "revoked_at": a["revoked_at"],
                "first_viewed_at": a["first_viewed_at"],
                "scan_count": a["scan_count"],
                "created_at": a["created_at"],
            },
            "token_status": bundle["token_status"],
        }
    finally:
        conn.close()


def _bundle_to_detail(bundle: dict[str, Any], base: str) -> dict[str, Any]:
    """§4.4 PUT 200 응답 = get_prescription과 동일 요약 형태(토큰 불변 확인용)."""
    p, a = bundle["prescription"], bundle["access"]
    status = bundle["token_status"]
    if status == db.TOKEN_STATUS_ACTIVE and a["first_viewed_at"]:
        status = "viewed"
    return {
        "id": p["id"],
        "prescription_id": p["id"],
        "status": status,
        "version": p["version"],
        "lang": p["lang"],
        "patient_label": p["patient_label"],
        "note": p["note"],
        "origin": p["origin"],
        "created_at": p["created_at"],
        "revised_at": p["revised_at"],
        "reissue_of": p["reissue_of"],
        "items": bundle["items"],
        "access": {
            "token": a["token"],
            "url": f"{base}/p/{a['token']}",
            "short_code": a["short_code"],
            "short_code_display": a["short_code_display"],
            "expires_at": a["expires_at"],
            "revoked_at": a["revoked_at"],
            "first_viewed_at": a["first_viewed_at"],
            "scan_count": a["scan_count"],
            "created_at": a["created_at"],
        },
        "token_status": bundle["token_status"],
    }


@router.put("/prescriptions/{prescription_id}")
async def edit_prescription(prescription_id: str, request: Request):
    """§4.4/D4: 수정 = 버전 업. 토큰·URL·QR 불변, items 전체 교체본, events[rx.edited].
    구버전은 prescription_revisions에 JSON 보관 — 렌더는 항상 최신."""
    payload, err = await _json_body(request)
    if err:
        return err
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        cfg = request.app.state.config

        # items 전체 교체본 필수 + 생성과 동일 검증(패턴·슬롯·CUSTOM 규칙)
        err = _validate_items(cfg, payload.get("items"))
        if err:
            return err
        label = payload.get("patient_label")
        if label is not None and (not isinstance(label, str) or len(label) > 20):
            return _verr("patient_label", "must be a string of <= 20 chars (D13)")
        lang = payload.get("lang")
        if lang is not None and not isinstance(lang, str):
            return _verr("lang", "must be a string")

        try:
            bundle = db.edit_prescription(conn, prescription_id, pharmacy_id, payload)
        except LookupError:
            return _err(404, "NOT_FOUND", "not found")  # 미존재·타 약국 — 존재 은닉
        except ValueError:
            # revoked 처방은 수정 불가(§5.2) — 구건은 부활하지 않는다
            return _err(410, "LINK_REVOKED", "cannot edit a revoked prescription")
        return JSONResponse(_bundle_to_detail(bundle, _base_url(request)), status_code=200)
    finally:
        conn.close()


@router.get("/prescriptions/{prescription_id}/qr")
def redisplay_qr(prescription_id: str, request: Request):
    """§4.8/D18: 재표시용 url 반환(qr_svg 없음 — 클라 렌더) + events[qr.redisplayed].
    발급이 아니므로 분모 오염 방지(§6.3-②) — rx.created와 분리된 재표시 신호."""
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        bundle = db.get_prescription_bundle(conn, prescription_id)
        if bundle is None or bundle["prescription"]["pharmacy_id"] != pharmacy_id:
            return _err(404, "NOT_FOUND", "not found")  # 타 약국 리소스도 404(§4.1)
        a = bundle["access"]
        db.record_event(
            conn, "qr.redisplayed",
            token=a["token"], prescription_id=prescription_id, pharmacy_id=pharmacy_id,
        )
        return {
            "prescription_id": prescription_id,
            "token": a["token"],
            "url": f"{_base_url(request)}/p/{a['token']}",
            "short_code": a["short_code"],
            "short_code_display": a["short_code_display"],
            "expires_at": a["expires_at"],
        }
    finally:
        conn.close()


@router.post("/prescriptions/{prescription_id}/scan-failure")
async def scan_failure(prescription_id: str, request: Request):
    """§4.2/§2.5: 스캔 실패 사유 원탭 기록 → events[scan.failed].
    scan.failed의 유일한 정당 발생원(§6.2 — /c 오입력은 view.invalid로 분리)."""
    payload, err = await _json_body(request)
    if err:
        return err
    conn = db.get_conn()
    try:
        pharmacy_id, err = _require_pharmacy(request, conn)
        if err:
            return err
        reason = payload.get("reason")
        if reason not in SCAN_FAILURE_REASONS:
            return _verr("reason", f"must be one of {list(SCAN_FAILURE_REASONS)}")
        bundle = db.get_prescription_bundle(conn, prescription_id)
        if bundle is None or bundle["prescription"]["pharmacy_id"] != pharmacy_id:
            return _err(404, "NOT_FOUND", "not found")  # 타 약국 리소스도 404(§4.1)
        a = bundle["access"]
        db.record_event(
            conn, "scan.failed",
            token=a["token"], prescription_id=prescription_id, pharmacy_id=pharmacy_id,
            props={"reason": reason},
        )
        return JSONResponse({"ok": True}, status_code=201)
    finally:
        conn.close()


@router.get("/drugs")
def search_drugs(request: Request):
    """§4.5 자동완성 — q 2자 미만 빈 배열, limit 기본 8·최대 20.
    어떤 실패로도 200 + 배열(입력 흐름을 절대 막지 않는다) — 쿼리 파싱도 직접 수행.

    운영 검색에는 승인된 production source만 포함한다. 명시적 데모 약국에서만
    Tier 3 demo source를 더해 UX를 시연한다. 클라이언트가 임의 쿼리 파라미터로
    demo 범위를 켤 수 없게 약국 컨텍스트만 사용한다.
    """
    try:
        q = request.query_params.get("q") or ""
        try:
            limit = int(request.query_params.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        conn = db.get_conn()
        try:
            include_demo = (
                request.headers.get("X-Pharmacy-Id") == db.DEMO_CATALOG_PHARMACY_ID
            )
            return db.search_drugs(conn, q, limit=limit, include_demo=include_demo)
        finally:
            conn.close()
    except Exception:
        return []


@router.post("/events")
async def post_events(request: Request):
    """§4.7/D16 비콘 — 환자 웹뷰 전용, 항상 204.
    token 필수, 화이트리스트 외 type·미존재 토큰·크기 초과(≤20개, ≤2KB)는 조용히 폐기.
    text/plain(sendBeacon 기본)도 본문은 JSON으로 파싱."""
    try:
        # §4.7: ≤2KB. Content-Length가 상한 초과면 본문 버퍼링 전에 거부(메모리 소진 DoS 차단).
        # 프록시 client_max_body_size가 1차 방어선, 이 헤더 가드가 2차 — sendBeacon은 CL을
        # 생략할 수 있으므로 아래 len(raw) 검사를 backstop으로 유지한다.
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > EVENTS_MAX_BODY_BYTES:
            return Response(status_code=204)
        raw = await request.body()
        if not raw or len(raw) > EVENTS_MAX_BODY_BYTES:
            return Response(status_code=204)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return Response(status_code=204)
        token = data.get("token")
        events = data.get("events")
        if (not isinstance(token, str) or not token
                or not isinstance(events, list) or len(events) > EVENTS_MAX_COUNT):
            return Response(status_code=204)

        conn = db.get_conn()
        try:
            status, bundle = db.get_bundle_by_token(conn, token)
            if status == db.TOKEN_STATUS_MISSING:
                return Response(status_code=204)  # 미존재 토큰 — 조용히 폐기
            prescription_id = bundle["prescription"]["id"]
            pharmacy_id = bundle["prescription"]["pharmacy_id"]
            viewer_id = request.cookies.get("ivid")
            ua_class = _ua_class(request.headers.get("user-agent"))

            wrote = False
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                etype = ev.get("type")
                if etype not in EVENT_TYPE_WHITELIST:
                    continue  # 화이트리스트 외 — 조용히 폐기
                ceid = ev.get("client_event_id")
                if ceid is not None and not isinstance(ceid, str):
                    ceid = None
                cts = ev.get("client_ts")
                if not isinstance(cts, int) or isinstance(cts, bool):
                    cts = None
                props = ev.get("props")
                if not isinstance(props, dict):
                    props = None
                db.record_event(
                    conn, etype,
                    token=token, prescription_id=prescription_id, pharmacy_id=pharmacy_id,
                    viewer_id=viewer_id, ua_class=ua_class,
                    client_event_id=ceid, client_ts=cts, props=props,
                    commit=False,  # 마지막에 일괄 커밋 (client_event_id 중복은 개별 IGNORE)
                )
                wrote = True
            if wrote:
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # D16: 어떤 오류도 밖으로 내보내지 않는다
    return Response(status_code=204)
