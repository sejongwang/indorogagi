"""Dose-unit contract coverage for pharmacist input and patient rendering."""
from __future__ import annotations

import uuid

import pytest

from app.config import DOSE_UNITS, load_config
from app.routers.patient import _PICTO, _contextual_unit_pair, _pictos


EXTENDED_UNITS = (
    "measuring_spoon",
    "inhalation",
    "packet",
    "suppository",
    "injection",
    "patch",
    "spray",
)


def test_every_supported_unit_has_bilingual_full_and_short_labels():
    i18n = load_config()["i18n"]

    assert tuple(i18n["dose_units"]) == DOSE_UNITS
    for unit in DOSE_UNITS:
        assert i18n["dose_units"][unit]["en"]
        assert i18n["dose_units"][unit]["hi"]
        assert i18n["ui"]["dose_units_short"][unit]["en"]
        assert i18n["ui"]["dose_units_short"][unit]["hi"]
        assert unit in _PICTO
        assert _pictos(unit, 1) == [_PICTO[unit]]


def test_pharmacist_form_bootstraps_every_unit_choice(client):
    page = client.get("/rx/new")

    assert page.status_code == 200
    for unit in DOSE_UNITS:
        assert f'"{unit}"' in page.text


@pytest.mark.parametrize("unit", DOSE_UNITS)
def test_prescription_api_accepts_and_persists_every_supported_unit(
    client, rx_payload, unit
):
    payload = rx_payload(cid=str(uuid.uuid4()))
    payload["items"][0]["dose_unit"] = unit
    if unit == "drop":
        payload["items"][0]["administration_route"] = "ophthalmic"

    issued = client.post(
        "/api/prescriptions",
        json=payload,
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )

    assert issued.status_code == 201, issued.text
    detail = client.get(
        f"/api/prescriptions/{issued.json()['id']}",
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )
    assert detail.status_code == 200
    assert detail.json()["items"][0]["dose_unit"] == unit


def test_unmarked_household_teaspoon_is_not_a_dose_unit(client, rx_payload):
    payload = rx_payload()
    payload["items"][0]["dose_unit"] = "teaspoon"

    response = client.post(
        "/api/prescriptions",
        json=payload,
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["field"] == "items.0.dose_unit"


def test_dose_unit_must_be_explicitly_selected(client, rx_payload):
    payload = rx_payload()
    payload["items"][0].pop("dose_unit")

    response = client.post(
        "/api/prescriptions",
        json=payload,
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["field"] == "items.0.dose_unit"


def test_marked_spoon_warning_is_visible_on_js_free_patient_page(
    client, issue, rx_payload
):
    payload = rx_payload()
    payload["lang"] = "en"
    payload["items"][0]["dose_unit"] = "measuring_spoon"
    issued = issue(payload)
    assert issued.status_code == 201, issued.text

    page = client.get(f"/p/{issued.json()['token']}?lang=en")
    core = page.text.split("<script>", 1)[0]

    assert "marked 5 ml spoon" in core
    assert "not a household teaspoon" in core
    assert 'id="p-measuring-spoon"' in core


@pytest.mark.parametrize(
    ("route", "en", "hi"),
    (
        ("oral", "oral drop", "मुँह से देने वाली बूंद"),
        ("ophthalmic", "eye drop", "आँख की बूंद"),
        ("otic", "ear drop", "कान की बूंद"),
    ),
)
def test_catalog_route_clarifies_drop_label_without_changing_unit(route, en, hi):
    i18n = load_config()["i18n"]

    label = _contextual_unit_pair("drop", {"route_code": route}, i18n, short=True)

    assert label == {"hi": hi, "en": en}
    assert "drop" in DOSE_UNITS
    assert not {"oral_drop", "eye_drop", "ear_drop"}.intersection(DOSE_UNITS)


@pytest.mark.parametrize("unit", EXTENDED_UNITS)
def test_extended_unit_patient_page_uses_text_number_and_own_svg(
    client, issue, rx_payload, unit
):
    payload = rx_payload()
    payload["lang"] = "en"
    payload["items"][0]["dose_unit"] = unit
    if unit == "drop":
        payload["items"][0]["administration_route"] = "ophthalmic"
    issued = issue(payload)
    assert issued.status_code == 201, issued.text

    page = client.get(f"/p/{issued.json()['token']}?lang=en")
    core = page.text.split("<script>", 1)[0]
    label = load_config()["i18n"]["ui"]["dose_units_short"][unit]["en"]

    assert label in core
    assert "<strong class=\"dose-number\">1</strong>" in core
    assert f'<use href="#{_PICTO[unit]}"' in core


def test_free_text_drop_requires_and_preserves_pharmacist_confirmed_route(
    client, rx_payload
):
    missing = rx_payload(drug_name="Handwritten free-text drops")
    missing["items"][0]["dose_unit"] = "drop"
    response = client.post(
        "/api/prescriptions",
        json=missing,
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["field"] == "items.0.administration_route"

    payload = rx_payload(drug_name="Handwritten free-text drops")
    payload["lang"] = "en"
    payload["items"][0].update(
        {"dose_unit": "drop", "administration_route": "otic"}
    )
    issued = client.post(
        "/api/prescriptions",
        json=payload,
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )
    assert issued.status_code == 201, issued.text

    detail = client.get(
        f"/api/prescriptions/{issued.json()['id']}",
        headers={"X-Pharmacy-Id": "ph-demo-001"},
    )
    assert detail.json()["items"][0]["administration_route"] == "otic"
    patient = client.get(f"/p/{issued.json()['token']}?lang=en")
    assert "ear drop" in patient.text.split("<script>", 1)[0]
