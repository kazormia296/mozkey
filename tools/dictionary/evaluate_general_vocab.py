#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate general vocabulary coverage for Mozkey.

This script does not modify dictionaries.
It prepares quality_regression_main input files, runs quality_regression_main,
validates general-vocab TSV files, and summarizes top1/top5/top10 coverage.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List


REQUIRED_COLUMNS = {"id", "key", "value"}
RECOMMENDED_COLUMNS = [
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

ALLOWED_PRIORITY = {"high", "medium", "low"}
ALLOWED_RISK_HINT = {"low", "medium", "high"}
ALLOWED_EXPECTED_BEHAVIOR = {
    "exact_preferred",
    "variant_allowed",
    "coverage_probe",
    "risk_probe",
    "semantic_competitor_probe",
}
ALLOWED_POS_HINT = {
    "noun",
    "sahen_noun",
    "adjective",
    "adverb",
    "verb",
    "phrase",
    "unknown",
}

PARTICLE_LIKE_KEYS = {
    "には", "にも", "では", "でも", "とは", "のに", "まで", "だけ", "ほど",
    "から", "より", "として", "について", "なのか", "なのだ", "なんです",
}

ID_RE = re.compile(r"^G\d{6}$")


def read_vocab(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []
        missing = REQUIRED_COLUMNS - set(fieldnames)
        if missing:
            raise ValueError(
                "Missing required columns: %s. Detected columns: %r. "
                "The input file must be a real tab-separated TSV file."
                % (", ".join(sorted(missing)), fieldnames)
            )

        rows: List[Dict[str, str]] = []
        for row in reader:
            if not any((value or "").strip() for value in row.values()):
                continue
            normalized = {key: (value or "").strip() for key, value in row.items()}
            if not normalized.get("id") or not normalized.get("key") or not normalized.get("value"):
                rows.append(normalized)
                continue
            rows.append(normalized)
        return rows


def acceptable_values(row: Dict[str, str]) -> List[str]:
    raw = row.get("acceptable_values", "")
    values = []
    for value in raw.split("|"):
        value = value.strip()
        if value:
            values.append(value)
    return values


def competing_values(row: Dict[str, str]) -> List[str]:
    raw = row.get("competing_values", "")
    values = []
    for value in raw.split("|"):
        value = value.strip()
        if value:
            values.append(value)
    return values


def normalized_equal(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return unicodedata.normalize("NFKC", left) == unicodedata.normalize("NFKC", right)


def is_hiragana_reading(s: str) -> bool:
    if not s:
        return False
    return all("\u3040" <= ch <= "\u309f" or ch == "ー" for ch in s)


def is_hiragana_only(s: str) -> bool:
    if not s:
        return False
    return all("\u3040" <= ch <= "\u309f" or ch == "ー" for ch in s)


def risk_flags(row: Dict[str, str]) -> List[str]:
    key = row.get("key", "")
    value = row.get("value", "")
    flags: List[str] = []

    if len(key) <= 2:
        flags.append("short_key")
    if len(value) <= 1:
        flags.append("one_char_value")
    if is_hiragana_only(value):
        flags.append("hiragana_value")
    if key in PARTICLE_LIKE_KEYS:
        flags.append("particle_like_key")
    if row.get("risk_hint", "").lower() in {"medium", "high"}:
        flags.append("risk_hint_" + row.get("risk_hint", "").lower())

    return flags


def validate(args: argparse.Namespace) -> None:
    path = Path(args.input)

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []

    rows = read_vocab(path)
    errors: List[str] = []
    warnings: List[str] = []

    for column in REQUIRED_COLUMNS:
        if column not in fieldnames:
            errors.append("missing required column: %s" % column)

    for column in RECOMMENDED_COLUMNS:
        if column not in fieldnames:
            warnings.append("missing recommended column: %s" % column)

    seen_ids = set()
    seen_pairs = set()

    for line_no, row in enumerate(rows, start=2):
        row_id = row.get("id", "")
        key = row.get("key", "")
        value = row.get("value", "")
        priority = row.get("priority", "")
        risk_hint = row.get("risk_hint", "")
        expected_behavior = row.get("expected_behavior", "")
        pos_hint = row.get("pos_hint", "")
        variants = acceptable_values(row)
        competitors = competing_values(row)

        label = "%s line=%d key=%r value=%r" % (row_id or "<no-id>", line_no, key, value)

        if not row_id:
            errors.append("%s: empty id" % label)
        elif not ID_RE.match(row_id):
            errors.append("%s: id must match G000000 style" % label)

        if row_id in seen_ids:
            errors.append("%s: duplicate id" % label)
        seen_ids.add(row_id)

        pair = (key, value)
        if pair in seen_pairs:
            warnings.append("%s: duplicate key/value pair" % label)
        seen_pairs.add(pair)

        if not key:
            errors.append("%s: empty key" % label)
        elif not is_hiragana_reading(key):
            errors.append("%s: key must be hiragana reading" % label)

        if not value:
            errors.append("%s: empty value" % label)

        if priority and priority not in ALLOWED_PRIORITY:
            errors.append("%s: invalid priority=%r" % (label, priority))

        if risk_hint and risk_hint not in ALLOWED_RISK_HINT:
            errors.append("%s: invalid risk_hint=%r" % (label, risk_hint))

        if expected_behavior and expected_behavior not in ALLOWED_EXPECTED_BEHAVIOR:
            errors.append("%s: invalid expected_behavior=%r" % (label, expected_behavior))

        if pos_hint and pos_hint not in ALLOWED_POS_HINT:
            warnings.append("%s: unknown pos_hint=%r" % (label, pos_hint))

        if value in variants:
            errors.append("%s: acceptable_values must not include canonical value" % label)

        if value in competitors:
            errors.append("%s: competing_values must not include canonical value" % label)

        if variants and competitors and expected_behavior != "semantic_competitor_probe":
            warnings.append("%s: both acceptable_values and competing_values are present" % label)

        if "|" in value:
            errors.append("%s: value must be one canonical form, variants go to acceptable_values" % label)

        if key in PARTICLE_LIKE_KEYS and expected_behavior != "risk_probe":
            errors.append("%s: particle-like key must not be in general vocab batch" % label)

        if len(key) <= 2 and expected_behavior not in {"risk_probe", "variant_allowed"}:
            warnings.append("%s: short key should be risk_probe or removed" % label)

        if is_hiragana_only(value) and expected_behavior not in {"variant_allowed", "risk_probe"}:
            warnings.append("%s: hiragana-only value may be orthographic preference, not missing vocab" % label)

        if variants and expected_behavior not in {"variant_allowed", "semantic_competitor_probe"}:
            warnings.append("%s: acceptable_values present but expected_behavior is not variant_allowed or semantic_competitor_probe" % label)

        if expected_behavior == "variant_allowed" and not variants:
            warnings.append("%s: variant_allowed but acceptable_values is empty" % label)

        if competitors and expected_behavior != "semantic_competitor_probe":
            warnings.append("%s: competing_values present but expected_behavior is not semantic_competitor_probe" % label)

        if expected_behavior == "semantic_competitor_probe" and not competitors:
            warnings.append("%s: semantic_competitor_probe but competing_values is empty" % label)

        if row.get("risk_hint") == "high" and expected_behavior != "risk_probe":
            warnings.append("%s: high risk row should usually be risk_probe" % label)

    print("Validation:")
    print("  file: %s" % path)
    print("  rows: %d" % len(rows))
    print("  errors: %d" % len(errors))
    print("  warnings: %d" % len(warnings))

    if errors:
        print("")
        print("Errors:")
        for item in errors:
            print("  - %s" % item)

    if warnings:
        print("")
        print("Warnings:")
        for item in warnings:
            print("  - %s" % item)

    if errors or (args.strict and warnings):
        sys.exit(1)


def write_quality_file(rows: List[Dict[str, str]], out_path: Path, expected_rank: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        for row in rows:
            writer.writerow([
                row["id"],
                row["key"],
                row["value"],
                "Conversion Expected",
                str(expected_rank),
                "1.0",
                "desktop",
            ])


def prepare(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    rows = read_vocab(input_path)

    write_quality_file(rows, out_dir / "general_vocab_top1.tsv", 0)
    write_quality_file(rows, out_dir / "general_vocab_top5.tsv", 4)
    write_quality_file(rows, out_dir / "general_vocab_top10.tsv", 9)

    print("Prepared quality regression TSV files:")
    print("  %s" % (out_dir / "general_vocab_top1.tsv"))
    print("  %s" % (out_dir / "general_vocab_top5.tsv"))
    print("  %s" % (out_dir / "general_vocab_top10.tsv"))
    print("Rows: %d" % len(rows))


def run_quality(args: argparse.Namespace) -> None:
    quality_main = Path(args.quality_main)
    data_file = Path(args.mozc_data)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        ("top1", out_dir / "general_vocab_top1.tsv", out_dir / "result_top1.tsv"),
        ("top5", out_dir / "general_vocab_top5.tsv", out_dir / "result_top5.tsv"),
        ("top10", out_dir / "general_vocab_top10.tsv", out_dir / "result_top10.tsv"),
    ]

    for name, test_file, result_file in jobs:
        cmd = [
            str(quality_main),
            "--test_files=%s" % test_file,
            "--data_file=%s" % data_file,
            "--data_type=%s" % args.data_type,
            "--engine_type=%s" % args.engine_type,
        ]

        with result_file.open("wb") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT)

        print("%s: exit_code=%d output=%s" % (name, proc.returncode, result_file))


def read_text_lines_auto(path: Path) -> List[str]:
    data = path.read_bytes()

    for encoding in ("utf-8-sig", "utf-16", "cp932"):
        try:
            return data.decode(encoding).splitlines()
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace").splitlines()


def parse_quality_output(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)

    result: List[Dict[str, str]] = []

    for line in read_text_lines_auto(path):
        if not line:
            continue

        cols = line.split("\t")
        if len(cols) < 5:
            continue

        status = cols[0].strip()
        if status not in {"OK:", "FAILED:"}:
            continue

        result.append({
            "ok": "true" if status == "OK:" else "false",
            "actual": cols[2],
            "raw_status": status,
        })

    return result


def get_quality_result(results: List[Dict[str, str]], index: int) -> Dict[str, str]:
    if index < len(results):
        return results[index]
    return {"ok": "false", "actual": "", "raw_status": "MISSING:"}


def summarize(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    rows = read_vocab(input_path)

    top1 = parse_quality_output(out_dir / "result_top1.tsv")
    top5 = parse_quality_output(out_dir / "result_top5.tsv")
    top10 = parse_quality_output(out_dir / "result_top10.tsv")

    if len(top1) != len(rows) or len(top5) != len(rows) or len(top10) != len(rows):
        print("WARNING: result row count mismatch.")
        print("  input rows: %d" % len(rows))
        print("  top1 rows:  %d" % len(top1))
        print("  top5 rows:  %d" % len(top5))
        print("  top10 rows: %d" % len(top10))

    report_path = out_dir / "general_vocab_batch_report.tsv"
    summary = {
        "ok_top1": 0,
        "orthographic_variant_top1": 0,
        "normalized_variant_top1": 0,
        "semantic_competitor_top1": 0,
        "ok_top5": 0,
        "weak_top10": 0,
        "missing_or_not_top10": 0,
        "manual_review_risky": 0,
    }

    with report_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "id", "key", "value", "acceptable_values", "competing_values",
            "category", "subcategory", "pos_hint", "priority", "risk_hint",
            "expected_behavior", "status", "actual_top1",
            "top1_ok", "top5_ok", "top10_ok",
            "risk_flags", "recommendation", "note",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()

        for index, row in enumerate(rows):
            r1 = get_quality_result(top1, index)
            r5 = get_quality_result(top5, index)
            r10 = get_quality_result(top10, index)

            flags = risk_flags(row)
            risky = bool(flags)
            variants = acceptable_values(row)
            competitors = competing_values(row)

            if r1["ok"] == "true":
                status = "ok_top1"
                recommendation = "no_action"
            elif r1.get("actual", "") in variants:
                status = "orthographic_variant_top1"
                recommendation = "no_dictionary_addition"
            elif normalized_equal(r1.get("actual", ""), row.get("value", "")):
                status = "normalized_variant_top1"
                recommendation = "no_dictionary_addition"
            elif r1.get("actual", "") in competitors:
                status = "semantic_competitor_top1"
                recommendation = "manual_review_semantic_competition"
            elif r5["ok"] == "true":
                status = "ok_top5"
                recommendation = "no_dictionary_addition"
            elif r10["ok"] == "true":
                status = "weak_top10"
                recommendation = "manual_review_cost_or_history"
            else:
                status = "missing_or_not_top10"
                recommendation = "candidate_if_safe"

            if risky and status not in {"ok_top1", "orthographic_variant_top1", "normalized_variant_top1"}:
                summary["manual_review_risky"] += 1
                recommendation = "manual_review_risky"

            summary[status] += 1

            writer.writerow({
                "id": row.get("id", ""),
                "key": row.get("key", ""),
                "value": row.get("value", ""),
                "acceptable_values": row.get("acceptable_values", ""),
                "competing_values": row.get("competing_values", ""),
                "category": row.get("category", ""),
                "subcategory": row.get("subcategory", ""),
                "pos_hint": row.get("pos_hint", ""),
                "priority": row.get("priority", ""),
                "risk_hint": row.get("risk_hint", ""),
                "expected_behavior": row.get("expected_behavior", ""),
                "status": status,
                "actual_top1": r1.get("actual", ""),
                "top1_ok": r1.get("ok", "false"),
                "top5_ok": r5.get("ok", "false"),
                "top10_ok": r10.get("ok", "false"),
                "risk_flags": ",".join(flags),
                "recommendation": recommendation,
                "note": row.get("note", ""),
            })

    print("Report written:")
    print("  %s" % report_path)
    print("")
    print("Summary:")
    for k in [
        "ok_top1",
        "orthographic_variant_top1",
        "normalized_variant_top1",
        "semantic_competitor_top1",
        "ok_top5",
        "weak_top10",
        "missing_or_not_top10",
        "manual_review_risky",
    ]:
        print("  %s: %d" % (k, summary[k]))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--input", required=True)
    p_validate.add_argument("--strict", action="store_true")
    p_validate.set_defaults(func=validate)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--input", required=True)
    p_prepare.add_argument("--out-dir", required=True)
    p_prepare.set_defaults(func=prepare)

    p_run = sub.add_parser("run")
    p_run.add_argument("--quality-main", required=True)
    p_run.add_argument("--mozc-data", required=True)
    p_run.add_argument("--out-dir", required=True)
    p_run.add_argument("--data-type", default="oss")
    p_run.add_argument("--engine-type", default="desktop")
    p_run.set_defaults(func=run_quality)

    p_summarize = sub.add_parser("summarize")
    p_summarize.add_argument("--input", required=True)
    p_summarize.add_argument("--out-dir", required=True)
    p_summarize.set_defaults(func=summarize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
