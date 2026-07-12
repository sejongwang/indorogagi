"""Catalog review, audit, snapshot-retirement, and operations UI contracts."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import catalog_governance as governance
from app import db, drug_catalog
from scripts import catalog_governance_demo, catalog_import


def _source(version: str, *, snapshot_mode: str = "delta") -> dict:
    return {
        "slug": "synthetic-governance-tests",
        "name": "Synthetic governance tests",
        "operator": "indoro test suite",
        "tier": 3,
        "usage_scope": "demo",
        "reuse_status": "demo_only",
        "source_url": "https://example.invalid/synthetic-governance-tests",
        "input_uri": f"synthetic-governance-{version}.json",
        "version": version,
        "snapshot_mode": snapshot_mode,
        "package_schema_version": "2",
    }


def _record(source_record_id: str, name: str, *, complete: bool = True) -> dict:
    result = {
        "source_record_id": source_record_id,
        "brand_name": name,
        "form": "tablet",
        "route": "oral",
    }
    if complete:
        result.update(
            {
                "generic_name": f"Invented {name} Ingredient",
                "ingredients": [
                    {"name": f"Invented {name} Ingredient", "strength": "10 mg"}
                ],
                "strength": "10 mg",
                "review_status": "unverified",
            }
        )
    return result


@pytest.fixture()
def governance_conn(tmp_path):
    path = tmp_path / "governance.db"
    db.init_db(path)
    conn = db.get_conn(path)
    yield conn
    conn.close()


def _import(conn, version: str, records: list[dict], *, snapshot_mode: str = "delta"):
    return drug_catalog.import_catalog(
        conn,
        _source(version, snapshot_mode=snapshot_mode),
        records,
        database_mode="demo",
    )


def _presentation(conn, source_record_id: str):
    return conn.execute(
        "SELECT * FROM drug_presentations WHERE source_record_id=?", (source_record_id,)
    ).fetchone()


def _review_fields(**overrides):
    values = {
        "reason_code": "human_review",
        "note": "Synthetic catalog fields checked for test purposes.",
        "reviewer_id": "reviewer-1",
        "reviewer_role": "pharmacist reviewer",
    }
    values.update(overrides)
    return values


def test_review_transitions_are_atomic_append_only_and_optimistically_locked(
    governance_conn,
):
    conn = governance_conn
    _import(conn, "1", [_record("incomplete", "DEMO Incomplete", complete=False)])
    row = _presentation(conn, "incomplete")
    assert row["workflow_review_status"] == "needs_review"

    governance.transition_presentation(
        conn,
        row["id"],
        action="review_approved",
        expected_record_version=row["record_version"],
        **_review_fields(),
    )
    approved = _presentation(conn, "incomplete")
    assert approved["workflow_review_status"] == "approved"
    assert approved["record_version"] == row["record_version"] + 1
    audit = conn.execute(
        "SELECT * FROM drug_review_decisions WHERE presentation_id=?",
        (row["id"],),
    ).fetchone()
    assert audit["action"] == "review_approved"
    assert (audit["previous_review_status"], audit["next_review_status"]) == (
        "needs_review",
        "approved",
    )
    assert audit["normalized_projection_sha256"] == approved[
        "normalized_projection_sha256"
    ]
    approved_snapshot = json.loads(audit["normalized_snapshot_json"])
    assert approved_snapshot["brand_name_raw"] == "DEMO Incomplete"
    assert approved_snapshot == json.loads(approved["normalized_projection_json"])

    with pytest.raises(governance.StaleRecordVersion):
        governance.transition_presentation(
            conn,
            row["id"],
            action="review_rejected",
            expected_record_version=row["record_version"],
            **_review_fields(note="A competing stale decision."),
        )
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM drug_review_decisions WHERE presentation_id=?",
        (row["id"],),
    ).fetchone()["n"] == 1

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE drug_review_decisions SET note='mutated' WHERE id=?", (audit["id"],))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM drug_review_decisions WHERE id=?", (audit["id"],))
    conn.rollback()

    before = _presentation(conn, "incomplete")
    conn.execute(
        """CREATE TEMP TRIGGER fail_audit_insert BEFORE INSERT ON drug_review_decisions
           BEGIN SELECT RAISE(ABORT, 'forced audit failure'); END"""
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="forced audit failure"):
        governance.transition_presentation(
            conn,
            before["id"],
            action="review_requested",
            expected_record_version=before["record_version"],
            **_review_fields(note="This transaction must roll back."),
        )
    after = _presentation(conn, "incomplete")
    assert after["workflow_review_status"] == before["workflow_review_status"]
    assert after["record_version"] == before["record_version"]


def test_review_detail_and_decision_use_immutable_projection_ingredients(
    governance_conn,
):
    conn = governance_conn
    left = _record("ingredient-case-a", "DEMO Ingredient A")
    left["generic_name"] = "Paracetamol"
    left["ingredients"] = [{"name": "Paracetamol", "strength": "10 mg"}]
    right = _record("ingredient-case-b", "DEMO Ingredient B")
    right["generic_name"] = "PARACETAMOL"
    right["ingredients"] = [{"name": "PARACETAMOL", "strength": "10 mg"}]
    _import(conn, "1", [left, right])

    left_row = _presentation(conn, "ingredient-case-a")
    shared_ingredient = conn.execute(
        """SELECT i.name_raw
           FROM drug_presentation_ingredients pi
           JOIN drug_ingredients i ON i.id=pi.ingredient_id
           WHERE pi.presentation_id=?""",
        (left_row["id"],),
    ).fetchone()
    assert shared_ingredient["name_raw"] == "PARACETAMOL"

    detail = governance.get_review_detail(conn, left_row["id"])
    assert detail["ingredients"][0]["name_raw"] == "Paracetamol"
    governance.transition_presentation(
        conn,
        left_row["id"],
        action="review_requested",
        expected_record_version=left_row["record_version"],
        **_review_fields(reason_code="ingredient_projection_review"),
    )
    review_row = _presentation(conn, "ingredient-case-a")
    governance.transition_presentation(
        conn,
        review_row["id"],
        action="review_approved",
        expected_record_version=review_row["record_version"],
        **_review_fields(reason_code="ingredient_projection_approved"),
    )
    decision = conn.execute(
        """SELECT normalized_snapshot_json
           FROM drug_review_decisions
           WHERE presentation_id=? AND action='review_approved'
           ORDER BY rowid DESC LIMIT 1""",
        (left_row["id"],),
    ).fetchone()
    decision_snapshot = json.loads(decision["normalized_snapshot_json"])
    assert decision_snapshot["ingredients"][0]["name_raw"] == "Paracetamol"


def test_lifecycle_state_is_separate_and_retired_is_not_a_general_ui_transition(
    governance_conn,
):
    conn = governance_conn
    _import(conn, "1", [_record("active", "DEMO Active")])
    row = _presentation(conn, "active")
    governance.transition_presentation(
        conn,
        row["id"],
        action="lifecycle_inactivated",
        expected_record_version=row["record_version"],
        **_review_fields(reason_code="source_hold"),
    )
    inactive = _presentation(conn, "active")
    assert inactive["operational_lifecycle_status"] == "inactive"
    assert inactive["workflow_review_status"] == "unverified"
    assert drug_catalog.search_catalog(conn, "DEMO Active", include_demo=True) == []

    governance.transition_presentation(
        conn,
        inactive["id"],
        action="lifecycle_reactivated",
        expected_record_version=inactive["record_version"],
        **_review_fields(reason_code="source_hold_cleared"),
    )
    assert _presentation(conn, "active")["operational_lifecycle_status"] == "active"
    with pytest.raises(governance.InvalidStateTransition):
        governance.transition_presentation(
            conn,
            inactive["id"],
            action="retirement_applied",
            expected_record_version=_presentation(conn, "active")["record_version"],
            **_review_fields(),
        )


def test_later_import_invalidates_human_approval_without_reactivating_lifecycle(
    governance_conn,
):
    conn = governance_conn
    _import(conn, "1", [_record("anchor", "DEMO Anchor", complete=False)])
    first = _presentation(conn, "anchor")
    governance.transition_presentation(
        conn,
        first["id"],
        action="review_approved",
        expected_record_version=first["record_version"],
        **_review_fields(),
    )
    approved = _presentation(conn, "anchor")
    governance.transition_presentation(
        conn,
        approved["id"],
        action="lifecycle_inactivated",
        expected_record_version=approved["record_version"],
        **_review_fields(reason_code="temporary_hold"),
    )

    changed = _record("anchor", "DEMO Anchor Revised")
    report = _import(conn, "2", [changed])
    current = _presentation(conn, "anchor")
    assert current["brand_name_raw"] == "DEMO Anchor Revised"
    assert current["workflow_review_status"] == "needs_review"
    assert current["operational_lifecycle_status"] == "inactive"
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM drug_review_decisions
           WHERE presentation_id=? AND action='source_record_updated'""",
        (current["id"],),
    ).fetchone()["n"] == 1
    assert report["normalization_changed_count"] == 1
    assert report["normalization_unchanged_count"] == 0
    assert report["review_reopened_count"] == 1
    assert report["review_required_count"] == 1
    change = report["normalization_changes"][0]
    assert change["source_record_id"] == "anchor"
    assert "brand_name_raw" in change["changed_fields"]
    assert change["normalized_diff"]["brand_name_raw"] == {
        "previous": "DEMO Anchor",
        "next": "DEMO Anchor Revised",
    }
    run_record = conn.execute(
        """SELECT normalized_projection_sha256,
                  previous_normalized_projection_sha256,
                  normalized_diff_json, review_required
           FROM drug_import_run_records
           WHERE presentation_id=? ORDER BY rowid DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    assert run_record["normalized_projection_sha256"] == current[
        "normalized_projection_sha256"
    ]
    assert run_record["previous_normalized_projection_sha256"]
    assert json.loads(run_record["normalized_diff_json"])["brand_name_raw"] == {
        "previous": "DEMO Anchor",
        "next": "DEMO Anchor Revised",
    }
    assert run_record["review_required"] == 1


def test_excluded_later_source_payload_preserves_last_valid_fields_and_reopens_review(
    governance_conn,
):
    conn = governance_conn
    _import(
        conn,
        "1",
        [_record("anchor", "DEMO Valid Anchor")],
        snapshot_mode="full",
    )
    first = _presentation(conn, "anchor")
    governance.transition_presentation(
        conn,
        first["id"],
        action="review_requested",
        expected_record_version=first["record_version"],
        **_review_fields(reason_code="initial_review"),
    )
    first = _presentation(conn, "anchor")
    governance.transition_presentation(
        conn,
        first["id"],
        action="review_approved",
        expected_record_version=first["record_version"],
        **_review_fields(reason_code="initial_approval"),
    )
    approved = _presentation(conn, "anchor")
    original_source_row_id = approved["current_source_record_row_id"]

    report = _import(
        conn,
        "2",
        [{"source_record_id": "anchor", "brand_name": ""}],
        snapshot_mode="full",
    )
    assert report["excluded"] == 1
    assert report["retirement_batch_id"] is None
    current = _presentation(conn, "anchor")
    assert current["brand_name_raw"] == "DEMO Valid Anchor"
    assert current["current_source_record_row_id"] == original_source_row_id
    assert current["workflow_review_status"] == "needs_review"
    assert current["operational_lifecycle_status"] == "active"

    audit = conn.execute(
        """SELECT * FROM drug_review_decisions
           WHERE presentation_id=? AND action='source_record_quarantined'""",
        (current["id"],),
    ).fetchone()
    assert audit["previous_review_status"] == "approved"
    assert audit["next_review_status"] == "needs_review"
    failed_link = conn.execute(
        """SELECT irr.ingest_status, irr.presentation_id, sr.raw_json
           FROM drug_import_run_records irr
           JOIN drug_source_records sr ON sr.id=irr.source_record_row_id
           WHERE irr.import_run_id=?""",
        (audit["import_run_id"],),
    ).fetchone()
    assert failed_link["ingest_status"] == "excluded"
    assert failed_link["presentation_id"] == current["id"]
    assert json.loads(failed_link["raw_json"])["source_record_id"] == "anchor"

    queue_item = next(
        item
        for item in governance.list_review_queue(conn)
        if item["id"] == current["id"]
    )
    assert queue_item["import_run_id"] == audit["import_run_id"]
    assert queue_item["latest_ingest_status"] == "excluded"
    assert queue_item["latest_evidence_not_applied"] == 1
    assert queue_item["latest_ingest_errors"]
    assert governance.list_review_queue(
        conn, import_run=audit["import_run_id"]
    )[0]["id"] == current["id"]

    detail = governance.get_review_detail(conn, current["id"])
    assert detail["import_run_id"] != audit["import_run_id"]
    assert detail["latest_import_run_id"] == audit["import_run_id"]
    assert detail["latest_ingest_status"] == "excluded"
    assert detail["latest_evidence_is_current"] is False
    assert detail["raw_record"]["brand_name"] == "DEMO Valid Anchor"
    assert detail["latest_raw_record"]["brand_name"] == ""


def test_excluded_later_payload_preserves_human_rejection_and_search_block(
    governance_conn,
):
    conn = governance_conn
    _import(
        conn,
        "1",
        [_record("rejected-anchor", "DEMO Rejected Anchor", complete=False)],
        snapshot_mode="full",
    )
    row = _presentation(conn, "rejected-anchor")
    governance.transition_presentation(
        conn,
        row["id"],
        action="review_rejected",
        expected_record_version=row["record_version"],
        **_review_fields(reason_code="confirmed_rejection"),
    )
    rejected = _presentation(conn, "rejected-anchor")
    assert rejected["workflow_review_status"] == "rejected"
    assert drug_catalog.search_catalog(
        conn, "DEMO Rejected Anchor", include_demo=True
    ) == []

    report = _import(
        conn,
        "2",
        [{"source_record_id": "rejected-anchor", "brand_name": ""}],
        snapshot_mode="full",
    )
    current = _presentation(conn, "rejected-anchor")
    assert current["workflow_review_status"] == "rejected"
    assert current["operational_lifecycle_status"] == "active"
    assert report["review_reopened_count"] == 0
    assert report["review_required_count"] == 0
    assert drug_catalog.search_catalog(
        conn, "DEMO Rejected Anchor", include_demo=True
    ) == []
    audit = conn.execute(
        """SELECT * FROM drug_review_decisions
           WHERE presentation_id=? AND action='source_record_quarantined'
           ORDER BY rowid DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    assert audit["previous_review_status"] == "rejected"
    assert audit["next_review_status"] == "rejected"
    assert "rejection remains in force" in audit["note"]


def test_projection_change_preserves_human_rejection_and_search_block(
    governance_conn,
):
    """반례 트레이스(formal/spec.md S1): reject → 상류 projection 변경 임포트 → 검색 재유입.

    사람이 검색에서 차단한 레코드(rejected)를 자동 임포트가 needs_review로 되돌리면
    needs_review는 검색 가능 상태이므로 사람 개입 없이 차단이 풀린다(INV-8).
    excluded/quarantined 경로(위 테스트)와 동일하게 rejected는 유지돼야 하며,
    재검토는 사람의 review_requested 전이로만 연다."""
    conn = governance_conn
    _import(conn, "1", [_record("blocked", "DEMO Blocked", complete=False)])
    row = _presentation(conn, "blocked")
    governance.transition_presentation(
        conn,
        row["id"],
        action="review_rejected",
        expected_record_version=row["record_version"],
        **_review_fields(reason_code="dangerous_lookalike"),
    )
    rejected = _presentation(conn, "blocked")
    assert drug_catalog.search_catalog(conn, "DEMO Blocked", include_demo=True) == []

    # 상류가 표시 필드를 바꾼 후속 임포트 — projection 변경 경로(excluded 아님)
    report = _import(conn, "2", [_record("blocked", "DEMO Blocked Revised", complete=False)])
    current = _presentation(conn, "blocked")
    assert current["workflow_review_status"] == "rejected", (
        "projection 변경이 사람의 rejected를 자동으로 되돌리면 차단 레코드가 검색에 재유입된다"
    )
    assert drug_catalog.search_catalog(conn, "DEMO Blocked", include_demo=True) == []
    assert drug_catalog.search_catalog(
        conn, "DEMO Blocked Revised", include_demo=True
    ) == []
    assert report["review_reopened_count"] == 0

    # 근거(evidence)는 전진해야 한다: 버전 증가 + 감사 행 + 변경 diff 보존
    assert current["record_version"] == rejected["record_version"] + 1
    audit = conn.execute(
        """SELECT * FROM drug_review_decisions
           WHERE presentation_id=? AND action='source_record_updated'
           ORDER BY rowid DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    assert audit["previous_review_status"] == "rejected"
    assert audit["next_review_status"] == "rejected"

    # 재개는 사람의 몫: review_requested(rejected→needs_review)는 계속 가능해야 한다
    governance.transition_presentation(
        conn,
        current["id"],
        action="review_requested",
        expected_record_version=current["record_version"],
        **_review_fields(reason_code="source_changed_reconsider"),
    )
    reopened = _presentation(conn, "blocked")
    assert reopened["workflow_review_status"] == "needs_review"


def test_projection_change_still_reopens_human_approval(governance_conn):
    """대칭 확인: approved의 리셋(신뢰 하향 방향)은 유지된다 — 기존 계약 회귀 방지."""
    conn = governance_conn
    _import(conn, "1", [_record("anchor2", "DEMO Anchor Two", complete=False)])
    row = _presentation(conn, "anchor2")
    governance.transition_presentation(
        conn,
        row["id"],
        action="review_approved",
        expected_record_version=row["record_version"],
        **_review_fields(),
    )
    _import(conn, "2", [_record("anchor2", "DEMO Anchor Two Revised", complete=False)])
    assert _presentation(conn, "anchor2")["workflow_review_status"] == "needs_review"


def test_failed_same_raw_reprocess_does_not_replace_applied_run_identity(
    governance_conn, monkeypatch
):
    conn = governance_conn
    initial_normalization_version = drug_catalog.NORMALIZATION_VERSION
    record = _record("same-raw", "DEMO Same Raw", complete=False)
    _import(conn, "1", [record])
    first = _presentation(conn, "same-raw")
    governance.transition_presentation(
        conn,
        first["id"],
        action="review_approved",
        expected_record_version=first["record_version"],
        **_review_fields(reason_code="initial_projection_approved"),
    )
    applied = _presentation(conn, "same-raw")
    applied_run_record_id = applied["current_import_run_record_id"]
    applied_context = conn.execute(
        """SELECT import_run_id, source_record_row_id
           FROM drug_import_run_records WHERE id=?""",
        (applied_run_record_id,),
    ).fetchone()

    monkeypatch.setattr(
        drug_catalog,
        "NORMALIZATION_VERSION",
        f"{initial_normalization_version}-forced-persistence-failure",
    )

    def fail_presentation_write(*_args, **_kwargs):
        raise RuntimeError("forced persistence failure")

    monkeypatch.setattr(drug_catalog, "_write_presentation", fail_presentation_write)
    report = _import(conn, "2", [record])
    assert report["replayed"] is False
    assert report["quarantined"] == 1

    current = _presentation(conn, "same-raw")
    assert current["current_import_run_record_id"] == applied_run_record_id
    assert current["workflow_review_status"] == "needs_review"
    latest = conn.execute(
        """SELECT * FROM drug_import_run_records
           WHERE presentation_id=? ORDER BY rowid DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    assert latest["id"] != applied_run_record_id
    assert latest["source_record_row_id"] == applied_context["source_record_row_id"]
    assert latest["ingest_status"] == "quarantined"

    detail = governance.get_review_detail(conn, current["id"])
    assert detail["import_run_id"] == applied_context["import_run_id"]
    assert detail["latest_import_run_id"] == latest["import_run_id"]
    assert detail["latest_evidence_is_current"] is False
    queue_item = next(
        item
        for item in governance.list_review_queue(conn)
        if item["id"] == current["id"]
    )
    assert queue_item["latest_evidence_not_applied"] == 1

    governance.transition_presentation(
        conn,
        current["id"],
        action="review_approved",
        expected_record_version=current["record_version"],
        **_review_fields(reason_code="last_applied_projection_reapproved"),
    )
    decision = conn.execute(
        """SELECT import_run_id FROM drug_review_decisions
           WHERE presentation_id=? AND action='review_approved'
           ORDER BY rowid DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    assert decision["import_run_id"] == applied_context["import_run_id"]
    snapshot = drug_catalog.get_presentation_snapshot(conn, current["id"])
    assert snapshot["provenance"]["normalization_version"] == (
        initial_normalization_version
    )


def test_delta_first_full_and_followup_full_retirement_workflow(governance_conn):
    conn = governance_conn
    records = [
        _record("stay", "DEMO Stay"),
        _record("keep", "DEMO Keep"),
        _record("retire", "DEMO Retire"),
    ]
    first = _import(conn, "1", records, snapshot_mode="full")
    assert first["retirement_baseline_missing"] is True
    assert first["retirement_candidate_count"] == 0

    delta = _import(conn, "1.1", [records[0]], snapshot_mode="delta")
    assert delta["retirement_batch_id"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM drug_retirement_batches").fetchone()["n"] == 0

    second = _import(conn, "2", [records[0]], snapshot_mode="full")
    assert second["retirement_baseline_missing"] is False
    assert second["retirement_candidate_count"] == 2
    batch = governance.get_retirement_batch(conn, second["retirement_batch_id"])
    version_before_candidate_review = batch["record_version"]
    assert batch["status"] == "proposed"
    assert all(item["decision"] == "pending" for item in batch["candidates"])
    assert _presentation(conn, "retire")["operational_lifecycle_status"] == "active"
    assert drug_catalog.search_catalog(conn, "DEMO Retire", include_demo=True)

    with pytest.raises(governance.InvalidStateTransition, match="unresolved"):
        governance.approve_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(),
        )

    by_source_id = {item["source_record_id"]: item for item in batch["candidates"]}
    keep = by_source_id["keep"]
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        keep["id"],
        decision="keep_active",
        expected_candidate_version=keep["record_version"],
        expected_presentation_version=keep["current_presentation_version"],
        **_review_fields(reason_code="source_omission_investigated"),
    )
    with pytest.raises(governance.StaleRecordVersion):
        governance.approve_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=version_before_candidate_review,
            **_review_fields(reason_code="stale_batch_view"),
        )
    batch = governance.get_retirement_batch(conn, batch["id"])
    retire = next(item for item in batch["candidates"] if item["source_record_id"] == "retire")
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        retire["id"],
        decision="needs_investigation",
        expected_candidate_version=retire["record_version"],
        expected_presentation_version=retire["current_presentation_version"],
        **_review_fields(reason_code="more_evidence_needed"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    with pytest.raises(governance.InvalidStateTransition, match="unresolved"):
        governance.approve_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(),
        )
    retire = next(item for item in batch["candidates"] if item["source_record_id"] == "retire")
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        retire["id"],
        decision="retire",
        expected_candidate_version=retire["record_version"],
        expected_presentation_version=retire["current_presentation_version"],
        **_review_fields(reason_code="confirmed_missing_from_full_snapshot"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.approve_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="all_candidates_resolved"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    assert _presentation(conn, "retire")["operational_lifecycle_status"] == "active"
    governance.apply_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="approved_batch_apply"),
    )
    applied = governance.get_retirement_batch(conn, batch["id"])
    assert applied["status"] == "applied"
    assert _presentation(conn, "retire")["operational_lifecycle_status"] == "retired"
    assert _presentation(conn, "keep")["operational_lifecycle_status"] == "active"
    assert drug_catalog.search_catalog(conn, "DEMO Retire", include_demo=True) == []
    version = _presentation(conn, "retire")["record_version"]
    audit_count = conn.execute(
        """SELECT COUNT(*) AS n FROM drug_review_decisions
           WHERE presentation_id=? AND action='retirement_applied'""",
        (_presentation(conn, "retire")["id"],),
    ).fetchone()["n"]
    governance.apply_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="idempotent_retry"),
    )
    assert _presentation(conn, "retire")["record_version"] == version
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM drug_review_decisions
           WHERE presentation_id=? AND action='retirement_applied'""",
        (_presentation(conn, "retire")["id"],),
    ).fetchone()["n"] == audit_count

    batch_event = conn.execute(
        """SELECT * FROM drug_retirement_batch_events
           WHERE batch_id=? AND action='batch_applied'""",
        (batch["id"],),
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE drug_retirement_batch_events SET note='mutated' WHERE id=?",
            (batch_event["id"],),
        )
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "DELETE FROM drug_retirement_batch_events WHERE id=?",
            (batch_event["id"],),
        )
    conn.rollback()
    event_versions = [
        (event["expected_batch_version"], event["resulting_batch_version"])
        for event in governance.get_retirement_batch(conn, batch["id"])["events"]
    ]
    assert event_versions[0] == (0, 1)
    assert all(
        next_expected == previous_result
        for (_, previous_result), (next_expected, _) in zip(
            event_versions, event_versions[1:]
        )
    )
    assert event_versions[-1][1] == governance.get_retirement_batch(
        conn, batch["id"]
    )["record_version"]


def test_cancelled_omission_is_reproposed_by_a_later_full_snapshot(governance_conn):
    conn = governance_conn
    stay = _record("stay", "DEMO Stay")
    omitted = _record("omitted", "DEMO Omitted")
    _import(conn, "1", [stay, omitted], snapshot_mode="full")
    second = _import(conn, "2", [stay], snapshot_mode="full")
    first_batch = governance.get_retirement_batch(conn, second["retirement_batch_id"])
    governance.cancel_retirement_batch(
        conn,
        first_batch["id"],
        expected_batch_version=first_batch["record_version"],
        **_review_fields(reason_code="insufficient_evidence"),
    )
    assert _presentation(conn, "omitted")["operational_lifecycle_status"] == "active"

    third = _import(conn, "3", [stay], snapshot_mode="full")
    assert third["retirement_batch_id"] is not None
    assert third["retirement_batch_id"] != first_batch["id"]
    next_batch = governance.get_retirement_batch(conn, third["retirement_batch_id"])
    assert [item["source_record_id"] for item in next_batch["candidates"]] == [
        "omitted"
    ]
    assert next_batch["candidates"][0]["decision"] == "pending"


def test_retirement_apply_rejects_stale_presentation_as_one_transaction(governance_conn):
    conn = governance_conn
    records = [_record("stay", "DEMO Stay"), _record("gone", "DEMO Gone")]
    _import(conn, "1", records, snapshot_mode="full")
    report = _import(conn, "2", records[:1], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    candidate = batch["candidates"][0]
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        candidate["id"],
        decision="retire",
        expected_candidate_version=candidate["record_version"],
        expected_presentation_version=candidate["current_presentation_version"],
        **_review_fields(),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.approve_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(),
    )
    gone = _presentation(conn, "gone")
    governance.transition_presentation(
        conn,
        gone["id"],
        action="review_requested",
        expected_record_version=gone["record_version"],
        **_review_fields(),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    with pytest.raises(governance.StaleRecordVersion):
        governance.apply_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(),
        )
    assert governance.get_retirement_batch(conn, batch["id"])["status"] == "approved"
    assert _presentation(conn, "gone")["operational_lifecycle_status"] == "active"


def test_retirement_approval_rejects_newer_presentation_evidence(governance_conn):
    conn = governance_conn
    records = [_record("stay", "DEMO Stay"), _record("gone", "DEMO Gone")]
    _import(conn, "1", records, snapshot_mode="full")
    report = _import(conn, "2", records[:1], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    candidate = batch["candidates"][0]
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        candidate["id"],
        decision="retire",
        expected_candidate_version=candidate["record_version"],
        expected_presentation_version=candidate["current_presentation_version"],
        **_review_fields(reason_code="confirmed_snapshot_omission"),
    )
    gone = _presentation(conn, "gone")
    governance.transition_presentation(
        conn,
        gone["id"],
        action="review_requested",
        expected_record_version=gone["record_version"],
        **_review_fields(reason_code="newer_source_evidence"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    event_count = len(batch["events"])
    with pytest.raises(governance.StaleRecordVersion, match="newer catalog evidence"):
        governance.approve_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(reason_code="must_not_approve_stale_evidence"),
        )
    current = governance.get_retirement_batch(conn, batch["id"])
    assert current["status"] == "under_review"
    assert len(current["events"]) == event_count


def test_retirement_apply_rolls_back_every_projection_when_audit_insert_fails(
    governance_conn,
):
    conn = governance_conn
    records = [
        _record("stay", "DEMO Stay"),
        _record("gone-a", "DEMO Gone A"),
        _record("gone-b", "DEMO Gone B"),
    ]
    _import(conn, "1", records, snapshot_mode="full")
    report = _import(conn, "2", records[:1], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    for candidate in list(batch["candidates"]):
        governance.decide_retirement_candidate(
            conn,
            batch["id"],
            candidate["id"],
            decision="retire",
            expected_candidate_version=candidate["record_version"],
            expected_presentation_version=candidate["current_presentation_version"],
            **_review_fields(reason_code="confirmed_snapshot_omission"),
        )
        batch = governance.get_retirement_batch(conn, batch["id"])
    governance.approve_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="batch_resolved"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    presentation_versions = {
        item["presentation_id"]: item["current_presentation_version"]
        for item in batch["candidates"]
    }
    conn.execute(
        """CREATE TEMP TRIGGER fail_retirement_audit
           BEFORE INSERT ON drug_review_decisions
           WHEN NEW.action='retirement_applied'
           BEGIN SELECT RAISE(ABORT, 'forced retirement audit failure'); END"""
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced retirement audit failure"):
        governance.apply_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(reason_code="atomic_apply"),
        )
    unchanged = governance.get_retirement_batch(conn, batch["id"])
    assert unchanged["status"] == "approved"
    assert unchanged["record_version"] == batch["record_version"]
    assert all(item["applied_at"] is None for item in unchanged["candidates"])
    for presentation_id, version in presentation_versions.items():
        row = conn.execute(
            "SELECT operational_lifecycle_status, record_version FROM drug_presentations WHERE id=?",
            (presentation_id,),
        ).fetchone()
        assert row["operational_lifecycle_status"] == "active"
        assert row["record_version"] == version
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM drug_review_decisions
           WHERE retirement_batch_id=? AND action='retirement_applied'""",
        (batch["id"],),
    ).fetchone()["n"] == 0
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM drug_retirement_batch_events
           WHERE batch_id=? AND action='batch_applied'""",
        (batch["id"],),
    ).fetchone()["n"] == 0


def test_source_record_reappearance_invalidates_older_retirement_candidate(
    governance_conn,
):
    conn = governance_conn
    stay = _record("stay", "DEMO Stay")
    reappeared = _record("reappeared", "DEMO Reappeared")
    _import(conn, "1", [stay, reappeared], snapshot_mode="full")
    report = _import(conn, "2", [stay], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    candidate = batch["candidates"][0]

    # The next full source release restores the same stable source ID. The older
    # omission candidate is no longer valid, even if a reviewer loads its latest page.
    third = _import(conn, "3", [stay, reappeared], snapshot_mode="full")
    assert third["retirement_candidate_count"] == 0
    refreshed = governance.get_retirement_batch(conn, batch["id"])["candidates"][0]
    assert refreshed["current_presentation_version"] > refreshed[
        "expected_presentation_version"
    ]
    with pytest.raises(governance.StaleRecordVersion, match="candidate was created"):
        governance.decide_retirement_candidate(
            conn,
            batch["id"],
            candidate["id"],
            decision="retire",
            expected_candidate_version=candidate["record_version"],
            expected_presentation_version=refreshed["current_presentation_version"],
            **_review_fields(reason_code="obsolete_omission_candidate"),
        )
    unchanged = governance.get_retirement_batch(conn, batch["id"])
    assert unchanged["candidates"][0]["decision"] == "pending"
    assert _presentation(conn, "reappeared")["operational_lifecycle_status"] == "active"


def test_retirement_preserves_issued_snapshot_and_blocks_new_catalog_link(governance_conn):
    conn = governance_conn
    records = [_record("stay", "DEMO Stay"), _record("issued", "DEMO Issued")]
    _import(conn, "1", records, snapshot_mode="full")
    issued = _presentation(conn, "issued")
    db.upsert_pharmacy(
        conn,
        {"id": db.DEMO_CATALOG_PHARMACY_ID, "name": "Synthetic Snapshot Pharmacy"},
    )

    def payload(client_input_id: str):
        return {
            "client_input_id": client_input_id,
            "lang": "en",
            "items": [
                {
                    "position": 1,
                    "drug_name_raw": "DEMO Issued",
                    "drug_input_raw": "DEMO Issued",
                    "drug_id": issued["id"],
                    "drug_match_state": "selected",
                    "pattern_key": "OD_MORNING",
                    "doses": {"M": 1, "N": 0, "E": 0, "H": 0},
                    "dose_unit": "tablet",
                    "timing_food": "after_food",
                    "duration_days": 5,
                }
            ],
        }

    first_rx = db.create_prescription(
        conn, db.DEMO_CATALOG_PHARMACY_ID, payload("snapshot-before-retirement")
    )
    snapshot_before = conn.execute(
        """SELECT drug_catalog_snapshot_json FROM prescription_items
           WHERE prescription_id=?""",
        (first_rx["id"],),
    ).fetchone()["drug_catalog_snapshot_json"]
    assert json.loads(snapshot_before)["brand_name"] == "DEMO Issued"

    report = _import(conn, "2", records[:1], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    candidate = batch["candidates"][0]
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        candidate["id"],
        decision="retire",
        expected_candidate_version=candidate["record_version"],
        expected_presentation_version=candidate["current_presentation_version"],
        **_review_fields(),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.approve_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.apply_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(),
    )
    assert conn.execute(
        """SELECT drug_catalog_snapshot_json FROM prescription_items
           WHERE prescription_id=?""",
        (first_rx["id"],),
    ).fetchone()["drug_catalog_snapshot_json"] == snapshot_before
    assert db.get_prescription_bundle(conn, first_rx["id"])["items"][0][
        "drug_catalog_snapshot"
    ]["brand_name"] == "DEMO Issued"

    second_rx = db.create_prescription(
        conn, db.DEMO_CATALOG_PHARMACY_ID, payload("snapshot-after-retirement")
    )
    new_item = conn.execute(
        """SELECT drug_id, drug_match_state, drug_catalog_snapshot_json,
                  drug_selection_warning_json
           FROM prescription_items WHERE prescription_id=?""",
        (second_rx["id"],),
    ).fetchone()
    assert new_item["drug_id"] is None
    assert new_item["drug_match_state"] == "free_text"
    assert new_item["drug_catalog_snapshot_json"] is None
    assert "retired_catalog_link_cleared" in new_item["drug_selection_warning_json"]


def test_retirement_keeps_existing_qr_and_english_hindi_patient_pages_immutable(
    tmp_path, monkeypatch
):
    path = tmp_path / "retirement-patient-regression.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    conn = db.get_conn(path)
    try:
        stay = _record("patient-stay", "DEMO Patient Stay")
        issued_record = _record("patient-issued", "DEMO Patient Issued")
        _import(conn, "1", [stay, issued_record], snapshot_mode="full")
        issued_presentation = _presentation(conn, "patient-issued")
        db.upsert_pharmacy(
            conn,
            {
                "id": db.DEMO_CATALOG_PHARMACY_ID,
                "name": "Synthetic Patient Regression Pharmacy",
                "area": "Synthetic fixture only",
            },
        )
    finally:
        conn.close()

    from app.main import create_app

    payload = {
        "client_input_id": "governance-patient-snapshot",
        "lang": "hi",
        "patient_label": "Synthetic patient",
        "items": [
            {
                "position": 1,
                "drug_name_raw": "DEMO Patient Issued",
                "drug_input_raw": "DEMO Patient Issued",
                "drug_id": issued_presentation["id"],
                "drug_match_state": "selected",
                "pattern_key": "OD_MORNING",
                "doses": {"M": 1, "N": 0, "E": 0, "H": 0},
                "dose_unit": "tablet",
                "timing_food": "after_food",
                "duration_days": 5,
            }
        ],
    }
    headers = {"X-Pharmacy-Id": db.DEMO_CATALOG_PHARMACY_ID}
    with TestClient(create_app()) as client:
        issued = client.post("/api/prescriptions", json=payload, headers=headers)
        assert issued.status_code == 201
        issued_json = issued.json()

        conn = db.get_conn(path)
        try:
            report = _import(conn, "2", [stay], snapshot_mode="full")
            batch = governance.get_retirement_batch(
                conn, report["retirement_batch_id"]
            )
            candidate = next(
                item
                for item in batch["candidates"]
                if item["source_record_id"] == "patient-issued"
            )
            governance.decide_retirement_candidate(
                conn,
                batch["id"],
                candidate["id"],
                decision="retire",
                expected_candidate_version=candidate["record_version"],
                expected_presentation_version=candidate[
                    "current_presentation_version"
                ],
                **_review_fields(reason_code="confirmed_snapshot_omission"),
            )
            batch = governance.get_retirement_batch(conn, batch["id"])
            governance.approve_retirement_batch(
                conn,
                batch["id"],
                expected_batch_version=batch["record_version"],
                **_review_fields(reason_code="batch_resolved"),
            )
            batch = governance.get_retirement_batch(conn, batch["id"])
            governance.apply_retirement_batch(
                conn,
                batch["id"],
                expected_batch_version=batch["record_version"],
                **_review_fields(reason_code="batch_applied"),
            )
        finally:
            conn.close()

        detail = client.get(
            f"/api/prescriptions/{issued_json['id']}", headers=headers
        )
        qr = client.get(
            f"/api/prescriptions/{issued_json['id']}/qr", headers=headers
        )
        patient_hi = client.get(f"/p/{issued_json['token']}?lang=hi")
        patient_en = client.get(f"/p/{issued_json['token']}?lang=en")
        assert detail.status_code == qr.status_code == 200
        assert qr.json()["token"] == issued_json["token"]
        assert qr.json()["url"].endswith(f"/p/{issued_json['token']}")
        assert detail.json()["items"][0]["drug_catalog_snapshot"][
            "brand_name"
        ] == "DEMO Patient Issued"
        assert patient_hi.status_code == patient_en.status_code == 200
        assert "DEMO Patient Issued" in patient_hi.text
        assert "DEMO Patient Issued" in patient_en.text

        conn = db.get_conn(path)
        try:
            assert drug_catalog.search_catalog(
                conn, "DEMO Patient Issued", include_demo=True
            ) == []
        finally:
            conn.close()


def test_replay_identity_includes_importer_and_normalization_versions(
    governance_conn, monkeypatch
):
    conn = governance_conn
    source = {**_source("1"), "code_revision": "test-commit-abc123"}
    records = [_record("versioned", "DEMO Versioned")]
    initial_importer_version = drug_catalog.IMPORTER_VERSION
    initial_normalization_version = drug_catalog.NORMALIZATION_VERSION
    first = drug_catalog.import_catalog(conn, source, records, database_mode="demo")
    replay = drug_catalog.import_catalog(conn, source, records, database_mode="demo")
    assert first["replayed"] is False
    assert replay["replayed"] is True
    row = _presentation(conn, "versioned")
    governance.transition_presentation(
        conn,
        row["id"],
        action="review_requested",
        expected_record_version=row["record_version"],
        **_review_fields(reason_code="versioned_review_requested"),
    )
    row = _presentation(conn, "versioned")
    governance.transition_presentation(
        conn,
        row["id"],
        action="review_approved",
        expected_record_version=row["record_version"],
        **_review_fields(reason_code="versioned_projection_approved"),
    )
    approved = _presentation(conn, "versioned")
    monkeypatch.setattr(drug_catalog, "IMPORTER_VERSION", "drug-catalog-v-next")
    second = drug_catalog.import_catalog(conn, source, records, database_mode="demo")
    assert second["replayed"] is False
    after_importer_change = _presentation(conn, "versioned")
    assert after_importer_change["workflow_review_status"] == "approved"
    assert after_importer_change["record_version"] == approved["record_version"] + 1
    assert second["normalization_unchanged_count"] == 1
    assert second["normalization_changed_count"] == 0
    assert second["review_reopened_count"] == 0
    assert second["review_required_count"] == 0
    assert second["normalization_changes"] == []
    monkeypatch.setattr(drug_catalog, "NORMALIZATION_VERSION", "catalog-normalization-v-next")
    third = drug_catalog.import_catalog(conn, source, records, database_mode="demo")
    assert third["replayed"] is False
    after_normalization_change = _presentation(conn, "versioned")
    assert after_normalization_change["workflow_review_status"] == "approved"
    assert third["normalization_unchanged_count"] == 1
    assert third["normalization_changed_count"] == 0
    latest_run_record = conn.execute(
        """SELECT normalized_projection_sha256,
                  previous_normalized_projection_sha256,
                  normalized_diff_json, review_required
           FROM drug_import_run_records
           WHERE presentation_id=? ORDER BY rowid DESC LIMIT 1""",
        (after_normalization_change["id"],),
    ).fetchone()
    assert latest_run_record["normalized_projection_sha256"] == latest_run_record[
        "previous_normalized_projection_sha256"
    ]
    assert json.loads(latest_run_record["normalized_diff_json"]) == {}
    assert latest_run_record["review_required"] == 0
    refresh_events = conn.execute(
        """SELECT previous_review_status, next_review_status,
                  normalized_projection_sha256, normalized_snapshot_json
           FROM drug_review_decisions
           WHERE presentation_id=? AND action='source_record_updated'
           ORDER BY rowid""",
        (after_normalization_change["id"],),
    ).fetchall()
    assert len(refresh_events) == 2
    assert all(
        (event["previous_review_status"], event["next_review_status"])
        == ("approved", "approved")
        for event in refresh_events
    )
    assert all(
        event["normalized_projection_sha256"]
        == after_normalization_change["normalized_projection_sha256"]
        for event in refresh_events
    )
    assert all(event["normalized_snapshot_json"] for event in refresh_events)
    runs = conn.execute(
        """SELECT importer_version, normalization_version, package_schema_version,
                  snapshot_mode, package_content_sha256, code_revision
           FROM drug_import_runs ORDER BY rowid"""
    ).fetchall()
    assert len(runs) == 3
    assert {row["importer_version"] for row in runs} == {
        initial_importer_version,
        "drug-catalog-v-next",
    }
    assert {row["normalization_version"] for row in runs} == {
        initial_normalization_version,
        "catalog-normalization-v-next",
    }
    assert all(row["package_schema_version"] == "2" for row in runs)
    assert all(row["snapshot_mode"] == "delta" for row in runs)
    assert all(row["package_content_sha256"] for row in runs)
    assert all(row["code_revision"] == "test-commit-abc123" for row in runs)


def test_dangerous_similar_name_filter_applies_before_limit(governance_conn):
    conn = governance_conn
    distractors = [
        _record("noise-a", "Aegis", complete=False),
        _record("noise-b", "Borealis", complete=False),
        _record("noise-c", "Cygnus", complete=False),
        _record("noise-d", "Drakon", complete=False),
    ]
    similar_left = _record("similar-left", "Zetamine", complete=False)
    similar_right = _record("similar-right", "Zetamime", complete=False)
    _import(conn, "1", distractors + [similar_left, similar_right])
    target_ids = {
        _presentation(conn, "similar-left")["id"],
        _presentation(conn, "similar-right")["id"],
    }
    candidates = drug_catalog.build_quality_report(conn)[
        "dangerous_similar_name_candidates"
    ]
    assert any(
        {candidate["left_id"], candidate["right_id"]} == target_ids
        for candidate in candidates
    )

    results = governance.list_review_queue(
        conn, issue="dangerous_similar_name", limit=2
    )
    assert {item["id"] for item in results} == target_ids


def test_read_only_retirement_preview_reports_missing_without_creating_batch(tmp_path):
    path = tmp_path / "preview.db"
    db.init_db(path)
    conn = db.get_conn(path)
    records = [_record("stay", "DEMO Stay"), _record("missing", "DEMO Missing")]
    try:
        drug_catalog.import_catalog(
            conn,
            _source("1", snapshot_mode="full"),
            records,
            database_mode="demo",
        )
    finally:
        conn.close()

    incoming_source = _source("2", snapshot_mode="full")
    report = catalog_import.run_catalog_import(
        incoming_source,
        records[:1],
        dry_run=True,
        database_mode="demo",
        retirement_preview_db=path,
    )
    assert report["retirement_preview"]["baseline_missing"] is False
    assert report["retirement_preview"]["candidate_count"] == 1
    assert report["retirement_preview"]["candidates"][0]["source_record_id"] == "missing"
    conn = db.get_conn(path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_retirement_batches"
        ).fetchone()["n"] == 0
    finally:
        conn.close()

    delta_source = _source("2.1", snapshot_mode="delta")
    with pytest.raises(ValueError, match="full snapshot"):
        catalog_import.run_catalog_import(
            delta_source,
            records[:1],
            dry_run=True,
            database_mode="demo",
            retirement_preview_db=path,
        )


def test_retired_presentations_are_not_reproposed_by_preview_or_later_full_snapshot(
    governance_conn,
):
    conn = governance_conn
    stay = _record("stay", "DEMO Stay")
    retired_record = _record("retired", "DEMO Already Retired")
    _import(conn, "1", [stay, retired_record], snapshot_mode="full")
    report = _import(conn, "2", [stay], snapshot_mode="full")
    batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
    candidate = batch["candidates"][0]
    governance.decide_retirement_candidate(
        conn,
        batch["id"],
        candidate["id"],
        decision="retire",
        expected_candidate_version=candidate["record_version"],
        expected_presentation_version=candidate["current_presentation_version"],
        **_review_fields(reason_code="confirmed_snapshot_omission"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.approve_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="batch_resolved"),
    )
    batch = governance.get_retirement_batch(conn, batch["id"])
    governance.apply_retirement_batch(
        conn,
        batch["id"],
        expected_batch_version=batch["record_version"],
        **_review_fields(reason_code="batch_applied"),
    )

    # A later package may still mention the retired source ID. Processing that
    # evidence must not silently reactivate it, and the next diff must not repropose it.
    third = _import(conn, "3", [stay, retired_record], snapshot_mode="full")
    assert third["retirement_candidate_count"] == 0
    assert _presentation(conn, "retired")["operational_lifecycle_status"] == "retired"
    preview = governance.preview_full_snapshot_retirement(
        conn,
        _source("4", snapshot_mode="full")["slug"],
        ["stay"],
    )
    assert preview["baseline_missing"] is False
    assert preview["candidate_count"] == 0

    fourth = _import(conn, "4", [stay], snapshot_mode="full")
    assert fourth["retirement_batch_id"] is None
    assert fourth["retirement_candidate_count"] == 0


def test_snapshot_mode_and_package_schema_are_governed_source_metadata():
    delta = _source("1", snapshot_mode="delta")
    full = {**delta, "snapshot_mode": "full"}
    next_schema = {**delta, "package_schema_version": "3"}
    assert drug_catalog.source_metadata_sha256(delta) != drug_catalog.source_metadata_sha256(full)
    assert drug_catalog.source_metadata_sha256(delta) != drug_catalog.source_metadata_sha256(
        next_schema
    )


def test_synthetic_governance_demo_refuses_production_and_unmarked_populated_databases(
    tmp_path,
):
    production_path = tmp_path / "production.db"
    db.init_db(production_path)
    conn = db.get_conn(production_path)
    try:
        now = db.now_utc()
        conn.execute(
            """INSERT INTO catalog_database_meta
               (id, catalog_mode, created_at, updated_at)
               VALUES (1, 'production', ?, ?)""",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="production database"):
        catalog_governance_demo.build_demo(production_path)

    unmarked_path = tmp_path / "unmarked-populated.db"
    db.init_db(unmarked_path)
    conn = db.get_conn(unmarked_path)
    try:
        db.upsert_pharmacy(conn, {"id": "existing", "name": "Existing Pharmacy"})
    finally:
        conn.close()
    with pytest.raises(ValueError, match="empty or explicitly demo-mode"):
        catalog_governance_demo.build_demo(unmarked_path)

    demo_path = tmp_path / "isolated-demo.db"
    result = catalog_governance_demo.build_demo(demo_path)
    assert result["db_path"] == str(demo_path.resolve())
    conn = db.get_conn(demo_path)
    try:
        assert conn.execute(
            "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
        ).fetchone()["catalog_mode"] == "demo"
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_retirement_batches"
        ).fetchone()["n"] >= 1
    finally:
        conn.close()


def test_migration_backfill_is_idempotent_and_preserves_existing_rows(tmp_path):
    path = tmp_path / "migration.db"
    db.init_db(path)
    conn = db.get_conn(path)
    try:
        db.upsert_pharmacy(conn, {"id": "p1", "name": "Migration Pharmacy"})
        legacy_record = _record("backfill", "DEMO Backfill", complete=False)
        legacy_record["status"] = "inactive"
        _import(conn, "1", [legacy_record])
        issued = db.create_prescription(
            conn,
            "p1",
            {
                "client_input_id": "legacy-migration-prescription",
                "lang": "en",
                "items": [
                    {
                        "position": 1,
                        "drug_name_raw": "Legacy free-text medicine",
                        "drug_id": None,
                        "pattern_key": "OD_MORNING",
                        "doses": {"M": 1, "N": 0, "E": 0, "H": 0},
                        "dose_unit": "tablet",
                        "timing_food": "after_food",
                        "duration_days": 3,
                    }
                ],
            },
        )
        conn.execute(
            """INSERT INTO events (event_type, ts, is_bot, is_internal)
               VALUES ('migration.marker', '2026-07-10T00:00:00Z', 0, 1)"""
        )
        prescription_count = conn.execute(
            "SELECT COUNT(*) AS n FROM prescriptions"
        ).fetchone()["n"]
        token_count = conn.execute(
            "SELECT COUNT(*) AS n FROM access_tokens"
        ).fetchone()["n"]
        event_count = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]

        # Recreate the PR #2 boundary: keep catalog/prescription data but remove only
        # governance tables/columns and the v5/v6 markers before running init_db().
        conn.execute("DROP INDEX IF EXISTS idx_drug_presentations_governance")
        for table in (
            "drug_retirement_batch_events",
            "drug_retirement_candidates",
            "drug_retirement_batches",
            "drug_review_decisions",
        ):
            conn.execute(f"DROP TABLE {table}")
        for column in (
            "normalization_version",
            "package_schema_version",
            "snapshot_mode",
            "package_content_sha256",
            "code_revision",
        ):
            conn.execute(f"ALTER TABLE drug_import_runs DROP COLUMN {column}")
        for column in (
            "workflow_review_status",
            "operational_lifecycle_status",
            "record_version",
            "normalized_projection_json",
            "normalized_projection_sha256",
            "current_import_run_record_id",
        ):
            conn.execute(f"ALTER TABLE drug_presentations DROP COLUMN {column}")
        for column in (
            "normalized_projection_json",
            "normalized_projection_sha256",
            "previous_normalized_projection_sha256",
            "normalized_diff_json",
            "review_required",
        ):
            conn.execute(f"ALTER TABLE drug_import_run_records DROP COLUMN {column}")
        conn.execute("DELETE FROM schema_migrations WHERE version IN (5,6,7)")
        conn.commit()
    finally:
        conn.close()
    db.init_db(path)
    conn = db.get_conn(path)
    try:
        assert _presentation(conn, "backfill")["workflow_review_status"] == "needs_review"
        assert _presentation(conn, "backfill")["operational_lifecycle_status"] == "inactive"
        assert conn.execute("SELECT COUNT(*) AS n FROM prescriptions").fetchone()["n"] == prescription_count
        assert conn.execute("SELECT COUNT(*) AS n FROM access_tokens").fetchone()["n"] == token_count
        assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == event_count
        token_status, token_bundle = db.get_bundle_by_token(conn, issued["token"])
        assert token_status == "active"
        assert token_bundle["prescription"]["id"] == issued["id"]
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=5"
        ).fetchone()["n"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=6"
        ).fetchone()["n"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=7"
        ).fetchone()["n"] == 1
        assert {
            row["name"]
            for row in conn.execute("PRAGMA table_info(drug_import_run_records)")
        } >= {
            "normalized_projection_json",
            "normalized_projection_sha256",
            "previous_normalized_projection_sha256",
            "normalized_diff_json",
            "review_required",
        }
        assert "current_import_run_record_id" in {
            row["name"]
            for row in conn.execute("PRAGMA table_info(drug_presentations)")
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        row = _presentation(conn, "backfill")
        assert row["current_import_run_record_id"]
        assert row["normalized_projection_json"] is None
        assert row["normalized_projection_sha256"] is None
        for action in ("review_approved", "review_rejected"):
            with pytest.raises(
                governance.GovernanceValidationError,
                match="normalized projection must be reprocessed",
            ):
                governance.transition_presentation(
                    conn,
                    row["id"],
                    action=action,
                    expected_record_version=row["record_version"],
                    **_review_fields(reason_code="legacy_projection_blocked"),
                )
        assert _presentation(conn, "backfill")["record_version"] == row["record_version"]
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_review_decisions WHERE presentation_id=?",
            (row["id"],),
        ).fetchone()["n"] == 0
        governance.transition_presentation(
            conn,
            row["id"],
            action="lifecycle_reactivated",
            expected_record_version=row["record_version"],
            **_review_fields(reason_code="source_inactive_override_reviewed"),
        )
        active_version = _presentation(conn, "backfill")["record_version"]
    finally:
        conn.close()

    # Startup must not replay the one-time backfill over a later human decision.
    db.init_db(path)
    db.init_db(path)
    conn = db.get_conn(path)
    try:
        row = _presentation(conn, "backfill")
        assert row["operational_lifecycle_status"] == "active"
        assert row["record_version"] == active_version
        assert conn.execute(
            """SELECT COUNT(*) AS n FROM drug_review_decisions
               WHERE presentation_id=? AND action='lifecycle_reactivated'""",
            (row["id"],),
        ).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM prescriptions").fetchone()["n"] == prescription_count
        assert conn.execute("SELECT COUNT(*) AS n FROM access_tokens").fetchone()["n"] == token_count
        assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == event_count
    finally:
        conn.close()


def test_operations_ui_is_default_off_uses_post_prg_conflict_and_escapes_notes(
    tmp_path, monkeypatch
):
    path = tmp_path / "ops-ui.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    conn = db.get_conn(path)
    try:
        _import(conn, "1", [_record("ui", "DEMO UI <Long> Name", complete=False)])
        row = _presentation(conn, "ui")
        presentation_id = row["id"]
        original_version = row["record_version"]
    finally:
        conn.close()
    from app.main import create_app

    with TestClient(create_app()) as disabled:
        assert disabled.get("/catalog/review").status_code == 404
        assert disabled.post(f"/catalog/review/{presentation_id}/decision", data={}).status_code == 404
        openapi_paths = disabled.get("/openapi.json").json()["paths"]
        assert not any(path.startswith("/catalog/") for path in openapi_paths)

    with TestClient(
        create_app(catalog_ops_enabled=True),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        queue = client.get("/catalog/review?review_status=needs_review")
        assert queue.status_code == 200
        assert queue.headers["cache-control"] == "no-store"
        assert queue.headers["x-frame-options"] == "DENY"
        assert queue.headers["x-content-type-options"] == "nosniff"
        assert queue.headers["referrer-policy"] == "same-origin"
        assert "frame-ancestors 'none'" in queue.headers["content-security-policy"]
        assert "patient_label" not in queue.text
        assert "access_tokens" not in queue.text
        assert "DEMO UI &lt;Long&gt; Name" in queue.text
        assert client.get("/catalog/review", headers={"host": "attacker.invalid"}).status_code == 404
        assert client.get(f"/catalog/review/{presentation_id}/decision").status_code == 405
        assert client.get(
            f"/catalog/review/{presentation_id}?result=fabricated-success"
        ).text.find("fabricated-success") == -1
        assert client.post(
            f"/catalog/review/{presentation_id}/decision", json={}
        ).status_code == 422

        note = '<img src=x onerror="window.__audit_xss=1"> checked as text'
        cross_origin = client.post(
            f"/catalog/review/{presentation_id}/decision",
            headers={"origin": "http://attacker.invalid", "sec-fetch-site": "cross-site"},
            data={
                "action": "review_approved",
                "expected_record_version": str(original_version),
                "reason_code": "cross_origin_attempt",
                "note": "Must be rejected before mutation.",
                "reviewer_id": "attacker",
                "reviewer_role": "untrusted origin",
            },
        )
        assert cross_origin.status_code == 403
        opaque_cross_origin = client.post(
            f"/catalog/review/{presentation_id}/decision",
            headers={"origin": "null"},
            data={
                "action": "review_approved",
                "expected_record_version": str(original_version),
                "reason_code": "opaque_cross_origin_attempt",
                "note": "Must be rejected without a same-origin Referer.",
                "reviewer_id": "attacker",
                "reviewer_role": "untrusted origin",
            },
        )
        assert opaque_cross_origin.status_code == 403
        response = client.post(
            f"/catalog/review/{presentation_id}/decision",
            headers={
                "origin": "null",
                "referer": f"http://127.0.0.1/catalog/review/{presentation_id}",
                "sec-fetch-site": "same-origin",
            },
            data={
                "action": "review_approved",
                "expected_record_version": str(original_version),
                "reason_code": "ui_review",
                "note": note,
                "reviewer_id": "ui-reviewer",
                "reviewer_role": "pharmacist reviewer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert detail.status_code == 200
        assert "&lt;img src=x onerror=&#34;window.__audit_xss=1&#34;&gt;" in detail.text
        assert '<img src=x onerror="window.__audit_xss=1">' not in detail.text
        stale = client.post(
            f"/catalog/review/{presentation_id}/decision",
            data={
                "action": "review_rejected",
                "expected_record_version": str(original_version),
                "reason_code": "stale",
                "note": "Must not be applied.",
                "reviewer_id": "other-reviewer",
                "reviewer_role": "pharmacist reviewer",
            },
        )
        assert stale.status_code == 409
        assert 'data-qa="conflict-message"' in stale.text
        invalid_note = client.post(
            f"/catalog/review/{presentation_id}/decision",
            data={
                "action": "review_requested",
                "expected_record_version": str(original_version + 1),
                "reason_code": "empty_note",
                "note": "",
                "reviewer_id": "ui-reviewer",
                "reviewer_role": "pharmacist reviewer",
            },
        )
        assert invalid_note.status_code == 422

    conn = db.get_conn(path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_review_decisions WHERE presentation_id=?",
            (presentation_id,),
        ).fetchone()["n"] == 1
        assert json.loads(
            conn.execute(
                "SELECT raw_json FROM drug_source_records WHERE presentation_id=?",
                (presentation_id,),
            ).fetchone()["raw_json"]
        )["source_record_id"] == "ui"
    finally:
        conn.close()


def test_operations_ui_blocks_legacy_projection_decisions_until_reprocessed(
    tmp_path, monkeypatch
):
    path = tmp_path / "ops-legacy-projection.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    conn = db.get_conn(path)
    try:
        _import(
            conn,
            "1",
            [_record("legacy-ui", "DEMO Legacy Projection", complete=False)],
        )
        row = _presentation(conn, "legacy-ui")
        presentation_id = row["id"]
        original_version = row["record_version"]
        conn.execute(
            """UPDATE drug_presentations
               SET normalized_projection_json=NULL,
                   normalized_projection_sha256=NULL
               WHERE id=?""",
            (presentation_id,),
        )
        conn.commit()
    finally:
        conn.close()

    from app.main import create_app

    with TestClient(
        create_app(catalog_ops_enabled=True),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        detail = client.get(f"/catalog/review/{presentation_id}")
        assert detail.status_code == 200
        assert 'data-qa="projection-reprocess-required"' in detail.text
        assert 'value="review_approved"' not in detail.text
        assert 'value="review_rejected"' not in detail.text
        rejected = client.post(
            f"/catalog/review/{presentation_id}/decision",
            data={
                "action": "review_rejected",
                "expected_record_version": str(original_version),
                "reason_code": "legacy_projection",
                "note": "Must be reprocessed before a human decision.",
                "reviewer_id": "ui-reviewer",
                "reviewer_role": "pharmacist reviewer",
            },
        )
        assert rejected.status_code == 422
        assert "must be reprocessed before rejection" in rejected.text

    conn = db.get_conn(path)
    try:
        current = _presentation(conn, "legacy-ui")
        assert current["record_version"] == original_version
        assert current["workflow_review_status"] == "needs_review"
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_review_decisions WHERE presentation_id=?",
            (presentation_id,),
        ).fetchone()["n"] == 0

        _import(
            conn,
            "2",
            [_record("legacy-ui", "DEMO Legacy Projection", complete=False)],
        )
        reprocessed = _presentation(conn, "legacy-ui")
        assert reprocessed["normalized_projection_json"]
        assert reprocessed["normalized_projection_sha256"]
        governance.transition_presentation(
            conn,
            reprocessed["id"],
            action="review_approved",
            expected_record_version=reprocessed["record_version"],
            **_review_fields(reason_code="reprocessed_projection_reviewed"),
        )
        audit = conn.execute(
            """SELECT normalized_snapshot_json, normalized_projection_sha256
               FROM drug_review_decisions
               WHERE presentation_id=? AND action='review_approved'
               ORDER BY rowid DESC LIMIT 1""",
            (presentation_id,),
        ).fetchone()
        assert json.loads(audit["normalized_snapshot_json"])["brand_name_raw"] == (
            "DEMO Legacy Projection"
        )
        assert audit["normalized_projection_sha256"]
    finally:
        conn.close()


def test_operations_ui_does_not_label_applied_retirement_as_stale(tmp_path, monkeypatch):
    path = tmp_path / "ops-retirement-ui.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    conn = db.get_conn(path)
    try:
        records = [
            _record("ui-stay", "DEMO UI Stay"),
            _record("ui-retire", "DEMO UI Retire"),
        ]
        _import(conn, "1", records, snapshot_mode="full")
        report = _import(conn, "2", [records[0]], snapshot_mode="full")
        batch = governance.get_retirement_batch(conn, report["retirement_batch_id"])
        candidate = batch["candidates"][0]
        governance.decide_retirement_candidate(
            conn,
            batch["id"],
            candidate["id"],
            decision="retire",
            expected_candidate_version=candidate["record_version"],
            expected_presentation_version=candidate["current_presentation_version"],
            **_review_fields(reason_code="confirmed_snapshot_omission"),
        )
        batch = governance.get_retirement_batch(conn, batch["id"])
        governance.approve_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(reason_code="batch_resolved"),
        )
        batch = governance.get_retirement_batch(conn, batch["id"])
        governance.apply_retirement_batch(
            conn,
            batch["id"],
            expected_batch_version=batch["record_version"],
            **_review_fields(reason_code="batch_applied"),
        )
        batch_id = batch["id"]
    finally:
        conn.close()

    from app.main import create_app

    with TestClient(
        create_app(catalog_ops_enabled=True),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        detail = client.get(f"/catalog/retirements/{batch_id}")
        assert detail.status_code == 200
        assert "Current search: retired" in detail.text
        assert "Applied after version check" in detail.text
        assert "Stale presentation version" not in detail.text


def test_retirement_operations_ui_uses_prg_conflicts_idempotency_and_cancel(
    tmp_path, monkeypatch
):
    path = tmp_path / "ops-retirement-http.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    conn = db.get_conn(path)
    try:
        stay = _record("http-stay", "DEMO HTTP Stay")
        retire = _record("http-retire", "DEMO HTTP Retire")
        cancel = _record("http-cancel", "DEMO HTTP Cancel")
        _import(conn, "1", [stay, retire], snapshot_mode="full")
        first_report = _import(conn, "2", [stay], snapshot_mode="full")
        first_batch = governance.get_retirement_batch(
            conn, first_report["retirement_batch_id"]
        )
        first_candidate = first_batch["candidates"][0]

        _import(conn, "3", [stay, cancel], snapshot_mode="full")
        cancel_report = _import(conn, "4", [stay], snapshot_mode="full")
        cancel_batch = governance.get_retirement_batch(
            conn, cancel_report["retirement_batch_id"]
        )
    finally:
        conn.close()

    from app.main import create_app

    operator = {
        "reason_code": "browser_workflow_test",
        "note": "Synthetic operator decision for PRG verification.",
        "reviewer_id": "http-reviewer",
        "reviewer_role": "synthetic pharmacist reviewer",
    }
    with TestClient(
        create_app(catalog_ops_enabled=True),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as client:
        candidate_response = client.post(
            f"/catalog/retirements/{first_batch['id']}/candidates/{first_candidate['id']}",
            data={
                **operator,
                "decision": "retire",
                "expected_candidate_version": str(first_candidate["record_version"]),
                "expected_presentation_version": str(
                    first_candidate["current_presentation_version"]
                ),
            },
            follow_redirects=False,
        )
        assert candidate_response.status_code == 303
        assert client.get(candidate_response.headers["location"]).status_code == 200

        conn = db.get_conn(path)
        try:
            candidate_audit_count = conn.execute(
                """SELECT COUNT(*) AS n FROM drug_review_decisions
                   WHERE retirement_candidate_id=? AND action='retirement_marked'""",
                (first_candidate["id"],),
            ).fetchone()["n"]
        finally:
            conn.close()
        assert candidate_audit_count == 1
        assert client.get(candidate_response.headers["location"]).status_code == 200

        stale = client.post(
            f"/catalog/retirements/{first_batch['id']}/candidates/{first_candidate['id']}",
            data={
                **operator,
                "decision": "keep_active",
                "expected_candidate_version": str(first_candidate["record_version"]),
                "expected_presentation_version": str(
                    first_candidate["current_presentation_version"]
                ),
            },
        )
        assert stale.status_code == 409
        assert 'data-qa="conflict-message"' in stale.text

        conn = db.get_conn(path)
        try:
            current = governance.get_retirement_batch(conn, first_batch["id"])
        finally:
            conn.close()
        approve = client.post(
            f"/catalog/retirements/{current['id']}/approve",
            data={
                **operator,
                "expected_batch_version": str(current["record_version"]),
            },
            follow_redirects=False,
        )
        assert approve.status_code == 303

        conn = db.get_conn(path)
        try:
            approved = governance.get_retirement_batch(conn, first_batch["id"])
        finally:
            conn.close()
        apply = client.post(
            f"/catalog/retirements/{approved['id']}/apply",
            data={
                **operator,
                "expected_batch_version": str(approved["record_version"]),
            },
            follow_redirects=False,
        )
        assert apply.status_code == 303
        assert client.get(apply.headers["location"]).status_code == 200

        conn = db.get_conn(path)
        try:
            applied = governance.get_retirement_batch(conn, first_batch["id"])
            audit_count = conn.execute(
                """SELECT COUNT(*) AS n FROM drug_review_decisions
                   WHERE retirement_batch_id=? AND action='retirement_applied'""",
                (first_batch["id"],),
            ).fetchone()["n"]
        finally:
            conn.close()
        retry = client.post(
            f"/catalog/retirements/{applied['id']}/apply",
            data={
                **operator,
                "expected_batch_version": str(approved["record_version"]),
            },
            follow_redirects=False,
        )
        assert retry.status_code == 303
        conn = db.get_conn(path)
        try:
            assert conn.execute(
                """SELECT COUNT(*) AS n FROM drug_review_decisions
                   WHERE retirement_batch_id=? AND action='retirement_applied'""",
                (first_batch["id"],),
            ).fetchone()["n"] == audit_count
        finally:
            conn.close()

        cancel_response = client.post(
            f"/catalog/retirements/{cancel_batch['id']}/cancel",
            data={
                **operator,
                "expected_batch_version": str(cancel_batch["record_version"]),
            },
            follow_redirects=False,
        )
        assert cancel_response.status_code == 303
        assert client.get(cancel_response.headers["location"]).status_code == 200
        assert client.get(f"/catalog/retirements/{first_batch['id']}/apply").status_code == 405
        assert client.post(
            "/catalog/retirements/not-a-batch/cancel",
            data={**operator, "expected_batch_version": "1"},
        ).status_code == 404
