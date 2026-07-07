"""약사측 화면 라우터 — /rx/new (P1), /rx/{prescription_id}/qr (P3).

서버는 템플릿 + 최소 데이터만 내려준다 — 폼 로직·API 호출·QR 렌더는 static/rx.js.
- /rx/new: config(patterns.yaml + i18n.yaml) 메타를 그대로 임베드 — 패턴·라벨의
  단일 소스(§4.8 "클라 하드코딩 금지"를 서버사이드 임베드로 충족, fetch 왕복 0회).
- /rx/{id}/qr: prescription_id만 임베드 — 데이터는 클라가 GET /api/prescriptions/{id}
  (X-Pharmacy-Id는 localStorage 설정값, 데모 기본 "ph-demo-001")로 로드.
  존재 검증도 그 호출에 위임(미존재·타약국 → 404 → 화면 인라인 에러).
QR은 클라 JS 렌더(D18 — 서버 qr_svg 미생성).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/rx/new", response_class=HTMLResponse)
def rx_new(request: Request) -> HTMLResponse:
    """P1 새 처방 입력 폼."""
    cfg = request.app.state.config
    meta = {
        "patterns": cfg["patterns"],
        "pattern_order": cfg["pattern_order"],
        "slot_order": cfg["slot_order"],
        "i18n": cfg["i18n"],
    }
    return request.app.state.templates.TemplateResponse(
        request, "rx_new.html", {"meta": meta}
    )


@router.get("/rx/{prescription_id}/qr", response_class=HTMLResponse)
def rx_qr(request: Request, prescription_id: str) -> HTMLResponse:
    """P3 발급 완료·QR 표시 (최초 발급·재표시 겸용)."""
    return request.app.state.templates.TemplateResponse(
        request, "rx_qr.html", {"prescription_id": prescription_id}
    )
