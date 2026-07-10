"""Human review and full-snapshot retirement operations for the drug catalog.

Source-derived normalization status remains on ``drug_presentations.review_status``
and ``lifecycle_status``.  This module owns the separate human workflow projection,
optimistic locking, and append-only decision ledgers.  It never recommends a drug,
infers a diagnosis, or changes issued prescription snapshots.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


REVIEW_STATUSES = ("unverified", "needs_review", "approved", "rejected")
LIFECYCLE_STATUSES = ("active", "inactive", "retired")
CANDIDATE_DECISIONS = ("pending", "keep_active", "retire", "needs_investigation")


class CatalogGovernanceError(ValueError):
    """Base class for safe operator-facing catalog governance failures."""


class CatalogNotFound(CatalogGovernanceError):
    pass


class StaleRecordVersion(CatalogGovernanceError):
    pass


class InvalidStateTransition(CatalogGovernanceError):
    pass


class GovernanceValidationError(CatalogGovernanceError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id() -> str:
    return str(uuid.uuid4())


def _require_text(value: Any, field: str, *, maximum: int = 2000) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernanceValidationError(f"{field} is required")
    if len(text) > maximum:
        raise GovernanceValidationError(f"{field} exceeds {maximum} characters")
    return text


def _json_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _normalized_projection_ready(row: sqlite3.Row | dict[str, Any]) -> bool:
    data = dict(row)
    raw = data.get("normalized_projection_json")
    expected = data.get("normalized_projection_sha256")
    if not raw or not expected:
        return False
    try:
        projection = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(projection, dict):
        return False
    canonical = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected


def _latest_source_context(conn: sqlite3.Connection, presentation_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT p.source_id, p.source_record_id, sr.raw_sha256,
                  irr.import_run_id
           FROM drug_presentations p
           LEFT JOIN drug_import_run_records irr
             ON irr.id=p.current_import_run_record_id
           LEFT JOIN drug_source_records sr ON sr.id=irr.source_record_row_id
           WHERE p.id=?
           LIMIT 1""",
        (presentation_id,),
    ).fetchone()
    if row is None:
        raise CatalogNotFound("catalog presentation not found")
    return dict(row)


def _append_decision(
    conn: sqlite3.Connection,
    *,
    presentation: sqlite3.Row | dict[str, Any],
    action: str,
    previous_review_status: str,
    next_review_status: str,
    previous_lifecycle_status: str,
    next_lifecycle_status: str,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
    expected_record_version: int,
    resulting_record_version: int,
    source_record_sha256: str | None = None,
    import_run_id: str | None = None,
    retirement_batch_id: str | None = None,
    retirement_candidate_id: str | None = None,
) -> str:
    data = dict(presentation)
    projection = conn.execute(
        """SELECT normalized_projection_json, normalized_projection_sha256
           FROM drug_presentations WHERE id=?""",
        (data["id"],),
    ).fetchone()
    if projection is None:
        raise CatalogNotFound("catalog presentation not found")
    if source_record_sha256 is None or import_run_id is None:
        context = _latest_source_context(conn, data["id"])
        source_record_sha256 = source_record_sha256 or context.get("raw_sha256")
        import_run_id = import_run_id or context.get("import_run_id")
    decision_id = _id()
    conn.execute(
        """INSERT INTO drug_review_decisions
           (id, presentation_id, retirement_batch_id, retirement_candidate_id,
            source_id, source_record_id, source_record_sha256, import_run_id, action,
            previous_review_status, next_review_status,
            previous_lifecycle_status, next_lifecycle_status,
            reason_code, note, reviewer_id, reviewer_role,
            expected_record_version, resulting_record_version,
            normalized_snapshot_json, normalized_projection_sha256, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            decision_id,
            data["id"],
            retirement_batch_id,
            retirement_candidate_id,
            data["source_id"],
            data["source_record_id"],
            source_record_sha256,
            import_run_id,
            action,
            previous_review_status,
            next_review_status,
            previous_lifecycle_status,
            next_lifecycle_status,
            reason_code,
            note,
            reviewer_id,
            reviewer_role,
            expected_record_version,
            resulting_record_version,
            projection["normalized_projection_json"],
            projection["normalized_projection_sha256"],
            _now(),
        ),
    )
    return decision_id


def _append_batch_event(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    action: str,
    previous_status: str | None,
    next_status: str,
    actor_id: str,
    actor_role: str,
    reason_code: str,
    note: str,
    expected_version: int,
    resulting_version: int,
) -> str:
    event_id = _id()
    conn.execute(
        """INSERT INTO drug_retirement_batch_events
           (id, batch_id, action, previous_status, next_status, actor_id, actor_role,
            reason_code, note, expected_batch_version, resulting_batch_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id, batch_id, action, previous_status, next_status, actor_id,
            actor_role, reason_code, note, expected_version, resulting_version, _now(),
        ),
    )
    return event_id


def transition_presentation(
    conn: sqlite3.Connection,
    presentation_id: str,
    *,
    action: str,
    expected_record_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    """Atomically validate, project, and audit one human decision."""
    reason_code = _require_text(reason_code, "reason_code", maximum=80)
    note = _require_text(note, "note")
    reviewer_id = _require_text(reviewer_id, "reviewer_id", maximum=120)
    reviewer_role = _require_text(reviewer_role, "reviewer_role", maximum=120)
    try:
        expected = int(expected_record_version)
    except (TypeError, ValueError) as exc:
        raise GovernanceValidationError("expected_record_version must be an integer") from exc

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM drug_presentations WHERE id=?", (presentation_id,)
        ).fetchone()
        if row is None:
            raise CatalogNotFound("catalog presentation not found")
        if row["record_version"] != expected:
            raise StaleRecordVersion(
                f"stale presentation version: expected {expected}, current {row['record_version']}"
            )

        previous_review = row["workflow_review_status"]
        previous_lifecycle = row["operational_lifecycle_status"]
        next_review = previous_review
        next_lifecycle = previous_lifecycle
        if action == "review_requested":
            if previous_review not in ("unverified", "rejected", "approved"):
                raise InvalidStateTransition(
                    f"review_requested is not allowed from {previous_review}"
                )
            next_review = "needs_review"
        elif action == "review_approved":
            if previous_review != "needs_review":
                raise InvalidStateTransition(
                    f"review_approved is not allowed from {previous_review}"
                )
            if not _normalized_projection_ready(row):
                raise GovernanceValidationError(
                    "normalized projection must be reprocessed before approval"
                )
            next_review = "approved"
        elif action == "review_rejected":
            if previous_review != "needs_review":
                raise InvalidStateTransition(
                    f"review_rejected is not allowed from {previous_review}"
                )
            if not _normalized_projection_ready(row):
                raise GovernanceValidationError(
                    "normalized projection must be reprocessed before rejection"
                )
            next_review = "rejected"
        elif action == "lifecycle_inactivated":
            if previous_lifecycle != "active":
                raise InvalidStateTransition(
                    f"lifecycle_inactivated is not allowed from {previous_lifecycle}"
                )
            next_lifecycle = "inactive"
        elif action == "lifecycle_reactivated":
            if previous_lifecycle != "inactive":
                raise InvalidStateTransition(
                    f"lifecycle_reactivated is not allowed from {previous_lifecycle}"
                )
            next_lifecycle = "active"
        else:
            raise InvalidStateTransition(f"unsupported catalog action: {action}")

        resulting_version = expected + 1
        cursor = conn.execute(
            """UPDATE drug_presentations
               SET workflow_review_status=?, operational_lifecycle_status=?,
                   record_version=?, updated_at=?
               WHERE id=? AND record_version=?""",
            (
                next_review, next_lifecycle, resulting_version, _now(),
                presentation_id, expected,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleRecordVersion("catalog presentation changed during review")
        _append_decision(
            conn,
            presentation=row,
            action=action,
            previous_review_status=previous_review,
            next_review_status=next_review,
            previous_lifecycle_status=previous_lifecycle,
            next_lifecycle_status=next_lifecycle,
            reason_code=reason_code,
            note=note,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            expected_record_version=expected,
            resulting_record_version=resulting_version,
        )
        conn.execute(
            "UPDATE drugs SET verified=? WHERE id=?",
            (int(next_review == "approved"), presentation_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_review_detail(conn, presentation_id)


def record_source_refresh(
    conn: sqlite3.Connection,
    presentation: sqlite3.Row,
    *,
    import_run_id: str,
    source_record_sha256: str,
    normalized_projection_changed: bool,
) -> tuple[str, int]:
    """Advance source evidence and reopen review only when projection fields changed.

    The caller owns the importer transaction. Every processing run increments the
    optimistic version so older retirement evidence becomes stale. It never reactivates
    a retired/inactive item; an identical deterministic projection preserves the human
    decision, while a changed projection reopens approval/rejection for review.
    """
    previous_review = presentation["workflow_review_status"]
    next_review = previous_review
    if normalized_projection_changed and previous_review in ("approved", "rejected"):
        next_review = "needs_review"
    previous_lifecycle = presentation["operational_lifecycle_status"]
    expected = presentation["record_version"]
    resulting = expected + 1
    cursor = conn.execute(
        """UPDATE drug_presentations
           SET workflow_review_status=?, record_version=?, updated_at=?
           WHERE id=? AND record_version=?""",
        (next_review, resulting, _now(), presentation["id"], expected),
    )
    if cursor.rowcount != 1:
        raise StaleRecordVersion("catalog presentation changed during import")
    _append_decision(
        conn,
        presentation=presentation,
        action="source_record_updated",
        previous_review_status=previous_review,
        next_review_status=next_review,
        previous_lifecycle_status=previous_lifecycle,
        next_lifecycle_status=previous_lifecycle,
        reason_code=(
            "normalized_projection_changed"
            if normalized_projection_changed
            else "source_evidence_reprocessed"
        ),
        note=(
            "A later source or normalization run changed the normalized presentation; "
            "prior approval or rejection was reopened when applicable."
            if normalized_projection_changed
            else "A later source or normalization run produced the same normalized presentation; "
            "the human review decision was preserved while evidence version advanced."
        ),
        reviewer_id="catalog-importer",
        reviewer_role="automated importer",
        expected_record_version=expected,
        resulting_record_version=resulting,
        source_record_sha256=source_record_sha256,
        import_run_id=import_run_id,
    )
    return next_review, resulting


def record_source_ingest_issue(
    conn: sqlite3.Connection,
    presentation: sqlite3.Row,
    *,
    import_run_id: str,
    source_record_sha256: str,
    ingest_status: str,
) -> tuple[str, int]:
    """Keep the last valid searchable fields but invalidate human approval.

    A later package may carry the same stable source ID with an excluded or
    quarantined payload. Treating that ID as simply "present" without surfacing the
    failed update would leave stale approved facts searchable. The importer owns the
    surrounding transaction and links the failed raw evidence to this presentation.
    """
    if ingest_status not in ("excluded", "quarantined"):
        raise GovernanceValidationError("ingest_status must be excluded or quarantined")
    previous_review = presentation["workflow_review_status"]
    next_review = "rejected" if previous_review == "rejected" else "needs_review"
    previous_lifecycle = presentation["operational_lifecycle_status"]
    expected = presentation["record_version"]
    resulting = expected + 1
    cursor = conn.execute(
        """UPDATE drug_presentations
           SET workflow_review_status=?, record_version=?, updated_at=?
           WHERE id=? AND record_version=?""",
        (next_review, resulting, _now(), presentation["id"], expected),
    )
    if cursor.rowcount != 1:
        raise StaleRecordVersion("catalog presentation changed during failed source ingest")
    _append_decision(
        conn,
        presentation=presentation,
        action="source_record_quarantined",
        previous_review_status=previous_review,
        next_review_status=next_review,
        previous_lifecycle_status=previous_lifecycle,
        next_lifecycle_status=previous_lifecycle,
        reason_code=f"source_record_{ingest_status}",
        note=(
            "A later source payload could not replace the last valid searchable fields; "
            + (
                "the existing human rejection remains in force."
                if previous_review == "rejected"
                else "the existing presentation requires human re-checking."
            )
        ),
        reviewer_id="catalog-importer",
        reviewer_role="automated importer",
        expected_record_version=expected,
        resulting_record_version=resulting,
        source_record_sha256=source_record_sha256,
        import_run_id=import_run_id,
    )
    conn.execute("UPDATE drugs SET verified=0 WHERE id=?", (presentation["id"],))
    return next_review, resulting


def list_review_queue(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    source: str = "",
    review_status: str = "",
    lifecycle_status: str = "",
    issue: str = "",
    import_run: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if q.strip():
        from app.drug_catalog import normalize_search

        term = f"%{normalize_search(q)}%"
        clauses.append(
            "(p.brand_name_search LIKE ? OR p.generic_name_search LIKE ? "
            "OR p.strength_search LIKE ? OR p.source_record_id LIKE ?)"
        )
        params.extend((term, term, term, f"%{q.strip()}%"))
    if source:
        clauses.append("s.slug=?")
        params.append(source)
    if review_status in REVIEW_STATUSES:
        clauses.append("p.workflow_review_status=?")
        params.append(review_status)
    if lifecycle_status in LIFECYCLE_STATUSES:
        clauses.append("p.operational_lifecycle_status=?")
        params.append(lifecycle_status)
    if import_run:
        clauses.append(
            "EXISTS (SELECT 1 FROM drug_import_run_records irr_filter "
            "WHERE irr_filter.presentation_id=p.id "
            "AND irr_filter.import_run_id=?)"
        )
        params.append(import_run)
    if issue == "missing_ingredient":
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM drug_presentation_ingredients pi "
            "WHERE pi.presentation_id=p.id)"
        )
    elif issue == "missing_strength":
        clauses.append("(p.strength_raw IS NULL OR TRIM(p.strength_raw)='')")
    elif issue == "missing_dosage_form":
        clauses.append("(p.dosage_form_raw IS NULL OR TRIM(p.dosage_form_raw)='')")
    elif issue == "missing_route":
        clauses.append("(p.route_raw IS NULL OR TRIM(p.route_raw)='')")

    bounded_limit = max(1, min(int(limit), 500))
    # Similar-name safety candidates are computed across the full catalog. Applying
    # the generic queue limit before that filter can silently hide every candidate.
    limit_clause = "" if issue == "dangerous_similar_name" else "LIMIT ?"
    if limit_clause:
        params.append(bounded_limit)
    rows = conn.execute(
        f"""SELECT p.*, s.slug AS source_slug, s.name AS source_name,
                   sr.raw_sha256, irr.import_run_id,
                   irr.ingest_status AS latest_ingest_status,
                   irr.errors_json AS latest_ingest_errors_json,
                   latest_sr.raw_sha256 AS latest_raw_sha256,
                   CASE
                     WHEN irr.id=p.current_import_run_record_id THEN 0
                     ELSE 1
                   END AS latest_evidence_not_applied,
                   d.reviewer_id AS last_reviewer_id,
                   d.reviewer_role AS last_reviewer_role,
                   d.created_at AS last_reviewed_at
            FROM drug_presentations p
            JOIN drug_sources s ON s.id=p.source_id
            LEFT JOIN drug_import_run_records applied_irr
              ON applied_irr.id=p.current_import_run_record_id
            LEFT JOIN drug_source_records sr
              ON sr.id=applied_irr.source_record_row_id
            LEFT JOIN drug_import_run_records irr ON irr.id=(
                SELECT irr2.id FROM drug_import_run_records irr2
                WHERE irr2.presentation_id=p.id
                ORDER BY irr2.created_at DESC, irr2.rowid DESC LIMIT 1
            )
            LEFT JOIN drug_source_records latest_sr
              ON latest_sr.id=irr.source_record_row_id
            LEFT JOIN drug_review_decisions d ON d.id=(
                SELECT d2.id FROM drug_review_decisions d2
                WHERE d2.presentation_id=p.id
                ORDER BY d2.created_at DESC, d2.rowid DESC LIMIT 1
            )
            WHERE {' AND '.join(clauses)}
            ORDER BY
              CASE p.workflow_review_status
                WHEN 'needs_review' THEN 0 WHEN 'unverified' THEN 1
                WHEN 'rejected' THEN 2 ELSE 3 END,
              p.updated_at DESC, p.brand_name_norm
            {limit_clause}""",
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["incomplete_fields"] = _json_list(data.pop("incomplete_fields_json", None))
        data["warnings"] = _json_list(data.pop("warnings_json", None))
        data["latest_ingest_errors"] = _json_list(
            data.pop("latest_ingest_errors_json", None)
        )
        result.append(data)
    if issue == "dangerous_similar_name":
        from app.drug_catalog import build_quality_report

        dangerous_ids: set[str] = set()
        for candidate in build_quality_report(conn).get(
            "dangerous_similar_name_candidates", []
        ):
            dangerous_ids.update(candidate.get("presentation_ids") or [])
            for key in ("left_id", "right_id"):
                if candidate.get(key):
                    dangerous_ids.add(candidate[key])
        result = [
            item for item in result if item["id"] in dangerous_ids
        ][:bounded_limit]
    return result


def get_review_detail(conn: sqlite3.Connection, presentation_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT p.*, s.slug AS source_slug, s.name AS source_name,
                  s.usage_scope AS source_usage_scope,
                  sr.raw_json, sr.raw_sha256, sr.created_at AS raw_imported_at,
                  current_irr.import_run_id,
                  ir.version AS source_version, ir.importer_version,
                  ir.normalization_version, ir.package_schema_version,
                  ir.snapshot_mode, ir.accessed_at, ir.published_at,
                  ir.source_updated_at AS import_source_updated_at,
                  latest_irr.import_run_id AS latest_import_run_id,
                  latest_irr.id AS latest_import_run_record_id,
                  latest_irr.ingest_status AS latest_ingest_status,
                  latest_irr.errors_json AS latest_ingest_errors_json,
                  latest_irr.source_record_row_id AS latest_source_record_row_id,
                  latest_sr.raw_json AS latest_raw_json,
                  latest_sr.raw_sha256 AS latest_raw_sha256,
                  latest_ir.version AS latest_source_version,
                  latest_ir.importer_version AS latest_importer_version,
                  latest_ir.normalization_version AS latest_normalization_version,
                  latest_ir.package_schema_version AS latest_package_schema_version
           FROM drug_presentations p
           JOIN drug_sources s ON s.id=p.source_id
           LEFT JOIN drug_import_run_records current_irr
             ON current_irr.id=p.current_import_run_record_id
           LEFT JOIN drug_source_records sr
             ON sr.id=current_irr.source_record_row_id
           LEFT JOIN drug_import_runs ir ON ir.id=current_irr.import_run_id
           LEFT JOIN drug_import_run_records latest_irr ON latest_irr.id=(
               SELECT irr3.id FROM drug_import_run_records irr3
               WHERE irr3.presentation_id=p.id
               ORDER BY irr3.created_at DESC, irr3.rowid DESC LIMIT 1
           )
           LEFT JOIN drug_source_records latest_sr
             ON latest_sr.id=latest_irr.source_record_row_id
           LEFT JOIN drug_import_runs latest_ir ON latest_ir.id=latest_irr.import_run_id
           WHERE p.id=?
           LIMIT 1""",
        (presentation_id,),
    ).fetchone()
    if row is None:
        raise CatalogNotFound("catalog presentation not found")
    detail = dict(row)
    detail["normalized_projection_ready"] = _normalized_projection_ready(detail)
    detail["incomplete_fields"] = _json_list(detail.pop("incomplete_fields_json", None))
    detail["warnings"] = _json_list(detail.pop("warnings_json", None))
    try:
        detail["raw_record"] = json.loads(detail.pop("raw_json") or "null")
    except json.JSONDecodeError:
        detail["raw_record"] = {"unparseable_raw": True}
    detail["latest_ingest_errors"] = _json_list(
        detail.pop("latest_ingest_errors_json", None)
    )
    try:
        latest_raw = json.loads(detail.pop("latest_raw_json") or "null")
        detail["latest_raw_record"] = latest_raw
    except json.JSONDecodeError:
        detail["latest_raw_record"] = {"unparseable_raw": True}
    detail["latest_evidence_is_current"] = bool(
        detail.get("latest_import_run_record_id")
        and detail.get("latest_import_run_record_id")
        == detail.get("current_import_run_record_id")
    )
    projection: dict[str, Any] | None = None
    if detail["normalized_projection_ready"]:
        parsed_projection = json.loads(detail["normalized_projection_json"])
        projection = parsed_projection if isinstance(parsed_projection, dict) else None
    projected_ingredients = projection.get("ingredients") if projection else None
    if isinstance(projected_ingredients, list):
        detail["ingredients"] = [
            dict(item) for item in projected_ingredients if isinstance(item, dict)
        ]
    else:
        # Legacy rows have no immutable normalized projection until reprocessed.
        # Keep them inspectable, but review_approved/review_rejected remain blocked.
        detail["ingredients"] = [
            dict(item)
            for item in conn.execute(
                """SELECT i.name_raw, pi.strength_raw, pi.strength_value,
                          pi.strength_unit, pi.basis_raw
                   FROM drug_presentation_ingredients pi
                   JOIN drug_ingredients i ON i.id=pi.ingredient_id
                   WHERE pi.presentation_id=? ORDER BY pi.ordinal""",
                (presentation_id,),
            ).fetchall()
        ]
    detail["aliases"] = [
        dict(item)
        for item in conn.execute(
            """SELECT alias_raw, alias_type, language, review_status
               FROM drug_aliases WHERE presentation_id=? ORDER BY alias_raw""",
            (presentation_id,),
        ).fetchall()
    ]
    from app.drug_catalog import _unit_options

    detail["unit_options"] = _unit_options(
        detail.get("dosage_form_code"), detail.get("route_code")
    )
    detail["audit_events"] = []
    for item in conn.execute(
        """SELECT * FROM drug_review_decisions
           WHERE presentation_id=? ORDER BY created_at DESC, rowid DESC""",
        (presentation_id,),
    ).fetchall():
        event = dict(item)
        try:
            snapshot = json.loads(event.get("normalized_snapshot_json") or "null")
            event["normalized_snapshot"] = snapshot if isinstance(snapshot, dict) else None
        except json.JSONDecodeError:
            event["normalized_snapshot"] = None
        detail["audit_events"].append(event)
    return detail


def retirement_candidate_presentations(
    conn: sqlite3.Connection,
    source_id: str,
    incoming_source_record_ids: Iterable[str],
) -> list[sqlite3.Row]:
    """Return non-retired records missing from a new full source snapshot.

    Eligibility is durable across consecutive omissions: a record only needs to have
    appeared in any earlier successful full snapshot, not necessarily the immediately
    previous one. An unresolved/approved candidate is not duplicated into another
    open batch; a cancelled or fully applied keep-active decision may be reconsidered
    when a later full snapshot supplies new omission evidence.
    """
    incoming = {
        str(value) for value in incoming_source_record_ids if str(value).strip()
    }
    rows = conn.execute(
        """SELECT DISTINCT p.*
           FROM drug_presentations p
           WHERE p.source_id=?
             AND p.operational_lifecycle_status<>'retired'
             AND EXISTS (
                 SELECT 1
                 FROM drug_import_run_records irr
                 JOIN drug_import_runs ir ON ir.id=irr.import_run_id
                 WHERE irr.presentation_id=p.id
                   AND ir.source_id=p.source_id
                   AND ir.mode='apply' AND ir.status='completed'
                   AND ir.snapshot_mode='full'
             )
             AND NOT EXISTS (
                 SELECT 1
                 FROM drug_retirement_candidates c
                 JOIN drug_retirement_batches b ON b.id=c.batch_id
                 WHERE c.presentation_id=p.id
                   AND b.status IN ('proposed','under_review','approved')
             )
           ORDER BY p.source_record_id""",
        (source_id,),
    ).fetchall()
    return [row for row in rows if row["source_record_id"] not in incoming]


def create_retirement_batch(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    import_run_id: str,
    baseline_import_run_id: str,
    candidates: Iterable[sqlite3.Row | dict[str, Any]],
    created_by: str = "catalog-importer",
) -> str | None:
    """Create a proposed batch only; presentation lifecycle remains unchanged."""
    existing = conn.execute(
        "SELECT id FROM drug_retirement_batches WHERE import_run_id=?", (import_run_id,)
    ).fetchone()
    if existing:
        return existing["id"]
    candidate_rows = [dict(value) for value in candidates]
    if not candidate_rows:
        return None
    batch_id = _id()
    created_at = _now()
    conn.execute(
        """INSERT INTO drug_retirement_batches
           (id, source_id, import_run_id, baseline_import_run_id, snapshot_mode,
            status, candidate_count, created_by, created_at, reason)
           VALUES (?, ?, ?, ?, 'full', 'proposed', ?, ?, ?, ?)""",
        (
            batch_id, source_id, import_run_id, baseline_import_run_id,
            len(candidate_rows), created_by, created_at,
            "Source record IDs were absent from a later approved full snapshot.",
        ),
    )
    for candidate in candidate_rows:
        conn.execute(
            """INSERT INTO drug_retirement_candidates
               (id, batch_id, presentation_id, source_record_id,
                previous_lifecycle_status, expected_presentation_version, decision,
                record_version)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 1)""",
            (
                _id(), batch_id, candidate["id"], candidate["source_record_id"],
                candidate["operational_lifecycle_status"], candidate["record_version"],
            ),
        )
    _append_batch_event(
        conn,
        batch_id=batch_id,
        action="batch_created",
        previous_status=None,
        next_status="proposed",
        actor_id=created_by,
        actor_role="automated importer",
        reason_code="full_snapshot_missing_records",
        note="Candidates were proposed only; no presentation lifecycle was changed.",
        expected_version=0,
        resulting_version=1,
    )
    return batch_id


def list_retirement_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """SELECT b.*, s.slug AS source_slug, s.name AS source_name,
                      SUM(CASE WHEN c.decision IN ('pending','needs_investigation')
                               THEN 1 ELSE 0 END) AS unresolved_count
               FROM drug_retirement_batches b
               JOIN drug_sources s ON s.id=b.source_id
               LEFT JOIN drug_retirement_candidates c ON c.batch_id=b.id
               GROUP BY b.id ORDER BY b.created_at DESC"""
        ).fetchall()
    ]


def get_retirement_batch(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT b.*, s.slug AS source_slug, s.name AS source_name,
                  current_run.version AS import_version,
                  baseline_run.version AS baseline_version
           FROM drug_retirement_batches b
           JOIN drug_sources s ON s.id=b.source_id
           JOIN drug_import_runs current_run ON current_run.id=b.import_run_id
           JOIN drug_import_runs baseline_run ON baseline_run.id=b.baseline_import_run_id
           WHERE b.id=?""",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise CatalogNotFound("retirement batch not found")
    result = dict(row)
    result["candidates"] = [
        dict(item)
        for item in conn.execute(
            """SELECT c.*, p.brand_name_raw, p.generic_name_raw, p.strength_raw,
                      p.dosage_form_raw, p.route_raw,
                      p.workflow_review_status, p.operational_lifecycle_status,
                      p.record_version AS current_presentation_version
               FROM drug_retirement_candidates c
               JOIN drug_presentations p ON p.id=c.presentation_id
               WHERE c.batch_id=? ORDER BY p.brand_name_norm, c.source_record_id""",
            (batch_id,),
        ).fetchall()
    ]
    result["events"] = [
        dict(item)
        for item in conn.execute(
            """SELECT * FROM drug_retirement_batch_events
               WHERE batch_id=? ORDER BY created_at, rowid""",
            (batch_id,),
        ).fetchall()
    ]
    result["unresolved_count"] = sum(
        item["decision"] in ("pending", "needs_investigation")
        for item in result["candidates"]
    )
    result["stale_count"] = sum(
        not item["applied_at"]
        and item["current_presentation_version"]
        != item["expected_presentation_version"]
        for item in result["candidates"]
    )
    return result


def decide_retirement_candidate(
    conn: sqlite3.Connection,
    batch_id: str,
    candidate_id: str,
    *,
    decision: str,
    expected_candidate_version: int,
    expected_presentation_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    if decision not in CANDIDATE_DECISIONS[1:]:
        raise GovernanceValidationError("unsupported retirement candidate decision")
    reason_code = _require_text(reason_code, "reason_code", maximum=80)
    note = _require_text(note, "note")
    reviewer_id = _require_text(reviewer_id, "reviewer_id", maximum=120)
    reviewer_role = _require_text(reviewer_role, "reviewer_role", maximum=120)
    try:
        expected = int(expected_candidate_version)
        visible_presentation_version = int(expected_presentation_version)
    except (TypeError, ValueError) as exc:
        raise GovernanceValidationError(
            "candidate and presentation versions must be integers"
        ) from exc
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """SELECT c.id AS candidate_id,
                      c.record_version AS candidate_record_version,
                      c.expected_presentation_version,
                      b.status AS batch_status,
                      b.record_version AS batch_record_version, p.*
               FROM drug_retirement_candidates c
               JOIN drug_retirement_batches b ON b.id=c.batch_id
               JOIN drug_presentations p ON p.id=c.presentation_id
               WHERE c.id=? AND c.batch_id=?""",
            (candidate_id, batch_id),
        ).fetchone()
        if row is None:
            raise CatalogNotFound("retirement candidate not found")
        if row["batch_status"] not in ("proposed", "under_review"):
            raise InvalidStateTransition("candidate decisions require an open batch")
        if row["candidate_record_version"] != expected:
            raise StaleRecordVersion("retirement candidate changed in another review")
        if row["record_version"] != visible_presentation_version:
            raise StaleRecordVersion(
                "catalog presentation changed after this candidate was displayed"
            )
        if row["expected_presentation_version"] != row["record_version"]:
            raise StaleRecordVersion(
                "catalog presentation changed after this retirement candidate was created; "
                "cancel the batch and investigate the newer source evidence"
            )
        resulting = expected + 1
        cursor = conn.execute(
            """UPDATE drug_retirement_candidates
               SET decision=?, reason_code=?, review_note=?, reviewer_id=?, reviewed_at=?,
                   expected_presentation_version=?, record_version=?
               WHERE id=? AND batch_id=? AND record_version=?""",
            (
                decision, reason_code, note, reviewer_id, _now(),
                visible_presentation_version, resulting,
                candidate_id, batch_id, expected,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleRecordVersion("retirement candidate changed during review")
        batch_expected = row["batch_record_version"]
        batch_resulting = batch_expected + 1
        batch_cursor = conn.execute(
            """UPDATE drug_retirement_batches
               SET status='under_review', record_version=?
               WHERE id=? AND record_version=? AND status IN ('proposed','under_review')""",
            (batch_resulting, batch_id, batch_expected),
        )
        if batch_cursor.rowcount != 1:
            raise StaleRecordVersion("retirement batch changed during candidate review")
        action = {
            "keep_active": "retirement_kept",
            "retire": "retirement_marked",
            "needs_investigation": "retirement_investigation",
        }[decision]
        _append_decision(
            conn,
            presentation=row,
            action=action,
            previous_review_status=row["workflow_review_status"],
            next_review_status=row["workflow_review_status"],
            previous_lifecycle_status=row["operational_lifecycle_status"],
            next_lifecycle_status=row["operational_lifecycle_status"],
            reason_code=reason_code,
            note=note,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            expected_record_version=visible_presentation_version,
            resulting_record_version=visible_presentation_version,
            retirement_batch_id=batch_id,
            retirement_candidate_id=row["candidate_id"],
        )
        _append_batch_event(
            conn,
            batch_id=batch_id,
            action="candidate_decided",
            previous_status=row["batch_status"],
            next_status="under_review",
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            reason_code=reason_code,
            note=f"Candidate {row['source_record_id']}: {decision}. {note}",
            expected_version=batch_expected,
            resulting_version=batch_resulting,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_retirement_batch(conn, batch_id)


def approve_retirement_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    expected_batch_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    return _change_batch_state(
        conn,
        batch_id,
        operation="approve",
        expected_batch_version=expected_batch_version,
        reason_code=reason_code,
        note=note,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
    )


def cancel_retirement_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    expected_batch_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    return _change_batch_state(
        conn,
        batch_id,
        operation="cancel",
        expected_batch_version=expected_batch_version,
        reason_code=reason_code,
        note=note,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
    )


def _change_batch_state(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    operation: str,
    expected_batch_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    reason_code = _require_text(reason_code, "reason_code", maximum=80)
    note = _require_text(note, "note")
    reviewer_id = _require_text(reviewer_id, "reviewer_id", maximum=120)
    reviewer_role = _require_text(reviewer_role, "reviewer_role", maximum=120)
    expected = int(expected_batch_version)
    conn.execute("BEGIN IMMEDIATE")
    try:
        batch = conn.execute(
            "SELECT * FROM drug_retirement_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise CatalogNotFound("retirement batch not found")
        if batch["record_version"] != expected:
            raise StaleRecordVersion("retirement batch changed in another review")
        previous = batch["status"]
        if operation == "approve":
            if previous not in ("proposed", "under_review"):
                raise InvalidStateTransition("only an open retirement batch can be approved")
            stale = [
                row["source_record_id"]
                for row in conn.execute(
                    """SELECT c.source_record_id
                       FROM drug_retirement_candidates c
                       JOIN drug_presentations p ON p.id=c.presentation_id
                       WHERE c.batch_id=?
                         AND p.record_version<>c.expected_presentation_version""",
                    (batch_id,),
                ).fetchall()
            ]
            if stale:
                raise StaleRecordVersion(
                    "retirement candidates have newer catalog evidence: "
                    + ", ".join(stale)
                )
            unresolved = conn.execute(
                """SELECT COUNT(*) AS n FROM drug_retirement_candidates
                   WHERE batch_id=? AND decision NOT IN ('keep_active','retire')""",
                (batch_id,),
            ).fetchone()["n"]
            if unresolved:
                raise InvalidStateTransition(
                    f"retirement batch has {unresolved} unresolved candidates"
                )
            next_status = "approved"
            action = "batch_approved"
            timestamp_column = "approved_at"
        elif operation == "cancel":
            if previous not in ("proposed", "under_review", "approved"):
                raise InvalidStateTransition("applied or cancelled batches cannot be cancelled")
            next_status = "cancelled"
            action = "batch_cancelled"
            timestamp_column = "cancelled_at"
        else:
            raise InvalidStateTransition("unsupported retirement batch operation")
        resulting = expected + 1
        approved_by = reviewer_id if operation == "approve" else batch["approved_by"]
        cursor = conn.execute(
            f"""UPDATE drug_retirement_batches
                SET status=?, approved_by=?, {timestamp_column}=?, reason=?, record_version=?
                WHERE id=? AND record_version=?""",
            (
                next_status, approved_by, _now(), note, resulting, batch_id, expected,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleRecordVersion("retirement batch changed during decision")
        _append_batch_event(
            conn,
            batch_id=batch_id,
            action=action,
            previous_status=previous,
            next_status=next_status,
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            reason_code=reason_code,
            note=note,
            expected_version=expected,
            resulting_version=resulting,
        )
        if operation == "cancel":
            for candidate in conn.execute(
                """SELECT c.id AS candidate_id, p.*
                   FROM drug_retirement_candidates c
                   JOIN drug_presentations p ON p.id=c.presentation_id
                   WHERE c.batch_id=?""",
                (batch_id,),
            ).fetchall():
                _append_decision(
                    conn,
                    presentation=candidate,
                    action="retirement_cancelled",
                    previous_review_status=candidate["workflow_review_status"],
                    next_review_status=candidate["workflow_review_status"],
                    previous_lifecycle_status=candidate["operational_lifecycle_status"],
                    next_lifecycle_status=candidate["operational_lifecycle_status"],
                    reason_code=reason_code,
                    note=note,
                    reviewer_id=reviewer_id,
                    reviewer_role=reviewer_role,
                    expected_record_version=candidate["record_version"],
                    resulting_record_version=candidate["record_version"],
                    retirement_batch_id=batch_id,
                    retirement_candidate_id=candidate["candidate_id"],
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_retirement_batch(conn, batch_id)


def apply_retirement_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    expected_batch_version: int,
    reason_code: str,
    note: str,
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    """Apply approved retire choices atomically; repeated apply is a no-op."""
    reason_code = _require_text(reason_code, "reason_code", maximum=80)
    note = _require_text(note, "note")
    reviewer_id = _require_text(reviewer_id, "reviewer_id", maximum=120)
    reviewer_role = _require_text(reviewer_role, "reviewer_role", maximum=120)
    expected = int(expected_batch_version)
    conn.execute("BEGIN IMMEDIATE")
    try:
        batch = conn.execute(
            "SELECT * FROM drug_retirement_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise CatalogNotFound("retirement batch not found")
        if batch["status"] == "applied":
            conn.rollback()
            return get_retirement_batch(conn, batch_id)
        if batch["record_version"] != expected:
            raise StaleRecordVersion("retirement batch changed in another review")
        if batch["status"] != "approved":
            raise InvalidStateTransition("retirement batch must be approved before apply")
        candidates = conn.execute(
            """SELECT c.id AS candidate_id, c.decision,
                      c.expected_presentation_version, p.*
               FROM drug_retirement_candidates c
               JOIN drug_presentations p ON p.id=c.presentation_id
               WHERE c.batch_id=? ORDER BY c.rowid""",
            (batch_id,),
        ).fetchall()
        if any(item["decision"] not in ("keep_active", "retire") for item in candidates):
            raise InvalidStateTransition("retirement batch still has unresolved candidates")
        stale = [
            item["source_record_id"] for item in candidates
            if item["record_version"] != item["expected_presentation_version"]
        ]
        if stale:
            raise StaleRecordVersion(
                "retirement candidates changed after proposal: " + ", ".join(stale)
            )
        applied_at = _now()
        for item in candidates:
            if item["decision"] == "retire":
                previous_lifecycle = item["operational_lifecycle_status"]
                if previous_lifecycle not in ("active", "inactive"):
                    raise InvalidStateTransition(
                        f"cannot retire lifecycle {previous_lifecycle}"
                    )
                next_version = item["record_version"] + 1
                cursor = conn.execute(
                    """UPDATE drug_presentations
                       SET operational_lifecycle_status='retired', record_version=?, updated_at=?
                       WHERE id=? AND record_version=?""",
                    (next_version, applied_at, item["id"], item["record_version"]),
                )
                if cursor.rowcount != 1:
                    raise StaleRecordVersion("presentation changed during retirement apply")
                _append_decision(
                    conn,
                    presentation=item,
                    action="retirement_applied",
                    previous_review_status=item["workflow_review_status"],
                    next_review_status=item["workflow_review_status"],
                    previous_lifecycle_status=previous_lifecycle,
                    next_lifecycle_status="retired",
                    reason_code=reason_code,
                    note=note,
                    reviewer_id=reviewer_id,
                    reviewer_role=reviewer_role,
                    expected_record_version=item["record_version"],
                    resulting_record_version=next_version,
                    retirement_batch_id=batch_id,
                    retirement_candidate_id=item["candidate_id"],
                )
            conn.execute(
                "UPDATE drug_retirement_candidates SET applied_at=? WHERE id=?",
                (applied_at, item["candidate_id"]),
            )
        resulting = expected + 1
        cursor = conn.execute(
            """UPDATE drug_retirement_batches
               SET status='applied', applied_at=?, reason=?, record_version=?
               WHERE id=? AND record_version=?""",
            (applied_at, note, resulting, batch_id, expected),
        )
        if cursor.rowcount != 1:
            raise StaleRecordVersion("retirement batch changed during apply")
        _append_batch_event(
            conn,
            batch_id=batch_id,
            action="batch_applied",
            previous_status="approved",
            next_status="applied",
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            reason_code=reason_code,
            note=note,
            expected_version=expected,
            resulting_version=resulting,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_retirement_batch(conn, batch_id)


def preview_full_snapshot_retirement(
    conn: sqlite3.Connection,
    source_slug: str,
    incoming_source_record_ids: Iterable[str],
) -> dict[str, Any]:
    """Read-only full-snapshot diff used by CLI dry-run and tests."""
    source = conn.execute(
        "SELECT id, slug FROM drug_sources WHERE slug=?", (source_slug,)
    ).fetchone()
    if source is None:
        return {
            "source_slug": source_slug,
            "baseline_import_run_id": None,
            "baseline_missing": True,
            "candidate_count": 0,
            "candidates": [],
        }
    baseline = conn.execute(
        """SELECT id, version, completed_at FROM drug_import_runs
           WHERE source_id=? AND mode='apply' AND status='completed'
             AND snapshot_mode='full'
           ORDER BY completed_at DESC, rowid DESC LIMIT 1""",
        (source["id"],),
    ).fetchone()
    if baseline is None:
        return {
            "source_slug": source_slug,
            "baseline_import_run_id": None,
            "baseline_missing": True,
            "candidate_count": 0,
            "candidates": [],
        }
    candidates = [
        {
            "source_record_id": row["source_record_id"],
            "presentation_id": row["id"],
            "brand_name_raw": row["brand_name_raw"],
            "generic_name_raw": row["generic_name_raw"],
            "strength_raw": row["strength_raw"],
            "dosage_form_raw": row["dosage_form_raw"],
            "operational_lifecycle_status": row["operational_lifecycle_status"],
            "record_version": row["record_version"],
        }
        for row in retirement_candidate_presentations(
            conn, source["id"], incoming_source_record_ids
        )
    ]
    return {
        "source_slug": source_slug,
        "baseline_import_run_id": baseline["id"],
        "baseline_version": baseline["version"],
        "baseline_missing": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
