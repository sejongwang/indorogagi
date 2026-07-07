"""환자측 /p/{token} 계약 테스트 — view.first 원자성(§6.2)·토큰 상태(D5/D8)·S1 렌더 스모크."""
from __future__ import annotations

import re
import uuid

PHARMACY_HEADERS = {"X-Pharmacy-Id": "ph-demo-001"}


# ---------------------------------------------------------------- 5. view.first 원자성

def test_view_first_atomic(client, db_conn, issue, rx_payload):
    """GET /p/{token} 2회 → view.first 정확히 1건(도달률 분자), view.opened 2건."""
    token = issue(rx_payload()).json()["token"]

    for _ in range(2):
        r = client.get(f"/p/{token}")
        assert r.status_code == 200

    n_first = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='view.first' AND token=?",
        (token,),
    ).fetchone()["n"]
    n_opened = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='view.opened' AND token=?",
        (token,),
    ).fetchone()["n"]
    assert n_first == 1
    assert n_opened == 2

    # first_viewed_at 선점 컬럼도 채워져 있어야 함
    fv = db_conn.execute(
        "SELECT first_viewed_at FROM access_tokens WHERE token=?", (token,)
    ).fetchone()["first_viewed_at"]
    assert fv is not None


# ---------------------------------------------------------------- 6. 토큰 상태 (D5·D8)

def test_missing_token_waiting_page(client):
    """미존재 토큰 → 200 대기 페이지(D8): HTML + meta refresh. JSON 금지(§4.1)."""
    r = client.get("/p/" + "A" * 22)  # 형식은 유효하나 미발급
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert re.search(r'http-equiv\s*=\s*["\']refresh["\']', r.text, re.IGNORECASE)


def test_expired_token_410(client, db_conn, issue, rx_payload):
    """만료 조작(expires_at을 과거로) → 410 HTML."""
    token = issue(rx_payload()).json()["token"]
    db_conn.execute(
        "UPDATE access_tokens SET expires_at='2020-01-01T00:00:00Z' WHERE token=?",
        (token,),
    )
    db_conn.commit()

    r = client.get(f"/p/{token}")
    assert r.status_code == 410
    assert r.headers["content-type"].startswith("text/html")  # 환자 라우트는 JSON 금지


def test_reissue_old_410_new_200(client, issue, rx_payload):
    """D5: reissue = 새 처방·새 토큰, 구 토큰 즉시 410. 구 처방은 부활하지 않는다."""
    j1 = issue(rx_payload()).json()

    r2 = client.post(
        f"/api/prescriptions/{j1['id']}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )
    assert r2.status_code == 201
    j2 = r2.json()
    assert j2["token"] != j1["token"]
    assert j2["short_code"] != j1["short_code"]
    assert j2["reissue_of"] == j1["id"]

    r_old = client.get(f"/p/{j1['token']}")
    assert r_old.status_code == 410
    assert r_old.headers["content-type"].startswith("text/html")

    r_new = client.get(f"/p/{j2['token']}")
    assert r_new.status_code == 200


# ---------------------------------------------------------------- 8. S1 렌더 스모크

def test_render_smoke_infographic(client, issue, rx_payload):
    """s1-landing.html 이식 마커: 매트릭스·픽토그램 카드·식전후 스트립 + 이중 언어(D12)."""
    token = issue(rx_payload()).json()["token"]

    r = client.get(f"/p/{token}")
    assert r.status_code == 200
    text = r.text
    assert "mx-row" in text        # 약×시간 매트릭스
    assert "slot-strip" in text    # 픽토그램 카드 슬롯
    assert "food-strip" in text    # 식전후 스트립
    assert 'lang="hi"' in text     # 기본 언어 = prescriptions.lang (D12)

    r_en = client.get(f"/p/{token}", params={"lang": "en"})
    assert r_en.status_code == 200
    assert re.search(r'<html[^>]*\blang="en"', r_en.text)  # ?lang= 우선 (D12)


# ---------------------------------------------------------------- 9. 이벤트 스키마 (§6.2)

def test_revoked_410_records_view_expired_not_invalid(client, db_conn, issue, rx_payload):
    """§6.2: revoked-410 조회는 view.expired(props.status=revoked) — view.invalid는 /c 오입력 전용."""
    j1 = issue(rx_payload()).json()
    client.post(
        f"/api/prescriptions/{j1['id']}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )
    assert client.get(f"/p/{j1['token']}").status_code == 410

    ev = db_conn.execute(
        "SELECT event_type, props_json FROM events "
        "WHERE token=? AND event_type LIKE 'view.%' ORDER BY id DESC LIMIT 1",
        (j1["token"],),
    ).fetchone()
    assert ev["event_type"] == "view.expired"      # revoked도 view.expired 계열(§6.2)
    assert '"status": "revoked"' in ev["props_json"]
    # view.invalid는 이 경로에서 발생하지 않는다
    n_invalid = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='view.invalid'"
    ).fetchone()["n"]
    assert n_invalid == 0


def test_code_miss_records_view_invalid_no_raw_code(client, db_conn):
    """§6.2·H13: /c 코드 오입력 → view.invalid(props.reason=code_miss), 오입력 코드 원문 미복제.
    scan.failed는 이 경로에서 발생하지 않는다(약사 scan-failure API 전용)."""
    r = client.get("/c", params={"code": "ZZZZ-ZZZZ"})
    assert r.status_code == 404

    row = db_conn.execute(
        "SELECT event_type, props_json, token, src FROM events WHERE event_type='view.invalid'"
    ).fetchone()
    assert row is not None
    assert row["src"] == "code"
    assert '"reason": "code_miss"' in row["props_json"]
    assert "ZZZZ" not in (row["props_json"] or "")  # H13: 오입력 코드 원문 복제 금지
    assert row["token"] is None
    # scan.failed는 오입력 경로에서 발생하지 않는다(KPI 오염 방지 — finding 1)
    n_scan = db_conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type='scan.failed'"
    ).fetchone()["n"]
    assert n_scan == 0


# ---------------------------------------------------------------- 10. 홈 코드 입력 폼 (§2.5)

def test_home_code_form_js_free(client):
    """GET / = 코드 입력 폼(가족 대행 경로). <form method=get action=/c>, JS 불요, hi/en."""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'action="/c"' in r.text
    assert 'method="get"' in r.text
    assert 'name="code"' in r.text
    assert "<script" not in r.text.lower()  # JS 불요
