#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append rows to a general vocabulary TSV safely."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List


REQUIRED_COLUMNS = [
    "id",
    "key",
    "value",
    "acceptable_values",
    "competing_values",
    "category",
    "subcategory",
    "pos_hint",
    "priority",
    "risk_hint",
    "expected_behavior",
    "note",
]


def read_rows(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]
    return fieldnames, rows


def fail(message: str) -> None:
    print("ERROR: %s" % message)
    sys.exit(1)


def validate_fieldnames(path: Path, fieldnames: List[str]) -> None:
    if fieldnames != REQUIRED_COLUMNS:
        fail(
            "%s has unexpected columns.\nExpected: %r\nActual:   %r"
            % (path, REQUIRED_COLUMNS, fieldnames)
        )


def validate_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    for index, row in enumerate(rows, start=2):
        row_id = row.get("id", "")
        key = row.get("key", "")
        value = row.get("value", "")

        if not row_id or not key or not value:
            fail("%s line %d has empty id/key/value: %r" % (path, index, row))

        if "?" in key or "?" in value:
            fail("%s line %d looks corrupted: %r" % (path, index, row))

        if "\t" in key or "\t" in value:
            fail("%s line %d contains embedded tab: %r" % (path, index, row))


def write_rows(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--append", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_path = Path(args.base)
    append_path = Path(args.append)

    base_fields, base_rows = read_rows(base_path)
    append_fields, append_rows = read_rows(append_path)

    validate_fieldnames(base_path, base_fields)
    validate_fieldnames(append_path, append_fields)
    validate_rows(base_path, base_rows)
    validate_rows(append_path, append_rows)

    base_ids = {row["id"] for row in base_rows}
    append_ids = [row["id"] for row in append_rows]

    duplicate_in_append = sorted({row_id for row_id in append_ids if append_ids.count(row_id) > 1})
    if duplicate_in_append:
        fail("duplicate ids in append file: %r" % duplicate_in_append)

    duplicate_existing = sorted(row_id for row_id in append_ids if row_id in base_ids)
    if duplicate_existing:
        fail("ids already exist in base file: %r" % duplicate_existing)

    merged_rows = base_rows + append_rows

    print("Base rows:   %d" % len(base_rows))
    print("Append rows: %d" % len(append_rows))
    print("Merged rows: %d" % len(merged_rows))

    if args.dry_run:
        print("Dry run only. No file changed.")
        return

    write_rows(base_path, base_fields, merged_rows)
    print("Updated: %s" % base_path)


if __name__ == "__main__":
    main()
