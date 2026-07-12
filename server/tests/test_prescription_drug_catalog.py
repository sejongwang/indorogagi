"""Prescription integration regressions for the source-aware drug catalog.

The catalog identifies a medicine presentation only.  Regimen fields remain
pharmacist-entered, and an issued prescription owns an immutable server-built
snapshot even if the catalog is updated later.
"""
from __future__ import annotations

import json
import sqlite3

from app import db
from app.drug_catalog import (
    import_catalog,
    records_sha256,
    source_metadata_sha256,
)


PHARMACY_HEADERS = {"X-Pharmacy-Id": "ph-demo-001"}


def _source(
    slug: str,
    *,
    version: str = "2026-07-v1",
    usage_scope: str = "production",
) -> dict:
    is_demo = usage_scope == "demo"
    return {
        "slug": slug,
        "name": f"Integration source {slug}",
        "operator": "Integration Test Authority",
        "tier": 3 if is_demo else 1,
        "usage_scope": usage_scope,
        "reuse_status": "demo_only" if is_demo else "approved",
        "license_name": "Test data licence",
        "license_url": "https://example.invalid/licence",
        "attribution_text": "Source: Integration Test Authority",
        "version": version,
        "published_at": "2026-07-01",
        "source_updated_at": "2026-07-01",
        "accessed_at": "2026-07-10",
        "input_uri": "https://example.invalid/catalog.json",
    }


def _record(source_record_id: str, brand_name: str, **overrides) -> dict:
    item = {
        "source_record_id": source_record_id,
        "name_type": "brand",
        "brand_name": brand_name,
        "generic_name": "Invented Catalog Ingredient",
        "ingredients": [
            {"name": "Invented Catalog Ingredient", "strength": "25 mg"}
        ],
        "strength": "25 mg",
        "form": "tablet",
        "route": "oral",
        "manufacturer": "Integration Laboratories",
        "aliases": [],
        "status": "active",
        "review_status": "verified",
    }
    item.update(overrides)
    return item


def _import_one(
    conn,
    *,
    slug: str,
    source_record_id: str,
    brand_name: str,
    version: str = "2026-07-v1",
    usage_scope: str = "production",
    **record_overrides,
) -> str:
    metadata = _source(slug, version=version, usage_scope=usage_scope)
    records = [_record(source_record_id, brand_name, **record_overrides)]
    approval_registry = None
    if usage_scope == "production":
        approval_registry = {
            "schema_version": 2,
            "approved_packages": {
                slug: {
                    "approval_status": "approved",
                    "approved_at": "2026-07-10",
                    "approved_by_role": "integration test data governance",
                    "records_sha256": records_sha256(records),
                    "source_metadata_sha256": source_metadata_sha256(metadata),
                }
            },
        }
    import_catalog(
        conn,
        metadata,
        records,
        approval_registry=approval_registry,
        database_mode="demo",
    )
    return conn.execute(
        """SELECT p.id
           FROM drug_presentations p
           JOIN drug_sources s ON s.id = p.source_id
           WHERE s.slug = ? AND p.source_record_id = ?""",
        (slug, source_record_id),
    ).fetchone()["id"]


def _stored_item(conn, prescription_id: str):
    return conn.execute(
        "SELECT * FROM prescription_items WHERE prescription_id = ?",
        (prescription_id,),
    ).fetchone()


def _selected_payload(rx_payload, presentation_id: str, name: str) -> dict:
    payload = rx_payload(drug_name=name, pattern_key="OD_NIGHT", duration_days=13)
    payload["items"][0].update(
        {
            "drug_input_raw": "anchor 25",
            "drug_id": presentation_id,
            "drug_match_state": "selected",
            "doses": {"M": 0, "N": 0, "E": 0, "H": 0.5},
            "dose_unit": "capsule",
            "timing_food": "before_food",
            "duration_days": 13,
            "total_quantity": 6.5,
        }
    )
    return payload


def test_legacy_catalog_id_cannot_be_promoted_to_new_verified_selection(
    client, db_conn, rx_payload
):
    db_conn.execute(
        """INSERT INTO drugs
           (id, brand_name, generic_name, strength, form, aliases_json,
            caution_keys_json, source, verified, created_at)
           VALUES ('legacy-commercial-row', 'Legacy Commercial 10', 'Invented Ingredient',
                   '10 mg', 'tablet', '[]', '[]', 'tata-1mg', 1, ?)""",
        (db.now_utc(),),
    )
    db_conn.commit()
    payload = _selected_payload(
        rx_payload, "legacy-commercial-row", "Legacy Commercial 10"
    )

    response = client.post(
        "/api/prescriptions", json=payload, headers=PHARMACY_HEADERS
    )
    assert response.status_code == 201
    stored = _stored_item(db_conn, response.json()["id"])
    assert stored["drug_id"] is None
    assert stored["drug_match_state"] == "free_text"
    assert stored["drug_catalog_snapshot_json"] is None
    assert "catalog_id_not_found_free_text_preserved" in json.loads(
        stored["drug_selection_warning_json"]
    )


def test_selected_snapshot_survives_catalog_update_and_existing_qr_flow(
    client, db_conn, rx_payload
):
    presentation_id = _import_one(
        db_conn,
        slug="snapshot-source",
        source_record_id="stable-row",
        brand_name="Anchor Original 25",
    )
    payload = _selected_payload(rx_payload, presentation_id, "Anchor Original 25")
    # The server must rebuild the snapshot by ID, never trust client-supplied facts.
    payload["items"][0]["drug_catalog_snapshot"] = {
        "brand_name": "Forged Client Name",
        "strength": "999 mg",
    }

    issued = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert issued.status_code == 201
    issued_json = issued.json()
    stored_before = _stored_item(db_conn, issued_json["id"])
    snapshot_json_before = stored_before["drug_catalog_snapshot_json"]
    snapshot_before = json.loads(snapshot_json_before)

    assert stored_before["drug_id"] == presentation_id
    assert stored_before["drug_match_state"] == "selected"
    assert stored_before["drug_input_raw"] == "anchor 25"
    assert snapshot_before["id"] == presentation_id
    assert snapshot_before["brand_name"] == "Anchor Original 25"
    assert snapshot_before["strength"] == "25 mg"

    same_id = _import_one(
        db_conn,
        slug="snapshot-source",
        source_record_id="stable-row",
        brand_name="Anchor Corrected 50",
        version="2026-08-v2",
        generic_name="Corrected Catalog Ingredient",
        ingredients=[
            {"name": "Corrected Catalog Ingredient", "strength": "50 mg"}
        ],
        strength="50 mg",
    )
    assert same_id == presentation_id
    assert db_conn.execute(
        "SELECT brand_name FROM drugs WHERE id = ?", (presentation_id,)
    ).fetchone()["brand_name"] == "Anchor Corrected 50"

    stored_after = _stored_item(db_conn, issued_json["id"])
    assert stored_after["drug_catalog_snapshot_json"] == snapshot_json_before

    detail = client.get(
        f"/api/prescriptions/{issued_json['id']}", headers=PHARMACY_HEADERS
    )
    assert detail.status_code == 200
    detail_item = detail.json()["items"][0]
    assert detail_item["drug_catalog_snapshot"]["brand_name"] == "Anchor Original 25"
    assert detail_item["drug"]["brand_name"] == "Anchor Original 25"

    qr = client.get(
        f"/api/prescriptions/{issued_json['id']}/qr", headers=PHARMACY_HEADERS
    )
    assert qr.status_code == 200
    assert qr.json()["token"] == issued_json["token"]
    assert qr.json()["url"].endswith(f"/p/{issued_json['token']}")


def test_free_text_fallback_still_issues_and_redisplays_qr(client, db_conn, rx_payload):
    payload = rx_payload(drug_name="Paper-only invented medicine")
    payload["items"][0].update(
        {
            "drug_input_raw": "Paper-only invented medicine",
            "drug_id": None,
            "drug_match_state": "free_text",
        }
    )

    issued = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert issued.status_code == 201
    item = _stored_item(db_conn, issued.json()["id"])
    assert item["drug_name_raw"] == "Paper-only invented medicine"
    assert item["drug_input_raw"] == "Paper-only invented medicine"
    assert item["drug_id"] is None
    assert item["drug_match_state"] == "free_text"
    assert item["drug_catalog_snapshot_json"] is None

    detail = client.get(
        f"/api/prescriptions/{issued.json()['id']}", headers=PHARMACY_HEADERS
    )
    assert detail.status_code == 200
    assert detail.json()["items"][0]["drug"] is None

    qr = client.get(
        f"/api/prescriptions/{issued.json()['id']}/qr", headers=PHARMACY_HEADERS
    )
    assert qr.status_code == 200
    assert qr.json()["url"].endswith(f"/p/{issued.json()['token']}")


def test_selected_then_modified_clears_catalog_link(client, db_conn, rx_payload):
    presentation_id = _import_one(
        db_conn,
        slug="modified-source",
        source_record_id="modified-row",
        brand_name="Catalog Name Before Edit",
    )
    payload = _selected_payload(
        rx_payload, presentation_id, "Pharmacist corrected free-text name"
    )
    payload["items"][0].update(
        {
            "drug_input_raw": "Catalog Name",
            # A stale or malicious client may still send the old ID.  The server
            # enforces the modified-state invariant and clears the link itself.
            "drug_id": presentation_id,
            "drug_match_state": "selected_then_modified",
        }
    )

    issued = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert issued.status_code == 201
    item = _stored_item(db_conn, issued.json()["id"])
    assert item["drug_name_raw"] == "Pharmacist corrected free-text name"
    assert item["drug_input_raw"] == "Catalog Name"
    assert item["drug_match_state"] == "selected_then_modified"
    assert item["drug_id"] is None
    assert item["drug_catalog_snapshot_json"] is None


def test_invalid_catalog_id_safely_falls_back_to_free_text(
    client, db_conn, rx_payload
):
    payload = rx_payload(drug_name="Readable name survives invalid catalog link")
    payload["items"][0].update(
        {
            "drug_input_raw": "Readable name",
            "drug_id": "not-a-real-catalog-id",
            "drug_match_state": "selected",
        }
    )

    issued = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert issued.status_code == 201
    item = _stored_item(db_conn, issued.json()["id"])
    assert item["drug_name_raw"] == "Readable name survives invalid catalog link"
    assert item["drug_id"] is None
    assert item["drug_match_state"] == "free_text"
    assert item["drug_catalog_snapshot_json"] is None
    assert json.loads(item["drug_selection_warning_json"])

    detail_item = client.get(
        f"/api/prescriptions/{issued.json()['id']}", headers=PHARMACY_HEADERS
    ).json()["items"][0]
    assert detail_item["drug"] is None
    assert detail_item["drug_selection_warnings"]


def test_catalog_selection_never_supplies_regimen_defaults(
    client, db_conn, rx_payload
):
    presentation_id = _import_one(
        db_conn,
        slug="identity-only-source",
        source_record_id="identity-row",
        brand_name="Identity Only Product",
        # Even if an upstream row contains regimen-shaped keys, they are not
        # catalog identity facts and must not enter search or prescriptions.
        default_pattern_key="QID",
        default_timing_food="after_food",
        default_dose_unit="tablet",
        duration_days=99,
        doses={"M": 1, "N": 1, "E": 1, "H": 1},
    )

    search = client.get("/api/drugs", params={"q": "Identity Only"})
    assert search.status_code == 200
    result = next(row for row in search.json() if row["id"] == presentation_id)
    assert result["default_pattern_key"] is None
    assert result["default_timing_food"] is None
    assert result["default_dose_unit"] is None
    assert "duration_days" not in result
    assert "doses" not in result

    payload = _selected_payload(rx_payload, presentation_id, "Identity Only Product")
    issued = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert issued.status_code == 201
    detail_item = client.get(
        f"/api/prescriptions/{issued.json()['id']}", headers=PHARMACY_HEADERS
    ).json()["items"][0]
    assert detail_item["pattern_key"] == "OD_NIGHT"
    assert detail_item["doses"] == {"M": 0.0, "N": 0.0, "E": 0.0, "H": 0.5}
    assert detail_item["dose_unit"] == "capsule"
    assert detail_item["timing_food"] == "before_food"
    assert detail_item["duration_days"] == 13


def test_demo_catalog_requires_demo_pharmacy_header(client, db_conn):
    _import_one(
        db_conn,
        slug="scope-production-source",
        source_record_id="production-row",
        brand_name="Scoped Product Production",
    )
    _import_one(
        db_conn,
        slug="scope-demo-source",
        source_record_id="demo-row",
        brand_name="Scoped Product Demo",
        usage_scope="demo",
    )

    production = client.get("/api/drugs", params={"q": "Scoped Product"})
    assert production.status_code == 200
    assert [row["brand_name"] for row in production.json()] == [
        "Scoped Product Production"
    ]

    # A query flag or arbitrary pharmacy header must not widen source scope.
    query_flag = client.get(
        "/api/drugs", params={"q": "Scoped Product Demo", "include_demo": "true"}
    )
    assert query_flag.status_code == 200
    assert query_flag.json() == []
    other_pharmacy = client.get(
        "/api/drugs",
        params={"q": "Scoped Product Demo"},
        headers={"X-Pharmacy-Id": "ph-not-the-demo"},
    )
    assert other_pharmacy.status_code == 200
    assert other_pharmacy.json() == []

    demo = client.get(
        "/api/drugs", params={"q": "Scoped Product"}, headers=PHARMACY_HEADERS
    )
    assert demo.status_code == 200
    assert {row["brand_name"] for row in demo.json()} == {
        "Scoped Product Production",
        "Scoped Product Demo",
    }
    assert {row["usage_scope"] for row in demo.json()} == {"production", "demo"}


def test_demo_and_inactive_catalog_ids_cannot_bypass_issue_boundary(
    client, db_conn, rx_payload
):
    other_pharmacy = "ph-production-test"
    db.upsert_pharmacy(
        db_conn,
        {
            "id": other_pharmacy,
            "name": "Production Test Pharmacy",
            "area": "Delhi",
            "pincode": "110001",
            "has_printer": 0,
        },
    )
    demo_id = _import_one(
        db_conn,
        slug="issue-boundary-demo",
        source_record_id="demo-row",
        brand_name="DEMO Boundary Product",
        usage_scope="demo",
    )
    inactive_id = _import_one(
        db_conn,
        slug="issue-boundary-inactive",
        source_record_id="inactive-row",
        brand_name="Inactive Boundary Product",
        status="inactive",
    )

    demo_payload = _selected_payload(
        rx_payload, demo_id, "DEMO Boundary Product"
    )
    demo_issue = client.post(
        "/api/prescriptions",
        json=demo_payload,
        headers={"X-Pharmacy-Id": other_pharmacy},
    )
    assert demo_issue.status_code == 201
    demo_item = _stored_item(db_conn, demo_issue.json()["id"])
    assert demo_item["drug_id"] is None
    assert demo_item["drug_match_state"] == "free_text"
    assert "demo_catalog_not_available_for_pharmacy" in demo_item[
        "drug_selection_warning_json"
    ]

    inactive_payload = _selected_payload(
        rx_payload, inactive_id, "Inactive Boundary Product"
    )
    inactive_issue = client.post(
        "/api/prescriptions", json=inactive_payload, headers=PHARMACY_HEADERS
    )
    assert inactive_issue.status_code == 201
    inactive_item = _stored_item(db_conn, inactive_issue.json()["id"])
    assert inactive_item["drug_id"] is None
    assert inactive_item["drug_match_state"] == "free_text"
    assert "inactive_catalog_link_cleared" in inactive_item[
        "drug_selection_warning_json"
    ]


def test_demo_catalog_item_forces_patient_demo_warning(
    client, db_conn, rx_payload
):
    demo_id = _import_one(
        db_conn,
        slug="patient-demo-banner",
        source_record_id="demo-row",
        brand_name="DEMO Banner Product",
        usage_scope="demo",
    )
    payload = _selected_payload(rx_payload, demo_id, "DEMO Banner Product")
    payload["note"] = None

    issued = client.post(
        "/api/prescriptions", json=payload, headers=PHARMACY_HEADERS
    )
    assert issued.status_code == 201
    patient = client.get(f"/p/{issued.json()['token']}?lang=en")
    assert patient.status_code == 200
    assert "DEMO ONLY — NOT MEDICAL ADVICE" in patient.text


def test_init_db_additively_migrates_legacy_prescription_items(tmp_path):
    path = tmp_path / "legacy-before-drug-catalog.db"
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(
            """CREATE TABLE prescription_items (
                   id TEXT PRIMARY KEY,
                   prescription_id TEXT NOT NULL,
                   position INTEGER NOT NULL,
                   drug_name_raw TEXT NOT NULL,
                   drug_id TEXT,
                   pattern_key TEXT NOT NULL,
                   dose_morning REAL NOT NULL DEFAULT 0,
                   dose_noon REAL NOT NULL DEFAULT 0,
                   dose_evening REAL NOT NULL DEFAULT 0,
                   dose_night REAL NOT NULL DEFAULT 0,
                   dose_unit TEXT NOT NULL DEFAULT 'tablet',
                   timing_food TEXT,
                   duration_days INTEGER,
                   total_quantity REAL,
                   prn_reason_key TEXT,
                   prn_max_per_day REAL,
                   prn_min_gap_hours REAL,
                   extra_params_json TEXT,
                   note TEXT
               )"""
        )
        legacy.execute(
            """INSERT INTO prescription_items
               (id, prescription_id, position, drug_name_raw, drug_id, pattern_key,
                dose_morning, dose_noon, dose_evening, dose_night, dose_unit,
                timing_food, duration_days, total_quantity)
               VALUES ('legacy-item', 'legacy-rx', 1, 'Legacy Free Text', NULL,
                       'TDS', 1, 1, 1, 0, 'tablet', 'after_food', 5, 15)"""
        )
        legacy.commit()
    finally:
        legacy.close()

    db.init_db(path)
    db.init_db(path)  # additive migration is restart-safe

    conn = db.get_conn(path)
    try:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(prescription_items)")
        }
        assert {
            "drug_input_raw",
            "drug_match_state",
            "drug_catalog_snapshot_json",
            "drug_selection_warning_json",
        } <= columns
        row = conn.execute(
            "SELECT * FROM prescription_items WHERE id = 'legacy-item'"
        ).fetchone()
        assert row["drug_name_raw"] == "Legacy Free Text"
        assert row["drug_match_state"] == "free_text"
        assert row["drug_id"] is None
        assert row["drug_catalog_snapshot_json"] is None
        assert row["drug_selection_warning_json"] is None
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version = 2"
        ).fetchone()["n"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM drug_presentations"
        ).fetchone()["n"] == 0
    finally:
        conn.close()
