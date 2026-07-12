"""대표 데모 처방과 환자 상태 URL 생성기.

실행: cd server && uv run python scripts/demo.py
서버 기동 여부와 무관하게 DB(server/var/indoro.db)에 직접 생성한다.
매 실행마다 새 client_input_id → 새 처방·새 토큰(기존 데모 건은 그대로 남음).
외부 호출은 하지 않으며 simple/general/complex와 pending/expired/revoked URL을 출력한다.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from app import db  # noqa: E402

BASE_URL = "http://127.0.0.1:8600"
PHARMACY_ID = "ph-demo-001"
DEMO_WARNING = (
    "DEMO ONLY — NOT MEDICAL ADVICE / "
    "केवल डेमो — यह वास्तविक दवा सलाह नहीं है"
)


def _item(
    pos: int,
    name: str,
    pattern: str,
    doses: dict[str, float],
    food: str | None,
    days: int,
    qty: float,
    *,
    unit: str = "tablet",
    administration_route: str | None = None,
    extra_params: dict | None = None,
    prn_reason_key: str | None = None,
    prn_max_per_day: float | None = None,
    prn_min_gap_hours: float | None = None,
) -> dict:
    return {
        "position": pos,
        "drug_name_raw": name,
        "drug_id": None,
        "pattern_key": pattern,
        "doses": doses,
        "dose_unit": unit,
        "administration_route": administration_route,
        "timing_food": food,
        "duration_days": days,
        "total_quantity": qty,
        "extra_params": extra_params,
        "prn_reason_key": prn_reason_key,
        "prn_max_per_day": prn_max_per_day,
        "prn_min_gap_hours": prn_min_gap_hours,
        "note": None,
    }


def _payload(label: str, items: list[dict]) -> dict:
    return {
        "client_input_id": str(uuid.uuid4()),
        "issued_at_client": db.now_utc(),
        "lang": "hi",
        "patient_label": label,
        "note": DEMO_WARNING,
        "items": items,
    }


def build_scenarios() -> dict[str, dict]:
    """새 UUID를 가진 대표 처방 3종을 API payload 형태로 반환한다."""
    z = {"M": 0, "N": 0, "E": 0, "H": 0}
    return {
        "simple": _payload(
            "DEMO · Simple",
            [
                _item(
                    1, "Dolo 650", "OD_MORNING",
                    {**z, "M": 1}, "after_food", 3, 3,
                ),
            ],
        ),
        "general": _payload(
            "DEMO · General",
            [
                _item(
                    1, "Pantocid 40", "OD_MORNING",
                    {**z, "M": 1}, "empty_stomach", 10, 10,
                ),
                _item(
                    2, "Augmentin 625 Duo", "BD",
                    {**z, "M": 1, "E": 1}, "after_food", 5, 10,
                ),
                # OD_NIGHT의 새 의미 계약은 bedtime(H) 슬롯이다.
                _item(
                    3, "Atorva 10", "OD_NIGHT",
                    {**z, "H": 1}, "after_food", 30, 30,
                ),
            ],
        ),
        "complex": _payload(
            "DEMO · Complex",
            [
                _item(
                    1, "Nebicard 2.5", "OD_MORNING",
                    {**z, "M": 0.5}, "before_food", 30, 15,
                ),
                _item(
                    2, "Ascoril LS Syrup", "TDS",
                    {**z, "M": 5, "N": 5, "E": 5}, "after_food", 5, 75,
                    unit="ml",
                ),
                _item(
                    3, "Uprise-D3 60K", "WEEKLY_ONCE",
                    {**z, "M": 1}, "with_food", 28, 4,
                    unit="capsule", extra_params={"day_of_week": "sun"},
                ),
                _item(
                    4, "Demo Lubricating Eye Drops Preservative-Free Multi-Dose Bottle 10 ml", "QID",
                    {"M": 1, "N": 1, "E": 1, "H": 1}, None, 7, 28,
                    unit="drop", administration_route="ophthalmic",
                ),
                _item(
                    5, "Demo SOS Pain Tablet", "PRN", z.copy(), "after_food", 5, 10,
                    extra_params={"dose_per_use": 1},
                    prn_reason_key="pain", prn_max_per_day=3, prn_min_gap_hours=6,
                ),
                _item(
                    6, "Demo Bedtime Sachet", "OD_NIGHT",
                    {**z, "H": 1}, "empty_stomach", 7, 7,
                    unit="sachet",
                ),
            ],
        ),
    }


def _status_payload(label: str) -> dict:
    return _payload(
        label,
        [
            _item(
                1, "Demo Status Tablet", "OD_MORNING",
                {"M": 1, "N": 0, "E": 0, "H": 0}, "after_food", 1, 1,
            ),
        ],
    )


def _result_urls(result: dict, *, base_url: str, item_count: int) -> dict:
    return {
        "prescription_id": result["id"],
        "token": result["token"],
        "short_code": result["short_code_display"],
        "expires_at": result["expires_at"],
        "item_count": item_count,
        "patient_url": f"{base_url}/p/{result['token']}",
        "qr_url": f"{base_url}/rx/{result['id']}/qr",
    }


def _new_missing_token(conn) -> str:
    for _ in range(8):
        token = db.new_token()
        if db.get_bundle_by_token(conn, token)[0] == db.TOKEN_STATUS_MISSING:
            return token
    raise RuntimeError("could not allocate a missing-token demo URL")


def create_demo_set(conn, *, base_url: str = BASE_URL) -> dict:
    """격리 가능한 DB 연결에 대표·상태 데모를 만들고 출력용 URL 보고서를 반환한다."""
    if db.get_pharmacy(conn, PHARMACY_ID) is None:
        raise RuntimeError(
            "데모 약국(ph-demo-001) 없음 — 먼저 `uv run python scripts/seed_import.py` 실행"
        )

    base = base_url.rstrip("/")
    scenario_report: dict[str, dict] = {}
    for name, payload in build_scenarios().items():
        result = db.create_prescription(conn, PHARMACY_ID, payload)
        scenario_report[name] = _result_urls(
            result, base_url=base, item_count=len(payload["items"])
        )

    pending_token = _new_missing_token(conn)

    expired = db.create_prescription(
        conn, PHARMACY_ID, _status_payload("DEMO · Expired state")
    )
    cur = conn.execute(
        "UPDATE access_tokens SET expires_at = ? WHERE token = ?",
        ("2000-01-01T00:00:00Z", expired["token"]),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError("failed to prepare expired demo token")
    conn.commit()

    revoked = db.create_prescription(
        conn, PHARMACY_ID, _status_payload("DEMO · Revoked state")
    )
    db.reissue(
        conn,
        revoked["id"],
        PHARMACY_ID,
        {"client_input_id": str(uuid.uuid4()), "reason": "other"},
    )

    return {
        "warning": DEMO_WARNING,
        "scenarios": scenario_report,
        "states": {
            "pending": f"{base}/p/{pending_token}",
            "expired": f"{base}/p/{expired['token']}",
            "revoked": f"{base}/p/{revoked['token']}",
        },
    }


def print_report(report: dict) -> None:
    print(f"WARNING         : {report['warning']}")
    for name, result in report["scenarios"].items():
        print(f"\n[{name}] ({result['item_count']} medicines)")
        print(f"prescription_id : {result['prescription_id']}")
        print(f"short_code      : {result['short_code']}")
        print(f"expires_at      : {result['expires_at']}")
        print(f"patient URL     : {result['patient_url']}")
        print(f"pharmacist QR   : {result['qr_url']}")
    print("\n[state demos]")
    for state, patient_url in report["states"].items():
        print(f"{state:16}: {patient_url}")


def main() -> None:
    db.init_db()
    conn = db.get_conn()
    try:
        try:
            report = create_demo_set(conn)
        except RuntimeError as exc:
            sys.exit(str(exc))
    finally:
        conn.close()
    print_report(report)


if __name__ == "__main__":
    main()
