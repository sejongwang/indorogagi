"""발급된 처방 스냅샷 불변성 회귀 (INV-1·INV-3, docs/01 §4.6 · docs/08 §4).

반례 CX-5: prescription_items.drug_id는 있으나 drug_catalog_snapshot_json이 NULL인
행(스냅샷 컬럼 도입 이전의 legacy 행이 정확히 이 형태)은, 환자 렌더 경로가
live drug_presentations를 재조회하므로 이후 카탈로그 편집·retirement가 이미 발급된
QR의 표시 내용을 조용히 바꾼다 — §4.6이 명시적으로 금지한 live 재조회.

두 개의 독립 헌터 에이전트가 db.py:1386-1395에서 수렴 확인했다.
"""
from __future__ import annotations

import uuid

from app import catalog_governance as governance
from app import db, drug_catalog

DEMO_PHARMACY = db.DEMO_CATALOG_PHARMACY_ID


def _review(**overrides):
    fields = {
        "reason_code": "human_review",
        "note": "Synthetic issue-boundary snapshot chain test.",
        "reviewer_id": "reviewer-1",
        "reviewer_role": "pharmacist reviewer",
    }
    fields.update(overrides)
    return fields


def _demo_source(version: str) -> dict:
    return {
        "slug": "synthetic-snapshot-tests",
        "name": "Synthetic snapshot tests",
        "operator": "indoro test suite",
        "tier": 3,
        "usage_scope": "demo",
        "reuse_status": "demo_only",
        "source_url": "https://example.invalid/snapshot-tests",
        "input_uri": f"snapshot-{version}.json",
        "version": version,
        "snapshot_mode": "delta",
        "package_schema_version": "2",
    }


def _demo_record(name: str) -> dict:
    return {
        "source_record_id": "snap-anchor",
        "brand_name": name,
        "form": "tablet",
        "route": "oral",
        "generic_name": f"Invented {name} Ingredient",
        "ingredients": [{"name": f"Invented {name} Ingredient", "strength": "10 mg"}],
        "strength": "10 mg",
        "review_status": "unverified",
    }


def _issue_selecting(conn, presentation_id: str, cid: str) -> str:
    payload = {
        "client_input_id": cid,
        "lang": "hi",
        "items": [
            {
                "position": 1,
                "drug_name_raw": "DEMO Original Brand",
                "drug_id": presentation_id,
                "drug_match_state": "selected",
                "pattern_key": "OD_MORNING",
                "doses": {"M": 1, "N": 0, "E": 0, "H": 0},
                "dose_unit": "tablet",
                "timing_food": "after_food",
                "duration_days": 5,
            }
        ],
    }
    result = db.create_prescription(conn, DEMO_PHARMACY, payload)
    return result["token"]


def test_issued_item_without_stored_snapshot_never_rerenders_from_live_catalog(
    test_db_path, db_conn
):
    """legacy 형태(drug_id 有, snapshot NULL) 항목이 카탈로그 변경에 영향받지 않는다."""
    conn = db_conn
    drug_catalog.import_catalog(
        conn, _demo_source("1"), [_demo_record("DEMO Original Brand")],
        database_mode="demo",
    )
    presentation_id = conn.execute(
        "SELECT id FROM drug_presentations WHERE source_record_id='snap-anchor'"
    ).fetchone()["id"]

    token = _issue_selecting(conn, presentation_id, str(uuid.uuid4()))

    # legacy 상태 재현: 스냅샷 컬럼을 NULL로 — 컬럼 도입 이전 발급 행과 동형
    conn.execute(
        "UPDATE prescription_items SET drug_catalog_snapshot_json = NULL "
        "WHERE drug_id = ?", (presentation_id,)
    )
    conn.commit()

    _, before = db.get_bundle_by_token(conn, token)
    item_before = before["items"][0]

    # 카탈로그를 상류에서 변경 — 이미 발급된 처방에는 영향이 없어야 한다
    conn.execute(
        "UPDATE drug_presentations SET brand_name_raw = 'DEMO Mutated Brand', "
        "brand_name_norm = 'demo mutated brand' WHERE id = ?", (presentation_id,)
    )
    conn.commit()

    _, after = db.get_bundle_by_token(conn, token)
    item_after = after["items"][0]

    # 렌더된 항목이 카탈로그 변경을 반영하면 INV-1/INV-3 위반
    assert item_after == item_before, (
        "발급된 항목이 live 카탈로그 재조회로 조용히 바뀌었다 (§4.6 위반)"
    )
    rendered = item_after.get("drug")
    if rendered is not None:
        assert "Mutated" not in str(rendered.get("brand_name", "")), (
            "발급 항목 렌더에 변경된 카탈로그 표시명이 새어들었다"
        )
    # 약사가 입력한 원문은 언제나 보존된다
    assert item_after["drug_name_raw"] == "DEMO Original Brand"


def test_issued_item_with_stored_snapshot_is_already_immutable(test_db_path, db_conn):
    """정상 경로(스냅샷 저장됨)는 이미 불변 — 회귀 가드로 함께 고정."""
    conn = db_conn
    drug_catalog.import_catalog(
        conn, _demo_source("1"), [_demo_record("DEMO Original Brand")],
        database_mode="demo",
    )
    presentation_id = conn.execute(
        "SELECT id FROM drug_presentations WHERE source_record_id='snap-anchor'"
    ).fetchone()["id"]
    token = _issue_selecting(conn, presentation_id, str(uuid.uuid4()))

    _, before = db.get_bundle_by_token(conn, token)
    conn.execute(
        "UPDATE drug_presentations SET brand_name_raw = 'DEMO Mutated Brand' WHERE id = ?",
        (presentation_id,),
    )
    conn.commit()
    _, after = db.get_bundle_by_token(conn, token)
    assert after["items"][0] == before["items"][0]
    assert "Mutated" not in after["items"][0]["drug"]["brand_name"]


def test_issuing_against_human_rejected_catalog_id_clears_link_preserves_free_text(
    test_db_path, db_conn
):
    """INV-8 발급 경계: 사람이 reject한 카탈로그 ID로 처방을 발급하면 카탈로그 링크를
    제거하고 약사 입력 원문만 보존한다 — rejected 사실이 발급 스냅샷에 박히지 않는다.

    이 안전 체인의 다른 분기(not-found/demo/inactive/retired)는 회귀 고정돼 있으나
    rejected 분기만 무커버리지였다(coverage-gap 헌터 지적)."""
    conn = db_conn
    drug_catalog.import_catalog(
        conn, _demo_source("1"), [_demo_record("DEMO Rejected Brand")],
        database_mode="demo",
    )
    row = conn.execute(
        "SELECT * FROM drug_presentations WHERE source_record_id='snap-anchor'"
    ).fetchone()
    presentation_id = row["id"]

    # 사람이 검색에서 차단: unverified → needs_review → rejected
    governance.transition_presentation(
        conn, presentation_id, action="review_requested",
        expected_record_version=row["record_version"], **_review(),
    )
    cur = conn.execute(
        "SELECT record_version FROM drug_presentations WHERE id=?", (presentation_id,)
    ).fetchone()["record_version"]
    governance.transition_presentation(
        conn, presentation_id, action="review_rejected",
        expected_record_version=cur, **_review(reason_code="dangerous_lookalike"),
    )

    token = _issue_selecting(conn, presentation_id, str(uuid.uuid4()))
    _, bundle = db.get_bundle_by_token(conn, token)
    item = bundle["items"][0]

    assert item["drug_id"] is None, "rejected 카탈로그 ID가 발급 스냅샷에 연결됐다"
    assert item["drug_match_state"] == "free_text"
    assert item["drug_catalog_snapshot"] is None
    assert item["drug_name_raw"] == "DEMO Original Brand"  # 약사 원문 보존
    assert "rejected_catalog_link_cleared_free_text_preserved" in (
        item.get("drug_selection_warnings") or []
    )
