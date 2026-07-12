"""Offline regressions for the pinned official-source acquisition pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.fetch_catalog_source import (
    main as fetch_main,
    validate_official_url,
    validate_pdf,
    verify_manifest,
)
from scripts.nppa_package import build_package

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "data" / "catalog"


def test_reviewed_nppa_csv_deterministically_rebuilds_pinned_package():
    generated = build_package(
        CATALOG_DIR / "nppa-anti-diabetes-2026-03.source.json",
        CATALOG_DIR / "nppa-anti-diabetes-2026-03.rows.csv",
    )
    pinned = json.loads(
        (CATALOG_DIR / "nppa-anti-diabetes-2026-03.json").read_text(encoding="utf-8")
    )

    assert generated == pinned
    assert [row["source_extra"]["source_row"] for row in pinned["records"]] == list(
        range(1, 12)
    )


def test_package_source_is_bound_to_exact_official_artifact_manifest():
    package = json.loads(
        (CATALOG_DIR / "nppa-anti-diabetes-2026-03.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (
            CATALOG_DIR
            / "nppa-anti-diabetes-2026-03.source-manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert package["source"]["source_artifact_sha256"] == manifest["sha256"]
    assert package["source"]["source_artifact_bytes"] == manifest["content_length"]
    assert package["source"]["source_url"] == manifest["url"]


def test_source_fetcher_allows_only_nppa_https_and_fails_closed_on_change():
    validate_official_url("https://www.nppa.gov.in/official.pdf")
    with pytest.raises(ValueError, match="approved NPPA host"):
        validate_official_url("https://www.tata1mg.com/commercial.pdf")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_official_url("http://www.nppa.gov.in/official.pdf")

    validate_pdf(b"%PDF-1.7\nreviewed", content_type="application/pdf")
    with pytest.raises(ValueError, match="not a PDF"):
        validate_pdf(b"<html>not the source</html>", content_type="text/html")

    manifest = {
        "url": "https://www.nppa.gov.in/official.pdf",
        "content_length": 10,
        "sha256": "pinned",
    }
    with pytest.raises(ValueError, match="sha256 changed"):
        verify_manifest({**manifest, "sha256": "changed"}, manifest)


def test_fetch_manifest_filename_cannot_escape_output_directory(
    tmp_path, monkeypatch
):
    manifest = {
        "url": "https://www.nppa.gov.in/official.pdf",
        "filename": "../escaped.pdf",
        "content_length": 17,
        "sha256": "pinned",
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.fetch_catalog_source.fetch_artifact",
        lambda url: (b"%PDF-1.7\npinned", {**manifest, "url": url}),
    )

    with pytest.raises(ValueError, match="plain basename"):
        fetch_main([str(manifest_path), "--output-dir", str(tmp_path / "cache")])
    assert not (tmp_path / "escaped.pdf").exists()
