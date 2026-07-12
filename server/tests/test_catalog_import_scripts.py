"""Focused CLI-boundary tests for source-aware catalog imports."""
from __future__ import annotations

import json

import pytest

from app import db
from app import drug_catalog
from app.drug_catalog import records_sha256, source_metadata_sha256
from scripts import catalog_import, seed_import


def _production_package() -> dict:
    return {
        "source": {
            "slug": "official-cli-test",
            "name": "Official CLI test source",
            "operator": "Test Public Authority",
            "tier": 1,
            "usage_scope": "production",
            "reuse_status": "approved",
            "license_name": "Test licence",
            "license_url": "https://example.invalid/licence",
            "attribution_text": "Source: Test Public Authority",
            "version": "2026-07",
            "published_at": "2026-07-01",
            "source_updated_at": "2026-07-01",
            "accessed_at": "2026-07-10",
            "input_uri": "https://example.invalid/catalog.json",
        },
        "records": [
            {
                "source_record_id": "official-1",
                "name_type": "generic",
                "brand_name": "Invented Official Tablet",
                "generic_name": "Invented Ingredient",
                "ingredients": [{"name": "Invented Ingredient", "strength": "10 mg"}],
                "strength": "10 mg",
                "form": "tablet",
                "route": "oral",
                "aliases": [],
            }
        ],
    }


@pytest.fixture(autouse=True)
def local_production_approval_registry(tmp_path, monkeypatch):
    package = _production_package()
    source = package["source"]
    registry = {
        "schema_version": 2,
        "approved_packages": {
            source["slug"]: {
                "approval_status": "approved",
                "approved_at": "2026-07-10",
                "approved_by_role": "test data governance",
                "records_sha256": records_sha256(package["records"]),
                "source_metadata_sha256": source_metadata_sha256(source),
            }
        },
    }
    path = tmp_path / "drug-sources.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(drug_catalog, "PRODUCTION_APPROVAL_REGISTRY_PATH", path)


def _demo_package() -> dict:
    return {
        "source": {
            "slug": "synthetic-cli-demo",
            "name": "Synthetic CLI demo",
            "operator": "indoro test suite",
            "tier": 3,
            "usage_scope": "demo",
            "reuse_status": "demo_only",
            "version": "1.0.0",
            "input_uri": "data/catalog/synthetic-cli-demo.json",
        },
        "records": [
            {
                "source_record_id": "demo-1",
                "brand_name": "DEMO Invented Tablet",
                "generic_name": "Invented Demo Ingredient",
                "ingredients": [
                    {"name": "Invented Demo Ingredient", "strength": "20 mg"}
                ],
                "strength": "20 mg",
                "form": "tablet",
                "route": "oral",
                "review_status": "unverified",
                "aliases": ["Demo Invented"],
            }
        ],
    }


def test_retirement_preview_flag_without_dry_run_leaves_no_committed_state(tmp_path):
    """반례 CX-6 (INV-9): --retirement-preview-db는 --dry-run 전용인데, 검증이 apply
    커밋 뒤에 있어 실수로 --dry-run 없이 부른 '실패' 명령이 완전한 apply를 커밋한다.

    운영자가 읽기 전용 프리뷰를 의도했으나 --dry-run을 빠뜨리면(apply가 기본), DB 파일
    생성·모드 브랜딩·source·import run·presentation이 커밋된 뒤 ValueError가 나서
    보고서도 없이 실패한다 — 실패한 명령이 상태를 남기면 안 된다는 계약(INV-9) 위반."""
    package = _demo_package()
    target = tmp_path / "target.db"
    baseline = tmp_path / "baseline.db"
    db.init_db(baseline)  # 존재하는 baseline이라 '파일 없음'이 아닌 플래그 검증에 도달

    with pytest.raises(ValueError):
        catalog_import.run_catalog_import(
            package["source"],
            package["records"],
            dry_run=False,
            db_path=str(target),
            database_mode="demo",
            retirement_preview_db=str(baseline),
        )

    # 실패한 프리뷰 오용은 대상 DB에 어떤 커밋도 남기지 않아야 한다
    if target.exists():
        conn = db.get_conn(target)
        try:
            runs = conn.execute("SELECT COUNT(*) AS n FROM drug_import_runs").fetchone()["n"]
            presentations = conn.execute(
                "SELECT COUNT(*) AS n FROM drug_presentations"
            ).fetchone()["n"]
        finally:
            conn.close()
        assert runs == 0, "실패한 프리뷰 명령이 import run을 커밋했다 (INV-9 위반)"
        assert presentations == 0, "실패한 프리뷰 명령이 presentation을 커밋했다"


def test_metaless_db_with_production_sources_refuses_demo_first_apply(tmp_path):
    """반례 CX-7 (INV-8): catalog_database_meta 행이 없는데 production source가 이미
    있는 DB(meta 테이블 도입 이전에 seed된 legacy DB의 상태)를 demo first-apply가
    조용히 demo로 브랜딩한다 — demo 레코드가 production 레코드 옆에 앉고, 이후 모든
    production refresh가 영구 거부된다.

    missing-row 분기가 production 방향만 보호(demo source가 있으면 production 거부)하고
    demo 방향(production source가 있으면 demo 거부)은 빠져 있던 비대칭이 원인."""
    path = tmp_path / "legacy.db"
    db.init_db(path)
    conn = db.get_conn(path)
    try:
        # meta 도입 이전 legacy 상태 재현: production source 존재 + meta 행 없음
        conn.execute(
            """INSERT INTO drug_sources
               (id, slug, name, operator, tier, usage_scope, reuse_status,
                created_at, updated_at)
               VALUES ('src-prod','official-legacy','Official legacy','Authority',
                       1,'production','approved','2026-01-01T00:00:00Z',
                       '2026-01-01T00:00:00Z')""",
        )
        conn.execute("DELETE FROM catalog_database_meta")
        conn.commit()

        package = _demo_package()
        with pytest.raises(ValueError):
            drug_catalog.import_catalog(
                conn, package["source"], package["records"], database_mode="demo"
            )

        # 브랜딩·demo source 유입이 없어야 한다
        meta = conn.execute(
            "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
        ).fetchone()
        assert meta is None or meta["catalog_mode"] == "production"
        demo_sources = conn.execute(
            "SELECT COUNT(*) AS n FROM drug_sources WHERE usage_scope='demo'"
        ).fetchone()["n"]
        assert demo_sources == 0, "demo source가 production DB에 유입됐다 (INV-8 위반)"
    finally:
        conn.close()


def test_generic_cli_dry_run_then_apply_preserves_raw_record(tmp_path):
    package = _production_package()
    package_path = tmp_path / "package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    database = tmp_path / "catalog.db"

    assert catalog_import.main(
        [
            str(package_path),
            "--dry-run",
            "--db-path",
            str(database),
            "--report-dir",
            str(tmp_path / "dry-reports"),
        ]
    ) == 0
    assert not database.exists()

    assert catalog_import.main(
        [
            str(package_path),
            "--apply",
            "--db-path",
            str(database),
            "--report-dir",
            str(tmp_path / "apply-reports"),
        ]
    ) == 0
    connection = db.get_conn(database)
    try:
        assert connection.execute(
            "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
        ).fetchone()["catalog_mode"] == "production"
        stored = connection.execute(
            "SELECT raw_json FROM drug_source_records WHERE source_record_id='official-1'"
        ).fetchone()
        assert json.loads(stored["raw_json"]) == package["records"][0]
    finally:
        connection.close()

    report = json.loads(
        (tmp_path / "apply-reports" / "drug-catalog-quality.json").read_text()
    )
    assert report["source_slug"] == "official-cli-test"
    assert report["dry_run"] is False
    assert "dangerous_similar_name_candidates" in report
    assert report["database_quality"]["raw_total"] == 1
    assert (tmp_path / "apply-reports" / "drug-catalog-quality.md").exists()


def test_seed_cli_keeps_production_and_demo_catalogs_in_separate_databases(
    tmp_path, monkeypatch
):
    production_path = tmp_path / "production.json"
    production_path.write_text(json.dumps(_production_package()), encoding="utf-8")
    demo_package = _demo_package()
    demo_path = tmp_path / "demo.json"
    demo_path.write_text(json.dumps(demo_package), encoding="utf-8")
    monkeypatch.setattr(seed_import, "NPPA_PATH", production_path)
    monkeypatch.setattr(seed_import, "DEMO_PATH", demo_path)

    dry_database = tmp_path / "dry.db"
    assert seed_import.main(
        [
            "--dry-run",
            "--db-path",
            str(dry_database),
            "--report-dir",
            str(tmp_path / "seed-dry-reports"),
        ]
    ) == 0
    assert not dry_database.exists()

    production_database = tmp_path / "production.db"
    assert seed_import.main(
        [
            "--apply",
            "--db-path",
            str(production_database),
            "--report-dir",
            str(tmp_path / "seed-production-reports"),
        ]
    ) == 0
    connection = db.get_conn(production_database)
    try:
        assert {
            (row["slug"], row["tier"], row["usage_scope"])
            for row in connection.execute(
                "SELECT slug, tier, usage_scope FROM drug_sources"
            )
        } == {("official-cli-test", 1, "production")}
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM drug_presentations WHERE usage_scope='demo'"
        ).fetchone()["n"] == 0
    finally:
        connection.close()

    demo_database = tmp_path / "demo.db"
    assert seed_import.main(
        [
            "--apply",
            "--catalog-scope",
            "demo",
            "--db-path",
            str(demo_database),
            "--report-dir",
            str(tmp_path / "seed-demo-reports"),
        ]
    ) == 0
    connection = db.get_conn(demo_database)
    try:
        assert {
            (row["slug"], row["tier"], row["usage_scope"])
            for row in connection.execute(
                "SELECT slug, tier, usage_scope FROM drug_sources"
            )
        } == {
            ("official-cli-test", 1, "production"),
            ("synthetic-cli-demo", 3, "demo"),
        }
        raw = connection.execute(
            "SELECT raw_json FROM drug_source_records WHERE source_record_id='demo-1'"
        ).fetchone()
        assert json.loads(raw["raw_json"]) == demo_package["records"][0]
        pharmacy = connection.execute(
            "SELECT id FROM pharmacies WHERE id=?", (seed_import.DEMO_PHARMACY["id"],)
        ).fetchone()
        assert pharmacy is not None
    finally:
        connection.close()

    report = json.loads(
        (tmp_path / "seed-demo-reports" / "drug-catalog-quality.json").read_text()
    )
    assert report["source_count"] == 2
    assert report["source_slugs"] == ["official-cli-test", "synthetic-cli-demo"]
    assert report["database_quality"]["raw_total"] == 2
    assert {row["usage_scope"] for row in report["source_breakdown"]} == {
        "production",
        "demo",
    }


def test_demo_seed_requires_explicit_nonproduction_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", tmp_path / "production.db")

    try:
        seed_import.run_seed_import(dry_run=False, catalog_scope="demo")
    except ValueError as exc:
        assert "explicit isolated --db-path" in str(exc)
    else:
        raise AssertionError("demo seed unexpectedly used the production database")

    try:
        seed_import.run_seed_import(
            dry_run=False,
            catalog_scope="demo",
            db_path=db.DB_PATH_DEFAULT,
        )
    except ValueError as exc:
        assert "cannot target the production database" in str(exc)
    else:
        raise AssertionError("demo seed unexpectedly accepted the production path")


def test_generic_import_rejects_demo_apply_to_default_database(tmp_path, monkeypatch):
    production_database = tmp_path / "production.db"
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", production_database)
    package = _demo_package()

    with pytest.raises(ValueError, match="explicit isolated --db-path"):
        catalog_import.run_catalog_import(
            package["source"], package["records"], dry_run=False
        )

    with pytest.raises(ValueError, match="cannot target the production database"):
        catalog_import.run_catalog_import(
            package["source"],
            package["records"],
            dry_run=False,
            db_path=production_database,
        )

    isolated_database = tmp_path / "demo.db"
    report = catalog_import.run_catalog_import(
        package["source"],
        package["records"],
        dry_run=False,
        db_path=isolated_database,
    )
    assert report["imported"] == 1
    assert isolated_database.exists()
    assert not production_database.exists()
    connection = db.get_conn(isolated_database)
    try:
        assert connection.execute(
            "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
        ).fetchone()["catalog_mode"] == "demo"
    finally:
        connection.close()

    production_package = _production_package()
    with pytest.raises(ValueError, match="catalog database mode is demo"):
        catalog_import.run_catalog_import(
            production_package["source"],
            production_package["records"],
            dry_run=False,
            db_path=isolated_database,
        )


def test_generic_cli_quarantines_malformed_record_and_imports_valid_sibling(tmp_path):
    package = _demo_package()
    package["records"] = ["malformed scalar", package["records"][0]]
    package_path = tmp_path / "malformed-with-valid.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    database = tmp_path / "isolated-demo.db"
    report_dir = tmp_path / "reports"

    assert catalog_import.main(
        [
            str(package_path),
            "--apply",
            "--db-path",
            str(database),
            "--report-dir",
            str(report_dir),
        ]
    ) == 0

    report = json.loads((report_dir / "drug-catalog-quality.json").read_text())
    assert report["raw_total"] == 2
    assert report["imported"] == 1
    assert report["excluded"] == 1
    assert report["quarantined"] == 1
    assert report["failed_records"][0]["quarantined"] is True

    connection = db.get_conn(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM drug_presentations"
        ).fetchone()["n"] == 1
        quarantined = connection.execute(
            """SELECT rr.ingest_status, sr.raw_json
               FROM drug_import_run_records rr
               JOIN drug_source_records sr ON sr.id = rr.source_record_row_id
               WHERE rr.ingest_status = 'quarantined'"""
        ).fetchone()
        assert quarantined["ingest_status"] == "quarantined"
        assert json.loads(quarantined["raw_json"]) == "malformed scalar"
    finally:
        connection.close()
