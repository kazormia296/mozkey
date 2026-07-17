#!/usr/bin/env python3
"""Run and compare fixed-corpus quality evaluations for the Mozkey migration."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Any

if __package__:
    from .compare_evaluation_quality import EvalRow, parse_row
else:
    from compare_evaluation_quality import EvalRow, parse_row


SCHEMA_VERSION = "mozkey.fixed_corpus_gate.v1"
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
FIXTURE_DIR = SCRIPT_DIR / "testdata" / "fixed_corpus"
ENGINE_NAMES = ("base_mozc", "hazkey", "mozkey")


@dataclasses.dataclass(frozen=True)
class CorpusCase:
    index: int
    case_id: str
    key: str
    expected: str
    mode: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_mode(columns: list[str]) -> str:
    mode = columns[3]
    if len(columns) < 5:
        return mode
    try:
        rank = int(columns[4])
    except ValueError as error:
        raise ValueError(f"invalid expected rank: {columns[4]!r}") from error
    return f"{mode} {rank}" if rank else mode


def load_corpus(path: pathlib.Path) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) < 4:
                raise ValueError(
                    f"{path}:{line_number}: expected at least four TSV columns"
                )
            cases.append(
                CorpusCase(
                    index=len(cases),
                    case_id=columns[0],
                    key=columns[1],
                    expected=columns[2],
                    mode=_expected_mode(columns),
                )
            )
    if not cases:
        raise ValueError(f"fixed corpus is empty: {path}")
    return cases


def load_engine_result(
    path: pathlib.Path, cases: Sequence[CorpusCase], engine_name: str
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for line in stream:
            row = parse_row(line)
            if row is not None:
                rows.append(row)

    if len(rows) != len(cases):
        raise ValueError(
            f"{engine_name}: result row count {len(rows)} does not match "
            f"corpus row count {len(cases)}: {path}"
        )

    for case, row in zip(cases, rows, strict=True):
        mismatches = []
        if row.key != case.key:
            mismatches.append(f"key={row.key!r} expected={case.key!r}")
        if row.expected != case.expected:
            mismatches.append(
                f"expected_value={row.expected!r} expected={case.expected!r}"
            )
        if row.mode != case.mode:
            mismatches.append(f"mode={row.mode!r} expected={case.mode!r}")
        if mismatches:
            raise ValueError(
                f"{engine_name}: result misalignment at case {case.index + 1} "
                f"({case.case_id}): {'; '.join(mismatches)}"
            )
    return rows


def _engine_summary(path: pathlib.Path, rows: Sequence[EvalRow]) -> dict[str, Any]:
    passed = sum(row.status == "OK" for row in rows)
    return {
        "result_path": str(path),
        "result_sha256": sha256_file(path),
        "passed": passed,
        "failed": len(rows) - passed,
    }


def run_gate(
    corpus_path: pathlib.Path,
    result_paths: dict[str, pathlib.Path],
    out_dir: pathlib.Path,
) -> dict[str, Any]:
    cases = load_corpus(corpus_path)
    results = {
        engine: load_engine_result(result_paths[engine], cases, engine)
        for engine in ENGINE_NAMES
    }

    comparison_rows: list[dict[str, str]] = []
    regressions = 0
    oracle_required = 0
    mozkey_improvements = 0
    all_failed = 0

    for case in cases:
        base = results["base_mozc"][case.index]
        hazkey = results["hazkey"][case.index]
        mozkey = results["mozkey"][case.index]
        required = base.status == "OK" or hazkey.status == "OK"
        mozkey_ok = mozkey.status == "OK"

        if required:
            oracle_required += 1
            if mozkey_ok:
                classification = "kept_oracle_case"
            else:
                classification = "regression"
                regressions += 1
        elif mozkey_ok:
            classification = "mozkey_improvement"
            mozkey_improvements += 1
        else:
            classification = "all_failed"
            all_failed += 1

        comparison_rows.append(
            {
                "case_index": str(case.index + 1),
                "case_id": case.case_id,
                "key": case.key,
                "expected": case.expected,
                "mode": case.mode,
                "base_mozc_status": base.status,
                "base_mozc_output": base.output,
                "hazkey_status": hazkey.status,
                "hazkey_output": hazkey.output,
                "mozkey_status": mozkey.status,
                "mozkey_output": mozkey.output,
                "oracle_required": "true" if required else "false",
                "classification": classification,
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / "comparison.tsv"
    fieldnames = list(comparison_rows[0])
    with comparison_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(comparison_rows)

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "decision": "pass" if regressions == 0 else "fail",
        "policy": (
            "For every case passed by base_mozc or hazkey, mozkey must also pass. "
            "Missing, reordered, or metadata-mismatched rows are invalid input."
        ),
        "corpus": {
            "path": str(corpus_path),
            "sha256": sha256_file(corpus_path),
            "cases": len(cases),
        },
        "engines": {
            engine: _engine_summary(result_paths[engine], results[engine])
            for engine in ENGINE_NAMES
        },
        "counts": {
            "oracle_required": oracle_required,
            "regressions": regressions,
            "mozkey_improvements": mozkey_improvements,
            "all_failed": all_failed,
        },
        "artifacts": {"comparison_tsv": str(comparison_path)},
    }
    summary_path = out_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")

    print(f"decision: {summary['decision']}")
    print(f"corpus cases: {len(cases)}")
    print(f"oracle-required cases: {oracle_required}")
    print(f"regressions: {regressions}")
    print(f"mozkey improvements: {mozkey_improvements}")
    print(f"comparison: {comparison_path}")
    print(f"summary: {summary_path}")
    return summary


def evaluate_engine(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.quality_main),
        f"--test_files={args.corpus}",
        f"--data_file={args.data_file}",
        f"--data_type={args.data_type}",
        f"--engine_type={args.engine_type}",
        f"--output={args.output}",
    ]
    subprocess.run(command, check=True)
    print(f"raw result: {args.output}")
    return 0


def gate_command(args: argparse.Namespace) -> int:
    summary = run_gate(
        corpus_path=args.corpus,
        result_paths={
            "base_mozc": args.base_mozc_result,
            "hazkey": args.hazkey_result,
            "mozkey": args.mozkey_result,
        },
        out_dir=args.out_dir,
    )
    return 0 if summary["decision"] == "pass" else 1


def smoke_command(args: argparse.Namespace) -> int:
    if args.out_dir is not None:
        summary = run_gate(
            corpus_path=FIXTURE_DIR / "corpus.tsv",
            result_paths={
                "base_mozc": FIXTURE_DIR / "base_mozc.tsv",
                "hazkey": FIXTURE_DIR / "hazkey.tsv",
                "mozkey": FIXTURE_DIR / "mozkey.tsv",
            },
            out_dir=args.out_dir,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="mozkey-fixed-corpus-") as temp_dir:
            summary = run_gate(
                corpus_path=FIXTURE_DIR / "corpus.tsv",
                result_paths={
                    "base_mozc": FIXTURE_DIR / "base_mozc.tsv",
                    "hazkey": FIXTURE_DIR / "hazkey.tsv",
                    "mozkey": FIXTURE_DIR / "mozkey.tsv",
                },
                out_dir=pathlib.Path(temp_dir),
            )
    if summary["decision"] != "pass":
        return 1
    print("Offline fixed-corpus fixture smoke passed.")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser(
        "evaluate", help="Run quality_regression_main for one engine/data pair."
    )
    evaluate.add_argument("--quality-main", type=pathlib.Path, required=True)
    evaluate.add_argument("--corpus", type=pathlib.Path, required=True)
    evaluate.add_argument("--data-file", type=pathlib.Path, required=True)
    evaluate.add_argument("--output", type=pathlib.Path, required=True)
    evaluate.add_argument("--data-type", default="oss")
    evaluate.add_argument("--engine-type", default="desktop")
    evaluate.set_defaults(func=evaluate_engine)

    gate = subparsers.add_parser(
        "gate", help="Compare base Mozc, Hazkey, and Mozkey raw TSV results."
    )
    gate.add_argument("--corpus", type=pathlib.Path, required=True)
    gate.add_argument("--base-mozc-result", type=pathlib.Path, required=True)
    gate.add_argument("--hazkey-result", type=pathlib.Path, required=True)
    gate.add_argument("--mozkey-result", type=pathlib.Path, required=True)
    gate.add_argument("--out-dir", type=pathlib.Path, required=True)
    gate.set_defaults(func=gate_command)

    smoke = subparsers.add_parser(
        "smoke", help="Run the bundled, network-free fixture gate."
    )
    smoke.add_argument(
        "--out-dir",
        type=pathlib.Path,
        help="Keep smoke artifacts here; otherwise use a temporary directory.",
    )
    smoke.set_defaults(func=smoke_command)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        out_dir = getattr(args, "out_dir", None)
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            error_summary = {
                "schema_version": SCHEMA_VERSION,
                "decision": "invalid_input",
                "error": str(error),
            }
            with (out_dir / "summary.json").open(
                "w", encoding="utf-8", newline="\n"
            ) as stream:
                json.dump(
                    error_summary,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
