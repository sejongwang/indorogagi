"""indoro DB 계층 — 스키마 7테이블 + 쿼리 헬퍼 전부.

정본: docs/01-dataflow.md §3(테이블)·§5(토큰·상태).
규약:
- 모든 공개 쓰기 헬퍼는 자체 커밋(단일 트랜잭션). record_event/claim_first_view/
  bump_scan_count는 commit=False로 상위 트랜잭션에 합류 가능.
- 시각은 전부 TEXT ISO-8601 UTC 'Z' (앱 레이어 주입, CURRENT_TIMESTAMP 미사용).
- bool = INTEGER 0/1, JSON = *_json TEXT + json_valid() CHECK.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 기본 DB 경로: server/var/indoro.db (gitignore 대상)
DB_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "var" / "indoro.db"

# D3: short_code = Crockford Base32 8자(40-bit) — I, L, O, U 제외
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

TOKEN_STATUS_ACTIVE = "active"
TOKEN_STATUS_REVOKED = "revoked"
TOKEN_STATUS_EXPIRED = "expired"
TOKEN_STATUS_MISSING = "missing"


class TokenCollisionError(Exception):
    """클라 사전생성 토큰의 UNIQUE 충돌 — 조용한 재생성 금지(§2.3), 라우터가 409 TOKEN_COLLISION 처리."""


# ---------------------------------------------------------------- 기본 유틸

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    # D9: UUIDv7 문자열 PK. 이 파이썬(3.11~3.13)엔 uuid.uuid7이 없어 uuid4 허용(주석 명기).
    make = getattr(uuid, "uuid7", None)
    return str(make()) if make is not None else str(uuid.uuid4())


def new_token() -> str:
    # D2: 128-bit, 22자 [A-Za-z0-9_-]
    return secrets.token_urlsafe(16)


def new_short_code() -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(8))


def short_code_display(short_code: str | None) -> str | None:
    # D3: 표시 XXXX-XXXX
    if not short_code:
        return None
    return f"{short_code[:4]}-{short_code[4:]}"


def _to_utc_z(ts: str | None) -> str | None:
    """'+05:30' 등 오프셋 포함 ISO 문자열을 UTC 'Z'로 정규화. 실패 시 None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def compute_expires_at(created_at: str, max_duration_days: int) -> str:
    # D6: expires_at = max(30일, max(duration_days)+7일), 상한 90일
    days = min(max(30, max_duration_days + 7), 90)
    base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 스키마

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pharmacies (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    area                 TEXT,
    pincode              TEXT,
    ui_lang              TEXT NOT NULL DEFAULT 'en',
    default_patient_lang TEXT NOT NULL DEFAULT 'hi',
    has_printer          INTEGER NOT NULL DEFAULT 0,
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id                TEXT PRIMARY KEY,
    pharmacy_id       TEXT NOT NULL REFERENCES pharmacies(id),
    client_input_id   TEXT NOT NULL,
    patient_label     TEXT,
    lang              TEXT NOT NULL DEFAULT 'hi',
    note              TEXT,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    version           INTEGER NOT NULL DEFAULT 1,
    entry_method      TEXT NOT NULL DEFAULT 'manual',
    origin            TEXT NOT NULL DEFAULT 'online' CHECK (origin IN ('online', 'offline')),
    input_duration_ms INTEGER,
    active_input_ms   INTEGER,
    issued_at_client  TEXT,
    reissue_of        TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (pharmacy_id, client_input_id)          -- D10: 멱등키
);
CREATE INDEX IF NOT EXISTS idx_prescriptions_pharmacy_created
    ON prescriptions (pharmacy_id, created_at DESC);

CREATE TABLE IF NOT EXISTS prescription_items (
    id                TEXT PRIMARY KEY,
    prescription_id   TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    drug_name_raw     TEXT NOT NULL,                -- 자유 텍스트가 정본
    drug_id           TEXT,                         -- drugs 논리 참조(FK 아님 — 존재 검증만, §4.3)
    pattern_key       TEXT NOT NULL,                -- patterns.yaml 키 참조(DB FK 아님)
    dose_morning      REAL NOT NULL DEFAULT 0,
    dose_noon         REAL NOT NULL DEFAULT 0,
    dose_evening      REAL NOT NULL DEFAULT 0,
    dose_night        REAL NOT NULL DEFAULT 0,
    dose_unit         TEXT NOT NULL DEFAULT 'tablet',
    timing_food       TEXT CHECK (timing_food IN ('before_food','after_food','with_food','empty_stomach') OR timing_food IS NULL),
    duration_days     INTEGER,
    total_quantity    REAL,
    prn_reason_key    TEXT,
    prn_max_per_day   REAL,
    prn_min_gap_hours REAL,
    extra_params_json TEXT CHECK (extra_params_json IS NULL OR json_valid(extra_params_json)),
    note              TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_prescription
    ON prescription_items (prescription_id, position);

CREATE TABLE IF NOT EXISTS prescription_revisions (
    id              TEXT PRIMARY KEY,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    payload_json    TEXT NOT NULL CHECK (json_valid(payload_json)),
    edit_reason     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revisions_prescription
    ON prescription_revisions (prescription_id, version);

CREATE TABLE IF NOT EXISTS access_tokens (
    token           TEXT PRIMARY KEY,               -- D2: secrets.token_urlsafe(16)
    prescription_id TEXT NOT NULL UNIQUE REFERENCES prescriptions(id) ON DELETE CASCADE,  -- D5: 1:1
    short_code      TEXT UNIQUE,                    -- D3: 전 기간 UNIQUE, NULL 허용
    origin          TEXT NOT NULL DEFAULT 'online',
    expires_at      TEXT NOT NULL,                  -- D6
    revoked_at      TEXT,
    first_viewed_at TEXT,
    scan_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drugs (
    id                  TEXT PRIMARY KEY,
    brand_name          TEXT NOT NULL,
    generic_name        TEXT,
    strength            TEXT,
    form                TEXT,
    default_pattern_key TEXT,
    default_timing_food TEXT,
    default_dose_unit   TEXT,
    aliases_json        TEXT CHECK (aliases_json IS NULL OR json_valid(aliases_json)),
    caution_keys_json   TEXT CHECK (caution_keys_json IS NULL OR json_valid(caution_keys_json)),
    source              TEXT,
    verified            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,  -- 유일한 INTEGER PK(D9 예외)
    event_type      TEXT NOT NULL,
    ts              TEXT NOT NULL,
    client_event_id TEXT UNIQUE,                    -- 비콘 재전송 중복 제거(NULL 다중 허용)
    client_ts       INTEGER,
    pharmacy_id     TEXT,                           -- FK 아님(§3.1) — 자유 TEXT
    prescription_id TEXT,
    token           TEXT,
    viewer_id       TEXT,
    src             TEXT,
    ua_class        TEXT,
    is_bot          INTEGER NOT NULL DEFAULT 0,
    is_internal     INTEGER NOT NULL DEFAULT 0,
    props_json      TEXT CHECK (props_json IS NULL OR json_valid(props_json))
);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_events_token ON events (token);
CREATE INDEX IF NOT EXISTS idx_events_pharmacy_ts ON events (pharmacy_id, ts);
"""


def get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    """WAL + foreign_keys ON + Row 팩토리 연결. 라우터는 요청마다 열고 닫는다."""
    path = Path(db_path) if db_path else DB_PATH_DEFAULT
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 이벤트

def record_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    token: str | None = None,
    prescription_id: str | None = None,
    pharmacy_id: str | None = None,
    viewer_id: str | None = None,
    src: str | None = None,
    ua_class: str | None = None,
    is_bot: int = 0,
    is_internal: int = 0,
    client_event_id: str | None = None,
    client_ts: int | None = None,
    props: dict[str, Any] | None = None,
    ts: str | None = None,
    commit: bool = True,
) -> bool:
    """이벤트 1건 INSERT. client_event_id 중복이면 조용히 폐기(False). D16 계약의 저장부."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO events
           (event_type, ts, client_event_id, client_ts, pharmacy_id, prescription_id,
            token, viewer_id, src, ua_class, is_bot, is_internal, props_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_type, ts or now_utc(), client_event_id, client_ts, pharmacy_id,
            prescription_id, token, viewer_id, src, ua_class, int(is_bot), int(is_internal),
            json.dumps(props, ensure_ascii=False) if props else None,
        ),
    )
    if commit:
        conn.commit()
    return cur.rowcount == 1


# ---------------------------------------------------------------- 토큰

def _issue_token_tx(
    conn: sqlite3.Connection,
    prescription_id: str,
    expires_at: str,
    *,
    token: str | None = None,
    origin: str = "online",
    created_at: str | None = None,
) -> dict[str, Any]:
    """커밋 없는 토큰 발급 코어. 서버 생성분은 UNIQUE 충돌 시 재생성 루프,
    클라 제공 토큰 충돌은 TokenCollisionError(조용한 재생성 금지 — §2.3)."""
    created = created_at or now_utc()
    provided = token is not None
    for _ in range(8):
        t = token if provided else new_token()
        sc = new_short_code()
        try:
            conn.execute(
                """INSERT INTO access_tokens
                   (token, prescription_id, short_code, origin, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (t, prescription_id, sc, origin, expires_at, created),
            )
        except sqlite3.IntegrityError as e:
            msg = str(e)
            if "access_tokens.token" in msg:
                if provided:
                    raise TokenCollisionError(t) from e
                continue  # 서버 생성분만 재시도
            if "access_tokens.short_code" in msg:
                continue  # short_code 재생성 루프(전 기간 UNIQUE — D3)
            raise
        return {
            "token": t,
            "short_code": sc,
            "short_code_display": short_code_display(sc),
            "origin": origin,
            "expires_at": expires_at,
            "created_at": created,
        }
    raise RuntimeError("token issuance failed after retries")


def issue_token(
    conn: sqlite3.Connection,
    prescription_id: str,
    expires_at: str,
    *,
    token: str | None = None,
    origin: str = "online",
) -> dict[str, Any]:
    """단독 호출용(자체 커밋). 처방 생성 흐름에서는 create_prescription이 내부 발급."""
    try:
        result = _issue_token_tx(conn, prescription_id, expires_at, token=token, origin=origin)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def claim_first_view(conn: sqlite3.Connection, token: str, *, ts: str | None = None,
                     commit: bool = True) -> bool:
    """view.first 원자 선점 — 1행 변경 성공 시에만 True (§6.2)."""
    cur = conn.execute(
        "UPDATE access_tokens SET first_viewed_at = ? WHERE token = ? AND first_viewed_at IS NULL",
        (ts or now_utc(), token),
    )
    if commit:
        conn.commit()
    return cur.rowcount == 1


def bump_scan_count(conn: sqlite3.Connection, token: str, *, commit: bool = True) -> None:
    conn.execute("UPDATE access_tokens SET scan_count = scan_count + 1 WHERE token = ?", (token,))
    if commit:
        conn.commit()


# ---------------------------------------------------------------- 처방 생성/조회

def find_by_client_input_id(
    conn: sqlite3.Connection, pharmacy_id: str, client_input_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM prescriptions WHERE pharmacy_id = ? AND client_input_id = ?",
        (pharmacy_id, client_input_id),
    ).fetchone()
    return dict(row) if row else None


def _issue_result(conn: sqlite3.Connection, presc: dict[str, Any], replayed: bool) -> dict[str, Any]:
    """§4.3 201/200 응답 뼈대(url 제외 — 라우터가 base_url로 조립)."""
    tok = conn.execute(
        "SELECT * FROM access_tokens WHERE prescription_id = ?", (presc["id"],)
    ).fetchone()
    tok = dict(tok) if tok else {}
    return {
        "id": presc["id"],
        "status": presc["status"],
        "version": presc["version"],
        "lang": presc["lang"],
        "token": tok.get("token"),
        "short_code": tok.get("short_code"),
        "short_code_display": short_code_display(tok.get("short_code")),
        "issued_at": presc["created_at"],
        "expires_at": tok.get("expires_at"),
        "reissue_of": presc.get("reissue_of"),
        "replayed": replayed,
    }


def _insert_items_tx(
    conn: sqlite3.Connection, presc_id: str, items: list[dict[str, Any]]
) -> int:
    """items를 prescription_items에 INSERT. 반환: max(duration_days)(만료 산정용).
    생성·수정(PUT) 공용 — 두 경로의 item 저장 규약이 갈라지지 않게 한다."""
    max_duration = 0
    for idx, item in enumerate(items):
        doses = item.get("doses") or {}
        duration = item.get("duration_days")
        if isinstance(duration, (int, float)):
            max_duration = max(max_duration, int(duration))
        drug_id = item.get("drug_id")
        if drug_id is not None:
            # §4.3: drug_id는 존재 검증만, 실패해도 무시(자유 텍스트가 항상 유효 경로)
            found = conn.execute("SELECT 1 FROM drugs WHERE id = ?", (drug_id,)).fetchone()
            if found is None:
                drug_id = None
        extra = item.get("extra_params")
        conn.execute(
            """INSERT INTO prescription_items
               (id, prescription_id, position, drug_name_raw, drug_id, pattern_key,
                dose_morning, dose_noon, dose_evening, dose_night, dose_unit, timing_food,
                duration_days, total_quantity, prn_reason_key, prn_max_per_day,
                prn_min_gap_hours, extra_params_json, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(), presc_id, item.get("position") or idx + 1,
                item["drug_name_raw"], drug_id, item["pattern_key"],
                doses.get("M") or 0, doses.get("N") or 0, doses.get("E") or 0, doses.get("H") or 0,
                item.get("dose_unit") or "tablet", item.get("timing_food"),
                duration, item.get("total_quantity"),
                item.get("prn_reason_key"), item.get("prn_max_per_day"),
                item.get("prn_min_gap_hours"),
                json.dumps(extra, ensure_ascii=False) if extra else None,
                item.get("note"),
            ),
        )
    return max_duration


def _insert_prescription_tx(
    conn: sqlite3.Connection,
    pharmacy_id: str,
    payload: dict[str, Any],
    *,
    reissue_of: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """커밋 없는 생성 코어: prescriptions + items + access_token + rx.created (단일 트랜잭션 소재)."""
    created = created_at or now_utc()
    presc_id = new_id()
    items: list[dict[str, Any]] = payload.get("items") or []
    metrics = payload.get("client_metrics") or {}
    client_token = payload.get("token") or None
    origin = payload.get("origin") or ("offline" if client_token else "online")

    conn.execute(
        """INSERT INTO prescriptions
           (id, pharmacy_id, client_input_id, patient_label, lang, note, status, version,
            entry_method, origin, input_duration_ms, active_input_ms, issued_at_client,
            reissue_of, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 'manual', ?, ?, ?, ?, ?, ?)""",
        (
            presc_id, pharmacy_id, payload["client_input_id"], payload.get("patient_label"),
            payload.get("lang") or "hi", payload.get("note"), origin,
            metrics.get("input_duration_ms"), metrics.get("active_input_ms"),
            payload.get("issued_at_client"), reissue_of, created,
        ),
    )

    max_duration = _insert_items_tx(conn, presc_id, items)

    expires_at = compute_expires_at(created, max_duration)
    tok = _issue_token_tx(
        conn, presc_id, expires_at, token=client_token, origin=origin, created_at=created
    )

    record_event(
        conn, "rx.created",
        token=tok["token"], prescription_id=presc_id, pharmacy_id=pharmacy_id,
        ts=created, commit=False,
        props={
            "n_items": len(items),
            "duration_ms": metrics.get("input_duration_ms"),
            "active_input_ms": metrics.get("active_input_ms"),
            "idle_gaps_count": metrics.get("idle_gaps_count"),
            "used_autocomplete_count": metrics.get("used_autocomplete_count"),
            "retry_count": metrics.get("retry_count"),
            "patterns": [i.get("pattern_key") for i in items],
            "origin": origin,
            "issued_at_client_utc": _to_utc_z(payload.get("issued_at_client")),
            "reissue_of": reissue_of,
        },
    )

    presc = dict(
        conn.execute("SELECT * FROM prescriptions WHERE id = ?", (presc_id,)).fetchone()
    )
    return _issue_result(conn, presc, replayed=False)


def create_prescription(
    conn: sqlite3.Connection, pharmacy_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """처방 생성(멱등 — D10). payload는 §4.3 POST 본문 형태(dict).
    동일 (pharmacy_id, client_input_id) 기존재 시 기존 발급 결과를 replayed=True로 반환.
    클라 사전생성 token 충돌 시 TokenCollisionError."""
    existing = find_by_client_input_id(conn, pharmacy_id, payload["client_input_id"])
    if existing is not None:
        return _issue_result(conn, existing, replayed=True)
    try:
        result = _insert_prescription_tx(conn, pharmacy_id, payload)
        conn.commit()
        return result
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "prescriptions.pharmacy_id, prescriptions.client_input_id" in str(e):
            # 동시 요청 레이스 — 먼저 커밋된 쪽을 replay로 반환
            existing = find_by_client_input_id(conn, pharmacy_id, payload["client_input_id"])
            if existing is not None:
                return _issue_result(conn, existing, replayed=True)
        raise
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- 번들 조회

def _drug_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    return {
        "id": d["id"],
        "brand_name": d["brand_name"],
        "generic_name": d["generic_name"],
        "strength": d["strength"],
        "form": d["form"],
        "default_pattern_key": d["default_pattern_key"],
        "default_timing_food": d["default_timing_food"],
        "default_dose_unit": d["default_dose_unit"],
        "aliases": json.loads(d["aliases_json"]) if d["aliases_json"] else [],
        "caution_keys": json.loads(d["caution_keys_json"]) if d["caution_keys_json"] else [],
        "verified": d["verified"],
    }


def _item_dict(row: sqlite3.Row, drug: dict[str, Any] | None) -> dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "position": d["position"],
        "drug_name_raw": d["drug_name_raw"],
        "drug_id": d["drug_id"],
        "drug": drug,
        "pattern_key": d["pattern_key"],
        "doses": {"M": d["dose_morning"], "N": d["dose_noon"],
                  "E": d["dose_evening"], "H": d["dose_night"]},
        "dose_unit": d["dose_unit"],
        "timing_food": d["timing_food"],
        "duration_days": d["duration_days"],
        "total_quantity": d["total_quantity"],
        "prn_reason_key": d["prn_reason_key"],
        "prn_max_per_day": d["prn_max_per_day"],
        "prn_min_gap_hours": d["prn_min_gap_hours"],
        "extra_params": json.loads(d["extra_params_json"]) if d["extra_params_json"] else None,
        "note": d["note"],
    }


def _token_status(access: dict[str, Any], presc_status: str, now: str) -> str:
    # §5.2: 저장 상태는 active|revoked 2종, expired는 파생
    if access.get("revoked_at") or presc_status == "revoked":
        return TOKEN_STATUS_REVOKED
    if access["expires_at"] <= now:  # 동일 포맷 ISO Z — 문자열 비교 = 시간 비교
        return TOKEN_STATUS_EXPIRED
    return TOKEN_STATUS_ACTIVE


def _build_bundle(conn: sqlite3.Connection, presc_row: sqlite3.Row,
                  access_row: sqlite3.Row, *, now: str | None = None) -> dict[str, Any]:
    presc = dict(presc_row)
    access = dict(access_row)
    now = now or now_utc()

    pharmacy_row = conn.execute(
        "SELECT * FROM pharmacies WHERE id = ?", (presc["pharmacy_id"],)
    ).fetchone()
    pharmacy = dict(pharmacy_row) if pharmacy_row else None

    items = []
    for item_row in conn.execute(
        "SELECT * FROM prescription_items WHERE prescription_id = ? ORDER BY position",
        (presc["id"],),
    ).fetchall():
        drug = None
        if item_row["drug_id"]:
            drug = _drug_dict(
                conn.execute("SELECT * FROM drugs WHERE id = ?", (item_row["drug_id"],)).fetchone()
            )
        items.append(_item_dict(item_row, drug))

    revised_at = conn.execute(
        "SELECT MAX(created_at) AS m FROM prescription_revisions WHERE prescription_id = ?",
        (presc["id"],),
    ).fetchone()["m"]
    presc["revised_at"] = revised_at

    return {
        "prescription": presc,
        "pharmacy": pharmacy,
        "access": {
            "token": access["token"],
            "short_code": access["short_code"],
            "short_code_display": short_code_display(access["short_code"]),
            "origin": access["origin"],
            "expires_at": access["expires_at"],
            "revoked_at": access["revoked_at"],
            "first_viewed_at": access["first_viewed_at"],
            "scan_count": access["scan_count"],
            "created_at": access["created_at"],
        },
        "items": items,
        "token_status": _token_status(access, presc["status"], now),
    }


def get_bundle_by_token(
    conn: sqlite3.Connection, token: str, *, now: str | None = None
) -> tuple[str, dict[str, Any] | None]:
    """(token_status, bundle) 반환. status ∈ active|revoked|expired|missing.
    missing이면 bundle=None — 라우터는 D8에 따라 200 대기 페이지."""
    access_row = conn.execute(
        "SELECT * FROM access_tokens WHERE token = ?", (token,)
    ).fetchone()
    if access_row is None:
        return TOKEN_STATUS_MISSING, None
    presc_row = conn.execute(
        "SELECT * FROM prescriptions WHERE id = ?", (access_row["prescription_id"],)
    ).fetchone()
    bundle = _build_bundle(conn, presc_row, access_row, now=now)
    return bundle["token_status"], bundle


def get_prescription_bundle(
    conn: sqlite3.Connection, prescription_id: str, *, now: str | None = None
) -> dict[str, Any] | None:
    presc_row = conn.execute(
        "SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)
    ).fetchone()
    if presc_row is None:
        return None
    access_row = conn.execute(
        "SELECT * FROM access_tokens WHERE prescription_id = ?", (prescription_id,)
    ).fetchone()
    if access_row is None:
        return None
    return _build_bundle(conn, presc_row, access_row, now=now)


def find_token_by_short_code(conn: sqlite3.Connection, short_code: str) -> str | None:
    """/c 코드 입력 경로용 — 정규화(대문자화·하이픈 제거·O→0·I/L→1)는 라우터 책임."""
    row = conn.execute(
        "SELECT token FROM access_tokens WHERE short_code = ?", (short_code,)
    ).fetchone()
    return row["token"] if row else None


# ---------------------------------------------------------------- 재발급 (D5)

def reissue(
    conn: sqlite3.Connection,
    prescription_id: str,
    pharmacy_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """D5: reissue = 새 처방 + 새 토큰, 구건 종결(구 토큰 revoked → 410).
    payload: {client_input_id(필수·새 UUID), reason, items?(없으면 구건 복사), lang?, ...}
    구건 스냅샷을 prescription_revisions에 보관. 미존재/타 약국이면 LookupError(라우터 404)."""
    old = get_prescription_bundle(conn, prescription_id)
    if old is None or old["prescription"]["pharmacy_id"] != pharmacy_id:
        raise LookupError(prescription_id)

    now = now_utc()
    reason = payload.get("reason") or "other"
    try:
        # 1) 구건 종결
        conn.execute(
            "UPDATE prescriptions SET status = 'revoked' WHERE id = ?", (prescription_id,)
        )
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? WHERE prescription_id = ?",
            (now, prescription_id),
        )
        # 2) 구건 스냅샷을 revision으로 보관 (감사 이력)
        conn.execute(
            """INSERT INTO prescription_revisions
               (id, prescription_id, version, payload_json, edit_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                new_id(), prescription_id, old["prescription"]["version"],
                json.dumps({"prescription": old["prescription"], "items": old["items"]},
                           ensure_ascii=False),
                f"reissue:{reason}", now,
            ),
        )
        record_event(
            conn, "rx.revoked",
            token=old["access"]["token"], prescription_id=prescription_id,
            pharmacy_id=pharmacy_id, ts=now, commit=False,
            props={"reason": reason, "version": old["prescription"]["version"]},
        )
        # 3) 새 처방 + 새 토큰 (rx.created 포함)
        new_payload = dict(payload)
        if not new_payload.get("items"):
            new_payload["items"] = old["items"]
        new_payload.setdefault("lang", old["prescription"]["lang"])
        new_payload.setdefault("patient_label", old["prescription"]["patient_label"])
        new_payload.setdefault("note", old["prescription"]["note"])
        result = _insert_prescription_tx(
            conn, pharmacy_id, new_payload, reissue_of=prescription_id, created_at=now
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- 수정 (D4)

def edit_prescription(
    conn: sqlite3.Connection,
    prescription_id: str,
    pharmacy_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """§4.4/D4: 수정 = 버전 업. 토큰·URL·QR 불변, items 전체 교체, 렌더는 항상 최신.
    구버전 스냅샷을 prescription_revisions에 보관하고 rx.edited 발생.
    payload: {items[](전체 교체본, 필수), edit_reason?, lang?, patient_label?, note?}.
    미존재/타 약국이면 LookupError(라우터 404). revoked 처방은 수정 불가(ValueError)."""
    old = get_prescription_bundle(conn, prescription_id)
    if old is None or old["prescription"]["pharmacy_id"] != pharmacy_id:
        raise LookupError(prescription_id)
    if old["prescription"]["status"] == "revoked":
        # 폐기된 처방은 부활하지 않는다(§5.2) — 수정도 불가. 재발급 경로를 쓴다.
        raise ValueError("cannot edit a revoked prescription")

    now = now_utc()
    old_presc = old["prescription"]
    old_version = old_presc["version"]
    new_version = old_version + 1
    items: list[dict[str, Any]] = payload.get("items") or []
    edit_reason = payload.get("edit_reason")

    # §6.2 rx.edited props.fields_changed — 변경 필드 목록(상세 값은 미복제, H13)
    fields_changed: list[str] = ["items"]
    for f in ("lang", "patient_label", "note"):
        if f in payload and payload.get(f) != old_presc.get(f):
            fields_changed.append(f)

    try:
        # 1) 구버전 스냅샷을 revision으로 보관 (감사 이력 — purge 시 payload_json 널링 §8.3)
        conn.execute(
            """INSERT INTO prescription_revisions
               (id, prescription_id, version, payload_json, edit_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                new_id(), prescription_id, old_version,
                json.dumps({"prescription": old_presc, "items": old["items"]},
                           ensure_ascii=False),
                edit_reason, now,
            ),
        )
        # 2) items 전체 교체 (부분 패치 아님 — §4.4)
        conn.execute(
            "DELETE FROM prescription_items WHERE prescription_id = ?", (prescription_id,)
        )
        max_duration = _insert_items_tx(conn, prescription_id, items)

        # 3) 헤더 버전 업 + 선택 헤더 필드 갱신 (토큰 불변 — D4)
        new_lang = payload.get("lang") if "lang" in payload else old_presc["lang"]
        new_label = payload.get("patient_label") if "patient_label" in payload else old_presc["patient_label"]
        new_note = payload.get("note") if "note" in payload else old_presc["note"]
        conn.execute(
            "UPDATE prescriptions SET version = ?, lang = ?, patient_label = ?, note = ? WHERE id = ?",
            (new_version, new_lang, new_label, new_note, prescription_id),
        )
        # 4) 만료창 재산정(D6 — duration이 바뀌면 반영). token/URL은 그대로.
        expires_at = compute_expires_at(old["access"]["created_at"], max_duration)
        conn.execute(
            "UPDATE access_tokens SET expires_at = ? WHERE prescription_id = ?",
            (expires_at, prescription_id),
        )

        record_event(
            conn, "rx.edited",
            token=old["access"]["token"], prescription_id=prescription_id,
            pharmacy_id=pharmacy_id, ts=now, commit=False,
            props={"version": new_version, "fields_changed": fields_changed,
                   "edit_reason": edit_reason},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return get_prescription_bundle(conn, prescription_id)


# ---------------------------------------------------------------- 목록 (§4.2)

def list_prescriptions(
    conn: sqlite3.Connection, pharmacy_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """§4.2: 최근 발급 목록 — 고정 ORDER BY created_at DESC LIMIT 20(페이징 없음).
    idx_prescriptions_pharmacy_created 인덱스 사용. 목록 화면에 QR 미노출(§8.2)이라
    토큰·short_code는 요약만 담고 url은 라우터가 조립한다."""
    now = now_utc()
    rows = conn.execute(
        """SELECT p.id, p.patient_label, p.lang, p.status, p.version, p.origin,
                  p.created_at, p.reissue_of,
                  a.token, a.short_code, a.expires_at, a.revoked_at,
                  a.first_viewed_at, a.scan_count
           FROM prescriptions p
           JOIN access_tokens a ON a.prescription_id = p.id
           WHERE p.pharmacy_id = ?
           ORDER BY p.created_at DESC
           LIMIT ?""",
        (pharmacy_id, max(1, min(int(limit), 20))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        status = _token_status(
            {"revoked_at": d["revoked_at"], "expires_at": d["expires_at"]}, d["status"], now
        )
        if status == TOKEN_STATUS_ACTIVE and d["first_viewed_at"]:
            status = "viewed"
        out.append({
            "id": d["id"],
            "prescription_id": d["id"],
            "patient_label": d["patient_label"],
            "lang": d["lang"],
            "status": status,
            "version": d["version"],
            "origin": d["origin"],
            "created_at": d["created_at"],
            "reissue_of": d["reissue_of"],
            "token": d["token"],
            "short_code": d["short_code"],
            "short_code_display": short_code_display(d["short_code"]),
            "expires_at": d["expires_at"],
            "first_viewed_at": d["first_viewed_at"],
            "scan_count": d["scan_count"],
        })
    return out


# ---------------------------------------------------------------- drugs / pharmacies

def search_drugs(conn: sqlite3.Connection, q: str, limit: int = 8) -> list[dict[str, Any]]:
    """§4.5: naive lower() LIKE — brand/generic/aliases. q 2자 미만은 빈 배열, limit 최대 20."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit), 20))
    needle = f"%{q.lower()}%"
    prefix = f"{q.lower()}%"
    rows = conn.execute(
        """SELECT * FROM drugs
           WHERE lower(brand_name) LIKE ?
              OR lower(IFNULL(generic_name, '')) LIKE ?
              OR lower(IFNULL(aliases_json, '')) LIKE ?
           ORDER BY verified DESC,
                    CASE WHEN lower(brand_name) LIKE ? THEN 0 ELSE 1 END,
                    brand_name
           LIMIT ?""",
        (needle, needle, needle, prefix, limit),
    ).fetchall()
    return [_drug_dict(r) for r in rows]


def import_drugs(conn: sqlite3.Connection, drugs: list[dict[str, Any]]) -> int:
    """drugs 테이블 upsert. 자연키 (brand_name, strength, form) — 재실행해도 id 보존(멱등).
    각 dict: brand_name(필수), generic_name, strength, form, aliases(list),
    caution_keys(list), source(str), verified, default_* 3종."""
    n = 0
    now = now_utc()
    try:
        for d in drugs:
            aliases = json.dumps(d.get("aliases") or [], ensure_ascii=False)
            cautions = json.dumps(d.get("caution_keys") or [], ensure_ascii=False)
            existing = conn.execute(
                """SELECT id FROM drugs
                   WHERE brand_name = ? AND IFNULL(strength,'') = ? AND IFNULL(form,'') = ?""",
                (d["brand_name"], d.get("strength") or "", d.get("form") or ""),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE drugs SET generic_name=?, default_pattern_key=?,
                       default_timing_food=?, default_dose_unit=?, aliases_json=?,
                       caution_keys_json=?, source=?, verified=? WHERE id=?""",
                    (
                        d.get("generic_name"), d.get("default_pattern_key"),
                        d.get("default_timing_food"), d.get("default_dose_unit"),
                        aliases, cautions, d.get("source"), d.get("verified", 0),
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO drugs
                       (id, brand_name, generic_name, strength, form, default_pattern_key,
                        default_timing_food, default_dose_unit, aliases_json,
                        caution_keys_json, source, verified, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id(), d["brand_name"], d.get("generic_name"), d.get("strength"),
                        d.get("form"), d.get("default_pattern_key"),
                        d.get("default_timing_food"), d.get("default_dose_unit"),
                        aliases, cautions, d.get("source"), d.get("verified", 0), now,
                    ),
                )
            n += 1
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise


def upsert_pharmacy(conn: sqlite3.Connection, pharmacy: dict[str, Any]) -> None:
    """온보딩/시드용 약국 upsert. pharmacy: {id, name, area, pincode, ui_lang,
    default_patient_lang, has_printer, is_active}."""
    conn.execute(
        """INSERT INTO pharmacies
           (id, name, area, pincode, ui_lang, default_patient_lang, has_printer, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, area=excluded.area, pincode=excluded.pincode,
             ui_lang=excluded.ui_lang, default_patient_lang=excluded.default_patient_lang,
             has_printer=excluded.has_printer, is_active=excluded.is_active""",
        (
            pharmacy["id"], pharmacy["name"], pharmacy.get("area"), pharmacy.get("pincode"),
            pharmacy.get("ui_lang", "en"), pharmacy.get("default_patient_lang", "hi"),
            pharmacy.get("has_printer", 0), pharmacy.get("is_active", 1), now_utc(),
        ),
    )
    conn.commit()


def get_pharmacy(conn: sqlite3.Connection, pharmacy_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM pharmacies WHERE id = ?", (pharmacy_id,)).fetchone()
    return dict(row) if row else None
