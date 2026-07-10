"""db.py 스캐폴드 스모크 테스트 — 멱등 생성·토큰 상태·first_view 선점·검색·reissue."""
from __future__ import annotations

import pytest

from app import db
from app.config import load_config


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    c = db.get_conn(path)
    db.upsert_pharmacy(c, {"id": "ph-demo-001", "name": "Sharma Medical Store",
                           "area": "Karol Bagh, Delhi", "has_printer": 0})
    yield c
    c.close()


def payload(cid: str = "9f1c6b2e-8a44-4c1f-b1d2-3e5a7c90d412") -> dict:
    return {
        "client_input_id": cid,
        "issued_at_client": "2026-07-06T15:41:22+05:30",
        "lang": "hi",
        "patient_label": "Mr. S",
        "items": [
            {"position": 1, "drug_name_raw": "Dolo 650", "drug_id": None,
             "pattern_key": "TDS", "doses": {"M": 1, "N": 1, "E": 1, "H": 0},
             "dose_unit": "tablet", "timing_food": "after_food",
             "duration_days": 5, "total_quantity": 15},
        ],
        "client_metrics": {"input_duration_ms": 27400, "active_input_ms": 21800},
    }


def test_create_and_idempotent_replay(conn):
    r1 = db.create_prescription(conn, "ph-demo-001", payload())
    assert r1["replayed"] is False
    assert len(r1["token"]) == 22
    assert len(r1["short_code"]) == 8
    assert all(ch in db.CROCKFORD_ALPHABET for ch in r1["short_code"])
    assert r1["short_code_display"] == f"{r1['short_code'][:4]}-{r1['short_code'][4:]}"

    r2 = db.create_prescription(conn, "ph-demo-001", payload())  # D10 멱등
    assert r2["replayed"] is True
    assert r2["token"] == r1["token"]

    # rx.created는 1건만 (분모 보호)
    n = conn.execute("SELECT COUNT(*) AS n FROM events WHERE event_type='rx.created'").fetchone()["n"]
    assert n == 1


def test_bundle_status_and_first_view(conn):
    r = db.create_prescription(conn, "ph-demo-001", payload())
    status, bundle = db.get_bundle_by_token(conn, r["token"])
    assert status == "active"
    assert bundle["pharmacy"]["name"] == "Sharma Medical Store"
    assert bundle["items"][0]["doses"] == {"M": 1, "N": 1, "E": 1, "H": 0}
    assert bundle["access"]["short_code_display"] == r["short_code_display"]

    assert db.get_bundle_by_token(conn, "no-such-token")[0] == "missing"  # D8 대기 페이지 경로

    assert db.claim_first_view(conn, r["token"]) is True   # 원자 선점
    assert db.claim_first_view(conn, r["token"]) is False  # 재선점 불가

    # D6: duration 5일 → max(30, 12) = 30일
    from datetime import datetime, timezone
    exp = datetime.fromisoformat(r["expires_at"].replace("Z", "+00:00"))
    created = datetime.fromisoformat(r["issued_at"].replace("Z", "+00:00"))
    assert (exp - created).days == 30


def test_client_token_and_collision(conn):
    p = payload("cid-offline-1")
    p["token"] = "hV8s3kQxWnA9cLd3Ye7Rk2"
    r = db.create_prescription(conn, "ph-demo-001", p)
    assert r["token"] == "hV8s3kQxWnA9cLd3Ye7Rk2"
    assert conn.execute("SELECT origin FROM prescriptions WHERE id=?",
                        (r["id"],)).fetchone()["origin"] == "offline"

    p2 = payload("cid-offline-2")
    p2["token"] = "hV8s3kQxWnA9cLd3Ye7Rk2"
    with pytest.raises(db.TokenCollisionError):  # §2.3 조용한 재생성 금지
        db.create_prescription(conn, "ph-demo-001", p2)


def test_reissue_revokes_old(conn):
    r1 = db.create_prescription(conn, "ph-demo-001", payload())
    r2 = db.reissue(conn, r1["id"], "ph-demo-001",
                    {"client_input_id": "new-cid-001", "reason": "wrong_patient"})
    assert r2["token"] != r1["token"]
    assert r2["reissue_of"] == r1["id"]
    assert db.get_bundle_by_token(conn, r1["token"])[0] == "revoked"   # 구건 → 410
    status, bundle = db.get_bundle_by_token(conn, r2["token"])
    assert status == "active"
    assert [i["drug_name_raw"] for i in bundle["items"]] == ["Dolo 650"]  # items 미지정 → 구건 복사

    with pytest.raises(LookupError):  # 타 약국 → 라우터 404 (존재 은닉)
        db.reissue(conn, r2["id"], "ph-other", {"client_input_id": "x"})


def test_import_and_search_drugs(conn):
    n = db.import_drugs(conn, [
        {"brand_name": "Dolo 650", "generic_name": "Paracetamol", "strength": "650 mg",
         "form": "tablet", "aliases": ["Dolo"], "source": "[]", "verified": 0},
        {"brand_name": "Augmentin 625 Duo", "generic_name": "Amoxicillin + Clavulanic Acid",
         "strength": "500/125 mg", "form": "tablet", "aliases": ["Augmentin"], "source": "[]"},
    ])
    assert n == 2
    assert db.import_drugs(conn, [  # 재실행 멱등(자연키 upsert)
        {"brand_name": "Dolo 650", "generic_name": "Paracetamol", "strength": "650 mg",
         "form": "tablet", "aliases": ["Dolo"], "source": "[]"},
    ]) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM drugs").fetchone()["n"] == 2

    assert db.search_drugs(conn, "d") == []                      # 2자 미만
    assert db.search_drugs(conn, "dolo")[0]["brand_name"] == "Dolo 650"
    assert db.search_drugs(conn, "amoxicillin")[0]["brand_name"] == "Augmentin 625 Duo"
    assert db.search_drugs(conn, "augmen")[0]["aliases"] == ["Augmentin"]  # aliases 매칭


def test_config_loads_and_validates():
    cfg = load_config()
    assert cfg["pattern_order"][0] == "OD_MORNING"
    assert len(cfg["pattern_order"]) == 9
    assert cfg["patterns"]["TDS"]["slots"] == ["M", "N", "E"]
    assert cfg["patterns"]["OD_NIGHT"]["slots"] == ["H"]
    assert cfg["patterns"]["OD_NIGHT"]["digits"] == "0-0-0-1"
    assert cfg["patterns"]["TDS"]["name"]["hi"] == "दिन में 3 बार"
    assert cfg["i18n"]["dose_units"]["tablet"]["hi"] == "गोली"
    assert cfg["i18n"]["duration_presets"] == [3, 5, 7, 10, 15, 30]


def test_init_migrates_unambiguous_legacy_od_night_to_bedtime(tmp_path):
    path = tmp_path / "legacy.db"
    db.init_db(path)
    c = db.get_conn(path)
    try:
        db.upsert_pharmacy(c, {"id": "ph-demo-001", "name": "Demo Pharmacy"})
        p = payload("legacy-od-night")
        p["items"][0].update({
            "pattern_key": "OD_NIGHT",
            "doses": {"M": 0, "N": 0, "E": 1, "H": 0},
        })
        issued = db.create_prescription(c, "ph-demo-001", p)
    finally:
        c.close()

    db.init_db(path)
    db.init_db(path)  # 재기동에도 멱등
    c = db.get_conn(path)
    try:
        status, bundle = db.get_bundle_by_token(c, issued["token"])
        assert status == "active"
        assert bundle["items"][0]["doses"] == {"M": 0, "N": 0, "E": 0, "H": 1}
    finally:
        c.close()
