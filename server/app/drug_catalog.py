"""출처 추적 의약품 카탈로그.

이 모듈은 약 식별과 약사 검색만 담당한다. 복용량, 빈도, 기간, 식전/식후,
진단, 대체약을 계산하거나 제안하지 않는다. 원본 문자열은 보존하고 identity와
search 정규화 문자열을 별도로 만든다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

IMPORTER_VERSION = "drug-catalog-v2"
NORMALIZATION_VERSION = "catalog-normalization-v2"
PACKAGE_SCHEMA_VERSION = "2"
PRODUCTION_APPROVAL_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "drug-sources.json"
)

_SPACE_RE = re.compile(r"\s+")
_SEARCH_PUNCT_RE = re.compile(r"[-‐‑‒–—_/.,;:()\[\]{}+%]+")
_DIGIT_LETTER_RE = re.compile(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)", re.UNICODE)
_RELEASE_RE = re.compile(r"(?<![A-Z0-9])(IR|SR|ER|CR|XR|MR)(?![A-Z0-9])", re.I)
_KNOWN_STRENGTH_UNIT_RE = re.compile(
    r"(?:\b(?:mg|g|mcg|ug|ml|l|iu|au|unit|units)\b|µg|%|w/v|w/w)", re.I
)
_SIMPLE_STRENGTH_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(mg|g|mcg|ug|µg|ml|l|iu|unit|units|%)\s*$",
    re.I,
)

_SOURCE_APPROVAL_FIELDS = (
    "slug",
    "name",
    "operator",
    "tier",
    "usage_scope",
    "reuse_status",
    "license_name",
    "license_url",
    "attribution_text",
    "source_url",
    "input_uri",
    "legal_review_required",
    "version",
    "published_at",
    "source_updated_at",
    "declared_as_of",
    "accessed_at",
    "source_artifact_manifest",
    "source_artifact_sha256",
    "source_artifact_bytes",
    "transformation_method",
    "snapshot_mode",
    "package_schema_version",
)

_FORM_ALIASES = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
    "syp": "syrup", "syrup": "syrup",
    "susp": "suspension", "suspension": "suspension",
    "drop": "drops", "drops": "drops", "eye drop": "drops", "eye drops": "drops",
    "ear drop": "drops", "ear drops": "drops", "oral drop": "drops", "oral drops": "drops",
    "ophthalmic solution": "drops", "otic solution": "drops",
    "inj": "injection", "injection": "injection", "injectable": "injection",
    "cream": "cream", "ointment": "ointment", "gel": "gel",
    "inhaler": "inhaler", "inhalation": "inhalation",
    "sachet": "sachet", "packet": "packet", "powder": "powder",
    "solution": "solution", "oral solution": "solution",
    "suppository": "suppository", "patch": "patch", "spray": "spray",
    "other": "other",
}

_ROUTE_ALIASES = {
    "oral": "oral", "by mouth": "oral",
    "eye": "ophthalmic", "ophthalmic": "ophthalmic", "ocular": "ophthalmic",
    "ear": "otic", "otic": "otic",
    "nasal": "nasal", "intranasal": "nasal",
    "topical": "topical", "skin": "topical",
    "inhaled": "inhalation", "inhalation": "inhalation",
    "rectal": "rectal", "vaginal": "vaginal", "transdermal": "transdermal",
    "intravenous": "intravenous", "iv": "intravenous",
    "intramuscular": "intramuscular", "im": "intramuscular",
    "subcutaneous": "subcutaneous", "sc": "subcutaneous",
}

# 후보일 뿐 처방 기본값이 아니다. 반 알은 unit이 아니라 tablet 수량 0.5다.
# 계량 스푼은 눈금이 표시된 5 ml 의약품용 도구만 뜻하며 가정용 teaspoon은 제외한다.
FORM_UNIT_OPTIONS: dict[tuple[str, str | None], list[str]] = {
    ("tablet", None): ["tablet"],
    ("capsule", None): ["capsule"],
    ("syrup", None): ["ml", "measuring_spoon"],
    ("suspension", None): ["ml", "measuring_spoon"],
    ("solution", "oral"): ["ml", "measuring_spoon"],
    ("drops", "oral"): ["drop"],
    ("drops", "ophthalmic"): ["drop"],
    ("drops", "otic"): ["drop"],
    ("drops", "nasal"): ["drop"],
    ("inhaler", None): ["puff"],
    ("inhalation", None): ["inhalation"],
    ("sachet", None): ["sachet"],
    ("packet", None): ["packet"],
    ("powder", None): ["sachet", "packet"],
    ("cream", None): ["application"],
    ("ointment", None): ["application"],
    ("gel", None): ["application"],
    ("suppository", None): ["suppository"],
    ("injection", None): ["injection"],
    ("patch", None): ["patch"],
    ("spray", None): ["spray"],
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_NORMALIZED_PROJECTION_FIELDS = (
    "name_type",
    "brand_name_raw",
    "brand_name_norm",
    "brand_name_search",
    "generic_name_raw",
    "generic_name_norm",
    "generic_name_search",
    "strength_raw",
    "strength_search",
    "dosage_form_raw",
    "dosage_form_code",
    "route_raw",
    "route_code",
    "release_modifier_raw",
    "release_modifier_code",
    "manufacturer_name",
    "marketer_name",
    "rx_classification",
    "short_display_name",
    "package",
    "usage_scope",
    "lifecycle_status",
    "review_status",
    "dedupe_fingerprint",
    "incomplete_fields",
    "warnings",
    "caution_keys",
)


def _normalized_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Return the exact deterministic projection written to searchable tables."""
    projection = {field: record.get(field) for field in _NORMALIZED_PROJECTION_FIELDS}
    projection["ingredients"] = sorted(
        (dict(item) for item in record.get("ingredients") or []),
        key=lambda item: (item.get("ordinal", 0), item.get("name_norm") or ""),
    )
    projection["aliases"] = sorted(
        (dict(item) for item in record.get("aliases") or []),
        key=lambda item: (
            item.get("norm") or "",
            item.get("alias_type") or "",
            item.get("language") or "",
        ),
    )
    return projection


def _normalized_projection_diff(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    previous = previous or {}
    return {
        field: {"previous": previous.get(field), "next": current.get(field)}
        for field in sorted(set(previous) | set(current))
        if previous.get(field) != current.get(field)
    }


def records_sha256(records: Iterable[Any]) -> str:
    """Return the canonical hash used by the local production approval registry."""
    return _sha(list(records))


def source_metadata_sha256(source: dict[str, Any]) -> str:
    """Hash only source identity/provenance fields governed by package approval."""
    values = {field: source.get(field) for field in _SOURCE_APPROVAL_FIELDS}
    values["snapshot_mode"] = source.get("snapshot_mode") or "delta"
    values["package_schema_version"] = str(
        source.get("package_schema_version") or PACKAGE_SCHEMA_VERSION
    )
    return _sha(values)


def _load_approval_registry(
    approval_registry: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    if approval_registry is None:
        with PRODUCTION_APPROVAL_REGISTRY_PATH.open(encoding="utf-8") as handle:
            registry = json.load(handle)
    else:
        registry = approval_registry
    if not isinstance(registry, dict):
        raise ValueError("production approval registry must be a JSON object")
    return registry, _sha(registry)


def _require_iso_date(source: dict[str, Any], field: str) -> str:
    raw = source.get(field)
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError(f"production source requires {field} as YYYY-MM-DD")
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"production source has invalid {field}") from exc
    return raw


def _production_approval(
    source: dict[str, Any],
    raw_records: list[Any],
    approval_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    registry, registry_sha = _load_approval_registry(approval_registry)
    approvals = registry.get("approved_packages")
    if not isinstance(approvals, dict):
        raise ValueError("production approval registry has no approved_packages map")
    entry = approvals.get(source["slug"])
    if not isinstance(entry, dict):
        raise ValueError(
            f"production package slug {source['slug']!r} is not registered for import"
        )
    releases = entry.get("releases")
    if isinstance(releases, dict):
        release = releases.get(str(source["version"]))
        if not isinstance(release, dict):
            raise ValueError("production package version is not registered for import")
        approval_entry = {**entry, **release}
        approval_entry.pop("releases", None)
    else:
        approval_entry = entry
    if approval_entry.get("approval_status") != "approved":
        raise ValueError("production package approval is not active")
    approved_at = approval_entry.get("approved_at")
    if not isinstance(approved_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", approved_at):
        raise ValueError("production package approval requires approved_at as YYYY-MM-DD")
    try:
        date.fromisoformat(approved_at)
    except ValueError as exc:
        raise ValueError("production package approval has invalid approved_at") from exc
    if not approval_entry.get("approved_by_role"):
        raise ValueError("production package approval requires approved_by_role")
    actual_records_sha = records_sha256(raw_records)
    if approval_entry.get("records_sha256") != actual_records_sha:
        raise ValueError("production package records SHA-256 does not match local approval")
    actual_source_sha = source_metadata_sha256(source)
    if approval_entry.get("source_metadata_sha256") != actual_source_sha:
        raise ValueError("production package source metadata does not match local approval")
    return {
        "registry_sha256": registry_sha,
        "records_sha256": actual_records_sha,
        "source_metadata_sha256": actual_source_sha,
        "entry": dict(approval_entry),
    }


def _simple_strength(raw: Any) -> tuple[float | None, str | None]:
    """Parse only a single scalar and unit; leave ratios/equivalents/combos raw-only."""
    if raw in (None, ""):
        return None, None
    text = str(raw).strip()
    if any(marker in text.casefold() for marker in ("/", "+", "equivalent", " as ")):
        return None, None
    match = _SIMPLE_STRENGTH_RE.fullmatch(text)
    if not match:
        return None, None
    unit = match.group(2).casefold()
    unit = {"ug": "mcg", "µg": "mcg", "units": "unit", "iu": "IU"}.get(unit, unit)
    return float(match.group(1)), unit


def _json_safe_record(value: Any) -> Any:
    """Retain malformed record evidence even if a direct caller passes non-JSON data."""
    try:
        _json(value)
        return value
    except (TypeError, ValueError):
        return {"unserializable_type": type(value).__name__, "repr": repr(value)}


def normalize_identity(value: Any) -> str:
    """병합 비교용: NFKC + casefold + 공백만 정리하고 구두점은 보존한다."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return _SPACE_RE.sub(" ", text).strip()


def normalize_search(value: Any) -> str:
    """검색용: identity 위에 구두점/하이픈과 숫자-문자 경계를 공백화한다."""
    text = normalize_identity(value)
    text = _SEARCH_PUNCT_RE.sub(" ", text)
    text = _DIGIT_LETTER_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _form_code(raw: Any) -> str | None:
    key = normalize_search(raw)
    return _FORM_ALIASES.get(key)


def _route_code(raw: Any) -> str | None:
    key = normalize_search(raw)
    return _ROUTE_ALIASES.get(key)


def _release_code(explicit: Any, *texts: Any) -> tuple[str | None, str | None]:
    if explicit:
        raw = str(explicit).strip()
        code = raw.upper()
        return raw, code if code in {"IR", "SR", "ER", "CR", "XR", "MR"} else None
    for text in texts:
        match = _RELEASE_RE.search(str(text or ""))
        if match:
            return match.group(0), match.group(1).upper()
    return None, None


def _unit_options(form: str | None, route: str | None) -> list[str]:
    return list(FORM_UNIT_OPTIONS.get((form or "", route), FORM_UNIT_OPTIONS.get((form or "", None), [])))


def _source_allowed(source: dict[str, Any]) -> None:
    required = ("slug", "name", "operator", "tier", "usage_scope", "reuse_status", "version")
    missing = [key for key in required if source.get(key) in (None, "")]
    if missing:
        raise ValueError(f"source metadata missing: {', '.join(missing)}")
    try:
        tier = int(source["tier"])
    except (TypeError, ValueError) as exc:
        raise ValueError("source tier must be 1, 2, or 3") from exc
    if tier not in (1, 2, 3):
        raise ValueError("source tier must be 1, 2, or 3")
    scope = source["usage_scope"]
    if scope not in ("production", "demo", "reference"):
        raise ValueError("source usage_scope must be production, demo, or reference")
    reuse = str(source["reuse_status"]).lower()
    approved = reuse == "approved" or reuse.startswith("approved_") or reuse.endswith("_approved")
    if scope == "production":
        if tier == 3:
            raise ValueError("Tier 3 source can never enter the production catalog")
        if not approved:
            raise ValueError("production source requires an approved reuse/licence status")
        if source.get("legal_review_required"):
            raise ValueError("production source is still marked as requiring legal review")
        required_provenance = {
            "license_name": source.get("license_name"),
            "license_url": source.get("license_url"),
            "attribution_text": source.get("attribution_text"),
            "source_url/input_uri": source.get("source_url") or source.get("input_uri"),
        }
        missing_provenance = [key for key, value in required_provenance.items() if not value]
        if missing_provenance:
            raise ValueError(
                "production source provenance missing: " + ", ".join(missing_provenance)
            )
        accessed_at = date.fromisoformat(_require_iso_date(source, "accessed_at"))
        date_fields = ("published_at", "source_updated_at", "declared_as_of")
        populated_dates = [field for field in date_fields if source.get(field) not in (None, "")]
        if not populated_dates:
            raise ValueError(
                "production source requires published_at, source_updated_at, or declared_as_of date"
            )
        for field in populated_dates:
            source_date = date.fromisoformat(_require_iso_date(source, field))
            if source_date > accessed_at:
                raise ValueError(f"production source {field} cannot be after accessed_at")
    if scope == "reference":
        raise ValueError("reference-only source cannot be imported into the searchable catalog")
    snapshot_mode = str(source.get("snapshot_mode") or "delta").lower()
    if snapshot_mode not in ("delta", "full"):
        raise ValueError("source snapshot_mode must be delta or full")
    package_schema_version = str(
        source.get("package_schema_version") or PACKAGE_SCHEMA_VERSION
    ).strip()
    if not package_schema_version:
        raise ValueError("source package_schema_version must not be empty")


def _normalize_aliases(raw_aliases: Any) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    for raw in raw_aliases or []:
        if isinstance(raw, str):
            value, language, alias_type, review = raw, "", "brand", "unverified"
        elif isinstance(raw, dict):
            value = raw.get("value") or raw.get("alias") or raw.get("name")
            language = str(raw.get("language") or raw.get("lang") or "")
            alias_type = str(raw.get("alias_type") or raw.get("type") or "brand")
            review = str(raw.get("review_status") or "unverified")
        else:
            continue
        if not value or not normalize_identity(value):
            continue
        aliases.append({
            "raw": str(value).strip(),
            "norm": normalize_identity(value),
            "search": normalize_search(value),
            "language": language,
            "alias_type": alias_type,
            "review_status": review if review in ("verified", "unverified", "needs_review") else "unverified",
        })
    return aliases


def _normalize_ingredients(record: dict[str, Any], generic: str | None) -> list[dict[str, Any]]:
    ingredients: list[dict[str, Any]] = []
    raw_items = record.get("ingredients") or []
    for index, raw in enumerate(raw_items, 1):
        if isinstance(raw, str):
            name, strength, ordinal, basis = raw, None, index, None
        elif isinstance(raw, dict):
            name = raw.get("name") or raw.get("ingredient")
            strength = raw.get("strength") or raw.get("strength_raw")
            ordinal = raw.get("ordinal") or index
            basis = raw.get("basis") or raw.get("basis_raw")
        else:
            continue
        if not name:
            continue
        strength_raw = str(strength).strip() if strength not in (None, "") else None
        strength_value, strength_unit = _simple_strength(strength_raw)
        ingredients.append({
            "name_raw": str(name).strip(),
            "name_norm": normalize_identity(name),
            "name_search": normalize_search(name),
            "strength_raw": strength_raw,
            "strength_value": strength_value,
            "strength_unit": strength_unit,
            "ordinal": int(ordinal),
            "basis_raw": str(basis).strip() if basis not in (None, "") else None,
        })
    if not ingredients and generic:
        # 명시적인 '+' 구분만 성분 순서로 보존한다. '/' 강도는 성분에 배정하지 않는다.
        names = [part.strip() for part in re.split(r"\s+\+\s+", generic) if part.strip()]
        ingredients = [{
            "name_raw": name,
            "name_norm": normalize_identity(name),
            "name_search": normalize_search(name),
            "strength_raw": None,
            "strength_value": None,
            "strength_unit": None,
            "ordinal": index,
            "basis_raw": None,
        } for index, name in enumerate(names, 1)]
    return ingredients


def _normalize_record(source: dict[str, Any], record: dict[str, Any], index: int) -> dict[str, Any]:
    raw_hash = _sha(record)
    supplied_source_record_id = record.get("source_record_id")
    if supplied_source_record_id in (None, ""):
        supplied_source_record_id = record.get("id")
    source_record_id = supplied_source_record_id
    if source_record_id in (None, ""):
        source_record_id = f"missing-{index:06d}-{raw_hash[:12]}"
    name_type = str(record.get("name_type") or "brand").lower()
    if name_type not in ("brand", "generic"):
        name_type = "brand"
    brand = record.get("brand_name") or record.get("display_name")
    generic = record.get("generic_name")
    if not brand and name_type == "generic":
        brand = generic
    brand = str(brand).strip() if brand not in (None, "") else None
    generic = str(generic).strip() if generic not in (None, "") else None
    if name_type == "generic" and not generic:
        generic = brand
    strength = record.get("strength") or record.get("strength_raw")
    strength = str(strength).strip() if strength not in (None, "") else None
    form_raw_value = record.get("form") or record.get("dosage_form")
    form_raw = str(form_raw_value).strip() if form_raw_value not in (None, "") else None
    form = _form_code(form_raw)
    route_raw_value = record.get("route")
    route_raw = str(route_raw_value).strip() if route_raw_value not in (None, "") else None
    route = _route_code(route_raw)
    release_raw, release = _release_code(record.get("release_modifier"), brand, form_raw)
    ingredients = _normalize_ingredients(record, generic)
    aliases = _normalize_aliases(record.get("aliases"))
    manufacturer = record.get("manufacturer") or record.get("manufacturer_name")
    marketer = record.get("marketer") or record.get("marketing_company")
    manufacturer = str(manufacturer).strip() if manufacturer not in (None, "") else None
    marketer = str(marketer).strip() if marketer not in (None, "") else None
    warnings: list[str] = []
    incomplete: list[str] = []
    if not brand:
        incomplete.append("brand_name")
    if not generic:
        incomplete.append("generic_name")
    if not strength:
        incomplete.append("strength")
    if not form_raw:
        incomplete.append("dosage_form")
    elif form is None:
        warnings.append("unknown_dosage_form")
    if route_raw and route is None:
        warnings.append("unknown_route")
    if record.get("release_modifier") and release is None:
        warnings.append("unknown_release_modifier")
    if form == "drops" and route is None:
        warnings.append("drops_route_missing")
    if len(ingredients) > 1 and strength and not all(item.get("strength_raw") for item in ingredients):
        warnings.append("combination_strength_mapping_unconfirmed")
    if supplied_source_record_id in (None, ""):
        warnings.append("source_record_id_missing")
    review = str(record.get("review_status") or "").lower()
    if review not in ("verified", "unverified", "needs_review"):
        review = "needs_review" if source["usage_scope"] == "demo" else "unverified"
    if incomplete or warnings:
        review = "needs_review"
    lifecycle = "inactive" if str(record.get("status") or "active").lower() == "inactive" else "active"
    fingerprint = "|".join((
        normalize_identity(brand), normalize_identity(generic), normalize_search(strength),
        form or normalize_search(form_raw), route or normalize_search(route_raw), release or "",
        normalize_identity(manufacturer), normalize_identity(marketer),
    ))
    excluded = not bool(brand)
    quarantined = False
    if source["usage_scope"] == "production" and supplied_source_record_id in (None, ""):
        excluded = True
        quarantined = True
        warnings.append("source_record_id_missing_production")
        review = "needs_review"
    return {
        "record_ordinal": index,
        "source_record_id": str(source_record_id),
        "raw": record,
        "raw_sha256": raw_hash,
        "excluded": excluded,
        "quarantined": quarantined,
        "quarantine_stage": "normalization" if quarantined else None,
        "name_type": name_type,
        "brand_name_raw": brand,
        "brand_name_norm": normalize_identity(brand),
        "brand_name_search": normalize_search(brand),
        "generic_name_raw": generic,
        "generic_name_norm": normalize_identity(generic),
        "generic_name_search": normalize_search(generic),
        "strength_raw": strength,
        "strength_search": normalize_search(strength),
        "dosage_form_raw": form_raw,
        "dosage_form_code": form,
        "route_raw": route_raw,
        "route_code": route,
        "release_modifier_raw": release_raw,
        "release_modifier_code": release,
        "manufacturer_name": manufacturer,
        "marketer_name": marketer,
        "rx_classification": record.get("rx_classification") or record.get("rx_class"),
        "short_display_name": record.get("short_display_name") or brand,
        "package": record.get("package"),
        "usage_scope": source["usage_scope"],
        "lifecycle_status": lifecycle,
        "review_status": review,
        "dedupe_fingerprint": fingerprint,
        "incomplete_fields": incomplete,
        "warnings": warnings,
        "ingredients": ingredients,
        "aliases": aliases,
        "caution_keys": list(record.get("caution_keys") or []),
    }


def _quarantined_record(source: dict[str, Any], raw: Any, index: int, error: Exception) -> dict[str, Any]:
    safe_raw = _json_safe_record(raw)
    raw_hash = _sha(safe_raw)
    source_record_id = None
    if isinstance(raw, dict):
        source_record_id = raw.get("source_record_id")
        if source_record_id in (None, ""):
            source_record_id = raw.get("id")
    if source_record_id in (None, ""):
        source_record_id = f"malformed-{index:06d}-{raw_hash[:12]}"
    source_record_id = str(source_record_id)
    reason = f"malformed_record:{type(error).__name__}:{error}"
    return {
        "record_ordinal": index,
        "source_record_id": source_record_id,
        "raw": safe_raw,
        "raw_sha256": raw_hash,
        "excluded": True,
        "quarantined": True,
        "quarantine_stage": "normalization",
        "name_type": "brand",
        "brand_name_raw": None,
        "brand_name_norm": "",
        "brand_name_search": "",
        "generic_name_raw": None,
        "generic_name_norm": "",
        "generic_name_search": "",
        "strength_raw": None,
        "strength_search": "",
        "dosage_form_raw": None,
        "dosage_form_code": None,
        "route_raw": None,
        "route_code": None,
        "release_modifier_raw": None,
        "release_modifier_code": None,
        "manufacturer_name": None,
        "marketer_name": None,
        "rx_classification": None,
        "short_display_name": None,
        "package": None,
        "usage_scope": source["usage_scope"],
        "lifecycle_status": "inactive",
        "review_status": "needs_review",
        "dedupe_fingerprint": f"quarantined|{raw_hash}",
        "incomplete_fields": ["malformed_record"],
        "warnings": [reason],
        "ingredients": [],
        "aliases": [],
        "caution_keys": [],
    }


def _quality_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    imported = [record for record in records if not record["excluded"]]
    fingerprints = Counter(record["dedupe_fingerprint"] for record in imported)
    duplicate_candidates = sum(count - 1 for count in fingerprints.values() if count > 1)
    missing = lambda field: sum(not bool(record.get(field)) for record in imported)
    unknown_units = sum(
        bool(record.get("strength_raw")) and not _KNOWN_STRENGTH_UNIT_RE.search(record["strength_raw"])
        for record in imported
    )
    return {
        "raw_total": len(records),
        "imported": len(imported),
        "excluded": len(records) - len(imported),
        "quarantined": sum(bool(record.get("quarantined")) for record in records),
        "needs_review": sum(record["review_status"] == "needs_review" for record in imported),
        "duplicate_candidates": duplicate_candidates,
        "missing_brand_name": sum(record["excluded"] for record in records),
        "missing_generic_name": missing("generic_name_raw"),
        "missing_strength": missing("strength_raw"),
        "missing_dosage_form": missing("dosage_form_raw"),
        "missing_manufacturer": missing("manufacturer_name"),
        "missing_source": 0,
        "unknown_strength_unit": unknown_units,
        "unknown_dosage_form": sum(
            bool(record.get("dosage_form_raw")) and not bool(record.get("dosage_form_code"))
            for record in imported
        ),
        "failed_records": [
            {
                "record_ordinal": record.get("record_ordinal"),
                "source_record_id": record["source_record_id"],
                "quarantined": bool(record.get("quarantined")),
                "stage": record.get("quarantine_stage"),
                "errors": record["incomplete_fields"] + record["warnings"],
            }
            for record in records if record["excluded"]
        ],
        "quarantined_records": [
            {
                "record_ordinal": record.get("record_ordinal"),
                "source_record_id": record["source_record_id"],
                "stage": record.get("quarantine_stage"),
                "errors": record["incomplete_fields"] + record["warnings"],
            }
            for record in records if record.get("quarantined")
        ],
    }


def _upsert_source(conn: sqlite3.Connection, source: dict[str, Any]) -> str:
    now = _now()
    row = conn.execute("SELECT * FROM drug_sources WHERE slug=?", (source["slug"],)).fetchone()
    source_id = row["id"] if row else _id()
    values = (
        source.get("name"), source.get("operator"), int(source["tier"]), source["usage_scope"],
        source["reuse_status"], source.get("license_name"), source.get("license_url"),
        source.get("attribution_text"), source.get("source_url") or source.get("input_uri"),
        int(bool(source.get("legal_review_required"))), now, source_id,
    )
    if row:
        expected = {
            "name": source.get("name"),
            "operator": source.get("operator"),
            "tier": int(source["tier"]),
            "usage_scope": source["usage_scope"],
            "reuse_status": source["reuse_status"],
            "license_name": source.get("license_name"),
            "license_url": source.get("license_url"),
            "attribution_text": source.get("attribution_text"),
            "source_url": source.get("source_url") or source.get("input_uri"),
            "legal_review_required": int(bool(source.get("legal_review_required"))),
        }
        changed = [key for key, value in expected.items() if row[key] != value]
        if changed:
            raise ValueError(
                f"source metadata changed for existing slug {source['slug']!r}: "
                f"{', '.join(changed)}; register a new versioned source slug"
            )
        conn.execute("UPDATE drug_sources SET updated_at=? WHERE id=?", (now, source_id))
    else:
        conn.execute(
            """INSERT INTO drug_sources
               (name, operator, tier, usage_scope, reuse_status, license_name, license_url,
                attribution_text, source_url, legal_review_required, updated_at, id, created_at, slug)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values + (now, source["slug"]),
        )
    return source_id


def _presentation_id(conn: sqlite3.Connection, source: dict[str, Any], source_id: str,
                     record: dict[str, Any]) -> tuple[str, bool]:
    row = conn.execute(
        "SELECT id FROM drug_presentations WHERE source_id=? AND source_record_id=?",
        (source_id, record["source_record_id"]),
    ).fetchone()
    if row:
        return row["id"], False
    if source["slug"] in ("legacy-demo-seed", "legacy-db-import"):
        legacy = conn.execute(
            """SELECT id FROM drugs WHERE brand_name=? AND IFNULL(strength,'')=?
               AND IFNULL(form,'')=? ORDER BY created_at LIMIT 1""",
            (record["brand_name_raw"], record.get("strength_raw") or "",
             record.get("dosage_form_code") or record.get("dosage_form_raw") or ""),
        ).fetchone()
        if legacy:
            return legacy["id"], True
    return _id(), True


def _existing_presentation(
    conn: sqlite3.Connection, source_id: str, source_record_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM drug_presentations WHERE source_id=? AND source_record_id=?",
        (source_id, source_record_id),
    ).fetchone()


def _write_relations(conn: sqlite3.Connection, presentation_id: str,
                     source_record_id: str, record: dict[str, Any]) -> None:
    now = _now()
    conn.execute("DELETE FROM drug_presentation_ingredients WHERE presentation_id=?", (presentation_id,))
    for item in sorted(record["ingredients"], key=lambda value: value["ordinal"]):
        ingredient = conn.execute(
            "SELECT id FROM drug_ingredients WHERE name_norm=?", (item["name_norm"],)
        ).fetchone()
        ingredient_id = ingredient["id"] if ingredient else _id()
        if ingredient:
            conn.execute(
                "UPDATE drug_ingredients SET name_raw=?, name_search=?, updated_at=? WHERE id=?",
                (item["name_raw"], item["name_search"], now, ingredient_id),
            )
        else:
            conn.execute(
                """INSERT INTO drug_ingredients
                   (id, name_raw, name_norm, name_search, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ingredient_id, item["name_raw"], item["name_norm"], item["name_search"], now, now),
            )
        conn.execute(
            """INSERT INTO drug_presentation_ingredients
               (presentation_id, ingredient_id, ordinal, strength_raw, strength_value,
                strength_unit, basis_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                presentation_id,
                ingredient_id,
                item["ordinal"],
                item.get("strength_raw"),
                item.get("strength_value"),
                item.get("strength_unit"),
                item.get("basis_raw"),
            ),
        )

    conn.execute("DELETE FROM drug_aliases WHERE presentation_id=?", (presentation_id,))
    for alias in record["aliases"]:
        conn.execute(
            """INSERT OR IGNORE INTO drug_aliases
               (id, presentation_id, alias_raw, alias_norm, alias_search, alias_type,
                language, script, review_status, source_record_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_id(), presentation_id, alias["raw"], alias["norm"], alias["search"],
             alias["alias_type"], alias["language"], None, alias["review_status"],
             source_record_id, now),
        )

    conn.execute("DELETE FROM drug_packages WHERE presentation_id=?", (presentation_id,))
    package = record.get("package")
    if package:
        if isinstance(package, str):
            description, quantity, unit, form = package, None, None, None
        else:
            description = package.get("description")
            quantity = package.get("quantity") or package.get("quantity_value")
            unit = package.get("unit") or package.get("quantity_unit")
            form = package.get("form") or package.get("package_form")
        conn.execute(
            """INSERT INTO drug_packages
               (id, presentation_id, description, quantity_value, quantity_unit,
                package_form, source_record_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (_id(), presentation_id, description, quantity, unit, form, source_record_id, now),
        )


def _write_presentation(
    conn: sqlite3.Connection,
    source: dict[str, Any],
    source_id: str,
    record: dict[str, Any],
    import_run_id: str,
) -> tuple[str, dict[str, Any]]:
    presentation_id, is_new = _presentation_id(conn, source, source_id, record)
    previous = None if is_new else conn.execute(
        "SELECT * FROM drug_presentations WHERE id=?", (presentation_id,)
    ).fetchone()
    projection = _normalized_projection(record)
    projection_json = _json(projection)
    projection_sha256 = _sha(projection)
    previous_projection: dict[str, Any] | None = None
    previous_projection_sha256 = None
    if previous is not None:
        previous_projection_sha256 = previous["normalized_projection_sha256"]
        try:
            parsed = json.loads(previous["normalized_projection_json"] or "null")
            previous_projection = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            previous_projection = None
    normalized_changed = is_new or previous_projection_sha256 != projection_sha256
    normalized_diff = (
        _normalized_projection_diff(previous_projection, projection)
        if normalized_changed
        else {}
    )
    now = _now()
    values = (
        source_id, record["source_record_id"], record["name_type"], record["brand_name_raw"],
        record["brand_name_norm"], record["brand_name_search"], record["generic_name_raw"],
        record["generic_name_norm"], record["generic_name_search"], record["strength_raw"],
        record["strength_search"], record["dosage_form_raw"], record["dosage_form_code"],
        record["route_raw"], record["route_code"], record["release_modifier_raw"],
        record["release_modifier_code"], record["manufacturer_name"], record["marketer_name"],
        record["rx_classification"], record["short_display_name"],
        _json(record.get("package")) if record.get("package") else None,
        record["usage_scope"], record["lifecycle_status"], record["review_status"],
        record["dedupe_fingerprint"], _json(record["incomplete_fields"]),
        _json(record["warnings"]), source.get("source_updated_at") or source.get("declared_as_of"), now,
    )
    if is_new:
        conn.execute(
            """INSERT INTO drug_presentations
               (source_id, source_record_id, name_type, brand_name_raw, brand_name_norm,
                brand_name_search, generic_name_raw, generic_name_norm, generic_name_search,
                strength_raw, strength_search, dosage_form_raw, dosage_form_code, route_raw,
                route_code, release_modifier_raw, release_modifier_code, manufacturer_name,
                marketer_name, rx_classification, short_display_name, package_summary,
                usage_scope, lifecycle_status, review_status, dedupe_fingerprint,
                incomplete_fields_json, warnings_json, source_updated_at, updated_at,
                workflow_review_status, operational_lifecycle_status, record_version,
                normalized_projection_json, normalized_projection_sha256, id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values + (
                "needs_review" if record["review_status"] == "needs_review" else "unverified",
                record["lifecycle_status"],
                1,
                projection_json,
                projection_sha256,
                presentation_id,
                now,
            ),
        )
    elif normalized_changed:
        conn.execute(
            """UPDATE drug_presentations SET
               source_id=?, source_record_id=?, name_type=?, brand_name_raw=?, brand_name_norm=?,
               brand_name_search=?, generic_name_raw=?, generic_name_norm=?, generic_name_search=?,
               strength_raw=?, strength_search=?, dosage_form_raw=?, dosage_form_code=?, route_raw=?,
               route_code=?, release_modifier_raw=?, release_modifier_code=?, manufacturer_name=?,
               marketer_name=?, rx_classification=?, short_display_name=?, package_summary=?,
               usage_scope=?, lifecycle_status=?, review_status=?, dedupe_fingerprint=?,
               incomplete_fields_json=?, warnings_json=?, source_updated_at=?, updated_at=?,
               normalized_projection_json=?, normalized_projection_sha256=?
               WHERE id=?""",
            values + (projection_json, projection_sha256, presentation_id),
        )
    else:
        conn.execute(
            "UPDATE drug_presentations SET source_updated_at=? WHERE id=?",
            (
                source.get("source_updated_at") or source.get("declared_as_of"),
                presentation_id,
            ),
        )
    previous_workflow_review = (
        previous["workflow_review_status"] if previous is not None else None
    )
    previous_operational_lifecycle = (
        previous["operational_lifecycle_status"] if previous is not None else None
    )
    if previous is not None:
        from app.catalog_governance import record_source_refresh

        next_review, _ = record_source_refresh(
            conn,
            previous,
            import_run_id=import_run_id,
            source_record_sha256=record["raw_sha256"],
            normalized_projection_changed=normalized_changed,
        )
    else:
        next_review = (
            "needs_review" if record["review_status"] == "needs_review" else "unverified"
        )
    if is_new or normalized_changed:
        _write_relations(conn, presentation_id, record["source_record_id"], record)

    aliases = [alias["raw"] for alias in record["aliases"]]
    cautions = record.get("caution_keys") or []
    governance = conn.execute(
        "SELECT workflow_review_status FROM drug_presentations WHERE id=?", (presentation_id,)
    ).fetchone()
    approved = int(bool(governance) and governance["workflow_review_status"] == "approved")
    legacy = conn.execute("SELECT id FROM drugs WHERE id=?", (presentation_id,)).fetchone()
    if legacy:
        conn.execute(
            """UPDATE drugs SET brand_name=?, generic_name=?, strength=?, form=?,
               default_pattern_key=NULL, default_timing_food=NULL, default_dose_unit=NULL,
               aliases_json=?, caution_keys_json=?, source=?, verified=? WHERE id=?""",
            (record["brand_name_raw"], record["generic_name_raw"], record["strength_raw"],
             record["dosage_form_code"] or record["dosage_form_raw"], _json(aliases), _json(cautions),
             source["slug"], approved, presentation_id),
        )
    else:
        conn.execute(
            """INSERT INTO drugs
               (id, brand_name, generic_name, strength, form, default_pattern_key,
                default_timing_food, default_dose_unit, aliases_json, caution_keys_json,
                source, verified, created_at)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)""",
            (presentation_id, record["brand_name_raw"], record["generic_name_raw"],
             record["strength_raw"], record["dosage_form_code"] or record["dosage_form_raw"],
             _json(aliases), _json(cautions), source["slug"],
             approved, now),
        )
    return presentation_id, {
        "change_type": "created" if is_new else "changed" if normalized_changed else "unchanged",
        "normalized_projection_json": projection_json,
        "normalized_projection_sha256": projection_sha256,
        "previous_normalized_projection_sha256": previous_projection_sha256,
        "normalized_diff": normalized_diff,
        "changed_fields": sorted(normalized_diff),
        "previous_workflow_review_status": previous_workflow_review,
        "next_workflow_review_status": next_review,
        "previous_operational_lifecycle_status": previous_operational_lifecycle,
        "next_operational_lifecycle_status": record["lifecycle_status"]
        if is_new
        else previous_operational_lifecycle,
        "review_required": next_review in ("unverified", "needs_review"),
        "review_reopened": bool(
            normalized_changed
            and previous_workflow_review in ("approved", "rejected")
            and next_review == "needs_review"
        ),
    }


def _source_record_row(
    conn: sqlite3.Connection,
    source_id: str,
    run_id: str,
    item: dict[str, Any],
    status: str,
    presentation_id: str | None,
) -> str:
    existing = conn.execute(
        """SELECT id FROM drug_source_records
           WHERE source_id=? AND source_record_id=? AND raw_sha256=?""",
        (source_id, item["source_record_id"], item["raw_sha256"]),
    ).fetchone()
    if existing:
        return existing["id"]
    row_id = _id()
    conn.execute(
        """INSERT INTO drug_source_records
           (id, source_id, import_run_id, source_record_id, raw_sha256, raw_json,
            ingest_status, errors_json, presentation_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id,
            source_id,
            run_id,
            item["source_record_id"],
            item["raw_sha256"],
            _json(item["raw"]),
            "excluded" if status == "quarantined" else status,
            _json(item["incomplete_fields"] + item["warnings"]),
            presentation_id,
            _now(),
        ),
    )
    return row_id


def _link_run_record(
    conn: sqlite3.Connection,
    run_id: str,
    raw_row_id: str,
    item: dict[str, Any],
    status: str,
    presentation_id: str | None,
    processing: dict[str, Any] | None = None,
) -> str:
    processing = processing or {}
    run_record_id = _id()
    conn.execute(
        """INSERT INTO drug_import_run_records
           (id, import_run_id, record_ordinal, source_record_row_id, source_record_id,
            ingest_status, errors_json, presentation_id,
            normalized_projection_json, normalized_projection_sha256,
            previous_normalized_projection_sha256, normalized_diff_json,
            review_required, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_record_id,
            run_id,
            item["record_ordinal"],
            raw_row_id,
            item["source_record_id"],
            status,
            _json(item["incomplete_fields"] + item["warnings"]),
            presentation_id,
            processing.get("normalized_projection_json"),
            processing.get("normalized_projection_sha256"),
            processing.get("previous_normalized_projection_sha256"),
            _json(processing.get("normalized_diff") or {}),
            int(bool(processing.get("review_required"))),
            _now(),
        ),
    )
    return run_record_id


def _accumulate_processing_report(
    report: dict[str, Any],
    *,
    source_record_id: str,
    presentation_id: str | None,
    processing: dict[str, Any],
) -> None:
    change_type = processing.get("change_type") or "ingest_issue"
    count_field = {
        "created": "normalization_created_count",
        "changed": "normalization_changed_count",
        "unchanged": "normalization_unchanged_count",
        "ingest_issue": "source_ingest_issue_count",
    }[change_type]
    report[count_field] += 1
    if processing.get("review_required"):
        report["review_required_count"] += 1
    if processing.get("review_reopened"):
        report["review_reopened_count"] += 1
    if change_type in ("created", "changed"):
        report["normalization_changes"].append(
            {
                "source_record_id": source_record_id,
                "presentation_id": presentation_id,
                "change_type": change_type,
                "previous_normalized_projection_sha256": processing.get(
                    "previous_normalized_projection_sha256"
                ),
                "normalized_projection_sha256": processing.get(
                    "normalized_projection_sha256"
                ),
                "changed_fields": processing.get("changed_fields") or [],
                "normalized_diff": processing.get("normalized_diff") or {},
                "review_required": bool(processing.get("review_required")),
                "review_reopened": bool(processing.get("review_reopened")),
            }
        )


def _ensure_database_mode(conn: sqlite3.Connection, expected_mode: str) -> None:
    """Persist the DB's catalog role and reject production/demo cross-contamination."""
    if expected_mode not in ("production", "demo"):
        raise ValueError("database_mode must be production or demo")
    row = conn.execute(
        "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
    ).fetchone()
    if row:
        if row["catalog_mode"] != expected_mode:
            raise ValueError(
                f"catalog database mode is {row['catalog_mode']}; "
                f"cannot import as {expected_mode}"
            )
        conn.execute(
            "UPDATE catalog_database_meta SET updated_at=? WHERE id=1", (_now(),)
        )
        return

    existing_demo = conn.execute(
        "SELECT 1 FROM drug_sources WHERE usage_scope='demo' LIMIT 1"
    ).fetchone()
    if existing_demo and expected_mode != "demo":
        raise ValueError("database already contains demo sources; production mode refused")
    now = _now()
    conn.execute(
        """INSERT INTO catalog_database_meta
           (id, catalog_mode, created_at, updated_at) VALUES (1, ?, ?, ?)""",
        (expected_mode, now, now),
    )


def import_catalog(
    conn: sqlite3.Connection,
    source: dict[str, Any],
    records: Iterable[Any],
    dry_run: bool = False,
    *,
    approval_registry: dict[str, Any] | None = None,
    database_mode: str | None = None,
) -> dict[str, Any]:
    """출처 패키지를 검증·정규화·임포트한다.

    같은 source/version/input hash 재실행은 동일 report를 반환한다. 같은 source record가
    수정되면 source record 원본 버전은 추가하고 presentation ID는 유지한다.
    """
    _source_allowed(source)
    expected_database_mode = database_mode or source["usage_scope"]
    if source["usage_scope"] == "demo" and expected_database_mode != "demo":
        raise ValueError("demo source requires a demo catalog database")
    raw_records = list(records)
    safe_raw_records = [_json_safe_record(record) for record in raw_records]
    snapshot_mode = str(source.get("snapshot_mode") or "delta").lower()
    package_schema_version = str(
        source.get("package_schema_version") or PACKAGE_SCHEMA_VERSION
    )
    code_revision = source.get("code_revision") or os.environ.get("INDORO_CODE_REVISION")
    approval = None
    if source["usage_scope"] == "production":
        approval = _production_approval(source, raw_records, approval_registry)

    normalized: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for index, raw in enumerate(raw_records, 1):
        try:
            if not isinstance(raw, dict):
                raise TypeError("catalog record must be an object")
            item = _normalize_record(source, dict(raw), index)
        except Exception as exc:
            item = _quarantined_record(source, raw, index, exc)
        if item["source_record_id"] in seen_source_ids:
            item["excluded"] = True
            item["quarantined"] = True
            item["quarantine_stage"] = "normalization"
            item["review_status"] = "needs_review"
            item["warnings"].append("duplicate_source_record_id_in_package")
        seen_source_ids.add(item["source_record_id"])
        normalized.append(item)

    package_content_hash = _sha({"source": source, "records": safe_raw_records})
    processing_hash = _sha(
        {
            "package_content_sha256": package_content_hash,
            "importer_version": IMPORTER_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "package_schema_version": package_schema_version,
            "snapshot_mode": snapshot_mode,
        }
    )
    report = _quality_from_records(normalized)
    report.update({
        "source_slug": source["slug"], "source_version": str(source["version"]),
        "usage_scope": source["usage_scope"], "dry_run": bool(dry_run),
        "source_accessed_at": source.get("accessed_at"),
        "source_updated_at": source.get("source_updated_at") or source.get("declared_as_of"),
        "source_input_uri": source.get("input_uri") or source.get("source_url"),
        "reuse_status": source.get("reuse_status"),
        "license_name": source.get("license_name"),
        "license_url": source.get("license_url"),
        "source_artifact_manifest": source.get("source_artifact_manifest"),
        "source_artifact_sha256": source.get("source_artifact_sha256"),
        "source_artifact_bytes": source.get("source_artifact_bytes"),
        "transformation_method": source.get("transformation_method"),
        # input_sha256 remains the database replay key for backward compatibility.
        # package_content_sha256 is the pure source+record content hash.
        "input_sha256": processing_hash,
        "package_content_sha256": package_content_hash,
        "records_sha256": records_sha256(safe_raw_records),
        "approval_registry_sha256": approval["registry_sha256"] if approval else None,
        "importer_version": IMPORTER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "package_schema_version": package_schema_version,
        "snapshot_mode": snapshot_mode,
        "code_revision": code_revision,
        "retirement_baseline_missing": False,
        "retirement_baseline_import_run_id": None,
        "retirement_batch_id": None,
        "retirement_candidate_count": 0,
        "normalization_created_count": 0,
        "normalization_changed_count": 0,
        "normalization_unchanged_count": 0,
        "source_ingest_issue_count": 0,
        "review_required_count": 0,
        "review_reopened_count": 0,
        "normalization_changes": [],
        "normalization_comparison_available": False,
    })
    if dry_run:
        return report

    input_hash = report["input_sha256"]
    try:
        conn.execute("BEGIN")
        _ensure_database_mode(conn, expected_database_mode)
        source_id = _upsert_source(conn, source)
        previous = conn.execute(
            """SELECT report_json FROM drug_import_runs
               WHERE source_id=? AND version=? AND input_sha256=? AND mode='apply'""",
            (source_id, str(source["version"]), input_hash),
        ).fetchone()
        if previous:
            conn.commit()
            # 오래된 저장 보고서에 새 provenance/품질 필드가 없더라도 현재
            # importer가 계산한 필드를 보충한다. 기존 run의 수치는 정본으로 유지한다.
            replay = {**report, **json.loads(previous["report_json"])}
            replay["replayed"] = True
            return replay

        run_id = _id()
        report["normalization_comparison_available"] = True
        started = _now()
        license_snapshot = {
            "reuse_status": source.get("reuse_status"),
            "license_name": source.get("license_name"),
            "license_url": source.get("license_url"),
            "attribution_text": source.get("attribution_text"),
            "legal_review_required": bool(source.get("legal_review_required")),
        }
        approval_snapshot = None
        if approval:
            approval_snapshot = {
                "registry_sha256": approval["registry_sha256"],
                "records_sha256": approval["records_sha256"],
                "source_metadata_sha256": approval["source_metadata_sha256"],
                "entry": approval["entry"],
            }
        conn.execute(
            """INSERT INTO drug_import_runs
               (id, source_id, version, published_at, source_updated_at, input_uri,
                input_sha256, records_sha256, accessed_at, source_snapshot_json,
                license_snapshot_json, approval_snapshot_json, approval_registry_sha256,
                importer_version, normalization_version, package_schema_version,
                snapshot_mode, package_content_sha256, code_revision,
                mode, status, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       'apply', 'completed', ?)""",
            (run_id, source_id, str(source["version"]), source.get("published_at"),
             source.get("source_updated_at") or source.get("declared_as_of"),
             source.get("input_uri") or source.get("source_url"),
             input_hash, report["records_sha256"], source.get("accessed_at"), _json(source),
             _json(license_snapshot), _json(approval_snapshot) if approval_snapshot else None,
             approval["registry_sha256"] if approval else None, IMPORTER_VERSION,
             NORMALIZATION_VERSION, package_schema_version, snapshot_mode,
             package_content_hash, code_revision, started),
        )

        for item in normalized:
            savepoint = f"catalog_record_{item['record_ordinal']}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                presentation_id = None
                processing: dict[str, Any] = {}
                status = "quarantined" if item.get("quarantined") else "excluded"
                if not item["excluded"]:
                    presentation_id, processing = _write_presentation(
                        conn, source, source_id, item, run_id
                    )
                    status = "needs_review" if item["review_status"] == "needs_review" else "imported"
                else:
                    previous = _existing_presentation(
                        conn, source_id, item["source_record_id"]
                    )
                    if previous is not None:
                        from app.catalog_governance import record_source_ingest_issue

                        presentation_id = previous["id"]
                        next_review, _ = record_source_ingest_issue(
                            conn,
                            previous,
                            import_run_id=run_id,
                            source_record_sha256=item["raw_sha256"],
                            ingest_status=status,
                        )
                        processing = {
                            "change_type": "ingest_issue",
                            "normalized_projection_json": previous[
                                "normalized_projection_json"
                            ],
                            "normalized_projection_sha256": previous[
                                "normalized_projection_sha256"
                            ],
                            "previous_normalized_projection_sha256": previous[
                                "normalized_projection_sha256"
                            ],
                            "normalized_diff": {
                                "source_ingest_status": {
                                    "previous": "valid_projection",
                                    "next": status,
                                }
                            },
                            "changed_fields": ["source_ingest_status"],
                            "review_required": next_review
                            in ("unverified", "needs_review"),
                            "review_reopened": previous["workflow_review_status"]
                            == "approved"
                            and next_review == "needs_review",
                        }
                raw_row_id = _source_record_row(
                    conn, source_id, run_id, item, status, presentation_id
                )
                run_record_id = _link_run_record(
                    conn,
                    run_id,
                    raw_row_id,
                    item,
                    status,
                    presentation_id,
                    processing,
                )
                if presentation_id and not item["excluded"]:
                    conn.execute(
                        """UPDATE drug_presentations
                           SET current_source_record_row_id=?,
                               current_import_run_record_id=?
                           WHERE id=?""",
                        (raw_row_id, run_record_id, presentation_id),
                    )
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                _accumulate_processing_report(
                    report,
                    source_record_id=item["source_record_id"],
                    presentation_id=presentation_id,
                    processing=processing,
                )
            except Exception as exc:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                item["excluded"] = True
                item["quarantined"] = True
                item["quarantine_stage"] = "persistence"
                item["review_status"] = "needs_review"
                item["warnings"].append(
                    f"record_persistence_failed:{type(exc).__name__}:{exc}"
                )
                evidence_savepoint = f"catalog_evidence_{item['record_ordinal']}"
                conn.execute(f"SAVEPOINT {evidence_savepoint}")
                previous = _existing_presentation(
                    conn, source_id, item["source_record_id"]
                )
                presentation_id = previous["id"] if previous is not None else None
                next_review = "needs_review"
                if previous is not None:
                    from app.catalog_governance import record_source_ingest_issue

                    next_review, _ = record_source_ingest_issue(
                        conn,
                        previous,
                        import_run_id=run_id,
                        source_record_sha256=item["raw_sha256"],
                        ingest_status="quarantined",
                    )
                processing = {
                    "change_type": "ingest_issue",
                    "normalized_projection_json": (
                        previous["normalized_projection_json"]
                        if previous is not None
                        else None
                    ),
                    "normalized_projection_sha256": (
                        previous["normalized_projection_sha256"]
                        if previous is not None
                        else None
                    ),
                    "previous_normalized_projection_sha256": (
                        previous["normalized_projection_sha256"]
                        if previous is not None
                        else None
                    ),
                    "normalized_diff": {
                        "source_ingest_status": {
                            "previous": "valid_projection" if previous is not None else None,
                            "next": "quarantined",
                        }
                    },
                    "changed_fields": ["source_ingest_status"],
                    "review_required": next_review in ("unverified", "needs_review"),
                    "review_reopened": bool(
                        previous is not None
                        and previous["workflow_review_status"] == "approved"
                        and next_review == "needs_review"
                    ),
                }
                raw_row_id = _source_record_row(
                    conn, source_id, run_id, item, "quarantined", presentation_id
                )
                _link_run_record(
                    conn,
                    run_id,
                    raw_row_id,
                    item,
                    "quarantined",
                    presentation_id,
                    processing,
                )
                conn.execute(f"RELEASE SAVEPOINT {evidence_savepoint}")
                _accumulate_processing_report(
                    report,
                    source_record_id=item["source_record_id"],
                    presentation_id=presentation_id,
                    processing=processing,
                )

        if snapshot_mode == "full":
            baseline = conn.execute(
                """SELECT id FROM drug_import_runs
                   WHERE source_id=? AND id<>? AND mode='apply' AND status='completed'
                     AND snapshot_mode='full'
                   ORDER BY completed_at DESC, rowid DESC LIMIT 1""",
                (source_id, run_id),
            ).fetchone()
            if baseline is None:
                report["retirement_baseline_missing"] = True
            else:
                report["retirement_baseline_import_run_id"] = baseline["id"]
                incoming_ids = {
                    item["source_record_id"]
                    for item in normalized
                    if "source_record_id_missing" not in item.get("warnings", [])
                }
                from app.catalog_governance import retirement_candidate_presentations

                missing = retirement_candidate_presentations(
                    conn, source_id, incoming_ids
                )
                if missing:
                    from app.catalog_governance import create_retirement_batch

                    batch_id = create_retirement_batch(
                        conn,
                        source_id=source_id,
                        import_run_id=run_id,
                        baseline_import_run_id=baseline["id"],
                        candidates=missing,
                    )
                    report["retirement_batch_id"] = batch_id
                    report["retirement_candidate_count"] = len(missing)

        final_quality = _quality_from_records(normalized)
        for key, value in final_quality.items():
            report[key] = value
        report["replayed"] = False
        completed = _now()
        conn.execute(
            """UPDATE drug_import_runs SET raw_total=?, imported=?, excluded=?, needs_review=?,
               duplicate_candidates=?, report_json=?, completed_at=? WHERE id=?""",
            (report["raw_total"], report["imported"], report["excluded"], report["needs_review"],
             report["duplicate_candidates"], _json(report), completed, run_id),
        )
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise


def _row_result(row: sqlite3.Row, aliases: list[str]) -> dict[str, Any]:
    data = dict(row)
    warnings = json.loads(data.get("warnings_json") or "[]")
    incomplete = json.loads(data.get("incomplete_fields_json") or "[]")
    workflow_review = data.get("workflow_review_status") or (
        "needs_review" if data.get("review_status") == "needs_review" else "unverified"
    )
    operational_lifecycle = data.get("operational_lifecycle_status") or data["lifecycle_status"]
    if workflow_review == "needs_review":
        warnings.append("Catalog record needs review; confirm every detail with the prescription.")
    elif workflow_review == "unverified":
        warnings.append("Catalog record is not yet pharmacist-verified.")
    elif workflow_review == "rejected":
        warnings.append("Catalog record was rejected in human review and cannot be newly selected.")
    if data["usage_scope"] == "demo":
        warnings.append("Demo catalog record; not an approved production source.")
    form = data.get("dosage_form_code") or data.get("dosage_form_raw")
    route = data.get("route_code") or data.get("route_raw")
    return {
        "id": data["id"],
        "brand_name": data["brand_name_raw"],
        "generic_name": data.get("generic_name_raw"),
        "strength": data.get("strength_raw"),
        "form": form,
        "route": route,
        "release_modifier": data.get("release_modifier_code") or data.get("release_modifier_raw"),
        "manufacturer": data.get("manufacturer_name"),
        "marketing_company": data.get("marketer_name"),
        "name_type": data.get("name_type"),
        "review_status": workflow_review,
        "lifecycle_status": operational_lifecycle,
        "source_review_status": data.get("review_status"),
        "source_lifecycle_status": data.get("lifecycle_status"),
        "record_version": data.get("record_version", 1),
        "usage_scope": data["usage_scope"],
        "aliases": aliases,
        "unit_options": _unit_options(data.get("dosage_form_code"), data.get("route_code")),
        "warnings": list(dict.fromkeys(warnings)),
        "incomplete_fields": incomplete,
        "verified": int(workflow_review == "approved"),
        "default_pattern_key": None,
        "default_timing_food": None,
        "default_dose_unit": None,
        "caution_keys": [],
    }


def search_catalog(conn: sqlite3.Connection, q: str, limit: int = 8,
                   include_demo: bool = False) -> list[dict[str, Any]]:
    """위험한 fuzzy 없이 exact/prefix/alias/ingredient/token/substring 순으로 검색."""
    query = normalize_search((q or "").strip())
    if len(query) < 2:
        return []
    limit = max(1, min(int(limit), 20))
    scopes = ("production", "demo") if include_demo else ("production",)
    placeholders = ",".join("?" for _ in scopes)
    rows = conn.execute(
        f"""SELECT * FROM drug_presentations
            WHERE operational_lifecycle_status='active'
              AND workflow_review_status<>'rejected'
              AND usage_scope IN ({placeholders})""",
        scopes,
    ).fetchall()
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    alias_map: dict[str, list[dict[str, str]]] = defaultdict(list)
    ingredient_map: dict[str, list[str]] = defaultdict(list)
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        qs = ",".join("?" for _ in batch)
        for alias in conn.execute(
            f"""SELECT presentation_id, alias_raw, alias_search, alias_type, language,
                       review_status
                FROM drug_aliases WHERE presentation_id IN ({qs})""",
            batch,
        ).fetchall():
            alias_map[alias["presentation_id"]].append(dict(alias))
        for ingredient in conn.execute(
            f"""SELECT pi.presentation_id, i.name_search
                FROM drug_presentation_ingredients pi
                JOIN drug_ingredients i ON i.id=pi.ingredient_id
                WHERE pi.presentation_id IN ({qs})
                ORDER BY pi.presentation_id, pi.ordinal""",
            batch,
        ).fetchall():
            ingredient_map[ingredient["presentation_id"]].append(ingredient["name_search"])

    matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in rows:
        brand = row["brand_name_search"] or ""
        generic = row["generic_name_search"] or ""
        aliases = alias_map.get(row["id"], [])
        alias_searches = [alias["alias_search"] for alias in aliases]
        ingredient_searches = ingredient_map.get(row["id"], [])
        combined = " ".join(filter(None, (
            brand, generic, row["strength_search"], normalize_search(row["dosage_form_raw"]),
            normalize_search(row["route_raw"]), normalize_search(row["manufacturer_name"]),
            normalize_search(row["marketer_name"]), *alias_searches, *ingredient_searches,
        )))
        rank: int | None = None
        if brand == query:
            rank = 0
        elif brand.startswith(query):
            rank = 1
        elif query in alias_searches:
            rank = 2
        elif any(alias.startswith(query) for alias in alias_searches):
            rank = 3
        elif generic == query or query in ingredient_searches:
            rank = 4
        elif generic.startswith(query) or any(
            ingredient.startswith(query) for ingredient in ingredient_searches
        ):
            rank = 5
        elif all(token in combined.split() for token in query.split()):
            rank = 6
        elif len(query) >= 3 and query in combined:
            rank = 7
        if rank is None:
            continue
        review_rank = {
            "approved": 0,
            "unverified": 1,
            "needs_review": 2,
            "rejected": 3,
        }[row["workflow_review_status"]]
        scope_rank = 0 if row["usage_scope"] == "production" else 1
        result = _row_result(row, [alias["alias_raw"] for alias in aliases])
        if rank in (2, 3):
            matched = next(
                (
                    alias for alias in aliases
                    if alias["alias_search"] == query
                ),
                next(
                    (
                        alias for alias in aliases
                        if alias["alias_search"].startswith(query)
                    ),
                    None,
                ),
            )
            if matched:
                result["matched_alias"] = {
                    "value": matched["alias_raw"],
                    "type": matched["alias_type"],
                    "language": matched["language"],
                    "review_status": matched["review_status"],
                }
                if matched["review_status"] != "verified":
                    result["warnings"].append(
                        "Matched alias is unverified; confirm the exact prescription spelling."
                    )
                    result["warnings"] = list(dict.fromkeys(result["warnings"]))
        matches.append(((rank, scope_rank, review_rank, brand, row["id"]), result))
    matches.sort(key=lambda item: item[0])
    return [result for _, result in matches[:limit]]


def get_presentation_snapshot(conn: sqlite3.Connection, presentation_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM drug_presentations WHERE id=?", (presentation_id,)).fetchone()
    if not row:
        return None
    aliases = [
        value["alias_raw"] for value in conn.execute(
            "SELECT alias_raw FROM drug_aliases WHERE presentation_id=? ORDER BY alias_raw",
            (presentation_id,),
        ).fetchall()
    ]
    snapshot = _row_result(row, aliases)
    snapshot["normalized_projection_sha256"] = row["normalized_projection_sha256"]
    snapshot["patient_display_generic"] = False
    snapshot["patient_caution_keys"] = []
    snapshot["snapshot_at"] = _now()
    provenance = conn.execute(
        """SELECT s.slug AS source_slug, s.name AS source_name,
                  sr.source_record_id, sr.raw_sha256,
                  ir.version AS source_version, ir.published_at, ir.source_updated_at,
                  ir.accessed_at, ir.input_uri, ir.input_sha256, ir.records_sha256,
                  ir.importer_version, ir.normalization_version,
                  ir.package_schema_version, ir.snapshot_mode, ir.code_revision,
                  ir.source_snapshot_json,
                  ir.license_snapshot_json, ir.approval_snapshot_json,
                  ir.approval_registry_sha256, ir.completed_at
           FROM drug_presentations p
           JOIN drug_sources s ON s.id=p.source_id
           LEFT JOIN drug_import_run_records irr
             ON irr.id=p.current_import_run_record_id
           LEFT JOIN drug_source_records sr
             ON sr.id=irr.source_record_row_id
           LEFT JOIN drug_import_runs ir ON ir.id=irr.import_run_id
           WHERE p.id=?
           LIMIT 1""",
        (presentation_id,),
    ).fetchone()
    if provenance:
        provenance_data = dict(provenance)
        for field in (
            "source_snapshot_json",
            "license_snapshot_json",
            "approval_snapshot_json",
        ):
            raw = provenance_data.pop(field, None)
            provenance_data[field.removesuffix("_json")] = json.loads(raw) if raw else None
        snapshot["provenance"] = provenance_data
    return snapshot


def _dangerous_similar_names(names: list[tuple[str, str]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    by_initial: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for presentation_id, name in names:
        norm = normalize_identity(name)
        if norm:
            by_initial[norm[0]].append((presentation_id, norm))
    for group in by_initial.values():
        for index, (left_id, left) in enumerate(group):
            for right_id, right in group[index + 1:]:
                if left == right or abs(len(left) - len(right)) > 3:
                    continue
                ratio = SequenceMatcher(None, left, right).ratio()
                if ratio >= 0.86:
                    candidates.append({"left_id": left_id, "left": left, "right_id": right_id,
                                       "right": right, "similarity": round(ratio, 3)})
                if len(candidates) >= 100:
                    return candidates
    return candidates


def build_quality_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """현재 canonical/source record 전체 품질을 JSON 직렬화 가능한 dict로 반환."""
    mode_row = conn.execute(
        "SELECT catalog_mode FROM catalog_database_meta WHERE id=1"
    ).fetchone()
    rows = conn.execute(
        """SELECT p.*, s.slug AS source_slug FROM drug_presentations p
           JOIN drug_sources s ON s.id=p.source_id"""
    ).fetchall()
    records = [dict(row) for row in rows]
    source_records = conn.execute(
        """SELECT source_record_id, raw_sha256, ingest_status, errors_json, import_run_id
           FROM drug_source_records"""
    ).fetchall()
    run_record_links = conn.execute(
        """SELECT import_run_id, record_ordinal, source_record_id, ingest_status,
                  errors_json, presentation_id, normalized_projection_sha256,
                  previous_normalized_projection_sha256, normalized_diff_json,
                  review_required
           FROM drug_import_run_records"""
    ).fetchall()
    fingerprint_counts = Counter(row["dedupe_fingerprint"] for row in records)
    brand_ingredients: dict[str, set[str]] = defaultdict(set)
    composition_brands: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in records:
        brand_ingredients[row["brand_name_norm"]].add(row.get("generic_name_norm") or "")
        composition_brands[(
            row.get("generic_name_norm") or "", row.get("strength_search") or "",
            row.get("dosage_form_code") or "", row.get("route_code") or "",
        )].add(row["brand_name_norm"])
    source_breakdown = [dict(row) for row in conn.execute(
        """SELECT s.slug, s.tier, s.usage_scope, COUNT(p.id) AS presentations
           FROM drug_sources s LEFT JOIN drug_presentations p ON p.source_id=s.id
           GROUP BY s.id ORDER BY s.slug"""
    ).fetchall()]
    report = {
        "generated_at": _now(),
        "database_mode": mode_row["catalog_mode"] if mode_row else None,
        "raw_total": len(source_records),
        "imported": len(records),
        "excluded": sum(row["ingest_status"] == "excluded" for row in source_records),
        "quarantined": sum(
            row["ingest_status"] == "quarantined" for row in run_record_links
        ),
        "import_run_record_links": len(run_record_links),
        "needs_review": sum(row["review_status"] == "needs_review" for row in records),
        "missing_normalized_projection": sum(
            not row.get("normalized_projection_sha256") for row in records
        ),
        "reprocessing_changed": sum(
            bool(row["previous_normalized_projection_sha256"])
            and row["normalized_projection_sha256"]
            != row["previous_normalized_projection_sha256"]
            for row in run_record_links
        ),
        "reprocessing_unchanged": sum(
            bool(row["previous_normalized_projection_sha256"])
            and row["normalized_projection_sha256"]
            == row["previous_normalized_projection_sha256"]
            for row in run_record_links
        ),
        "reprocessing_review_required": sum(
            bool(row["review_required"]) for row in run_record_links
        ),
        "duplicate_candidates": sum(count - 1 for count in fingerprint_counts.values() if count > 1),
        "missing_brand_name": sum(not row.get("brand_name_raw") for row in records),
        "missing_generic_name": sum(not row.get("generic_name_raw") for row in records),
        "missing_strength": sum(not row.get("strength_raw") for row in records),
        "missing_dosage_form": sum(not row.get("dosage_form_raw") for row in records),
        "missing_manufacturer": sum(not row.get("manufacturer_name") for row in records),
        "missing_source": sum(not row.get("source_id") for row in records),
        "unknown_strength_unit": sum(
            bool(row.get("strength_raw")) and not _KNOWN_STRENGTH_UNIT_RE.search(row["strength_raw"])
            for row in records
        ),
        "unknown_dosage_form": sum(
            bool(row.get("dosage_form_raw")) and not bool(row.get("dosage_form_code")) for row in records
        ),
        "same_brand_different_ingredient_candidates": [
            {"brand_norm": brand, "ingredient_variants": sorted(values)}
            for brand, values in brand_ingredients.items() if len(values) > 1
        ],
        "same_composition_multiple_brand_candidates": [
            {"composition": list(key), "brand_norms": sorted(values)}
            for key, values in composition_brands.items() if key[0] and len(values) > 1
        ],
        "dangerous_similar_name_candidates": _dangerous_similar_names(
            [(row["id"], row["brand_name_raw"]) for row in records]
        ),
        "source_breakdown": source_breakdown,
        "quarantined_records": [
            {
                "import_run_id": row["import_run_id"],
                "record_ordinal": row["record_ordinal"],
                "source_record_id": row["source_record_id"],
                "errors": json.loads(row["errors_json"] or "[]"),
            }
            for row in run_record_links if row["ingest_status"] == "quarantined"
        ],
    }
    return report


def quality_report_markdown(report: dict[str, Any]) -> str:
    metrics = (
        ("Raw source records", "raw_total"), ("Canonical presentations", "imported"),
        ("Excluded", "excluded"), ("Quarantined run records", "quarantined"),
        ("Needs review", "needs_review"),
        ("Missing normalized projection snapshot", "missing_normalized_projection"),
        ("Changed reprocessing records", "reprocessing_changed"),
        ("Unchanged reprocessing records", "reprocessing_unchanged"),
        ("Reprocessing records requiring review", "reprocessing_review_required"),
        ("Duplicate candidates", "duplicate_candidates"),
        ("Missing brand/product name", "missing_brand_name"),
        ("Missing ingredient name", "missing_generic_name"),
        ("Missing strength", "missing_strength"), ("Missing dosage form", "missing_dosage_form"),
        ("Missing manufacturer", "missing_manufacturer"), ("Missing source", "missing_source"),
        ("Unknown strength unit", "unknown_strength_unit"),
        ("Unknown dosage form", "unknown_dosage_form"),
    )
    lines = ["# Drug catalog quality report", "", f"Generated: {report.get('generated_at') or _now()}", "",
             "| Metric | Count |", "| --- | ---: |"]
    lines.extend(f"| {label} | {report.get(key, 0)} |" for label, key in metrics)
    lines.extend(["", "## Source breakdown", "", "| Source | Tier | Scope | Presentations |",
                  "| --- | ---: | --- | ---: |"])
    for source in report.get("source_breakdown") or []:
        lines.append(
            f"| {source['slug']} | {source['tier']} | {source['usage_scope']} | {source['presentations']} |"
        )
    lines.extend([
        "", "## Review queues", "",
        f"- Same brand, different ingredient candidates: {len(report.get('same_brand_different_ingredient_candidates') or [])}",
        f"- Same composition, multiple brand candidates: {len(report.get('same_composition_multiple_brand_candidates') or [])}",
        f"- Dangerous similar-name candidates: {len(report.get('dangerous_similar_name_candidates') or [])}",
        "", "No candidate in these queues is merged automatically.", "",
    ])
    return "\n".join(lines)
