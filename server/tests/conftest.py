"""API 통합 테스트 공용 fixture.

격리 전략: 라우터는 db.get_conn()(기본 경로 = db.DB_PATH_DEFAULT)을 사용하므로
DB_PATH_DEFAULT를 tmp 경로로 monkeypatch — 실 서버 DB(server/var/indoro.db)는 건드리지 않는다.
라우터가 미완이어도 계약(docs/01 §4 + 스캐폴드 계약) 기준으로 작성 — Integrate 단계가 실행한다.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db

PHARMACY_ID = "ph-demo-001"
PHARMACY_HEADERS = {"X-Pharmacy-Id": PHARMACY_ID}

# view.first는 is_bot 요청에서 판정 자체를 건너뛰므로(§6.2) 실 모바일 UA로 요청한다.
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
)


@pytest.fixture()
def test_db_path(tmp_path, monkeypatch) -> Path:
    """tmp DB 생성 + 데모 약국·시드 약 2종 주입. DB_PATH_DEFAULT monkeypatch 포함."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH_DEFAULT", path)
    db.init_db(path)
    conn = db.get_conn(path)
    try:
        db.upsert_pharmacy(conn, {
            "id": PHARMACY_ID, "name": "Sharma Medical Store",
            "area": "Karol Bagh, Delhi", "pincode": "110005", "has_printer": 0,
        })
        db.import_drugs(conn, [
            {"brand_name": "Dolo 650", "generic_name": "Paracetamol",
             "strength": "650 mg", "form": "tablet", "aliases": ["Dolo"],
             "source": "[]", "verified": 0},
            {"brand_name": "Augmentin 625 Duo",
             "generic_name": "Amoxicillin + Clavulanic Acid",
             "strength": "500/125 mg", "form": "tablet",
             "aliases": ["Augmentin"], "source": "[]", "verified": 0},
        ])
    finally:
        conn.close()
    return path


@pytest.fixture()
def client(test_db_path):
    # monkeypatch 이후 임포트 — app.main 모듈 레벨 `app = create_app()`이 실 DB를 잡지 않게.
    from app.main import create_app

    with TestClient(create_app()) as c:
        c.headers.update({"User-Agent": MOBILE_UA})
        yield c


@pytest.fixture()
def db_conn(test_db_path):
    """직접 검증용 커넥션(이벤트 카운트·만료 조작 등). WAL이라 앱 커넥션과 병행 안전."""
    conn = db.get_conn(test_db_path)
    yield conn
    conn.close()


@pytest.fixture()
def rx_payload():
    """§4.3 POST 본문 생성기 — client_input_id는 매 호출 새 UUID(멱등 테스트는 cid 고정 전달)."""
    def make(cid: str | None = None, *, duration_days: int = 5,
             drug_name: str = "Dolo 650", pattern_key: str = "TDS") -> dict:
        return {
            "client_input_id": cid or str(uuid.uuid4()),
            "issued_at_client": "2026-07-06T15:41:22+05:30",
            "lang": "hi",
            "patient_label": "Mr. S",
            "items": [
                {
                    "position": 1,
                    "drug_name_raw": drug_name,
                    "drug_id": None,
                    "pattern_key": pattern_key,
                    "doses": {"M": 1, "N": 1, "E": 1, "H": 0},
                    "dose_unit": "tablet",
                    "timing_food": "after_food",
                    "duration_days": duration_days,
                    "total_quantity": 3 * duration_days,
                    "extra_params": None,
                    "note": None,
                },
            ],
            "client_metrics": {
                "input_duration_ms": 27400, "active_input_ms": 21800,
                "idle_gaps_count": 1, "used_autocomplete_count": 0,
                "retry_count": 0, "app_version": "v0-web",
            },
        }
    return make


@pytest.fixture()
def issue(client):
    """POST /api/prescriptions 헬퍼 — httpx.Response 반환(상태코드 검증은 각 테스트 몫)."""
    def _issue(payload: dict):
        return client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    return _issue
