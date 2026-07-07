"""indoro FastAPI 앱 팩토리.

실행: cd server && uv run uvicorn app.main:app --port 8600
라우터 등록 방식: 각 라우터 모듈이 module-level `router = APIRouter(...)`를 노출하고
create_app()이 include_router — 라우트 추가 시 이 파일은 수정 불필요.
템플릿/설정 접근: request.app.state.templates / request.app.state.config.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import get_config
from .routers import api, patient, pharmacist

BASE_DIR = Path(__file__).resolve().parent.parent  # = server/


def create_app() -> FastAPI:
    app = FastAPI(title="indoro", docs_url=None, redoc_url=None)

    # 기동 시 1회: 스키마 보장 + 설정 로드(깨진 참조는 기동 실패 — §3.4)
    db.init_db()
    app.state.config = get_config()
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    app.include_router(api.router)         # /api/*  (JSON, X-Pharmacy-Id)
    app.include_router(patient.router)     # /p/{token}, /c, /  (HTML 전용 — §4.1)
    app.include_router(pharmacist.router)  # /rx/new, /rx/{id}/qr  (약사 화면)
    return app


app = create_app()
