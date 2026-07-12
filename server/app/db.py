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
import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 기본 DB 경로: server/var/indoro.db (gitignore 대상). 합성 Tier 3 fixture는
# INDORO_DB_PATH로 지정한 별도 demo DB에서만 실행한다.
DB_PATH_DEFAULT = Path(
    os.environ.get("INDORO_DB_PATH")
    or Path(__file__).resolve().parent.parent / "var" / "indoro.db"
)
DEMO_CATALOG_PHARMACY_ID = "ph-demo-001"

# D3: short_code = Crockford Base32 8자(40-bit) — I, L, O, U 제외
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

TOKEN_STATUS_ACTIVE = "active"
TOKEN_STATUS_REVOKED = "revoked"
TOKEN_STATUS_EXPIRED = "expired"
TOKEN_STATUS_MISSING = "missing"


class TokenCollisionError(Exception):
    """클라 사전생성 토큰의 UNIQUE 충돌 — 조용한 재생성 금지(§2.3), 라우터가 409 TOKEN_COLLISION 처리."""


class IdempotencyConflictError(Exception):
    """동일 멱등키 + 다른 본문(§4.1) — 조용한 replay는 수정 내용을 유실시킨다. 라우터가 409 처리."""


class PrescriptionExpiredError(Exception):
    """만료된 처방의 수정 시도 — 만료 토큰은 부활하지 않는다(§5.2). 라우터가 410 LINK_EXPIRED 처리."""


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


def client_request_sha256(payload: dict[str, Any]) -> str:
    """멱등 충돌 판정용 본문 해시(§4.1). 처방 내용을 결정하는 필드만 포함한다.

    client_metrics·issued_at_client은 제외 — 오프라인 outbox 재전송은 retry_count 등
    계측만 달라지며(§4.3), 계측 차이가 발급 replay를 막으면 안 된다. token은 포함 —
    같은 멱등키로 다른 토큰이 오면 클라 재생성(§2.3 금지 사항) 신호다."""
    core = {
        "patient_label": payload.get("patient_label"),
        "lang": payload.get("lang"),
        "note": payload.get("note"),
        "token": payload.get("token"),
        "items": payload.get("items"),
    }
    return hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


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
    client_request_sha256 TEXT,                    -- §4.1: 멱등 충돌 판정용 본문 해시
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
    drug_input_raw    TEXT,                         -- 카탈로그 선택 전 약사 입력 원문
    drug_id           TEXT,                         -- drugs 논리 참조(FK 아님 — 존재 검증만, §4.3)
    drug_match_state  TEXT NOT NULL DEFAULT 'free_text'
                      CHECK (drug_match_state IN ('free_text','selected','selected_then_modified')),
    drug_catalog_snapshot_json TEXT
                      CHECK (drug_catalog_snapshot_json IS NULL OR json_valid(drug_catalog_snapshot_json)),
    drug_selection_warning_json TEXT
                      CHECK (drug_selection_warning_json IS NULL OR json_valid(drug_selection_warning_json)),
    pattern_key       TEXT NOT NULL,                -- patterns.yaml 키 참조(DB FK 아님)
    dose_morning      REAL NOT NULL DEFAULT 0,
    dose_noon         REAL NOT NULL DEFAULT 0,
    dose_evening      REAL NOT NULL DEFAULT 0,
    dose_night        REAL NOT NULL DEFAULT 0,
    dose_unit         TEXT NOT NULL DEFAULT 'tablet',
    administration_route TEXT CHECK (
        administration_route IN ('oral','ophthalmic','otic','nasal','inhalation',
                                 'topical','rectal','vaginal','transdermal',
                                 'intravenous','intramuscular','subcutaneous','other')
        OR administration_route IS NULL
    ),
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

-- 의약품 카탈로그 v2. 기존 drugs는 처방 호환 projection으로 유지하고,
-- 신규 import/search는 출처·원본·presentation을 분리한다.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_database_meta (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    catalog_mode TEXT NOT NULL CHECK (catalog_mode IN ('production','demo')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drug_sources (
    id                    TEXT PRIMARY KEY,
    slug                  TEXT NOT NULL UNIQUE,
    name                  TEXT NOT NULL,
    operator              TEXT,
    tier                  INTEGER NOT NULL CHECK (tier IN (1,2,3)),
    usage_scope           TEXT NOT NULL CHECK (usage_scope IN ('production','demo','reference')),
    reuse_status          TEXT NOT NULL,
    license_name          TEXT,
    license_url           TEXT,
    attribution_text      TEXT,
    source_url            TEXT,
    legal_review_required INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drug_import_runs (
    id                TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES drug_sources(id),
    version           TEXT NOT NULL,
    published_at      TEXT,
    source_updated_at TEXT,
    input_uri         TEXT,
    input_sha256      TEXT NOT NULL,
    records_sha256    TEXT,
    accessed_at       TEXT,
    source_snapshot_json TEXT CHECK (source_snapshot_json IS NULL OR json_valid(source_snapshot_json)),
    license_snapshot_json TEXT CHECK (license_snapshot_json IS NULL OR json_valid(license_snapshot_json)),
    approval_snapshot_json TEXT CHECK (approval_snapshot_json IS NULL OR json_valid(approval_snapshot_json)),
    approval_registry_sha256 TEXT,
    importer_version  TEXT NOT NULL,
    normalization_version TEXT NOT NULL DEFAULT 'catalog-normalization-v1',
    package_schema_version TEXT NOT NULL DEFAULT '1',
    snapshot_mode     TEXT NOT NULL DEFAULT 'delta' CHECK (snapshot_mode IN ('delta','full')),
    package_content_sha256 TEXT,
    code_revision     TEXT,
    mode              TEXT NOT NULL CHECK (mode IN ('apply','dry_run')),
    status            TEXT NOT NULL CHECK (status IN ('completed','failed')),
    raw_total         INTEGER NOT NULL DEFAULT 0,
    imported          INTEGER NOT NULL DEFAULT 0,
    excluded          INTEGER NOT NULL DEFAULT 0,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    duplicate_candidates INTEGER NOT NULL DEFAULT 0,
    report_json       TEXT CHECK (report_json IS NULL OR json_valid(report_json)),
    started_at        TEXT NOT NULL,
    completed_at      TEXT,
    UNIQUE (source_id, version, input_sha256, mode)
);

CREATE TABLE IF NOT EXISTS drug_presentations (
    id                    TEXT PRIMARY KEY,
    source_id             TEXT NOT NULL REFERENCES drug_sources(id),
    source_record_id      TEXT NOT NULL,
    name_type             TEXT NOT NULL DEFAULT 'brand' CHECK (name_type IN ('brand','generic')),
    brand_name_raw        TEXT NOT NULL,
    brand_name_norm       TEXT NOT NULL,
    brand_name_search     TEXT NOT NULL,
    generic_name_raw      TEXT,
    generic_name_norm     TEXT,
    generic_name_search   TEXT,
    strength_raw          TEXT,
    strength_search       TEXT,
    dosage_form_raw       TEXT,
    dosage_form_code      TEXT,
    route_raw             TEXT,
    route_code            TEXT,
    release_modifier_raw  TEXT,
    release_modifier_code TEXT,
    manufacturer_name     TEXT,
    marketer_name         TEXT,
    rx_classification     TEXT,
    short_display_name    TEXT,
    package_summary       TEXT,
    usage_scope           TEXT NOT NULL CHECK (usage_scope IN ('production','demo')),
    lifecycle_status      TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_status IN ('active','inactive')),
    review_status         TEXT NOT NULL DEFAULT 'unverified'
                          CHECK (review_status IN ('verified','unverified','needs_review')),
    -- Source/normalization 상태와 사람이 결정한 운영 상태는 의도적으로 분리한다.
    -- 기존 review_status/lifecycle_status는 원본 package projection으로 유지된다.
    workflow_review_status TEXT NOT NULL DEFAULT 'unverified'
                          CHECK (workflow_review_status IN
                                 ('unverified','needs_review','approved','rejected')),
    operational_lifecycle_status TEXT NOT NULL DEFAULT 'active'
                          CHECK (operational_lifecycle_status IN
                                 ('active','inactive','retired')),
    record_version        INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1),
    normalized_projection_json TEXT CHECK (
        normalized_projection_json IS NULL OR json_valid(normalized_projection_json)
    ),
    normalized_projection_sha256 TEXT,
    dedupe_fingerprint    TEXT NOT NULL,
    incomplete_fields_json TEXT CHECK (incomplete_fields_json IS NULL OR json_valid(incomplete_fields_json)),
    warnings_json          TEXT CHECK (warnings_json IS NULL OR json_valid(warnings_json)),
    source_updated_at      TEXT,
    current_source_record_row_id TEXT REFERENCES drug_source_records(id),
    current_import_run_record_id TEXT REFERENCES drug_import_run_records(id),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE (source_id, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_drug_presentations_brand_search
    ON drug_presentations (brand_name_search);
CREATE INDEX IF NOT EXISTS idx_drug_presentations_generic_search
    ON drug_presentations (generic_name_search);
CREATE INDEX IF NOT EXISTS idx_drug_presentations_scope_status
    ON drug_presentations (usage_scope, lifecycle_status, review_status);

CREATE TABLE IF NOT EXISTS drug_source_records (
    id               TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES drug_sources(id),
    import_run_id    TEXT NOT NULL REFERENCES drug_import_runs(id),
    source_record_id TEXT NOT NULL,
    raw_sha256       TEXT NOT NULL,
    raw_json         TEXT NOT NULL CHECK (json_valid(raw_json)),
    ingest_status    TEXT NOT NULL CHECK (ingest_status IN ('imported','needs_review','excluded')),
    errors_json      TEXT CHECK (errors_json IS NULL OR json_valid(errors_json)),
    presentation_id  TEXT REFERENCES drug_presentations(id),
    created_at       TEXT NOT NULL,
    UNIQUE (source_id, source_record_id, raw_sha256)
);
CREATE INDEX IF NOT EXISTS idx_drug_source_records_current
    ON drug_source_records (source_id, source_record_id, created_at DESC);

-- A raw row is immutable and de-duplicated, while this link records that the
-- exact same raw record participated in every later source release/import run.
CREATE TABLE IF NOT EXISTS drug_import_run_records (
    id                   TEXT PRIMARY KEY,
    import_run_id        TEXT NOT NULL REFERENCES drug_import_runs(id) ON DELETE CASCADE,
    record_ordinal       INTEGER NOT NULL,
    source_record_row_id TEXT NOT NULL REFERENCES drug_source_records(id),
    source_record_id     TEXT NOT NULL,
    ingest_status        TEXT NOT NULL CHECK (ingest_status IN ('imported','needs_review','excluded','quarantined')),
    errors_json          TEXT CHECK (errors_json IS NULL OR json_valid(errors_json)),
    presentation_id      TEXT REFERENCES drug_presentations(id),
    normalized_projection_json TEXT CHECK (
        normalized_projection_json IS NULL OR json_valid(normalized_projection_json)
    ),
    normalized_projection_sha256 TEXT,
    previous_normalized_projection_sha256 TEXT,
    normalized_diff_json TEXT CHECK (
        normalized_diff_json IS NULL OR json_valid(normalized_diff_json)
    ),
    review_required      INTEGER NOT NULL DEFAULT 0 CHECK (review_required IN (0,1)),
    created_at           TEXT NOT NULL,
    UNIQUE (import_run_id, record_ordinal)
);
CREATE INDEX IF NOT EXISTS idx_drug_import_run_records_source_row
    ON drug_import_run_records (source_record_row_id, import_run_id);

CREATE TABLE IF NOT EXISTS drug_ingredients (
    id          TEXT PRIMARY KEY,
    name_raw    TEXT NOT NULL,
    name_norm   TEXT NOT NULL UNIQUE,
    name_search TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drug_presentation_ingredients (
    presentation_id TEXT NOT NULL REFERENCES drug_presentations(id) ON DELETE CASCADE,
    ingredient_id   TEXT NOT NULL REFERENCES drug_ingredients(id),
    ordinal         INTEGER NOT NULL,
    strength_raw    TEXT,
    strength_value  REAL,
    strength_unit   TEXT,
    basis_raw       TEXT,
    PRIMARY KEY (presentation_id, ordinal)
);

CREATE TABLE IF NOT EXISTS drug_aliases (
    id               TEXT PRIMARY KEY,
    presentation_id  TEXT NOT NULL REFERENCES drug_presentations(id) ON DELETE CASCADE,
    alias_raw        TEXT NOT NULL,
    alias_norm       TEXT NOT NULL,
    alias_search     TEXT NOT NULL,
    alias_type       TEXT NOT NULL DEFAULT 'brand',
    language         TEXT NOT NULL DEFAULT '',
    script           TEXT,
    review_status    TEXT NOT NULL DEFAULT 'unverified'
                     CHECK (review_status IN ('verified','unverified','needs_review')),
    source_record_id TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (presentation_id, alias_norm, alias_type, language)
);
CREATE INDEX IF NOT EXISTS idx_drug_aliases_search ON drug_aliases (alias_search);

CREATE TABLE IF NOT EXISTS drug_packages (
    id               TEXT PRIMARY KEY,
    presentation_id  TEXT NOT NULL REFERENCES drug_presentations(id) ON DELETE CASCADE,
    description      TEXT,
    quantity_value   REAL,
    quantity_unit    TEXT,
    package_form     TEXT,
    source_record_id TEXT,
    created_at       TEXT NOT NULL
);

-- 약사/데이터 운영자의 presentation 결정 이력. 현재 상태는 presentation에
-- projection하지만 이 원장은 UPDATE/DELETE할 수 없다.
CREATE TABLE IF NOT EXISTS drug_review_decisions (
    id                        TEXT PRIMARY KEY,
    presentation_id           TEXT NOT NULL REFERENCES drug_presentations(id),
    retirement_batch_id       TEXT,
    retirement_candidate_id   TEXT,
    source_id                 TEXT NOT NULL REFERENCES drug_sources(id),
    source_record_id          TEXT NOT NULL,
    source_record_sha256      TEXT,
    import_run_id             TEXT REFERENCES drug_import_runs(id),
    action                    TEXT NOT NULL CHECK (action IN (
        'review_requested','review_approved','review_rejected',
        'lifecycle_inactivated','lifecycle_reactivated',
        'source_record_updated','source_record_quarantined',
        'retirement_kept','retirement_marked',
        'retirement_investigation','retirement_applied','retirement_cancelled'
    )),
    previous_review_status    TEXT CHECK (
        previous_review_status IS NULL OR previous_review_status IN
        ('unverified','needs_review','approved','rejected')
    ),
    next_review_status        TEXT CHECK (
        next_review_status IS NULL OR next_review_status IN
        ('unverified','needs_review','approved','rejected')
    ),
    previous_lifecycle_status TEXT CHECK (
        previous_lifecycle_status IS NULL OR previous_lifecycle_status IN
        ('active','inactive','retired')
    ),
    next_lifecycle_status     TEXT CHECK (
        next_lifecycle_status IS NULL OR next_lifecycle_status IN
        ('active','inactive','retired')
    ),
    reason_code               TEXT NOT NULL,
    note                      TEXT NOT NULL,
    reviewer_id               TEXT NOT NULL,
    reviewer_role             TEXT NOT NULL,
    expected_record_version   INTEGER NOT NULL,
    resulting_record_version  INTEGER NOT NULL,
    normalized_snapshot_json  TEXT CHECK (
        normalized_snapshot_json IS NULL OR json_valid(normalized_snapshot_json)
    ),
    normalized_projection_sha256 TEXT,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drug_review_decisions_presentation
    ON drug_review_decisions (presentation_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS drug_review_decisions_no_update
BEFORE UPDATE ON drug_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'drug_review_decisions is append-only');
END;
CREATE TRIGGER IF NOT EXISTS drug_review_decisions_no_delete
BEFORE DELETE ON drug_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'drug_review_decisions is append-only');
END;

CREATE TABLE IF NOT EXISTS drug_retirement_batches (
    id                     TEXT PRIMARY KEY,
    source_id              TEXT NOT NULL REFERENCES drug_sources(id),
    import_run_id          TEXT NOT NULL UNIQUE REFERENCES drug_import_runs(id),
    baseline_import_run_id TEXT NOT NULL REFERENCES drug_import_runs(id),
    snapshot_mode          TEXT NOT NULL CHECK (snapshot_mode = 'full'),
    status                 TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed','under_review','approved','applied','cancelled')
    ),
    candidate_count        INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    created_by             TEXT NOT NULL,
    approved_by            TEXT,
    created_at             TEXT NOT NULL,
    approved_at            TEXT,
    applied_at             TEXT,
    cancelled_at           TEXT,
    reason                 TEXT,
    record_version         INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_drug_retirement_batches_source
    ON drug_retirement_batches (source_id, created_at DESC);

CREATE TABLE IF NOT EXISTS drug_retirement_candidates (
    id                            TEXT PRIMARY KEY,
    batch_id                      TEXT NOT NULL REFERENCES drug_retirement_batches(id) ON DELETE CASCADE,
    presentation_id               TEXT NOT NULL REFERENCES drug_presentations(id),
    source_record_id              TEXT NOT NULL,
    previous_lifecycle_status     TEXT NOT NULL CHECK (
        previous_lifecycle_status IN ('active','inactive','retired')
    ),
    expected_presentation_version INTEGER NOT NULL,
    decision                      TEXT NOT NULL DEFAULT 'pending' CHECK (
        decision IN ('pending','keep_active','retire','needs_investigation')
    ),
    reason_code                   TEXT,
    review_note                   TEXT,
    reviewer_id                   TEXT,
    reviewed_at                   TEXT,
    applied_at                    TEXT,
    record_version                INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1),
    UNIQUE (batch_id, presentation_id)
);
CREATE INDEX IF NOT EXISTS idx_drug_retirement_candidates_batch
    ON drug_retirement_candidates (batch_id, decision);

CREATE TABLE IF NOT EXISTS drug_retirement_batch_events (
    id                    TEXT PRIMARY KEY,
    batch_id              TEXT NOT NULL REFERENCES drug_retirement_batches(id),
    action                TEXT NOT NULL CHECK (action IN
        ('batch_created','candidate_decided','batch_approved','batch_applied','batch_cancelled')),
    previous_status       TEXT,
    next_status           TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    reason_code           TEXT NOT NULL,
    note                  TEXT NOT NULL,
    expected_batch_version INTEGER NOT NULL,
    resulting_batch_version INTEGER NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drug_retirement_batch_events_batch
    ON drug_retirement_batch_events (batch_id, created_at);
CREATE TRIGGER IF NOT EXISTS drug_retirement_batch_events_no_update
BEFORE UPDATE ON drug_retirement_batch_events
BEGIN
    SELECT RAISE(ABORT, 'drug_retirement_batch_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS drug_retirement_batch_events_no_delete
BEFORE DELETE ON drug_retirement_batch_events
BEGIN
    SELECT RAISE(ABORT, 'drug_retirement_batch_events is append-only');
END;

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


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    """기존 SQLite 파일에 additive column만 추가한다.

    테이블/컬럼/definition은 이 모듈의 상수만 전달한다. 사용자 입력을 받지 않는다.
    SQLite의 제한적인 ALTER TABLE을 벗어나는 재작성·재번호화는 하지 않는다.
    """
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path: str | Path | None = None) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        governance_migration_pending = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=5"
        ).fetchone() is None
        applied_run_migration_pending = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=7"
        ).fetchone() is None
        _ensure_column(conn, "prescriptions", "client_request_sha256", "TEXT")
        _ensure_column(conn, "prescription_items", "drug_input_raw", "TEXT")
        _ensure_column(
            conn,
            "prescription_items",
            "drug_match_state",
            "TEXT NOT NULL DEFAULT 'free_text' "
            "CHECK (drug_match_state IN ('free_text','selected','selected_then_modified'))",
        )
        _ensure_column(
            conn,
            "prescription_items",
            "drug_catalog_snapshot_json",
            "TEXT CHECK (drug_catalog_snapshot_json IS NULL OR json_valid(drug_catalog_snapshot_json))",
        )
        _ensure_column(
            conn,
            "prescription_items",
            "drug_selection_warning_json",
            "TEXT CHECK (drug_selection_warning_json IS NULL OR json_valid(drug_selection_warning_json))",
        )
        _ensure_column(
            conn,
            "prescription_items",
            "administration_route",
            "TEXT CHECK (administration_route IN "
            "('oral','ophthalmic','otic','nasal','inhalation','topical','rectal',"
            "'vaginal','transdermal','intravenous','intramuscular','subcutaneous','other') "
            "OR administration_route IS NULL)",
        )
        _ensure_column(conn, "drug_import_runs", "records_sha256", "TEXT")
        _ensure_column(conn, "drug_import_runs", "accessed_at", "TEXT")
        _ensure_column(
            conn,
            "drug_import_runs",
            "source_snapshot_json",
            "TEXT CHECK (source_snapshot_json IS NULL OR json_valid(source_snapshot_json))",
        )
        _ensure_column(
            conn,
            "drug_import_runs",
            "license_snapshot_json",
            "TEXT CHECK (license_snapshot_json IS NULL OR json_valid(license_snapshot_json))",
        )
        _ensure_column(
            conn,
            "drug_import_runs",
            "approval_snapshot_json",
            "TEXT CHECK (approval_snapshot_json IS NULL OR json_valid(approval_snapshot_json))",
        )
        _ensure_column(conn, "drug_import_runs", "approval_registry_sha256", "TEXT")
        _ensure_column(
            conn,
            "drug_import_runs",
            "normalization_version",
            "TEXT NOT NULL DEFAULT 'catalog-normalization-v1'",
        )
        _ensure_column(
            conn,
            "drug_import_runs",
            "package_schema_version",
            "TEXT NOT NULL DEFAULT '1'",
        )
        _ensure_column(
            conn,
            "drug_import_runs",
            "snapshot_mode",
            "TEXT NOT NULL DEFAULT 'delta' CHECK (snapshot_mode IN ('delta','full'))",
        )
        _ensure_column(conn, "drug_import_runs", "package_content_sha256", "TEXT")
        _ensure_column(conn, "drug_import_runs", "code_revision", "TEXT")
        _ensure_column(
            conn,
            "drug_presentations",
            "current_source_record_row_id",
            "TEXT REFERENCES drug_source_records(id)",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "current_import_run_record_id",
            "TEXT REFERENCES drug_import_run_records(id)",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "workflow_review_status",
            "TEXT NOT NULL DEFAULT 'unverified' CHECK (workflow_review_status IN "
            "('unverified','needs_review','approved','rejected'))",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "operational_lifecycle_status",
            "TEXT NOT NULL DEFAULT 'active' CHECK (operational_lifecycle_status IN "
            "('active','inactive','retired'))",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "record_version",
            "INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1)",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "normalized_projection_json",
            "TEXT CHECK (normalized_projection_json IS NULL OR "
            "json_valid(normalized_projection_json))",
        )
        _ensure_column(
            conn,
            "drug_presentations",
            "normalized_projection_sha256",
            "TEXT",
        )
        _ensure_column(
            conn,
            "drug_import_run_records",
            "normalized_projection_json",
            "TEXT CHECK (normalized_projection_json IS NULL OR "
            "json_valid(normalized_projection_json))",
        )
        _ensure_column(
            conn,
            "drug_import_run_records",
            "normalized_projection_sha256",
            "TEXT",
        )
        _ensure_column(
            conn,
            "drug_import_run_records",
            "previous_normalized_projection_sha256",
            "TEXT",
        )
        _ensure_column(
            conn,
            "drug_import_run_records",
            "normalized_diff_json",
            "TEXT CHECK (normalized_diff_json IS NULL OR json_valid(normalized_diff_json))",
        )
        _ensure_column(
            conn,
            "drug_import_run_records",
            "review_required",
            "INTEGER NOT NULL DEFAULT 0 CHECK (review_required IN (0,1))",
        )
        _ensure_column(
            conn,
            "drug_review_decisions",
            "normalized_snapshot_json",
            "TEXT CHECK (normalized_snapshot_json IS NULL OR "
            "json_valid(normalized_snapshot_json))",
        )
        _ensure_column(
            conn,
            "drug_review_decisions",
            "normalized_projection_sha256",
            "TEXT",
        )
        # One-time additive backfill. Re-running these statements at every startup would
        # silently overwrite later human review/lifecycle decisions without an audit row.
        if governance_migration_pending:
            # Source `verified` has no reviewer identity/audit proof, so it deliberately
            # does not become workflow `approved`. Restrictive source declarations seed
            # only the initial governance projection.
            conn.execute(
                """UPDATE drug_presentations
                   SET workflow_review_status='needs_review'
                   WHERE review_status='needs_review'
                     AND workflow_review_status='unverified'"""
            )
            conn.execute(
                """UPDATE drug_presentations
                   SET operational_lifecycle_status='inactive'
                   WHERE lifecycle_status='inactive'
                     AND operational_lifecycle_status='active'"""
            )
        if applied_run_migration_pending:
            conn.execute(
                """UPDATE drug_presentations AS p
                   SET current_import_run_record_id=(
                       SELECT irr.id
                       FROM drug_import_run_records irr
                       WHERE irr.presentation_id=p.id
                         AND irr.source_record_row_id=p.current_source_record_row_id
                         AND irr.ingest_status IN ('imported','needs_review')
                         AND (
                           p.normalized_projection_sha256 IS NULL
                           OR irr.normalized_projection_sha256=
                              p.normalized_projection_sha256
                         )
                       ORDER BY irr.rowid DESC
                       LIMIT 1
                   )
                   WHERE p.current_import_run_record_id IS NULL"""
            )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_drug_presentations_governance
               ON drug_presentations
                  (usage_scope, operational_lifecycle_status, workflow_review_status)"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (2, ?, ?, ?)""",
            ("source-aware drug catalog", "drug-catalog-v2-additive", now_utc()),
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (3, ?, ?, ?)""",
            (
                "catalog provenance snapshots and import-run record links",
                "drug-catalog-v3-provenance-additive",
                now_utc(),
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (4, ?, ?, ?)""",
            (
                "catalog database production/demo role",
                "drug-catalog-v4-database-mode",
                now_utc(),
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (5, ?, ?, ?)""",
            (
                "catalog review audit and full-snapshot retirement governance",
                "drug-catalog-v5-governance-additive",
                now_utc(),
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (6, ?, ?, ?)""",
            (
                "catalog normalized projection snapshots and reprocessing diffs",
                "drug-catalog-v6-normalized-audit-additive",
                now_utc(),
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (version, name, checksum, applied_at) VALUES (7, ?, ?, ?)""",
            (
                "catalog applied import-run record identity",
                "drug-catalog-v7-applied-run-record",
                now_utc(),
            ),
        )
        # 2026-07: OD_NIGHT의 설정 계약을 저녁(E)에서 밤(H)으로 바로잡았다.
        # 구 데이터 중 H가 비어 있고 E만 있는 명백한 레거시 행만 이동한다. E/H가
        # 모두 채워진 모호한 수기 데이터는 임상 의미를 추측하지 않고 그대로 둔다.
        conn.execute(
            "UPDATE prescription_items "
            "SET dose_night=dose_evening, dose_evening=0 "
            "WHERE pattern_key='OD_NIGHT' "
            "AND dose_night=0 AND dose_evening>0"
        )
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
    conn: sqlite3.Connection,
    presc_id: str,
    pharmacy_id: str,
    items: list[dict[str, Any]],
) -> int:
    """items를 prescription_items에 INSERT. 반환: max(duration_days)(만료 산정용).
    생성·수정(PUT) 공용 — 두 경로의 item 저장 규약이 갈라지지 않게 한다."""
    max_duration = 0
    for idx, item in enumerate(items):
        doses = item.get("doses") or {}
        duration = item.get("duration_days")
        if isinstance(duration, (int, float)):
            max_duration = max(max_duration, int(duration))
        drug_name_raw = str(item["drug_name_raw"]).strip()
        drug_input_raw = item.get("drug_input_raw")
        if not isinstance(drug_input_raw, str) or not drug_input_raw.strip():
            drug_input_raw = drug_name_raw
        else:
            drug_input_raw = drug_input_raw.strip()

        requested_drug_id = item.get("drug_id")
        requested_state = item.get("drug_match_state")
        if requested_state not in ("free_text", "selected", "selected_then_modified"):
            # 구 클라이언트는 match_state를 보내지 않는다. 유효 ID가 있으면 선택으로,
            # 아니면 자유 입력으로 해석하되 아래에서 반드시 서버 데이터로 재검증한다.
            requested_state = "selected" if requested_drug_id else "free_text"

        drug_id: str | None = None
        drug_snapshot: dict[str, Any] | None = None
        selection_warnings: list[str] = []
        match_state = requested_state

        if requested_state == "selected_then_modified":
            # 선택 뒤 이름이 바뀐 경우 잘못된 카탈로그 연결을 남기지 않는다.
            match_state = "selected_then_modified"
            selection_warnings.append("catalog_link_cleared_after_name_edit")
        elif requested_state == "selected" and requested_drug_id:
            from app.drug_catalog import get_presentation_snapshot, normalize_identity

            drug_snapshot = get_presentation_snapshot(conn, str(requested_drug_id))
            if drug_snapshot is None:
                # 신규 발급은 출처·scope가 검증된 v2 presentation만 선택으로 인정한다.
                # legacy drugs는 기존 발급 snapshot을 읽기 위한 호환 계층일 뿐이며,
                # 출처가 불명확한 ID를 production 선택으로 승격하지 않는다.
                match_state = "free_text"
                selection_warnings.append("catalog_id_not_found_free_text_preserved")
            elif (
                drug_snapshot.get("usage_scope") == "demo"
                and pharmacy_id != DEMO_CATALOG_PHARMACY_ID
            ):
                # API 검색 범위와 저장 경계가 같아야 한다. 다른 약국이 demo ID를
                # 직접 주입해도 처방은 중단하지 않고 자유 입력만 보존한다.
                drug_snapshot = None
                match_state = "free_text"
                selection_warnings.append(
                    "demo_catalog_not_available_for_pharmacy_free_text_preserved"
                )
            elif drug_snapshot.get("lifecycle_status") != "active":
                lifecycle_status = drug_snapshot.get("lifecycle_status")
                drug_snapshot = None
                match_state = "free_text"
                selection_warnings.append(
                    "inactive_catalog_link_cleared_free_text_preserved"
                    if lifecycle_status == "inactive"
                    else "retired_catalog_link_cleared_free_text_preserved"
                )
            elif drug_snapshot.get("review_status") == "rejected":
                drug_snapshot = None
                match_state = "free_text"
                selection_warnings.append(
                    "rejected_catalog_link_cleared_free_text_preserved"
                )
            elif normalize_identity(drug_name_raw) != normalize_identity(
                drug_snapshot.get("brand_name")
            ):
                # 클라이언트 상태가 오래됐거나 조작된 경우에도 수정된 표시명을 ID와
                # 결합하지 않는다. 표시명은 유지하고 원본 선택 링크만 해제한다.
                drug_snapshot = None
                match_state = "selected_then_modified"
                selection_warnings.append("catalog_name_mismatch_link_cleared")
            else:
                drug_id = str(requested_drug_id)
                match_state = "selected"
        elif requested_state == "selected":
            match_state = "free_text"
            selection_warnings.append("catalog_selection_missing_id_free_text_preserved")
        elif requested_drug_id:
            selection_warnings.append("catalog_id_ignored_for_free_text")

        if drug_snapshot is not None:
            review_status = drug_snapshot.get("review_status")
            if review_status == "needs_review":
                selection_warnings.append("catalog_record_needs_review")
            elif review_status == "unverified":
                selection_warnings.append("catalog_record_unverified")
            if drug_snapshot.get("usage_scope") == "demo":
                selection_warnings.append("demo_catalog_record")
            if drug_snapshot.get("lifecycle_status") != "active":
                selection_warnings.append("nonactive_catalog_record")
            if drug_snapshot.get("incomplete_fields"):
                selection_warnings.append("catalog_record_incomplete")
            unit_options = drug_snapshot.get("unit_options") or []
            if unit_options and item.get("dose_unit") not in unit_options:
                # 제형-단위 매핑은 처방 기본값이 아니다. 충돌을 기록할 뿐 입력을 바꾸지 않는다.
                selection_warnings.append("dose_unit_conflicts_with_catalog_form")

        extra = item.get("extra_params")
        conn.execute(
            """INSERT INTO prescription_items
               (id, prescription_id, position, drug_name_raw, drug_input_raw, drug_id,
                drug_match_state, drug_catalog_snapshot_json, drug_selection_warning_json,
                pattern_key,
                dose_morning, dose_noon, dose_evening, dose_night, dose_unit,
                administration_route, timing_food,
                duration_days, total_quantity, prn_reason_key, prn_max_per_day,
                prn_min_gap_hours, extra_params_json, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(), presc_id, item.get("position") or idx + 1,
                drug_name_raw, drug_input_raw, drug_id, match_state,
                json.dumps(drug_snapshot, ensure_ascii=False, sort_keys=True)
                if drug_snapshot else None,
                json.dumps(list(dict.fromkeys(selection_warnings)), ensure_ascii=False)
                if selection_warnings else None,
                item["pattern_key"],
                doses.get("M") or 0, doses.get("N") or 0, doses.get("E") or 0, doses.get("H") or 0,
                item["dose_unit"], item.get("administration_route"), item.get("timing_food"),
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
            reissue_of, client_request_sha256, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 'manual', ?, ?, ?, ?, ?, ?, ?)""",
        (
            presc_id, pharmacy_id, payload["client_input_id"], payload.get("patient_label"),
            payload.get("lang") or "hi", payload.get("note"), origin,
            metrics.get("input_duration_ms"), metrics.get("active_input_ms"),
            payload.get("issued_at_client"), reissue_of, client_request_sha256(payload), created,
        ),
    )

    max_duration = _insert_items_tx(conn, presc_id, pharmacy_id, items)

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


def _replay_or_conflict(
    conn: sqlite3.Connection, existing: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """멱등 replay 전 본문 대조(§4.1). 다르면 409 대상 — 조용한 replay는
    타임아웃 후 수정-재제출을 유실시킨다. 해시 없는 구 행은 비교 없이 replay(호환)."""
    stored = existing.get("client_request_sha256")
    if stored is not None and stored != client_request_sha256(payload):
        raise IdempotencyConflictError(existing["id"])
    return _issue_result(conn, existing, replayed=True)


def create_prescription(
    conn: sqlite3.Connection, pharmacy_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """처방 생성(멱등 — D10). payload는 §4.3 POST 본문 형태(dict).
    동일 (pharmacy_id, client_input_id) + 동일 본문이면 기존 발급 결과를 replayed=True로 반환,
    동일 멱등키 + 다른 본문이면 IdempotencyConflictError(§4.1).
    클라 사전생성 token 충돌 시 TokenCollisionError."""
    existing = find_by_client_input_id(conn, pharmacy_id, payload["client_input_id"])
    if existing is not None:
        return _replay_or_conflict(conn, existing, payload)
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
                return _replay_or_conflict(conn, existing, payload)
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
        "route": None,
        "release_modifier": None,
        "manufacturer": None,
        "marketing_company": None,
        "name_type": "brand",
        "review_status": "verified" if d["verified"] else "unverified",
        "lifecycle_status": "active",
        "usage_scope": "demo" if str(d.get("source") or "").startswith("legacy") else "production",
        "unit_options": [d["default_dose_unit"]] if d["default_dose_unit"] else [],
        "warnings": [],
        "incomplete_fields": [],
        # 내부 catalog caution은 약사 검색 정보다. 환자 노출은 별도 승인 필드만 허용한다.
        "patient_display_generic": False,
        "patient_caution_keys": [],
    }


def _item_dict(row: sqlite3.Row, drug: dict[str, Any] | None) -> dict[str, Any]:
    d = dict(row)
    snapshot = json.loads(d["drug_catalog_snapshot_json"]) \
        if d.get("drug_catalog_snapshot_json") else None
    selection_warnings = json.loads(d["drug_selection_warning_json"]) \
        if d.get("drug_selection_warning_json") else []
    return {
        "id": d["id"],
        "position": d["position"],
        "drug_name_raw": d["drug_name_raw"],
        "drug_input_raw": d.get("drug_input_raw") or d["drug_name_raw"],
        "drug_id": d["drug_id"],
        "drug_match_state": d.get("drug_match_state") or (
            "selected" if d["drug_id"] else "free_text"
        ),
        "drug_catalog_snapshot": snapshot,
        "drug_selection_warnings": selection_warnings,
        "drug": snapshot or drug,
        "pattern_key": d["pattern_key"],
        "doses": {"M": d["dose_morning"], "N": d["dose_noon"],
                  "E": d["dose_evening"], "H": d["dose_night"]},
        "dose_unit": d["dose_unit"],
        "administration_route": d.get("administration_route"),
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
        row_data = dict(item_row)
        drug = None
        if row_data.get("drug_catalog_snapshot_json"):
            # 발급 당시 약사가 확인한 immutable snapshot을 렌더한다. 이후 카탈로그 갱신은
            # 이미 발급된 처방의 표시명·제형·출처 경고를 소급 변경하지 않는다(§4.6).
            drug = json.loads(row_data["drug_catalog_snapshot_json"])
        # drug_id는 있으나 스냅샷이 없는 항목(스냅샷 컬럼 이전의 legacy 행)은 live
        # 카탈로그를 재조회하지 않는다 — 재조회는 이미 발급된 QR의 표시 내용을
        # 카탈로그 편집·retirement에 따라 조용히 바꾸므로 §4.6/INV-1·INV-3 위반이다.
        # 동결된 drug_name_raw 등 행 자체 컬럼만으로 렌더하고 enrichment는 생략한다.
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
    if old["prescription"]["status"] == "revoked" or old["access"]["revoked_at"]:
        # 이미 종결된 구건의 재발급은 폐기 시각·감사 이력을 덮어쓰고 대체본을
        # 분기시킨다(INV-10). 활성 대체본을 재발급해야 한다. 만료는 §4.4대로 허용.
        raise ValueError("cannot reissue a revoked prescription")

    now = now_utc()
    reason = payload.get("reason") or "other"
    try:
        # 1) 구건 종결 — 조건부 UPDATE: 동시 reissue 레이스에서도 종결은 정확히 1회
        cur = conn.execute(
            "UPDATE prescriptions SET status = 'revoked' "
            "WHERE id = ? AND status != 'revoked'",
            (prescription_id,),
        )
        if cur.rowcount != 1:
            raise ValueError("cannot reissue a revoked prescription")
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE prescription_id = ? AND revoked_at IS NULL",
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
    if old["token_status"] == TOKEN_STATUS_REVOKED:
        # 폐기된 처방은 부활하지 않는다(§5.2) — 수정도 불가. 재발급 경로를 쓴다.
        raise ValueError("cannot edit a revoked prescription")
    if old["token_status"] == TOKEN_STATUS_EXPIRED:
        # 만료 후 수정을 허용하면 4)의 expires_at 재산정이 죽은 토큰을 되살린다(§5.2
        # 금지 전이 expired→active). 만료 건은 재발급(신규 처방·신규 토큰) 경로만 유효.
        raise PrescriptionExpiredError(prescription_id)

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
        max_duration = _insert_items_tx(conn, prescription_id, pharmacy_id, items)

        # 3) 헤더 버전 업 + 선택 헤더 필드 갱신 (토큰 불변 — D4)
        #    status 조건: 위 가드 뒤 커밋 전에 reissue가 끼어든 레이스에서도
        #    폐기된 구건의 내용이 바뀌지 않게 한다.
        new_lang = payload.get("lang") if "lang" in payload else old_presc["lang"]
        new_label = payload.get("patient_label") if "patient_label" in payload else old_presc["patient_label"]
        new_note = payload.get("note") if "note" in payload else old_presc["note"]
        cur = conn.execute(
            "UPDATE prescriptions SET version = ?, lang = ?, patient_label = ?, note = ? "
            "WHERE id = ? AND status = 'active'",
            (new_version, new_lang, new_label, new_note, prescription_id),
        )
        if cur.rowcount != 1:
            raise ValueError("cannot edit a revoked prescription")
        # 4) 만료창 재산정(D6 — duration이 바뀌면 반영). token/URL은 그대로.
        #    expires_at 조건: 가드 통과 후 만료 경계를 넘은 경우에도 죽은 토큰을
        #    되살리지 않는다(§5.2 expired→active 금지 — 재산정은 살아 있는 토큰 한정).
        expires_at = compute_expires_at(old["access"]["created_at"], max_duration)
        cur = conn.execute(
            "UPDATE access_tokens SET expires_at = ? "
            "WHERE prescription_id = ? AND expires_at > ?",
            (expires_at, prescription_id, now),
        )
        if cur.rowcount != 1:
            raise PrescriptionExpiredError(prescription_id)

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

def search_drugs(
    conn: sqlite3.Connection, q: str, limit: int = 8, *, include_demo: bool = False
) -> list[dict[str, Any]]:
    """출처 승인 범위를 지키는 자동완성 검색.

    기본은 production-only다. v2 catalog가 없는 구 DB의 legacy 행은 provenance scope를
    판별할 수 없으므로 demo 컨텍스트에서만 LIKE fallback을 허용한다.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit), 20))
    from app.drug_catalog import search_catalog

    catalog_count = conn.execute(
        "SELECT COUNT(*) AS n FROM drug_presentations"
    ).fetchone()["n"]
    if catalog_count:
        return search_catalog(conn, q, limit=limit, include_demo=include_demo)

    if not include_demo:
        return []

    # 명시적 demo 컨텍스트에서만 구 설치의 입력 흐름을 위한 fallback을 허용한다.
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
    """구 seed/tests용 compatibility importer.

    입력은 Tier 3 demo source로 격리하고 v2 importer를 통과시킨다. 이전 default_*
    값은 복용 지시를 자동 결정할 수 있어 의도적으로 승계하지 않는다.
    """
    from app.drug_catalog import import_catalog

    source = {
        "slug": "legacy-db-import",
        "name": "indoro compatibility/demo medicine seed",
        "operator": "indoro",
        "tier": 3,
        "usage_scope": "demo",
        "reuse_status": "demo_only",
        "license_name": "Internal test/demo fixture only",
        "license_url": None,
        "attribution_text": "Demo data — not an approved production medicine source",
        "source_url": None,
        "legal_review_required": True,
        "version": "compat-v1",
    }
    records: list[dict[str, Any]] = []
    for d in drugs:
        key = "|".join((
            str(d.get("brand_name") or ""), str(d.get("strength") or ""),
            str(d.get("form") or ""),
        ))
        source_record_id = d.get("source_record_id") or (
            "legacy-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        )
        records.append({
            "source_record_id": str(source_record_id),
            "name_type": d.get("name_type") or "brand",
            "brand_name": d["brand_name"],
            "generic_name": d.get("generic_name"),
            "strength": d.get("strength"),
            "form": d.get("form"),
            "route": d.get("route"),
            "manufacturer": d.get("manufacturer"),
            "marketer": d.get("marketing_company") or d.get("marketer"),
            "package": d.get("package"),
            "aliases": d.get("aliases") or [],
            "caution_keys": d.get("caution_keys") or [],
            "review_status": "verified" if d.get("verified") else "needs_review",
            "status": "inactive" if d.get("status") == "inactive" else "active",
            "legacy_source_note": d.get("source"),
        })
    import_catalog(conn, source, records, dry_run=False)
    return len(records)


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
