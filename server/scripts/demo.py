"""데모 처방 1건(FX-A 3항목) 생성 후 토큰 URL 출력 — 즉시 데모용.

실행: cd server && uv run python scripts/demo.py
서버 기동 여부와 무관하게 DB(server/var/indoro.db)에 직접 생성한다.
매 실행마다 새 client_input_id → 새 처방·새 토큰(기존 데모 건은 그대로 남음).
FX-A: Dolo 650 TDS 5일 after_food / Azithral 500 OD_MORNING 3일 after_food
      / Pantocid 40 OD_MORNING 10일 empty_stomach, patient_label "Mr. S", lang hi.
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


def _item(pos: int, name: str, pattern: str, doses: dict, food: str,
          days: int, qty: float) -> dict:
    return {
        "position": pos,
        "drug_name_raw": name,
        "drug_id": None,
        "pattern_key": pattern,
        "doses": doses,
        "dose_unit": "tablet",
        "timing_food": food,
        "duration_days": days,
        "total_quantity": qty,
        "extra_params": None,
        "note": None,
    }


def main() -> None:
    payload = {
        "client_input_id": str(uuid.uuid4()),
        "issued_at_client": db.now_utc(),
        "lang": "hi",
        "patient_label": "Mr. S",
        "items": [
            _item(1, "Dolo 650", "TDS", {"M": 1, "N": 1, "E": 1, "H": 0},
                  "after_food", 5, 15),
            _item(2, "Azithral 500", "OD_MORNING", {"M": 1, "N": 0, "E": 0, "H": 0},
                  "after_food", 3, 3),
            _item(3, "Pantocid 40", "OD_MORNING", {"M": 1, "N": 0, "E": 0, "H": 0},
                  "empty_stomach", 10, 10),
        ],
    }
    db.init_db()
    conn = db.get_conn()
    try:
        if db.get_pharmacy(conn, PHARMACY_ID) is None:
            sys.exit("데모 약국(ph-demo-001) 없음 — 먼저 `uv run python scripts/seed_import.py` 실행")
        result = db.create_prescription(conn, PHARMACY_ID, payload)
    finally:
        conn.close()

    print(f"prescription_id : {result['id']}")
    print(f"short_code      : {result['short_code_display']}")
    print(f"expires_at      : {result['expires_at']}")
    print(f"patient URL     : {BASE_URL}/p/{result['token']}")
    print(f"pharmacist QR   : {BASE_URL}/rx/{result['id']}/qr")


if __name__ == "__main__":
    main()
