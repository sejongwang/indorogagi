"""Deterministically build/check the reviewed NPPA formulation package.

PDF table extraction is deliberately human-reviewed: OCR/layout guesses must not
silently become medicine identities. The committed CSV is the reviewed
transcription; this script converts it to the exact importer package.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "data/catalog/nppa-anti-diabetes-2026-03.source.json"
DEFAULT_ROWS = REPO_ROOT / "data/catalog/nppa-anti-diabetes-2026-03.rows.csv"
DEFAULT_PACKAGE = REPO_ROOT / "data/catalog/nppa-anti-diabetes-2026-03.json"


def build_package(source_path: Path, rows_path: Path) -> dict[str, Any]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    with rows_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ordinals = [int(row["source_row"]) for row in rows]
    if ordinals != list(range(1, len(rows) + 1)):
        raise ValueError("NPPA reviewed rows must be uniquely ordered from 1")
    records = []
    for row in rows:
        required = ("display_name", "generic_name", "strength", "form", "price_unit", "ceiling_price_inr")
        if any(not row.get(field, "").strip() for field in required):
            raise ValueError(f"NPPA reviewed row {row['source_row']} is incomplete")
        record = {
            "source_record_id": f"anti-diabetes-{int(row['source_row']):03d}",
            "name_type": "generic",
            "brand_name": row["display_name"].strip(),
            "generic_name": row["generic_name"].strip(),
            "ingredients": [{
                "name": row["generic_name"].strip(),
                "strength": row["strength"].strip(),
            }],
            "strength": row["strength"].strip(),
            "form": row["form"].strip(),
            "route": row.get("route", "").strip() or None,
            "aliases": [],
            "source_extra": {
                "ceiling_price_inr": float(row["ceiling_price_inr"]),
                "price_unit": row["price_unit"].strip(),
                "source_row": int(row["source_row"]),
            },
        }
        modifier = row.get("release_modifier", "").strip()
        if modifier:
            record["release_modifier"] = modifier
        records.append(record)
    return {"source": source, "records": records}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/check reviewed NPPA package")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    generated = build_package(args.source, args.rows)
    if args.write:
        args.package.write_text(
            json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    current = json.loads(args.package.read_text(encoding="utf-8"))
    if generated != current:
        raise ValueError("reviewed NPPA rows/source do not match the pinned package")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"NPPA package check failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
