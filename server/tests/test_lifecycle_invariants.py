"""처방·토큰 생명주기 안전 불변식 회귀 테스트 (formal/spec.md).

각 테스트는 formal/ 모델 탐색·정적 분석에서 나온 반례 트레이스를 실제 서버로 재현한다.
- INV-2: 만료·폐기된 처방은 어떤 전이로도 다시 active가 되지 않는다 (01 §5.2).
- INV-10: 되돌릴 수 없는 결정(폐기 시각·감사 이벤트)은 이후 전이가 덮어쓰지 않는다.
- T1c: 동일 멱등키 + 다른 본문은 409 IDEMPOTENCY_CONFLICT (01 §4.1) — 조용한 유실 금지.
"""
from __future__ import annotations

import uuid

from tests.conftest import PHARMACY_HEADERS

PAST_TS = "2020-01-01T00:00:00Z"


def _expire_token(db_conn, token: str) -> None:
    """시간 경과를 시뮬레이션 — expires_at을 과거로 옮긴다(파생 상태 expired)."""
    db_conn.execute(
        "UPDATE access_tokens SET expires_at = ? WHERE token = ?", (PAST_TS, token)
    )
    db_conn.commit()


# ---------------------------------------------------------------- INV-2

def test_edit_expired_prescription_does_not_resurrect_token(
    client, issue, rx_payload, db_conn
):
    """반례 트레이스: create(5d) → 만료 → PUT(90d) → GET /p/{token}.

    구현이 PUT에서 expires_at을 재산정하면 만료된 QR이 되살아난다(INV-2 위반).
    기대: 만료된 처방의 수정은 410이고, 환자 링크는 계속 410이다."""
    created = issue(rx_payload(duration_days=5)).json()
    token, rx_id = created["token"], created["id"]

    _expire_token(db_conn, token)
    assert client.get(f"/p/{token}").status_code == 410  # 전제: 만료 관측됨

    edit_body = rx_payload(duration_days=90)
    edit_body.pop("client_input_id")
    resp = client.put(
        f"/api/prescriptions/{rx_id}", json=edit_body, headers=PHARMACY_HEADERS
    )
    assert resp.status_code == 410, (
        "만료된 처방 수정은 거부돼야 한다 — 200이면 expires_at 재산정으로 토큰이 부활한다"
    )
    assert resp.json()["error"]["code"] == "LINK_EXPIRED"

    # 어떤 경우에도 환자 링크가 부활하면 안 된다 (INV-2의 실제 관측면)
    assert client.get(f"/p/{token}").status_code == 410

    # 내용도 바뀌지 않았어야 한다 (거부된 수정의 부분 효과 금지 — INV-9)
    row = db_conn.execute(
        "SELECT version FROM prescriptions WHERE id = ?", (rx_id,)
    ).fetchone()
    assert row["version"] == 1


def test_edit_active_prescription_still_works(client, issue, rx_payload, db_conn):
    """가드 추가가 정상 경로(active 수정 = 버전 업, 토큰 불변)를 깨지 않는다."""
    created = issue(rx_payload(duration_days=5)).json()
    token, rx_id = created["token"], created["id"]

    edit_body = rx_payload(duration_days=7)
    edit_body.pop("client_input_id")
    resp = client.put(
        f"/api/prescriptions/{rx_id}", json=edit_body, headers=PHARMACY_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["access"]["token"] == token
    assert client.get(f"/p/{token}").status_code == 200


# ---------------------------------------------------------------- INV-10

def test_second_reissue_of_revoked_prescription_is_rejected(
    client, issue, rx_payload, db_conn
):
    """반례 트레이스: create → reissue(cid r1) → reissue(cid r2, 같은 구건).

    구현이 구건 상태를 가드 없이 UPDATE하면: revoked_at이 새 시각으로 덮어써지고
    (INV-10), rx.revoked 감사가 중복되며, 대체 처방이 두 갈래로 발급된다.
    기대: 이미 폐기된 처방의 재발급은 410 — 대체본(replacement)을 재발급해야 한다."""
    created = issue(rx_payload()).json()
    old_id, old_token = created["id"], created["token"]

    first = client.post(
        f"/api/prescriptions/{old_id}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )
    assert first.status_code == 201
    original_revoked_at = db_conn.execute(
        "SELECT revoked_at FROM access_tokens WHERE token = ?", (old_token,)
    ).fetchone()["revoked_at"]
    assert original_revoked_at is not None

    second = client.post(
        f"/api/prescriptions/{old_id}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "other"},
        headers=PHARMACY_HEADERS,
    )
    assert second.status_code == 410, (
        "이미 폐기된 처방의 재발급이 성공하면 폐기 시각이 덮어써지고 대체본이 분기한다"
    )
    assert second.json()["error"]["code"] == "LINK_REVOKED"

    # 폐기 시각(provenance) 불변
    row = db_conn.execute(
        "SELECT revoked_at FROM access_tokens WHERE token = ?", (old_token,)
    ).fetchone()
    assert row["revoked_at"] == original_revoked_at

    # 감사 이벤트 중복 없음 + 대체본 분기 없음
    revoked_events = db_conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE event_type='rx.revoked' AND prescription_id=?",
        (old_id,),
    ).fetchone()["c"]
    assert revoked_events == 1
    replacements = db_conn.execute(
        "SELECT COUNT(*) AS c FROM prescriptions WHERE reissue_of = ?", (old_id,)
    ).fetchone()["c"]
    assert replacements == 1


def test_reissue_retry_with_same_cid_still_replays(client, issue, rx_payload):
    """가드가 정당한 재전송 멱등(같은 cid의 reissue 재시도 → 200 replay)을 깨지 않는다."""
    created = issue(rx_payload()).json()
    old_id = created["id"]
    cid = str(uuid.uuid4())

    first = client.post(
        f"/api/prescriptions/{old_id}/reissue",
        json={"client_input_id": cid, "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )
    assert first.status_code == 201

    retry = client.post(
        f"/api/prescriptions/{old_id}/reissue",
        json={"client_input_id": cid, "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True
    assert retry.json()["token"] == first.json()["token"]


def test_edit_revoked_prescription_is_rejected(client, issue, rx_payload, db_conn):
    """INV-2 revoked 트윈: 폐기된 처방의 수정은 410 — 구건은 부활하지 않는다.
    edit_prescription의 revoked 가드는 만료 트윈만 회귀 고정돼 있었다(coverage-gap 지적)."""
    created = issue(rx_payload()).json()
    old_id = created["id"]
    # reissue로 구건을 revoked 상태로 만든다
    client.post(
        f"/api/prescriptions/{old_id}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "input_error"},
        headers=PHARMACY_HEADERS,
    )

    edit_body = rx_payload(duration_days=30)
    edit_body.pop("client_input_id")
    resp = client.put(
        f"/api/prescriptions/{old_id}", json=edit_body, headers=PHARMACY_HEADERS
    )
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "LINK_REVOKED"
    # 폐기된 구건 버전이 오르지 않는다
    row = db_conn.execute(
        "SELECT version FROM prescriptions WHERE id = ?", (old_id,)
    ).fetchone()
    assert row["version"] == 1


def test_reissue_of_expired_prescription_still_allowed(
    client, issue, rx_payload, db_conn
):
    """§4.4: 만료된 처방의 재발급은 허용된다(신규 처방으로) — 가드는 revoked만 막는다."""
    created = issue(rx_payload()).json()
    _expire_token(db_conn, created["token"])

    resp = client.post(
        f"/api/prescriptions/{created['id']}/reissue",
        json={"client_input_id": str(uuid.uuid4()), "reason": "other"},
        headers=PHARMACY_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["reissue_of"] == created["id"]


# ---------------------------------------------------------------- T1c

def test_same_idempotency_key_with_different_items_is_conflict(
    client, issue, rx_payload, db_conn
):
    """반례 트레이스: POST(A) 성공 → 응답 유실 → 약사가 같은 폼에서 내용 수정 → POST(B, 같은 cid).

    구현이 본문 비교 없이 replay하면 수정본 B가 조용히 유실된다(약사는 성공으로 인식).
    기대: 409 IDEMPOTENCY_CONFLICT (01 §4.1)."""
    cid = str(uuid.uuid4())
    first = issue(rx_payload(cid, drug_name="Dolo 650"))
    assert first.status_code == 201

    conflict = issue(rx_payload(cid, drug_name="Augmentin 625 Duo"))
    assert conflict.status_code == 409, (
        "동일 멱등키 + 다른 items가 200 replay로 흡수되면 수정 내용이 조용히 유실된다"
    )
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # 원본 내용 보존 확인 (부분 효과 금지)
    item = db_conn.execute(
        "SELECT drug_name_raw FROM prescription_items pi JOIN prescriptions p "
        "ON pi.prescription_id = p.id WHERE p.client_input_id = ?",
        (cid,),
    ).fetchone()
    assert item["drug_name_raw"] == "Dolo 650"


def test_same_key_same_body_replays_even_with_different_metrics(issue, rx_payload):
    """오프라인 outbox 재전송은 retry_count 등 client_metrics만 달라진다(01 §4.3).
    계측 필드 차이는 충돌이 아니다 — 반드시 200 replay + 동일 token."""
    cid = str(uuid.uuid4())
    first = issue(rx_payload(cid))
    assert first.status_code == 201

    retry_body = rx_payload(cid)
    retry_body["client_metrics"]["retry_count"] = 3
    retry_body["client_metrics"]["input_duration_ms"] = 99999
    retry = issue(retry_body)
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True
    assert retry.json()["token"] == first.json()["token"]


def test_offline_replay_with_same_client_token_replays(issue, rx_payload):
    """오프라인 사전생성 token 포함 동일 재전송도 replay(§2.3 ① 자기 재전송)."""
    cid = str(uuid.uuid4())
    body = rx_payload(cid)
    body["token"] = "hV8s3kQxWnA9cLd3Ye7Rk2"
    first = issue(body)
    assert first.status_code == 201
    assert first.json()["token"] == "hV8s3kQxWnA9cLd3Ye7Rk2"

    retry = issue(rx_payload(cid) | {"token": "hV8s3kQxWnA9cLd3Ye7Rk2"})
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True
