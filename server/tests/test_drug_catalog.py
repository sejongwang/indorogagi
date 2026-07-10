"""Source-aware medicine catalog regression tests.

These tests intentionally use invented product names. They exercise identification,
provenance and search behavior without asserting medical recommendations.
"""
from __future__ import annotations

import json

import pytest

from app import db
from app.drug_catalog import (
    _unit_options,
    get_presentation_snapshot,
    import_catalog as _import_catalog,
    normalize_identity,
    normalize_search,
    records_sha256,
    search_catalog,
    source_metadata_sha256,
)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "catalog.db"
    db.init_db(path)
    connection = db.get_conn(path)
    yield connection
    connection.close()


def source(
    slug: str = "official-test-source",
    *,
    tier: int = 1,
    usage_scope: str = "production",
    reuse_status: str = "approved",
    version: str = "2026-07",
) -> dict:
    return {
        "slug": slug,
        "name": f"Test source {slug}",
        "operator": "Test Public Authority",
        "tier": tier,
        "usage_scope": usage_scope,
        "reuse_status": reuse_status,
        "license_name": "Test open data licence",
        "license_url": "https://example.invalid/licence",
        "attribution_text": "Source: Test Public Authority",
        "version": version,
        "published_at": "2026-07-01",
        "source_updated_at": "2026-07-01",
        "accessed_at": "2026-07-10",
        "input_uri": "https://example.invalid/catalog.json",
    }


def record(source_record_id: str, brand_name: str, **overrides) -> dict:
    base = {
        "source_record_id": source_record_id,
        "name_type": "brand",
        "brand_name": brand_name,
        "generic_name": "Invented Ingredient",
        "ingredients": [{"name": "Invented Ingredient", "strength": "500 mg"}],
        "strength": "500 mg",
        "form": "tablet",
        "route": "oral",
        "manufacturer": "Example Laboratories",
        "marketer": None,
        "package": None,
        "aliases": [],
        "status": "active",
    }
    base.update(overrides)
    return base


def table_count(conn, name: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]


def approval_registry(metadata: dict, records: list[dict]) -> dict:
    return {
        "schema_version": 2,
        "approved_packages": {
            metadata["slug"]: {
                "approval_status": "approved",
                "approved_at": "2026-07-10",
                "approved_by_role": "test data governance",
                "records_sha256": records_sha256(records),
                "source_metadata_sha256": source_metadata_sha256(metadata),
            }
        },
    }


def import_catalog(conn, metadata, records, dry_run=False):
    rows = list(records)
    registry = (
        approval_registry(metadata, rows)
        if metadata.get("usage_scope") == "production"
        else None
    )
    return _import_catalog(
        conn,
        metadata,
        rows,
        dry_run=dry_run,
        approval_registry=registry,
    )


def test_identity_and_search_normalization_preserve_raw_distinctions():
    assert normalize_identity("  Example   SR-500  ") == "example sr-500"
    assert normalize_identity("  हिंदी\u00a0  नाम  ") == "हिंदी नाम"

    assert normalize_search("Example-SR 500mg") == "example sr 500 mg"
    assert normalize_search("Example SR 500-mg") == "example sr 500 mg"


@pytest.mark.parametrize(
    ("form", "route", "expected"),
    (
        ("tablet", "oral", ["tablet"]),
        ("capsule", "oral", ["capsule"]),
        ("syrup", "oral", ["ml", "measuring_spoon"]),
        ("suspension", "oral", ["ml", "measuring_spoon"]),
        ("drops", "oral", ["drop"]),
        ("drops", "ophthalmic", ["drop"]),
        ("drops", "otic", ["drop"]),
        ("inhaler", "inhalation", ["puff"]),
        ("inhalation", "inhalation", ["inhalation"]),
        ("sachet", "oral", ["sachet"]),
        ("packet", "oral", ["packet"]),
        ("powder", "oral", ["sachet", "packet"]),
        ("suppository", "rectal", ["suppository"]),
        ("injection", "intramuscular", ["injection"]),
        ("patch", "transdermal", ["patch"]),
        ("spray", "nasal", ["spray"]),
    ),
)
def test_form_and_route_only_offer_supported_unit_candidates(form, route, expected):
    assert _unit_options(form, route) == expected

    # Identity keys must not erase release/form distinctions used for deduplication.
    assert normalize_identity("Example SR") != normalize_identity("Example ER")
    assert normalize_identity("Example tablet") != normalize_identity("Example capsule")


def test_import_is_idempotent_and_keeps_immutable_raw_record(conn):
    src = source()
    raw = record("row-001", "Example 500")

    first = import_catalog(conn, src, [raw], dry_run=False)
    counts_after_first = {
        name: table_count(conn, name)
        for name in (
            "drug_sources",
            "drug_import_runs",
            "drug_source_records",
            "drug_presentations",
            "drug_ingredients",
        )
    }
    second = import_catalog(conn, src, [raw], dry_run=False)
    counts_after_second = {
        name: table_count(conn, name)
        for name in counts_after_first
    }

    assert first["raw_total"] == 1
    assert first["imported"] == 1
    assert second["raw_total"] == 1
    assert counts_after_second == counts_after_first

    source_row = conn.execute(
        "SELECT source_record_id, raw_json, raw_sha256 FROM drug_source_records"
    ).fetchone()
    assert source_row["source_record_id"] == "row-001"
    assert json.loads(source_row["raw_json"])["brand_name"] == "Example 500"
    assert len(source_row["raw_sha256"]) == 64


def test_dry_run_reports_quality_without_mutating_catalog(conn):
    result = import_catalog(
        conn,
        source(),
        [
            record("complete", "Complete Example"),
            record(
                "incomplete",
                "Incomplete Example",
                generic_name=None,
                ingredients=[],
                strength=None,
                form=None,
            ),
        ],
        dry_run=True,
    )

    assert result["raw_total"] == 2
    assert result["needs_review"] >= 1
    assert table_count(conn, "drug_sources") == 0
    assert table_count(conn, "drug_source_records") == 0
    assert table_count(conn, "drug_presentations") == 0


@pytest.mark.parametrize(
    "metadata",
    [
        source(
            "tier-three-production",
            tier=3,
            usage_scope="production",
            reuse_status="prohibited_without_contract",
        ),
        source(
            "tier-two-unreviewed",
            tier=2,
            usage_scope="production",
            reuse_status="license_unclear",
        ),
    ],
)
def test_unapproved_source_cannot_enter_production(conn, metadata):
    with pytest.raises(ValueError, match="(?i)(source|tier|reuse|production|licen)"):
        import_catalog(conn, metadata, [record("row-1", "Blocked Example")])

    assert table_count(conn, "drug_sources") == 0
    assert table_count(conn, "drug_presentations") == 0


def test_tier_three_demo_is_isolated_from_production_search(conn):
    demo_source = source(
        "isolated-demo",
        tier=3,
        usage_scope="demo",
        reuse_status="demo_only",
    )
    import_catalog(conn, demo_source, [record("demo-1", "Demo Only Example")])

    assert search_catalog(conn, "Demo Only", include_demo=False) == []
    found = search_catalog(conn, "Demo Only", include_demo=True)
    assert found[0]["brand_name"] == "Demo Only Example"
    assert found[0]["usage_scope"] == "demo"


def test_same_source_record_id_in_different_sources_does_not_collide(conn):
    import_catalog(
        conn,
        source("official-a"),
        [record("shared-row-id", "Authority A Example")],
    )
    import_catalog(
        conn,
        source("official-b"),
        [record("shared-row-id", "Authority B Example")],
    )

    assert table_count(conn, "drug_sources") == 2
    assert table_count(conn, "drug_source_records") == 2
    assert table_count(conn, "drug_presentations") == 2


def test_changed_raw_record_creates_immutable_version_without_new_presentation(conn):
    src = source()
    import_catalog(conn, src, [record("row-versioned", "Versioned Example")])
    original_presentation_id = conn.execute(
        "SELECT id FROM drug_presentations"
    ).fetchone()["id"]

    changed = record(
        "row-versioned",
        "Versioned Example Corrected",
        aliases=["Versioned Example"],
    )
    import_catalog(conn, src, [changed])

    assert table_count(conn, "drug_source_records") == 2
    assert table_count(conn, "drug_presentations") == 1
    current = conn.execute(
        "SELECT id, brand_name_raw FROM drug_presentations"
    ).fetchone()
    assert current["id"] == original_presentation_id
    assert current["brand_name_raw"] == "Versioned Example Corrected"
    assert search_catalog(conn, "Versioned Example Corrected")[0]["id"] == original_presentation_id


def test_search_rank_is_exact_then_prefix_alias_ingredient_and_token(conn):
    records = [
        record(
            "exact",
            "Calmora",
            generic_name="Invented Alpha",
            ingredients=[{"name": "Invented Alpha", "strength": "10 mg"}],
            strength="10 mg",
            aliases=["Alpha Calm"],
        ),
        record(
            "prefix",
            "Calmora Plus",
            generic_name="Invented Beta",
            ingredients=[{"name": "Invented Beta", "strength": "20 mg"}],
            strength="20 mg",
        ),
        record(
            "ingredient",
            "Other Brand",
            generic_name="Calmora",
            ingredients=[{"name": "Calmora", "strength": "5 mg"}],
            strength="5 mg",
        ),
    ]
    import_catalog(conn, source(), records)

    exact = search_catalog(conn, "Calmora")
    assert [row["brand_name"] for row in exact[:3]] == [
        "Calmora",
        "Calmora Plus",
        "Other Brand",
    ]
    assert search_catalog(conn, "Alpha Calm")[0]["brand_name"] == "Calmora"
    assert search_catalog(conn, "Invented Beta")[0]["brand_name"] == "Calmora Plus"

    # V1 deliberately avoids edit-distance fuzzy matching for look-alike safety.
    assert search_catalog(conn, "Calmroa") == []


def test_strength_spacing_devanagari_alias_and_long_names_are_searchable(conn):
    long_name = (
        "Extremely Long Invented Combination Product Name With Distinguishing "
        "Release And Dosage Form Words"
    )
    import_catalog(
        conn,
        source(),
        [
            record(
                "unicode-long",
                long_name,
                generic_name="Invented Ingredient A + Invented Ingredient B",
                ingredients=[
                    {"name": "Invented Ingredient A", "strength": "500 mg"},
                    {"name": "Invented Ingredient B", "strength": "125 mg"},
                ],
                strength="500 mg + 125 mg",
                aliases=[{"value": "परीक्षण दवा", "language": "hi"}],
            )
        ],
    )

    assert search_catalog(conn, "500mg")[0]["brand_name"] == long_name
    assert search_catalog(conn, "500-mg")[0]["brand_name"] == long_name
    assert search_catalog(conn, "परीक्षण दवा")[0]["brand_name"] == long_name
    assert search_catalog(conn, "Extremely Long")[0]["brand_name"] == long_name

    ingredients = conn.execute(
        """SELECT i.name_raw
           FROM drug_presentation_ingredients pi
           JOIN drug_ingredients i ON i.id = pi.ingredient_id
           ORDER BY pi.ordinal"""
    ).fetchall()
    assert [row["name_raw"] for row in ingredients] == [
        "Invented Ingredient A",
        "Invented Ingredient B",
    ]


def test_form_route_release_and_strength_variants_are_never_merged(conn):
    variants = [
        record("tab-500", "Varion", strength="500 mg", form="tablet"),
        record("tab-1000", "Varion", strength="1000 mg", form="tablet"),
        record("capsule", "Varion", strength="500 mg", form="capsule"),
        record("sr", "Varion SR", release_modifier="SR"),
        record("er", "Varion ER", release_modifier="ER"),
        record("eye", "Clearway Drops", form="eye drops", route="ophthalmic"),
        record("ear", "Clearway Drops", form="ear drops", route="otic"),
        record("oral", "Clearway Drops", form="oral drops", route="oral"),
        record("syrup", "Liquora", form="syrup", strength="100 mg/5 ml"),
        record("suspension", "Liquora", form="suspension", strength="100 mg/5 ml"),
    ]
    import_catalog(conn, source(), variants)

    assert table_count(conn, "drug_presentations") == len(variants)

    varion = search_catalog(conn, "Varion", limit=20)
    identities = {
        (
            row["strength"],
            row["form"],
            row.get("route"),
            row.get("release_modifier"),
        )
        for row in varion
    }
    assert ("500 mg", "tablet", "oral", None) in identities
    assert ("1000 mg", "tablet", "oral", None) in identities
    assert ("500 mg", "capsule", "oral", None) in identities
    assert any(value[-1] == "SR" for value in identities)
    assert any(value[-1] == "ER" for value in identities)

    drops = search_catalog(conn, "Clearway Drops", limit=20)
    assert {row["route"] for row in drops} == {"ophthalmic", "otic", "oral"}
    assert {row["form"] for row in drops} == {"drops"}

    liquora = search_catalog(conn, "Liquora", limit=20)
    assert {row["form"] for row in liquora} == {"syrup", "suspension"}


def test_inactive_is_excluded_and_incomplete_result_is_marked(conn):
    import_catalog(
        conn,
        source(),
        [
            record("inactive", "Inactive Example", status="inactive"),
            record(
                "review",
                "Review Example",
                generic_name=None,
                ingredients=[],
                strength=None,
                form=None,
            ),
        ],
    )

    assert search_catalog(conn, "Inactive") == []
    result = search_catalog(conn, "Review Example")[0]
    assert result["review_status"] == "needs_review"
    assert result["warnings"]


def test_sql_wildcards_are_literal_search_input(conn):
    import_catalog(
        conn,
        source(),
        [
            record("ordinary", "Ordinary Example"),
            record("percent", "Percent 5% Example"),
        ],
    )

    # A user-supplied wildcard must not turn into a query that returns everything.
    assert search_catalog(conn, "%_") == []
    assert search_catalog(conn, "5% Example")[0]["brand_name"] == "Percent 5% Example"


def test_production_import_rejects_unregistered_and_tampered_packages(conn):
    metadata = source("hash-gated-source")
    rows = [record("hash-row", "Hash Gated Example")]

    with pytest.raises(ValueError, match="not registered"):
        _import_catalog(
            conn,
            metadata,
            rows,
            approval_registry={"approved_packages": {}},
        )

    registry = approval_registry(metadata, rows)
    tampered = [{**rows[0], "strength": "999 mg"}]
    with pytest.raises(ValueError, match="records SHA-256"):
        _import_catalog(
            conn,
            metadata,
            tampered,
            approval_registry=registry,
        )

    changed_metadata = {**metadata, "attribution_text": "Changed attribution"}
    with pytest.raises(ValueError, match="source metadata"):
        _import_catalog(
            conn,
            changed_metadata,
            rows,
            approval_registry=registry,
        )

    assert table_count(conn, "drug_sources") == 0


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("accessed_at", None, "accessed_at"),
        ("source_updated_at", "2026-07", "source_updated_at"),
        ("source_updated_at", None, "requires published_at"),
    ],
)
def test_production_requires_real_source_and_access_dates(conn, field, value, error):
    metadata = source("dated-source")
    metadata["published_at"] = None
    metadata["source_updated_at"] = "2026-07-01"
    metadata[field] = value
    rows = [record("dated-row", "Dated Example")]

    with pytest.raises(ValueError, match=error):
        _import_catalog(
            conn,
            metadata,
            rows,
            approval_registry=approval_registry(metadata, rows),
        )


def test_missing_production_source_record_id_is_quarantined(conn):
    metadata = source("missing-id-source")
    raw = record("temporary", "Missing Identifier Example")
    raw.pop("source_record_id")
    result = _import_catalog(
        conn,
        metadata,
        [raw],
        approval_registry=approval_registry(metadata, [raw]),
    )

    assert result["imported"] == 0
    assert result["excluded"] == 1
    assert result["quarantined"] == 1
    assert result["quarantined_records"][0]["stage"] == "normalization"
    assert table_count(conn, "drug_presentations") == 0
    linked = conn.execute(
        "SELECT ingest_status, errors_json FROM drug_import_run_records"
    ).fetchone()
    assert linked["ingest_status"] == "quarantined"
    assert "source_record_id_missing_production" in json.loads(linked["errors_json"])


def test_strength_parser_populates_only_unambiguous_values_and_preserves_raw_basis(conn):
    rows = [
        record(
            "simple",
            "Simple Strength",
            ingredients=[
                {"name": "Invented Ingredient", "strength": "500 mg", "basis": "as base"}
            ],
        ),
        record(
            "ratio",
            "Ratio Strength",
            ingredients=[{"name": "Invented Ratio", "strength": "100 mg/5 ml"}],
            generic_name="Invented Ratio",
            strength="100 mg/5 ml",
        ),
        record(
            "combined",
            "Combined Strength",
            ingredients=[{"name": "Invented Combined", "strength": "500 mg + 125 mg"}],
            generic_name="Invented Combined",
            strength="500 mg + 125 mg",
        ),
        record(
            "equivalent",
            "Equivalent Strength",
            ingredients=[
                {"name": "Invented Salt", "strength": "10 mg equivalent to 8 mg"}
            ],
            generic_name="Invented Salt",
            strength="10 mg equivalent to 8 mg",
        ),
    ]
    import_catalog(conn, source("strength-source"), rows)

    values = {
        row["strength_raw"]: (
            row["strength_value"], row["strength_unit"], row["basis_raw"]
        )
        for row in conn.execute(
            """SELECT strength_raw, strength_value, strength_unit, basis_raw
               FROM drug_presentation_ingredients"""
        )
    }
    assert values["500 mg"] == (500.0, "mg", "as base")
    assert values["100 mg/5 ml"][:2] == (None, None)
    assert values["500 mg + 125 mg"][:2] == (None, None)
    assert values["10 mg equivalent to 8 mg"][:2] == (None, None)


def test_source_and_license_snapshots_and_unchanged_raw_link_to_each_release(conn):
    rows = [record("stable-source-row", "Stable Provenance Example")]
    first_source = source("release-source", version="2026-07-v1")
    first_source["license_name"] = "Licence snapshot v1"
    import_catalog(conn, first_source, rows)

    second_source = source("release-source", version="2026-08-v2")
    second_source["license_name"] = "Licence snapshot v2"
    # A changed licence/provenance contract requires a new versioned source slug.
    second_source["slug"] = "release-source-v2"
    import_catalog(conn, second_source, rows)

    assert table_count(conn, "drug_source_records") == 2
    assert table_count(conn, "drug_import_run_records") == 2
    runs = conn.execute(
        """SELECT version, source_snapshot_json, license_snapshot_json,
                  approval_snapshot_json, records_sha256
           FROM drug_import_runs ORDER BY rowid"""
    ).fetchall()
    assert json.loads(runs[0]["license_snapshot_json"])["license_name"] == "Licence snapshot v1"
    assert json.loads(runs[1]["license_snapshot_json"])["license_name"] == "Licence snapshot v2"
    assert json.loads(runs[0]["source_snapshot_json"])["accessed_at"] == "2026-07-10"
    assert json.loads(runs[0]["approval_snapshot_json"])["records_sha256"] == runs[0]["records_sha256"]

    # Same source, a later approved version, and unchanged raw must reuse one raw row
    # while linking that immutable row to both import runs.
    same_slug_v1 = source("same-raw-release", version="v1")
    import_catalog(conn, same_slug_v1, rows)
    same_slug_v2 = source("same-raw-release", version="v2")
    import_catalog(conn, same_slug_v2, rows)
    source_id = conn.execute(
        "SELECT id FROM drug_sources WHERE slug='same-raw-release'"
    ).fetchone()["id"]
    assert conn.execute(
        "SELECT COUNT(*) n FROM drug_source_records WHERE source_id=?", (source_id,)
    ).fetchone()["n"] == 1
    assert conn.execute(
        """SELECT COUNT(*) n FROM drug_import_run_records irr
           JOIN drug_import_runs ir ON ir.id=irr.import_run_id
           WHERE ir.source_id=?""",
        (source_id,),
    ).fetchone()["n"] == 2
    presentation_id = conn.execute(
        "SELECT id FROM drug_presentations WHERE source_id=?", (source_id,)
    ).fetchone()["id"]
    snapshot = get_presentation_snapshot(conn, presentation_id)
    assert snapshot["provenance"]["source_version"] == "v2"
    assert snapshot["provenance"]["records_sha256"] == records_sha256(rows)


def test_unverified_alias_match_is_explicit_and_warned(conn):
    rows = [
        record(
            "alias-row",
            "Primary Example",
            aliases=[
                {"value": "Verified spelling", "review_status": "verified"},
                {"value": "Unverified spelling", "review_status": "unverified"},
            ],
        )
    ]
    import_catalog(conn, source("alias-source"), rows)

    verified = search_catalog(conn, "Verified spelling")[0]
    assert verified["matched_alias"]["review_status"] == "verified"
    assert not any("alias is unverified" in value.lower() for value in verified["warnings"])

    unverified = search_catalog(conn, "Unverified spelling")[0]
    assert unverified["matched_alias"]["value"] == "Unverified spelling"
    assert unverified["matched_alias"]["review_status"] == "unverified"
    assert any("alias is unverified" in value.lower() for value in unverified["warnings"])


def test_malformed_record_is_isolated_and_valid_sibling_imports(conn):
    metadata = source(
        "malformed-demo",
        tier=3,
        usage_scope="demo",
        reuse_status="demo_only",
    )
    malformed = record(
        "bad-row",
        "Bad Ingredient Ordinals",
        ingredients=[
            {"name": "Invented Bad A", "strength": "1 mg", "ordinal": 1},
            {"name": "Invented Bad B", "strength": "2 mg", "ordinal": 1},
        ],
    )
    valid = record("good-row", "Good Sibling")

    result = import_catalog(conn, metadata, [malformed, valid])

    assert result["imported"] == 1
    assert result["quarantined"] == 1
    assert search_catalog(conn, "Good Sibling", include_demo=True)[0]["brand_name"] == "Good Sibling"
    assert search_catalog(conn, "Bad Ingredient", include_demo=True) == []
    statuses = {
        row["source_record_id"]: row["ingest_status"]
        for row in conn.execute(
            "SELECT source_record_id, ingest_status FROM drug_import_run_records"
        )
    }
    assert statuses == {"bad-row": "quarantined", "good-row": "needs_review"}
