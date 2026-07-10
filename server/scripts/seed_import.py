"""Bootstrap an isolated production or demo catalog database.

Production is the default and imports only approved NPPA data. Demo scope imports
NPPA plus project-authored synthetic fixtures and requires an explicit non-production
database path. ``--dry-run`` never initializes the requested database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from app import db  # noqa: E402
from app.drug_catalog import build_quality_report, import_catalog  # noqa: E402
from scripts.catalog_import import (  # noqa: E402
    DEFAULT_REPORT_DIR,
    aggregate_quality_reports,
    attach_database_quality,
    load_package,
    print_report_summary,
    write_quality_reports,
)

REPO_ROOT = SERVER_DIR.parent
NPPA_PATH = REPO_ROOT / "data" / "catalog" / "nppa-anti-diabetes-2026-03.json"
DEMO_PATH = REPO_ROOT / "data" / "catalog" / "indoro-synthetic-demo-v1.json"

DEMO_PHARMACY = {
    "id": "ph-demo-001",
    "name": "Sharma Medical Store",
    "area": "Karol Bagh, Delhi",
    "pincode": "110005",
    "ui_lang": "en",
    "default_patient_lang": "hi",
    "has_printer": 0,
    "is_active": 1,
}


def run_seed_import(
    *,
    dry_run: bool,
    catalog_scope: str = "production",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    nppa_source, nppa_records = load_package(NPPA_PATH)
    if nppa_source.get("tier") != 1 or nppa_source.get("usage_scope") != "production":
        raise ValueError("NPPA seed package must remain Tier 1 production data")
    if catalog_scope not in ("production", "demo"):
        raise ValueError("catalog_scope must be production or demo")
    packages: tuple[tuple[dict[str, Any], list[dict[str, Any]]], ...] = (
        (nppa_source, nppa_records),
    )
    if catalog_scope == "demo":
        demo_source, demo_records = load_package(DEMO_PATH)
        if demo_source.get("tier") != 3 or demo_source.get("usage_scope") != "demo":
            raise ValueError("synthetic fixture package must remain Tier 3 demo data")
        packages += ((demo_source, demo_records),)
        if not dry_run:
            if db_path is None:
                raise ValueError("demo import requires an explicit isolated --db-path")
            if Path(db_path).resolve() == db.DB_PATH_DEFAULT.resolve():
                raise ValueError("demo import cannot target the production database")

    if dry_run:
        connection = db.get_conn(":memory:")
        connection.executescript(db.SCHEMA_SQL)
    else:
        db.init_db(db_path)
        connection = db.get_conn(db_path)
    try:
        reports = [
            import_catalog(
                connection,
                source,
                records,
                dry_run=dry_run,
                database_mode=catalog_scope,
            )
            for source, records in packages
        ]
        if dry_run:
            # Ephemeral relational pass for cross-source duplicate/similar-name queues.
            for source, records in packages:
                import_catalog(
                    connection,
                    source,
                    records,
                    dry_run=False,
                    database_mode=catalog_scope,
                )
        if not dry_run and catalog_scope == "demo":
            db.upsert_pharmacy(connection, DEMO_PHARMACY)
        database_quality = build_quality_report(connection)
    finally:
        connection.close()
    return attach_database_quality(aggregate_quality_reports(reports), database_quality)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap an isolated production or demo medicine catalog."
    )
    parser.add_argument(
        "--catalog-scope",
        choices=("production", "demo"),
        default="production",
        help="production imports NPPA only; demo adds synthetic fixtures and requires --db-path",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report without touching the requested database or demo pharmacy",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply both packages (the default when --dry-run is absent)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help=f"quality report directory (default: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="SQLite database path (default: server/var/indoro.db)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_seed_import(
            dry_run=args.dry_run,
            catalog_scope=args.catalog_scope,
            db_path=args.db_path,
        )
        json_path, markdown_path = write_quality_reports(report, args.report_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print_report_summary(report, json_path, markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
