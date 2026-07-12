"""환자측 /p/{token} 계약 테스트 — view.first 원자성(§6.2)·토큰 상태(D5/D8)·S1 렌더 스모크."""
from __future__ import annotations

import gzip
import re
import uuid
from urllib.parse import unquote

from app import db
from scripts import demo

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
    """환자 HTML은 표가 아닌 하루 흐름 포스터이고 핵심 정보가 SSR 된다."""
    token = issue(rx_payload()).json()["token"]

    r = client.get(f"/p/{token}")
    assert r.status_code == 200
    text = r.text
    assert "/static/patient-poster.css" in text
    assert "infographic-flow" in text
    assert "time-panel" in text
    assert "time-banner" in text
    assert "medicine-strip" in text
    assert "action-sequence" in text
    assert "packet-badge" in text
    assert "meal-node" in text
    assert "duration-node" in text
    assert 'data-food="after_food"' in text
    assert "Dolo 650" in text.split('id="time-N"', 1)[0]  # 첫 비어 있지 않은 시간 띠 안에 실제 약명
    body = text.split("<body>", 1)[1]
    assert "day-ribbon" not in body       # 시간 탭/대시보드형 요약 제거
    assert "first-glance" not in body     # 중복 카드 요약 제거
    assert "time-block" not in body       # 좌측 시간 열 + 우측 카드 구조 제거
    assert "dose-row" not in body
    assert "mx-row" not in body             # 이전 약×시간 관리형 매트릭스 제거
    assert "ph--video" not in body          # 미구현 미디어가 복약 정보보다 앞서지 않음
    assert "btn-audio" not in body
    assert [text.index(f'id="time-{slot}"') for slot in ("M", "N", "E", "H")] == sorted(
        text.index(f'id="time-{slot}"') for slot in ("M", "N", "E", "H")
    )
    assert 'lang="hi"' in text     # 기본 언어 = prescriptions.lang (D12)

    r_en = client.get(f"/p/{token}", params={"lang": "en"})
    assert r_en.status_code == 200
    assert re.search(r'<html[^>]*\blang="en"', r_en.text)  # ?lang= 우선 (D12)
    assert "Afternoon" in r_en.text


def test_infographic_ab_prototype_is_served_and_marked_demo(client):
    """비교용 정적 A/B는 실제 /static URL에서 열리고 의료 조언으로 오인되지 않는다."""
    r = client.get("/static/ux-infographic-poster-en.html")

    assert r.status_code == 200
    assert "Infographic strip prototype" in r.text
    assert "DEMO ONLY" in r.text
    assert all(label in r.text for label in ("Morning", "Afternoon", "Evening", "Night"))
    assert r.text.count('class="medicine-strip"') == 7
    assert r.text.count('class="prn-rule"') == 2
    assert "Repeat the packet" not in r.text
    assert "Packet 4 · 1 drop" not in r.text
    assert "Diagnosis is not inferred or displayed" in r.text


def test_complex_poster_keeps_units_half_dose_prn_and_long_names(client, issue):
    """6약에서도 봉투 번호·단위·반알·PRN 제한과 긴 약명이 잘리지 않고 SSR 된다."""
    payload = demo.build_scenarios()["complex"]
    long_name = (
        "Demo Very Long Combination Medicine Name With Extended Release "
        "and Multiple Strengths 500 mg / 125 mg"
    )
    payload["items"][3]["drug_name_raw"] = long_name
    issued = issue(payload)
    assert issued.status_code == 201, issued.text

    r = client.get(f"/p/{issued.json()['token']}")
    assert r.status_code == 200
    core = r.text.split("<script>", 1)[0]  # JavaScript 없이도 존재해야 하는 복약 내용
    assert long_name in core
    assert "½" in core
    assert "ml" in core
    assert "निशान वाले कप या सिरिंज से नापें" in core
    assert "कैप्सूल" in core
    assert "बूंद" in core
    assert "special-time" in core
    assert "हर बार" in core and "Demo SOS Pain Tablet" in core
    assert "दिन में ज़्यादा से ज़्यादा" in core
    assert ">3<" in core and ">6<" in core
    assert 'class="prn-rules"' in core
    assert core.count('class="prn-rule"') == 2
    assert all(f">{n}<" in core for n in range(1, 7))
    assert "text-overflow" not in core
    for icon_id in ("p-tab", "p-cap", "p-spoon", "p-drop", "p-puff", "p-sachet", "p-application"):
        assert f'id="{icon_id}"' in core

    # 핵심 환자 문서는 느린 네트워크에서도 작은 편이어야 한다.
    assert len(gzip.compress(r.content, compresslevel=9)) < 24 * 1024


def test_legacy_prn_without_limits_shows_ask_pharmacy_warning(client, db_conn, rx_payload):
    """신규 발급은 제한을 필수화하지만, 기존 PRN 행의 누락은 숨기지 않는다."""
    payload = rx_payload(pattern_key="PRN")
    item = payload["items"][0]
    item.update({
        "doses": {"M": 0, "N": 0, "E": 0, "H": 0},
        "extra_params": {"dose_per_use": 1},
        "prn_reason_key": "pain",
        "prn_max_per_day": None,
        "prn_min_gap_hours": None,
    })
    created = db.create_prescription(db_conn, "ph-demo-001", payload)

    r = client.get(f"/p/{created['token']}")
    assert r.status_code == 200
    assert "दिन की अधिकतम सीमा" in r.text
    assert "ask the pharmacy before taking it" in r.text


def test_patient_metadata_is_generic_and_privacy_page_exists(client, issue, rx_payload):
    payload = rx_payload(drug_name="Sensitive Demo Medicine")
    payload["patient_label"] = "PRIVATE LABEL"
    issued = issue(payload)
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    r = client.get(f"/p/{token}")

    head = r.text.split("</head>", 1)[0]
    assert "PRIVATE LABEL" not in r.text
    assert "Sensitive Demo Medicine" not in head
    assert token not in head
    whatsapp = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', r.text)
    assert whatsapp is not None
    share_target = unquote(whatsapp.group(1))
    assert "Sensitive Demo Medicine" not in share_target
    assert "PRIVATE LABEL" not in share_target
    assert f"/p/{token}?lang=hi&src=share" in share_target
    assert client.get("/privacy").status_code == 200


def test_pending_and_code_miss_have_js_free_retry_actions(client):
    pending = client.get("/p/" + "B" * 22)
    assert pending.status_code == 200
    assert 'class="retry"' in pending.text
    assert "<script" not in pending.text.lower()

    miss = client.get("/c", params={"code": "BAD-CODE"})
    assert miss.status_code == 404
    assert 'action="/c"' in miss.text
    assert 'name="code"' in miss.text
    assert "<script" not in miss.text.lower()


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
