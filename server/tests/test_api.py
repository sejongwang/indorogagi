"""약사측 /api 계약 테스트 — 멱등(D10)·토큰 형식(D2/D3)·만료 정책(D6)·drugs 검색(§4.5)·비콘(D16)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

import pytest

from app import db

PHARMACY_HEADERS = {"X-Pharmacy-Id": "ph-demo-001"}


def _days(a: str, b: str) -> int:
    da = datetime.fromisoformat(a.replace("Z", "+00:00"))
    db_ = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (db_ - da).days


# ---------------------------------------------------------------- 1. 멱등성 (D10)

def test_idempotent_create_replay(client, db_conn, rx_payload):
    """같은 client_input_id POST 2회 → 처방 1건, 같은 토큰. replay는 200 + replayed:true (§4.3)."""
    payload = rx_payload()

    r1 = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert r1.status_code == 201
    j1 = r1.json()
    assert j1["replayed"] is False
    assert j1["url"].endswith(f"/p/{j1['token']}")  # QR 페이로드 = url 그대로 (D2)

    r2 = client.post("/api/prescriptions", json=payload, headers=PHARMACY_HEADERS)
    assert r2.status_code == 200  # §4.3: 멱등 replay는 200
    j2 = r2.json()
    assert j2["replayed"] is True
    assert j2["token"] == j1["token"]  # 재시도가 새 토큰을 만들면 환자에게 링크 2개
    assert j2["id"] == j1["id"]

    n = db_conn.execute("SELECT COUNT(*) AS n FROM prescriptions").fetchone()["n"]
    assert n == 1
    # rx.created는 1건만 (도달률 분모 보호 — §6.2)
    n_ev = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='rx.created'"
    ).fetchone()["n"]
    assert n_ev == 1


# ---------------------------------------------------------------- 2. 토큰 형식 (D2·D3)

def test_token_and_short_code_format(issue, rx_payload):
    """토큰 = 128-bit url-safe 22자, short_code = Crockford Base32 8자·전역 유니크(D3)."""
    tokens: list[str] = []
    codes: list[str] = []
    for _ in range(3):
        r = issue(rx_payload())
        assert r.status_code == 201
        j = r.json()
        assert re.fullmatch(r"[A-Za-z0-9_-]{22}", j["token"])  # token_urlsafe(16) → 22자
        assert len(j["short_code"]) == 8
        assert all(ch in db.CROCKFORD_ALPHABET for ch in j["short_code"])  # I,L,O,U 제외
        assert j["short_code_display"] == f"{j['short_code'][:4]}-{j['short_code'][4:]}"
        tokens.append(j["token"])
        codes.append(j["short_code"])
    # 전역 유니크 — 발급 간 중복 없음(스키마 UNIQUE 제약은 test_db.py에서 검증)
    assert len(set(tokens)) == 3
    assert len(set(codes)) == 3


# ---------------------------------------------------------------- 3. 만료 정책 (D6)

@pytest.mark.parametrize(
    ("duration_days", "expect_days"),
    [
        (10, 30),   # max(30, 10+7) = 30
        (40, 47),   # max(30, 40+7) = 47
        (100, 90),  # 상한 90일 클램프
    ],
)
def test_expires_policy_d6(issue, rx_payload, duration_days, expect_days):
    r = issue(rx_payload(duration_days=duration_days))
    assert r.status_code == 201
    j = r.json()
    assert _days(j["issued_at"], j["expires_at"]) == expect_days


# ---------------------------------------------------------------- 4. drugs 검색 (§4.5)

def test_drugs_search_contract(client):
    """'do' → Dolo 포함, 1자 → 빈 배열, 0건도 항상 200 + 빈 배열."""
    r = client.get("/api/drugs", params={"q": "do"})
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert any("Dolo" in d["brand_name"] for d in results)

    r1 = client.get("/api/drugs", params={"q": "d"})  # 2자 미만
    assert r1.status_code == 200
    assert r1.json() == []

    r0 = client.get("/api/drugs", params={"q": "zzqqxx"})  # 0건도 200 — 입력 흐름을 막지 않음
    assert r0.status_code == 200
    assert r0.json() == []


# ---------------------------------------------------------------- 7. events 비콘 (D16)

def test_events_beacon_always_204(client, db_conn, issue, rx_payload):
    """정상·화이트리스트 외 type·token 없음 — 전부 204. 폐기는 조용히(저장 안 됨)."""
    token = issue(rx_payload()).json()["token"]

    ok = {
        "token": token,
        "events": [{
            "client_event_id": str(uuid.uuid4()),
            "type": "share.clicked",
            "client_ts": 1751871600000,
            "props": {"method": "copy"},
        }],
    }
    assert client.post("/api/events", json=ok).status_code == 204

    bad_type = {
        "token": token,
        "events": [{
            "client_event_id": str(uuid.uuid4()),
            "type": "totally.bogus",
            "client_ts": 1751871600001,
            "props": {},
        }],
    }
    assert client.post("/api/events", json=bad_type).status_code == 204

    no_token = {
        "events": [{
            "client_event_id": str(uuid.uuid4()),
            "type": "share.clicked",
            "client_ts": 1751871600002,
        }],
    }
    assert client.post("/api/events", json=no_token).status_code == 204  # token 필수 → 조용히 폐기

    n_ok = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='share.clicked'"
    ).fetchone()["n"]
    n_bad = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='totally.bogus'"
    ).fetchone()["n"]
    assert n_ok == 1   # 정상 1건만 저장 (token 없음 건은 폐기됐으므로 2가 아님)
    assert n_bad == 0  # 화이트리스트 외 type은 저장 자체가 없음


def test_events_oversized_body_rejected_before_buffering(client, db_conn):
    """§4.7: Content-Length가 상한 초과면 본문 버퍼링 전에 204(메모리 소진 DoS 차단).
    CL 헤더 가드가 1차, len(raw) 검사가 backstop(sendBeacon은 CL 생략 가능)."""
    # 헤더로 선언된 Content-Length가 상한 초과 → request.body() 전에 204
    big = client.post(
        "/api/events",
        headers={"Content-Length": "999999", "Content-Type": "application/json"},
        content=b"{}",
    )
    assert big.status_code == 204
    # backstop: CL 없이도 실제 본문이 2KB 초과면 204 (내용 폐기)
    payload = b'{"token":"x","events":[]}' + b" " * 3000
    r = client.post("/api/events", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 204
    # 두 건 모두 저장되지 않았다
    n = db_conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    assert n == 0


# ---------------------------------------------------------------- 9. 수정 = 버전 업 (D4·§4.4)

def test_put_edit_bumps_version_keeps_token(client, db_conn, issue, rx_payload):
    """PUT = items 전체 교체 + version+1, 토큰/URL 불변(D4). revision 보관 + rx.edited 1건."""
    j = issue(rx_payload(duration_days=5)).json()
    pid, tok = j["id"], j["token"]

    edit = rx_payload(duration_days=40)
    edit["items"][0]["drug_name_raw"] = "Dolo 650 (dose fixed)"
    edit["edit_reason"] = "dose correction"
    r = client.put(f"/api/prescriptions/{pid}", json=edit, headers=PHARMACY_HEADERS)
    assert r.status_code == 200
    je = r.json()
    assert je["version"] == 2
    assert je["access"]["token"] == tok  # D4: 토큰 불변
    assert je["items"][0]["drug_name_raw"] == "Dolo 650 (dose fixed)"
    # D6: duration 40 → max(30, 47) = 47일로 만료창 재산정
    assert _days(je["created_at"], je["access"]["expires_at"]) == 47

    n_rev = db_conn.execute("SELECT COUNT(*) AS n FROM prescription_revisions").fetchone()["n"]
    n_edited = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='rx.edited'"
    ).fetchone()["n"]
    assert n_rev == 1     # 구버전 스냅샷 보관
    assert n_edited == 1  # 서버 권위 이벤트(§6.2)


def test_put_edit_missing_or_other_pharmacy_404(client, issue, rx_payload):
    """미존재·타 약국 처방 수정 → 404(존재 은닉 §4.1)."""
    j = issue(rx_payload()).json()
    r = client.put(f"/api/prescriptions/{j['id']}", json=rx_payload(),
                   headers={"X-Pharmacy-Id": "ph-other"})
    assert r.status_code == 404


# ---------------------------------------------------------------- 10. 재표시 = qr.redisplayed (§4.8)

def test_qr_redisplay_emits_event_not_new_issue(client, db_conn, issue, rx_payload):
    """GET /qr = 기존 url 반환(발급 아님) + qr.redisplayed. rx.created는 여전히 1건(분모 보호)."""
    j = issue(rx_payload()).json()
    pid, tok = j["id"], j["token"]

    r = client.get(f"/api/prescriptions/{pid}/qr", headers=PHARMACY_HEADERS)
    assert r.status_code == 200
    assert r.json()["url"].endswith(f"/p/{tok}")  # 기존 url 그대로(클라 재렌더)

    n_redisplay = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='qr.redisplayed'"
    ).fetchone()["n"]
    n_created = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='rx.created'"
    ).fetchone()["n"]
    assert n_redisplay == 1
    assert n_created == 1  # §6.3-②: 재표시가 발급 분모를 오염시키지 않는다


# ---------------------------------------------------------------- 11. 목록 (§4.2)

def test_list_prescriptions_limit(client, issue, rx_payload):
    """최근 발급 목록 — created_at DESC. 타 약국 헤더 누락은 400·미존재 약국은 404."""
    issue(rx_payload())
    issue(rx_payload())
    r = client.get("/api/prescriptions", headers=PHARMACY_HEADERS)
    assert r.status_code == 200
    lst = r.json()["prescriptions"]
    assert len(lst) == 2
    assert all("url" in p and p["url"].endswith(f"/p/{p['token']}") for p in lst)

    assert client.get("/api/prescriptions").status_code == 400  # X-Pharmacy-Id 누락


# ---------------------------------------------------------------- 12. scan.failed (§2.5·§6.2)

def test_scan_failure_records_event(client, db_conn, issue, rx_payload):
    """POST /scan-failure = 약사 원탭 기록 → scan.failed(reason enum 4택). 잘못된 reason은 422."""
    j = issue(rx_payload()).json()
    pid, tok = j["id"], j["token"]

    r = client.post(f"/api/prescriptions/{pid}/scan-failure",
                    json={"reason": "feature_phone"}, headers=PHARMACY_HEADERS)
    assert r.status_code == 201
    bad = client.post(f"/api/prescriptions/{pid}/scan-failure",
                      json={"reason": "not_a_reason"}, headers=PHARMACY_HEADERS)
    assert bad.status_code == 422

    row = db_conn.execute(
        "SELECT props_json FROM events WHERE event_type='scan.failed' AND token=?", (tok,)
    ).fetchone()
    assert row is not None
    assert '"feature_phone"' in row["props_json"]
