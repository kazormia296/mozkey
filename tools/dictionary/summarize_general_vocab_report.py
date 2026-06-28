#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarize general vocabulary coverage report."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Dict, List


def read_report(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_tsv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    report_path = Path(args.report)
    out_dir = Path(args.out_dir)
    rows = read_report(report_path)

    status_counter = Counter(row.get("status", "") for row in rows)
    cat_counter = Counter(
        (
            row.get("category", ""),
            row.get("subcategory", ""),
            row.get("status", ""),
        )
        for row in rows
    )

    summary_rows = []
    for (category, subcategory, status), count in sorted(cat_counter.items()):
        summary_rows.append({
            "category": category,
            "subcategory": subcategory,
            "status": status,
            "count": str(count),
        })

    common_fields = [
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
        "status",
        "actual_top1",
        "recommendation",
        "risk_flags",
        "note",
    ]

    action_items = [
        row for row in rows
        if row.get("status") != "ok_top1"
    ]

    missing_candidates = [
        row for row in rows
        if row.get("status") == "missing_or_not_top10"
    ]

    variant_items = [
        row for row in rows
        if row.get("status") in {
            "orthographic_variant_top1",
            "normalized_variant_top1",
        }
    ]

    semantic_competitor_items = [
        row for row in rows
        if row.get("status") == "semantic_competitor_top1"
    ]

    weak_items = [
        row for row in rows
        if row.get("status") in {
            "ok_top5",
            "weak_top10",
        }
    ]

    write_tsv(
        out_dir / "general_vocab_category_status_summary.tsv",
        summary_rows,
        ["category", "subcategory", "status", "count"],
    )
    write_tsv(
        out_dir / "general_vocab_action_items.tsv",
        action_items,
        common_fields,
    )
    write_tsv(
        out_dir / "general_vocab_missing_candidates.tsv",
        missing_candidates,
        common_fields,
    )
    write_tsv(
        out_dir / "general_vocab_variant_items.tsv",
        variant_items,
        common_fields,
    )
    write_tsv(
        out_dir / "general_vocab_semantic_competitor_items.tsv",
        semantic_competitor_items,
        common_fields,
    )
    write_tsv(
        out_dir / "general_vocab_weak_items.tsv",
        weak_items,
        common_fields,
    )

    print("Rows: %d" % len(rows))
    print("")
    print("Status summary:")
    for status, count in sorted(status_counter.items()):
        print("  %s: %d" % (status, count))

    print("")
    print("Wrote:")
    print("  %s" % (out_dir / "general_vocab_category_status_summary.tsv"))
    print("  %s" % (out_dir / "general_vocab_action_items.tsv"))
    print("  %s" % (out_dir / "general_vocab_missing_candidates.tsv"))
    print("  %s" % (out_dir / "general_vocab_variant_items.tsv"))
    print("  %s" % (out_dir / "general_vocab_semantic_competitor_items.tsv"))
    print("  %s" % (out_dir / "general_vocab_weak_items.tsv"))


if __name__ == "__main__":
    main()
