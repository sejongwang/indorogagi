"""Build an isolated synthetic database for catalog-operations browser demos."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))

from app import catalog_governance as governance  # noqa: E402
from app import db  # noqa: E402
from app.drug_catalog import import_catalog  # noqa: E402
from scripts.catalog_import import load_package  # noqa: E402


FIXTURE_DIR = REPO_ROOT / "data" / "catalog" / "governance-demo"
PACKAGES = (
    FIXTURE_DIR / "full-v1.json",
    FIXTURE_DIR / "full-v2.json",
    FIXTURE_DIR / "delta-v2-1.json",
)


def _assert_isolated_demo_target(target: Path) -> None:
    """Reject a populated or production database before any migration/write occurs."""
    if not target.exists() or target.stat().st_size == 0:
        return
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "catalog_database_meta" in tables:
            mode = conn.execute(
                "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
            ).fetchone()
            if mode:
                if mode["catalog_mode"] != "demo":
                    raise ValueError("synthetic governance demo cannot target a production database")
                return
        populated = False
        for table in ("pharmacies", "prescriptions", "drug_sources", "drug_presentations"):
            if table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                populated = True
                break
        if populated:
            raise ValueError(
                "synthetic governance demo requires an empty or explicitly demo-mode database"
            )
    except sqlite3.DatabaseError as exc:
        raise ValueError("synthetic governance demo target is not a readable SQLite database") from exc
    finally:
        if "conn" in locals():
            conn.close()


def build_demo(db_path: str | Path) -> dict:
    target = Path(db_path).expanduser().resolve()
    if target == Path(db.DB_PATH_DEFAULT).expanduser().resolve():
        raise ValueError("synthetic governance demo cannot target the production database")
    _assert_isolated_demo_target(target)
    db.init_db(target)
    conn = db.get_conn(target)
    try:
        reports = []
        for package_path in PACKAGES:
            source, records = load_package(package_path)
            if source.get("tier") != 3 or source.get("usage_scope") != "demo":
                raise ValueError("governance demo fixtures must remain Tier 3 demo data")
            reports.append(
                import_catalog(conn, source, records, database_mode="demo")
            )
        db.upsert_pharmacy(
            conn,
            {
                "id": db.DEMO_CATALOG_PHARMACY_ID,
                "name": "indoro Synthetic Catalog Operations Demo Pharmacy",
                "area": "Synthetic fixture — not a real pharmacy",
                "ui_lang": "en",
                "default_patient_lang": "hi",
            },
        )

        complete = conn.execute(
            """SELECT id, workflow_review_status, record_version
               FROM drug_presentations WHERE source_record_id='gov-complete-001'"""
        ).fetchone()
        if complete and complete["workflow_review_status"] == "unverified":
            governance.transition_presentation(
                conn,
                complete["id"],
                action="review_requested",
                expected_record_version=complete["record_version"],
                reason_code="synthetic_demo_review",
                note="Synthetic demo record routed to review; not a medical approval.",
                reviewer_id="demo-pharmacist",
                reviewer_role="synthetic pharmacist reviewer",
            )
            complete = conn.execute(
                "SELECT id, workflow_review_status, record_version FROM drug_presentations WHERE id=?",
                (complete["id"],),
            ).fetchone()
        if complete and complete["workflow_review_status"] == "needs_review":
            governance.transition_presentation(
                conn,
                complete["id"],
                action="review_approved",
                expected_record_version=complete["record_version"],
                reason_code="synthetic_demo_reviewed",
                note="Synthetic data fields checked for UI demonstration only.",
                reviewer_id="demo-pharmacist",
                reviewer_role="synthetic pharmacist reviewer",
            )

        reconsider = conn.execute(
            """SELECT id, workflow_review_status, record_version
               FROM drug_presentations
               WHERE source_record_id='gov-missing-ingredient-002'"""
        ).fetchone()
        rejected_event = conn.execute(
            """SELECT 1 FROM drug_review_decisions
               WHERE presentation_id=? AND action='review_rejected' LIMIT 1""",
            (reconsider["id"],),
        ).fetchone() if reconsider else None
        if reconsider and not rejected_event:
            if reconsider["workflow_review_status"] == "unverified":
                governance.transition_presentation(
                    conn,
                    reconsider["id"],
                    action="review_requested",
                    expected_record_version=reconsider["record_version"],
                    reason_code="synthetic_incomplete",
                    note="Ingredient intentionally omitted in the synthetic fixture.",
                    reviewer_id="demo-pharmacist",
                    reviewer_role="synthetic pharmacist reviewer",
                )
                reconsider = conn.execute(
                    "SELECT * FROM drug_presentations WHERE id=?", (reconsider["id"],)
                ).fetchone()
            governance.transition_presentation(
                conn,
                reconsider["id"],
                action="review_rejected",
                expected_record_version=reconsider["record_version"],
                reason_code="synthetic_missing_ingredient",
                note="Rejected because the synthetic source intentionally omits ingredient data.",
                reviewer_id="demo-pharmacist",
                reviewer_role="synthetic pharmacist reviewer",
            )
            reconsider = conn.execute(
                "SELECT * FROM drug_presentations WHERE id=?", (reconsider["id"],)
            ).fetchone()
            governance.transition_presentation(
                conn,
                reconsider["id"],
                action="review_requested",
                expected_record_version=reconsider["record_version"],
                reason_code="synthetic_recheck_requested",
                note="Returned to the synthetic queue to demonstrate rejected-to-review flow.",
                reviewer_id="demo-pharmacist",
                reviewer_role="synthetic pharmacist reviewer",
            )

        batch = conn.execute(
            """SELECT id FROM drug_retirement_batches
               ORDER BY created_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
        return {
            "db_path": str(target),
            "reports": reports,
            "prescription_url": "/rx/new",
            "review_url": "/catalog/review",
            "retirement_url": f"/catalog/retirements/{batch['id']}" if batch else None,
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an isolated Tier 3 synthetic database for the catalog operations UI."
    )
    parser.add_argument("--db-path", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_demo(args.db_path)
    print(f"Synthetic demo database: {result['db_path']}")
    print(f"Prescription flow: {result['prescription_url']}")
    print(f"Review queue: {result['review_url']}")
    print(f"Retirement batch: {result['retirement_url'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
