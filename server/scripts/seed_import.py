"""data/drugs-seed.json → drugs 테이블 임포트 + 데모 약국 upsert.

실행: cd server && uv run python scripts/seed_import.py
매핑(drugs-README.md 정본): molecules → generic_name(" + " 평탄화), aliases → aliases_json,
source → source(직렬화 TEXT), verified=0 일괄. priority_rank·is_generic·category는 시드 전용(미저장).
default_pattern_key/default_timing_food/default_dose_unit은 null — 용법 제안 금지 원칙.
재실행 멱등: (brand_name, strength, form) 자연키 upsert(id 보존).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from app import db  # noqa: E402

REPO_ROOT = SERVER_DIR.parent
SEED_PATH = REPO_ROOT / "data" / "drugs-seed.json"

DEMO_PHARMACY = {
    "id": "ph-demo-001",
    "name": "Sharma Medical Store",
    "area": "Karol Bagh, Delhi",
    "pincode": "110005",
    "ui_lang": "en",
    "default_patient_lang": "hi",
    "has_printer": 0,
    "is_active": 1,
}


def map_entry(entry: dict) -> dict:
    molecules = entry.get("molecules") or []
    return {
        "brand_name": entry["brand_name"],
        "generic_name": " + ".join(molecules) if molecules else entry.get("generic_name"),
        "strength": entry.get("strength"),
        "form": entry.get("form"),
        "default_pattern_key": None,
        "default_timing_food": None,
        "default_dose_unit": None,
        "aliases": entry.get("aliases") or [],
        "caution_keys": [],
        "source": json.dumps(entry.get("source") or [], ensure_ascii=False),
        "verified": 0,
    }


def main() -> None:
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    entries = seed["drugs"]
    expected = seed.get("meta", {}).get("count")

    db.init_db()
    conn = db.get_conn()
    try:
        processed = db.import_drugs(conn, [map_entry(e) for e in entries])
        db.upsert_pharmacy(conn, DEMO_PHARMACY)
        total = conn.execute("SELECT COUNT(*) AS n FROM drugs").fetchone()["n"]
        ph = conn.execute("SELECT COUNT(*) AS n FROM pharmacies").fetchone()["n"]
    finally:
        conn.close()

    print(f"seed entries: {len(entries)} (meta.count={expected})")
    print(f"processed (insert/update): {processed}")
    print(f"drugs table total: {total}")
    print(f"pharmacies total: {ph} (demo: {DEMO_PHARMACY['id']})")
    if expected is not None and len(entries) != expected:
        print("WARNING: meta.count와 실제 항목 수 불일치")


if __name__ == "__main__":
    main()
