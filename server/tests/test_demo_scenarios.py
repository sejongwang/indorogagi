"""대표 데모 처방 생성기 계약 테스트."""
from __future__ import annotations

from app import db
from scripts import demo


def test_build_scenarios_has_simple_general_and_complex_sizes():
    scenarios = demo.build_scenarios()

    assert list(scenarios) == ["simple", "general", "complex"]
    assert {name: len(payload["items"]) for name, payload in scenarios.items()} == {
        "simple": 1,
        "general": 3,
        "complex": 6,
    }


def test_complex_scenario_covers_units_prn_and_special_pattern():
    items = demo.build_scenarios()["complex"]["items"]

    assert any(i["dose_unit"] == "tablet" and 0.5 in i["doses"].values() for i in items)
    assert any(i["dose_unit"] == "ml" for i in items)
    assert any(i["dose_unit"] == "capsule" for i in items)
    assert any(i["dose_unit"] == "drop" for i in items)

    prn = next(i for i in items if i["pattern_key"] == "PRN")
    assert prn["extra_params"] == {"dose_per_use": 1}
    assert prn["prn_max_per_day"] == 3
    assert prn["prn_min_gap_hours"] == 6
    assert prn["prn_reason_key"] == "pain"

    weekly = next(i for i in items if i["pattern_key"] == "WEEKLY_ONCE")
    assert weekly["extra_params"] == {"day_of_week": "sun"}


def test_every_scenario_is_bilingual_demo_only_and_od_night_uses_h_slot():
    scenarios = demo.build_scenarios()

    for payload in scenarios.values():
        assert "DEMO ONLY" in payload["note"]
        assert "NOT MEDICAL ADVICE" in payload["note"]
        assert "केवल डेमो" in payload["note"]
        assert "वास्तविक दवा सलाह नहीं" in payload["note"]

    night_items = [
        item
        for payload in scenarios.values()
        for item in payload["items"]
        if item["pattern_key"] == "OD_NIGHT"
    ]
    assert night_items
    assert all(item["doses"] == {"M": 0, "N": 0, "E": 0, "H": 1} for item in night_items)


def test_create_demo_set_returns_active_scenario_and_state_urls(db_conn):
    report = demo.create_demo_set(db_conn, base_url="https://demo.test/")

    assert list(report["scenarios"]) == ["simple", "general", "complex"]
    for name, expected_count in (("simple", 1), ("general", 3), ("complex", 6)):
        result = report["scenarios"][name]
        assert result["item_count"] == expected_count
        assert result["patient_url"] == f"https://demo.test/p/{result['token']}"
        assert result["qr_url"] == f"https://demo.test/rx/{result['prescription_id']}/qr"
        assert db.get_bundle_by_token(db_conn, result["token"])[0] == db.TOKEN_STATUS_ACTIVE

    assert set(report["states"]) == {"pending", "expired", "revoked"}
    storage_status = {
        "pending": db.TOKEN_STATUS_MISSING,
        "expired": db.TOKEN_STATUS_EXPIRED,
        "revoked": db.TOKEN_STATUS_REVOKED,
    }
    for expected_status, patient_url in report["states"].items():
        token = patient_url.rsplit("/", 1)[-1]
        actual_status = db.get_bundle_by_token(db_conn, token)[0]
        assert actual_status == storage_status[expected_status]

    notes = db_conn.execute("SELECT note FROM prescriptions").fetchall()
    assert notes
    assert all(row["note"] == demo.DEMO_WARNING for row in notes)


def test_print_report_includes_every_patient_qr_and_status_url(db_conn, capsys):
    report = demo.create_demo_set(db_conn, base_url="https://demo.test")

    demo.print_report(report)
    output = capsys.readouterr().out

    assert demo.DEMO_WARNING in output
    for result in report["scenarios"].values():
        assert result["patient_url"] in output
        assert result["qr_url"] in output
    for state, patient_url in report["states"].items():
        assert state in output
        assert patient_url in output
