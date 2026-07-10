"""Fetch a pinned official catalog artifact without scraping commercial sites.

The downloader is intentionally restricted to approved government hosts, caps the
payload size, validates PDF bytes, and compares the result with a committed
manifest. A changed artifact fails closed until data governance reviews and pins a
new manifest/package version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from email.parser import Parser
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"nppa.gov.in", "www.nppa.gov.in"})
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
USER_AGENT = "indoro-catalog-source-verifier/1.0 (+local data governance)"


def validate_official_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("source artifact URL must be HTTPS on an approved NPPA host")


def validate_pdf(data: bytes, *, content_type: str | None = None) -> None:
    if not data.startswith(b"%PDF-"):
        raise ValueError("source artifact is not a PDF")
    if not data or len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("source artifact size is outside the allowed range")
    if content_type and "application/pdf" not in content_type.lower():
        raise ValueError("source artifact response is not application/pdf")


def fetch_artifact(url: str) -> tuple[bytes, dict[str, Any]]:
    validate_official_url(url)
    curl = shutil.which("curl")
    if not curl:
        raise OSError("curl is required for system trust-store TLS verification")
    with tempfile.TemporaryDirectory(prefix="indoro-catalog-") as temp_dir:
        artifact_path = Path(temp_dir) / "artifact.pdf"
        header_path = Path(temp_dir) / "headers.txt"
        result = subprocess.run(
            [
                curl,
                "--proto", "=https",
                "--proto-redir", "=https",
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time", "30",
                "--max-filesize", str(MAX_ARTIFACT_BYTES),
                "--user-agent", USER_AGENT,
                "--dump-header", str(header_path),
                "--output", str(artifact_path),
                "--write-out", "%{url_effective}",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        final_url = result.stdout.strip()
        validate_official_url(final_url)
        data = artifact_path.read_bytes()
        header_blocks = [
            block for block in header_path.read_text(encoding="iso-8859-1").replace("\r\n", "\n").split("\n\n")
            if block.startswith("HTTP/")
        ]
        if not header_blocks:
            raise ValueError("source artifact response headers are missing")
        headers = Parser().parsestr(header_blocks[-1].split("\n", 1)[1])
        declared_length = headers.get("Content-Length")
        if declared_length and int(declared_length) != len(data):
            raise ValueError("source artifact Content-Length does not match downloaded bytes")
        content_type = headers.get("Content-Type")
        validate_pdf(data, content_type=content_type)
        metadata = {
            "url": final_url,
            "content_type": content_type,
            "content_length": len(data),
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return data, metadata


def verify_manifest(observed: dict[str, Any], manifest: dict[str, Any]) -> None:
    for key in ("url", "content_length", "sha256"):
        if observed.get(key) != manifest.get(key):
            raise ValueError(f"source artifact {key} changed; review a new package version")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify one pinned NPPA PDF")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    data, observed = fetch_artifact(manifest["url"])
    verify_manifest(observed, manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    filename = manifest.get("filename")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ValueError("source manifest filename must be a plain basename")
    destination = args.output_dir / filename
    destination.write_bytes(data)
    print(json.dumps({**observed, "path": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"catalog source fetch failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
