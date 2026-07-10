"""Import one source-aware medicine catalog package and write quality reports.

The input JSON contract is ``{"source": {...}, "records": [...]}``.  Apply is
the default mode; ``--dry-run`` performs normalization and quality checks without
opening or initializing the requested database.
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

REPO_ROOT = SERVER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "data" / "reports"
REPORT_BASENAME = "drug-catalog-quality"

QUALITY_FIELDS = (
    "raw_total",
    "imported",
    "excluded",
    "quarantined",
    "needs_review",
    "duplicate_candidates",
    "missing_brand_name",
    "missing_generic_name",
    "missing_strength",
    "missing_dosage_form",
    "missing_manufacturer",
    "missing_source",
    "unknown_strength_unit",
    "unknown_dosage_form",
)

REVIEW_QUEUE_FIELDS = (
    "same_brand_different_ingredient_candidates",
    "same_composition_multiple_brand_candidates",
    "dangerous_similar_name_candidates",
)


def load_package(path: str | Path) -> tuple[dict[str, Any], list[Any]]:
    """Load and minimally validate one ``{source, records}`` JSON package."""
    package_path = Path(path)
    with package_path.open(encoding="utf-8") as handle:
        package = json.load(handle)
    if not isinstance(package, dict):
        raise ValueError("catalog package must be a JSON object")
    source = package.get("source")
    records = package.get("records")
    if not isinstance(source, dict):
        raise ValueError("catalog package 'source' must be an object")
    if not isinstance(records, list):
        raise ValueError("catalog package 'records' must be an array")
    # 레코드 단위 오류는 전체 패키지를 버리지 않고 importer가 격리한다.
    # JSON scalar도 원본 증거로 그대로 넘긴다.
    return dict(source), [dict(record) if isinstance(record, dict) else record for record in records]


def run_catalog_import(
    source: dict[str, Any],
    records: list[Any],
    *,
    dry_run: bool,
    db_path: str | Path | None = None,
    database_mode: str | None = None,
) -> dict[str, Any]:
    """Run the catalog API and attach whole-database quality review queues.

    Dry-run normalizes once without writes, then applies into an ephemeral in-memory
    schema solely to calculate relational/cross-record checks. The requested DB is
    never opened or created in that mode.
    """
    effective_mode = database_mode or source.get("usage_scope")
    if effective_mode not in ("production", "demo"):
        raise ValueError("database_mode must be production or demo")
    if source.get("usage_scope") == "demo" and effective_mode != "demo":
        raise ValueError("demo source requires --database-mode demo")
    if not dry_run and effective_mode == "demo":
        if db_path is None:
            raise ValueError("demo import requires an explicit isolated --db-path")
        if Path(db_path).expanduser().resolve() == Path(db.DB_PATH_DEFAULT).expanduser().resolve():
            raise ValueError("demo import cannot target the production database")

    if dry_run:
        connection = db.get_conn(":memory:")
        connection.executescript(db.SCHEMA_SQL)
    else:
        db.init_db(db_path)
        connection = db.get_conn(db_path)
    try:
        report = import_catalog(
            connection,
            source,
            records,
            dry_run=dry_run,
            database_mode=effective_mode,
        )
        if dry_run:
            import_catalog(
                connection,
                source,
                records,
                dry_run=False,
                database_mode=effective_mode,
            )
        report["database_quality"] = build_quality_report(connection)
        for field in REVIEW_QUEUE_FIELDS:
            report[field] = report["database_quality"].get(field) or []
        report["source_breakdown"] = report["database_quality"].get("source_breakdown") or []
        return report
    finally:
        connection.close()


def aggregate_quality_reports(reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build one seed-run report while retaining each source report verbatim."""
    source_reports = [dict(report) for report in reports]
    aggregate: dict[str, Any] = {
        field: sum(int(report.get(field) or 0) for report in source_reports)
        for field in QUALITY_FIELDS
    }
    aggregate.update(
        {
            "dry_run": all(bool(report.get("dry_run")) for report in source_reports),
            "source_count": len(source_reports),
            "source_slugs": [report.get("source_slug") for report in source_reports],
            "source_reports": source_reports,
            "failed_records": [
                {
                    "source_slug": report.get("source_slug"),
                    **failed,
                }
                for report in source_reports
                for failed in report.get("failed_records", [])
            ],
        }
    )
    return aggregate


def attach_database_quality(
    report: dict[str, Any], database_quality: dict[str, Any]
) -> dict[str, Any]:
    """Attach cross-source queues without replacing per-import count semantics."""
    report["database_quality"] = database_quality
    report["generated_at"] = database_quality.get("generated_at")
    report["database_mode"] = database_quality.get("database_mode")
    source_reports = report.get("source_reports") or [report]
    report["replayed"] = bool(source_reports) and all(
        bool(source.get("replayed")) for source in source_reports
    )
    report["source_breakdown"] = database_quality.get("source_breakdown") or []
    for field in REVIEW_QUEUE_FIELDS:
        report[field] = database_quality.get(field) or []
    return report


def _markdown_metrics(report: dict[str, Any]) -> list[str]:
    lines = ["| Metric | Count |", "| --- | ---: |"]
    lines.extend(
        f"| {field.replace('_', ' ')} | {int(report.get(field) or 0)} |"
        for field in QUALITY_FIELDS
    )
    return lines


def _markdown_source(report: dict[str, Any], *, heading_level: int) -> list[str]:
    heading = "#" * heading_level
    lines = [
        f"{heading} Source: `{report.get('source_slug', 'unknown')}`",
        "",
        f"- Version: `{report.get('source_version', '')}`",
        f"- Usage scope: `{report.get('usage_scope', '')}`",
        f"- Mode: `{'dry-run' if report.get('dry_run') else 'apply'}`",
        f"- Source accessed: `{report.get('source_accessed_at') or ''}`",
        f"- Source updated: `{report.get('source_updated_at') or ''}`",
        f"- Source input: `{report.get('source_input_uri') or ''}`",
        f"- Reuse status: `{report.get('reuse_status') or ''}`",
        f"- Licence: `{report.get('license_name') or ''}` ({report.get('license_url') or 'not applicable'})",
        f"- Source artifact manifest: `{report.get('source_artifact_manifest') or 'not applicable'}`",
        f"- Source artifact SHA-256: `{report.get('source_artifact_sha256') or 'not applicable'}`",
        f"- Transformation: `{report.get('transformation_method') or 'not applicable'}`",
        f"- Input SHA-256: `{report.get('input_sha256', '')}`",
        f"- Records SHA-256: `{report.get('records_sha256', '')}`",
        f"- Approval registry SHA-256: `{report.get('approval_registry_sha256') or 'not applicable'}`",
        "",
        *_markdown_metrics(report),
    ]
    failed_records = report.get("failed_records") or []
    if failed_records:
        lines.extend(["", f"{heading}# Excluded records", ""])
        for failed in failed_records:
            record_id = str(failed.get("source_record_id", "unknown")).replace("`", "\\`")
            errors = ", ".join(str(value) for value in failed.get("errors") or []) or "unspecified"
            lines.append(f"- `{record_id}`: {errors}")
    return lines


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render an operator-readable companion to the machine-readable JSON."""
    lines = [
        "# Drug Catalog Quality Report",
        "",
        f"- Generated: `{report.get('generated_at') or ''}`",
        f"- Database mode: `{report.get('database_mode') or 'ephemeral dry-run'}`",
        f"- Idempotent replay: `{'yes' if report.get('replayed') else 'no'}`",
        "",
    ]
    source_reports = report.get("source_reports")
    if isinstance(source_reports, list):
        lines.extend(
            [
                f"- Mode: `{'dry-run' if report.get('dry_run') else 'apply'}`",
                f"- Sources: {int(report.get('source_count') or 0)}",
                "",
                "## Combined quality",
                "",
                *_markdown_metrics(report),
            ]
        )
        for source_report in source_reports:
            lines.extend(["", *_markdown_source(source_report, heading_level=2)])
    else:
        lines.extend(_markdown_source(report, heading_level=2))
    database_quality = report.get("database_quality") or {}
    if database_quality:
        lines.extend([
            "",
            "## Current catalog review queues",
            "",
            "These are candidates for human review. No records are merged automatically.",
            "",
            f"- Same brand, different ingredient: {len(database_quality.get('same_brand_different_ingredient_candidates') or [])}",
            f"- Same composition, multiple brands: {len(database_quality.get('same_composition_multiple_brand_candidates') or [])}",
            f"- Dangerous similar names: {len(database_quality.get('dangerous_similar_name_candidates') or [])}",
            "",
            "### Source breakdown",
            "",
            "| Source | Tier | Scope | Presentations |",
            "| --- | ---: | --- | ---: |",
        ])
        for source in database_quality.get("source_breakdown") or []:
            lines.append(
                f"| {source['slug']} | {source['tier']} | {source['usage_scope']} | {source['presentations']} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def write_quality_reports(
    report: dict[str, Any],
    report_dir: str | Path,
    *,
    basename: str = REPORT_BASENAME,
) -> tuple[Path, Path]:
    """Write deterministic JSON and Markdown quality-report files."""
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{basename}.json"
    markdown_path = directory / f"{basename}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def print_report_summary(
    report: dict[str, Any], json_path: Path, markdown_path: Path
) -> None:
    mode = "dry-run" if report.get("dry_run") else "apply"
    source_label = ", ".join(
        str(value)
        for value in (report.get("source_slugs") or [report.get("source_slug", "unknown")])
    )
    print(f"mode: {mode}")
    print(f"sources: {source_label}")
    print(
        "quality: "
        f"raw={report.get('raw_total', 0)} "
        f"imported={report.get('imported', 0)} "
        f"excluded={report.get('excluded', 0)} "
        f"needs_review={report.get('needs_review', 0)}"
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one {source, records} medicine catalog package."
    )
    parser.add_argument("package", type=Path, help="path to the catalog package JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report without touching the requested database",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply the package (the default when --dry-run is absent)",
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
    parser.add_argument(
        "--database-mode",
        choices=("production", "demo"),
        help="persist/verify the target DB role; defaults to the package usage scope",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        source, records = load_package(args.package)
        report = run_catalog_import(
            source,
            records,
            dry_run=args.dry_run,
            db_path=args.db_path,
            database_mode=args.database_mode,
        )
        json_path, markdown_path = write_quality_reports(report, args.report_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print_report_summary(report, json_path, markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
