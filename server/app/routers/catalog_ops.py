"""Explicitly gated, loopback-first catalog governance prototype UI."""
from __future__ import annotations

import ipaddress
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import db
from app import catalog_governance as governance


router = APIRouter(prefix="/catalog")
_MAX_FORM_BYTES = 16 * 1024
_RESULT_MESSAGES = {
    "decision-recorded": "Decision recorded in the append-only audit history.",
    "candidate-recorded": "Retirement candidate decision recorded.",
    "batch-approve-recorded": "Retirement batch approved after version checks.",
    "batch-apply-recorded": "Approved retirement batch applied.",
    "batch-cancel-recorded": "Retirement batch cancelled; no lifecycle was retired.",
}


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _header_host(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    try:
        parsed = urlsplit(f"//{value}")
        return parsed.hostname, parsed.port
    except ValueError:
        return None, None


def _default_port(scheme: str) -> int | None:
    return 443 if scheme == "https" else 80 if scheme == "http" else None


def _is_same_origin_url(
    value: str,
    *,
    request_scheme: str,
    request_host: str | None,
    request_port: int | None,
) -> bool:
    try:
        parsed = urlsplit(value)
        value_port = parsed.port or _default_port(parsed.scheme)
    except ValueError:
        return False
    return (
        parsed.scheme == request_scheme
        and parsed.hostname == request_host
        and value_port == request_port
    )


def _require_ops_access(request: Request) -> None:
    if not bool(getattr(request.app.state, "catalog_ops_enabled", False)):
        raise HTTPException(status_code=404)
    peer_host = request.client.host if request.client else None
    header_host, header_port = _header_host(request.headers.get("host"))
    if not _is_loopback(peer_host) or not _is_loopback(header_host):
        raise HTTPException(status_code=404)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            raise HTTPException(status_code=403, detail="cross-origin mutation denied")
        origin = request.headers.get("origin")
        if origin:
            request_port = header_port or _default_port(request.url.scheme)
            # Chromium automation and some privacy modes serialize a same-origin
            # navigation-form Origin as `null`. Accept that opaque value only when a
            # same-origin Referer survives the `same-origin` policy; sandboxed or
            # cross-site forms have no such proof and remain denied.
            proof = request.headers.get("referer") if origin == "null" else origin
            if not proof or not _is_same_origin_url(
                proof,
                request_scheme=request.url.scheme,
                request_host=header_host,
                request_port=request_port,
            ):
                raise HTTPException(status_code=403, detail="cross-origin mutation denied")


def _secure_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


def _redirect(location: str) -> RedirectResponse:
    return _secure_headers(RedirectResponse(location, status_code=303))


def _result_message(code: str | None) -> str | None:
    return _RESULT_MESSAGES.get(code or "")


def _render(
    request: Request,
    template: str,
    context: dict,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    response = request.app.state.templates.TemplateResponse(
        request,
        template,
        context,
        status_code=status_code,
    )
    return _secure_headers(response)


async def _form_data(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise governance.GovernanceValidationError(
            "application/x-www-form-urlencoded form required"
        )
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise governance.GovernanceValidationError("form body is too large")
    try:
        parsed = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=40,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise governance.GovernanceValidationError("invalid form body") from exc
    return {key: values[-1] for key, values in parsed.items() if values}


def _operator_fields(form: dict[str, str]) -> dict[str, str]:
    return {
        "reason_code": form.get("reason_code", ""),
        "note": form.get("note", ""),
        "reviewer_id": form.get("reviewer_id", ""),
        "reviewer_role": form.get("reviewer_role", ""),
    }


def _review_context(
    conn,
    presentation_id: str,
    *,
    flash: str | None = None,
    error: str | None = None,
    conflict: bool = False,
) -> dict:
    return {
        "detail": governance.get_review_detail(conn, presentation_id),
        "active_nav": "review",
        "flash": flash,
        "error": error,
        "conflict": conflict,
    }


def _retirement_context(
    conn,
    batch_id: str,
    *,
    flash: str | None = None,
    error: str | None = None,
    conflict: bool = False,
) -> dict:
    return {
        "batch": governance.get_retirement_batch(conn, batch_id),
        "active_nav": "retirements",
        "flash": flash,
        "error": error,
        "conflict": conflict,
    }


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request) -> HTMLResponse:
    _require_ops_access(request)
    filters = {
        key: request.query_params.get(key, "").strip()
        for key in ("q", "source", "review_status", "lifecycle_status", "issue", "import_run")
    }
    conn = db.get_conn()
    try:
        records = governance.list_review_queue(conn, **filters)
        sources = [
            dict(row)
            for row in conn.execute(
                "SELECT slug, name FROM drug_sources ORDER BY name, slug"
            ).fetchall()
        ]
        counts = {
            row["workflow_review_status"]: row["n"]
            for row in conn.execute(
                """SELECT workflow_review_status, COUNT(*) AS n
                   FROM drug_presentations GROUP BY workflow_review_status"""
            ).fetchall()
        }
        return _render(
            request,
            "catalog_review_list.html",
            {
                "records": records,
                "sources": sources,
                "counts": counts,
                "filters": filters,
                "active_nav": "review",
            },
        )
    finally:
        conn.close()


@router.get("/review/{presentation_id}", response_class=HTMLResponse)
def review_detail(presentation_id: str, request: Request) -> HTMLResponse:
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        flash = _result_message(request.query_params.get("result"))
        return _render(
            request,
            "catalog_review_detail.html",
            _review_context(conn, presentation_id, flash=flash),
        )
    except governance.CatalogNotFound as exc:
        raise HTTPException(status_code=404) from exc
    finally:
        conn.close()


@router.post("/review/{presentation_id}/decision", response_class=HTMLResponse)
async def review_decision(presentation_id: str, request: Request):
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        try:
            form = await _form_data(request)
            governance.transition_presentation(
                conn,
                presentation_id,
                action=form.get("action", ""),
                expected_record_version=int(form.get("expected_record_version", "0")),
                **_operator_fields(form),
            )
        except governance.StaleRecordVersion as exc:
            return _render(
                request,
                "catalog_review_detail.html",
                _review_context(conn, presentation_id, error=str(exc), conflict=True),
                status_code=409,
            )
        except (governance.CatalogGovernanceError, ValueError) as exc:
            return _render(
                request,
                "catalog_review_detail.html",
                _review_context(conn, presentation_id, error=str(exc)),
                status_code=422,
            )
        return _redirect(f"/catalog/review/{presentation_id}?result=decision-recorded")
    except governance.CatalogNotFound as exc:
        raise HTTPException(status_code=404) from exc
    finally:
        conn.close()


@router.get("/retirements", response_class=HTMLResponse)
def retirement_batches(request: Request) -> HTMLResponse:
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        return _render(
            request,
            "catalog_retirement_list.html",
            {
                "batches": governance.list_retirement_batches(conn),
                "active_nav": "retirements",
            },
        )
    finally:
        conn.close()


@router.get("/retirements/{batch_id}", response_class=HTMLResponse)
def retirement_detail(batch_id: str, request: Request) -> HTMLResponse:
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        return _render(
            request,
            "catalog_retirement_detail.html",
            _retirement_context(
                conn,
                batch_id,
                flash=_result_message(request.query_params.get("result")),
            ),
        )
    except governance.CatalogNotFound as exc:
        raise HTTPException(status_code=404) from exc
    finally:
        conn.close()


@router.post(
    "/retirements/{batch_id}/candidates/{candidate_id}",
    response_class=HTMLResponse,
)
async def retirement_candidate_decision(
    batch_id: str, candidate_id: str, request: Request
):
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        try:
            form = await _form_data(request)
            governance.decide_retirement_candidate(
                conn,
                batch_id,
                candidate_id,
                decision=form.get("decision", ""),
                expected_candidate_version=int(
                    form.get("expected_candidate_version", "0")
                ),
                expected_presentation_version=int(
                    form.get("expected_presentation_version", "0")
                ),
                **_operator_fields(form),
            )
        except governance.CatalogNotFound as exc:
            raise HTTPException(status_code=404) from exc
        except governance.StaleRecordVersion as exc:
            return _render(
                request,
                "catalog_retirement_detail.html",
                _retirement_context(conn, batch_id, error=str(exc), conflict=True),
                status_code=409,
            )
        except (governance.CatalogGovernanceError, ValueError) as exc:
            return _render(
                request,
                "catalog_retirement_detail.html",
                _retirement_context(conn, batch_id, error=str(exc)),
                status_code=422,
            )
        return _redirect(f"/catalog/retirements/{batch_id}?result=candidate-recorded")
    finally:
        conn.close()


async def _batch_action(batch_id: str, request: Request, operation: str):
    _require_ops_access(request)
    conn = db.get_conn()
    try:
        try:
            form = await _form_data(request)
            kwargs = {
                "expected_batch_version": int(form.get("expected_batch_version", "0")),
                **_operator_fields(form),
            }
            if operation == "approve":
                governance.approve_retirement_batch(conn, batch_id, **kwargs)
            elif operation == "apply":
                governance.apply_retirement_batch(conn, batch_id, **kwargs)
            elif operation == "cancel":
                governance.cancel_retirement_batch(conn, batch_id, **kwargs)
            else:
                raise governance.InvalidStateTransition("unsupported batch operation")
        except governance.CatalogNotFound as exc:
            raise HTTPException(status_code=404) from exc
        except governance.StaleRecordVersion as exc:
            return _render(
                request,
                "catalog_retirement_detail.html",
                _retirement_context(conn, batch_id, error=str(exc), conflict=True),
                status_code=409,
            )
        except (governance.CatalogGovernanceError, ValueError) as exc:
            return _render(
                request,
                "catalog_retirement_detail.html",
                _retirement_context(conn, batch_id, error=str(exc)),
                status_code=422,
            )
        return _redirect(
            f"/catalog/retirements/{batch_id}?result=batch-{operation}-recorded"
        )
    finally:
        conn.close()


@router.post("/retirements/{batch_id}/approve")
async def retirement_approve(batch_id: str, request: Request):
    return await _batch_action(batch_id, request, "approve")


@router.post("/retirements/{batch_id}/apply")
async def retirement_apply(batch_id: str, request: Request):
    return await _batch_action(batch_id, request, "apply")


@router.post("/retirements/{batch_id}/cancel")
async def retirement_cancel(batch_id: str, request: Request):
    return await _batch_action(batch_id, request, "cancel")
