#!/usr/bin/env python3
"""Run and compare fixed-corpus quality evaluations for the Mozkey migration."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from fractions import Fraction
from typing import Any

if __package__:
    from .compare_evaluation_quality import EvalRow, parse_row
else:
    from compare_evaluation_quality import EvalRow, parse_row


SCHEMA_VERSION = "mozkey.fixed_corpus_gate.v2"
AB_PROBE_SCHEMA = "hazkey.ab-probe-result.v3"
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
    category: str | None = None

    def accepts(self, output: str) -> bool:
        return output in self.expected.split("|")


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
    ab_probe_format = False
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
            if columns[:4] == ["id", "reading", "expected", "category"]:
                if cases:
                    raise ValueError(
                        f"{path}:{line_number}: ABProbe header must be the first row"
                    )
                ab_probe_format = True
                continue
            cases.append(
                CorpusCase(
                    index=len(cases),
                    case_id=columns[0],
                    key=columns[1],
                    expected=columns[2],
                    mode=(
                        "Conversion Expected"
                        if ab_probe_format
                        else _expected_mode(columns)
                    ),
                    category=columns[3] if ab_probe_format else None,
                )
            )
    if not cases:
        raise ValueError(f"fixed corpus is empty: {path}")
    return cases


def load_engine_result(
    path: pathlib.Path, cases: Sequence[CorpusCase], engine_name: str
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            row = parse_row(line)
            if row is None:
                if line.strip():
                    raise ValueError(
                        f"{engine_name}: invalid non-empty result row at "
                        f"{path}:{line_number}"
                    )
                continue
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


def validate_independent_evidence(
    result_paths: dict[str, pathlib.Path],
    results: dict[str, Sequence[EvalRow]],
) -> dict[str, str]:
    resolved_paths = {
        engine: result_paths[engine].resolve(strict=True) for engine in ENGINE_NAMES
    }
    if len(set(resolved_paths.values())) != len(ENGINE_NAMES):
        raise ValueError("engine result paths must refer to three distinct files")

    result_digests = {
        engine: sha256_file(result_paths[engine]) for engine in ENGINE_NAMES
    }
    if len(set(result_digests.values())) != len(ENGINE_NAMES):
        raise ValueError(
            "engine result artifacts must have distinct SHA-256 digests; "
            "reusing or copying one raw result is not independent evidence"
        )

    evidence_ids: dict[str, str] = {}
    for engine in ENGINE_NAMES:
        versions = {row.version for row in results[engine]}
        if "" in versions:
            raise ValueError(f"{engine}: every result row needs a non-empty evidence id")
        if len(versions) != 1:
            raise ValueError(
                f"{engine}: result rows contain inconsistent evidence ids: "
                f"{sorted(versions)!r}"
            )
        evidence_ids[engine] = next(iter(versions))
    if len(set(evidence_ids.values())) != len(ENGINE_NAMES):
        raise ValueError("each engine must have a distinct evidence id")
    return evidence_ids


def _engine_summary(path: pathlib.Path, rows: Sequence[EvalRow]) -> dict[str, Any]:
    passed = sum(row.status == "OK" for row in rows)
    return {
        "result_path": str(path),
        "result_sha256": sha256_file(path),
        "evidence_id": rows[0].version,
        "passed": passed,
        "failed": len(rows) - passed,
    }


def exact_mcnemar_two_sided(left_only: int, right_only: int) -> float:
    """Return the exact two-sided McNemar p-value for paired binary outcomes."""
    if left_only < 0 or right_only < 0:
        raise ValueError("McNemar discordant counts must be non-negative")
    discordant = left_only + right_only
    if discordant == 0:
        return 1.0
    lower_tail = sum(
        math.comb(discordant, index)
        for index in range(min(left_only, right_only) + 1)
    )
    probability = 2 * Fraction(lower_tail, 1 << discordant)
    return min(1.0, float(probability))


def _paired_diagnostics(
    cases: Sequence[CorpusCase],
    results: dict[str, Sequence[EvalRow]],
    excluded_categories: set[str] | None = None,
) -> dict[str, Any]:
    excluded = excluded_categories or set()
    indices = [
        case.index
        for case in cases
        if (case.category or "uncategorized") not in excluded
    ]
    comparisons: dict[str, Any] = {}
    for left, right in (
        ("base_mozc", "hazkey"),
        ("base_mozc", "mozkey"),
        ("hazkey", "mozkey"),
    ):
        both_pass = left_only = right_only = both_fail = 0
        for index in indices:
            left_passed = results[left][index].status == "OK"
            right_passed = results[right][index].status == "OK"
            if left_passed and right_passed:
                both_pass += 1
            elif left_passed:
                left_only += 1
            elif right_passed:
                right_only += 1
            else:
                both_fail += 1
        case_count = len(indices)
        left_passed = both_pass + left_only
        right_passed = both_pass + right_only
        p_value = exact_mcnemar_two_sided(left_only, right_only)
        comparisons[f"{left}_vs_{right}"] = {
            "left_engine": left,
            "right_engine": right,
            "both_pass": both_pass,
            "left_only": left_only,
            "right_only": right_only,
            "both_fail": both_fail,
            "left_passed": left_passed,
            "right_passed": right_passed,
            "left_pass_rate": left_passed / case_count if case_count else 0.0,
            "right_pass_rate": right_passed / case_count if case_count else 0.0,
            "right_minus_left_percentage_points": (
                100.0 * (right_passed - left_passed) / case_count
                if case_count
                else 0.0
            ),
            "exact_mcnemar_two_sided_p": p_value,
            "significant_at_0_05": p_value < 0.05,
        }

    oracle_required = regressions = mozkey_improvements = all_failed = 0
    for index in indices:
        base_passed = results["base_mozc"][index].status == "OK"
        hazkey_passed = results["hazkey"][index].status == "OK"
        mozkey_passed = results["mozkey"][index].status == "OK"
        required = base_passed or hazkey_passed
        if required:
            oracle_required += 1
            if not mozkey_passed:
                regressions += 1
        elif mozkey_passed:
            mozkey_improvements += 1
        else:
            all_failed += 1

    return {
        "cases": len(indices),
        "excluded_categories": sorted(excluded),
        "strict_union": {
            "oracle_required": oracle_required,
            "regressions": regressions,
            "mozkey_improvements": mozkey_improvements,
            "all_failed": all_failed,
        },
        "comparisons": comparisons,
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
    validate_independent_evidence(result_paths, results)

    comparison_rows: list[dict[str, str]] = []
    regressions = 0
    oracle_required = 0
    mozkey_improvements = 0
    all_failed = 0
    categories: dict[str, dict[str, Any]] = {}

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

        category = case.category or "uncategorized"
        category_summary = categories.setdefault(
            category,
            {
                "cases": 0,
                "oracle_required": 0,
                "regressions": 0,
                "mozkey_improvements": 0,
                "all_failed": 0,
                "engines": {
                    engine: {"passed": 0, "failed": 0}
                    for engine in ENGINE_NAMES
                },
            },
        )
        category_summary["cases"] += 1
        if required:
            category_summary["oracle_required"] += 1
        if classification == "regression":
            category_summary["regressions"] += 1
        elif classification == "mozkey_improvement":
            category_summary["mozkey_improvements"] += 1
        elif classification == "all_failed":
            category_summary["all_failed"] += 1
        for engine, row in (
            ("base_mozc", base),
            ("hazkey", hazkey),
            ("mozkey", mozkey),
        ):
            outcome = "passed" if row.status == "OK" else "failed"
            category_summary["engines"][engine][outcome] += 1

        comparison_rows.append(
            {
                "case_index": str(case.index + 1),
                "case_id": case.case_id,
                "category": category,
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
        "categories": categories,
        "paired_diagnostics": {
            "method": {
                "name": "exact_two_sided_mcnemar",
                "alpha": 0.05,
                "interpretation_limit": (
                    "A p-value at or above alpha means this artifact does not "
                    "show a statistically significant paired difference. It "
                    "does not prove equivalence or non-inferiority."
                ),
            },
            "scopes": {
                "all_cases": _paired_diagnostics(cases, results),
                **(
                    {
                        "excluding_protected": _paired_diagnostics(
                            cases, results, {"protected"}
                        )
                    }
                    if "protected" in categories
                    else {}
                ),
            },
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


def _write_quality_regression_corpus(
    cases: Sequence[CorpusCase], output: pathlib.Path
) -> None:
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# label\tkey\tvalue\tcommand\n")
        for case in cases:
            stream.write(
                f"{case.case_id}\t{case.key}\t{case.expected}\t{case.mode}\n"
            )


def _stamp_evidence(
    path: pathlib.Path, evidence_id: str, cases: Sequence[CorpusCase]
) -> None:
    stamped: list[str] = []
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            row = parse_row(line)
            if row is None:
                if line.strip():
                    raise ValueError(f"invalid evaluator output at {path}:{line_number}")
                continue
            columns = row.raw.split("\t")
            if len(stamped) >= len(cases):
                raise ValueError(f"evaluator produced too many rows: {path}")
            columns[0] = "OK:" if cases[len(stamped)].accepts(row.output) else "FAILED:"
            columns[5] = evidence_id
            stamped.append("\t".join(columns))
    if len(stamped) != len(cases):
        raise ValueError(
            f"evaluator produced {len(stamped)} rows for {len(cases)} corpus cases"
        )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        dir=path.parent,
        delete=False,
    ) as stream:
        temporary = pathlib.Path(stream.name)
        for line in stamped:
            stream.write(line + "\n")
    temporary.replace(path)


def evaluate_engine(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cases = load_corpus(args.corpus)
    evidence_id = (
        f"{args.evidence_id};"
        f"quality_main_sha256={sha256_file(args.quality_main)};"
        f"data_sha256={sha256_file(args.data_file)}"
    )
    with tempfile.TemporaryDirectory(prefix="mozkey-fixed-corpus-evaluate-") as temp:
        test_file = args.corpus
        if any(case.category is not None for case in cases):
            test_file = pathlib.Path(temp) / "quality-regression.tsv"
            _write_quality_regression_corpus(cases, test_file)
        command = [
            str(args.quality_main),
            f"--test_files={test_file}",
            f"--data_file={args.data_file}",
            f"--data_type={args.data_type}",
            f"--engine_type={args.engine_type}",
            f"--output={args.output}",
        ]
        subprocess.run(command, check=True)
    _stamp_evidence(args.output, evidence_id, cases)
    print(f"raw result: {args.output}")
    print(f"evidence id: {evidence_id}")
    return 0


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def normalize_ab_probe_results(args: argparse.Namespace) -> int:
    cases = load_corpus(args.corpus)
    if not all(case.category is not None for case in cases):
        raise ValueError("normalize-ab-probe requires an ABProbe corpus header")

    records: list[dict[str, Any]] = []
    with args.results.open("r", encoding="utf-8", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank ABProbe result row at {args.results}:{line_number}")
            try:
                record = json.loads(
                    line, object_pairs_hook=_json_object_without_duplicate_keys
                )
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(
                    f"invalid ABProbe JSON at {args.results}:{line_number}: {error}"
                ) from error
            if not isinstance(record, dict):
                raise ValueError(
                    f"ABProbe row must be an object at {args.results}:{line_number}"
                )
            records.append(record)

    if len(records) != len(cases):
        raise ValueError(
            f"ABProbe result row count {len(records)} does not match "
            f"corpus row count {len(cases)}"
        )

    corpus_digest = sha256_file(args.corpus)
    source_refs: set[str] = set()
    resource_fingerprints: set[str] = set()
    backend_versions: set[str] = set()
    acquisition_policies: set[tuple[int, int, int]] = set()
    normalized: list[tuple[str, str]] = []
    for case, record in zip(cases, records, strict=True):
        if record.get("schema") != AB_PROBE_SCHEMA:
            raise ValueError(
                f"{case.case_id}: expected schema {AB_PROBE_SCHEMA!r}, "
                f"got {record.get('schema')!r}"
            )
        if record.get("converter_backend") != args.expected_converter_backend:
            raise ValueError(
                f"{case.case_id}: converter backend mismatch: "
                f"{record.get('converter_backend')!r}"
            )
        if args.expected_backend and record.get("backend") != args.expected_backend:
            raise ValueError(
                f"{case.case_id}: backend mismatch: {record.get('backend')!r}"
            )
        if record.get("id") != case.case_id or record.get("reading") != case.key:
            raise ValueError(f"{case.case_id}: ABProbe result order or identity mismatch")
        if record.get("category") != case.category:
            raise ValueError(f"{case.case_id}: ABProbe category mismatch")
        corpus = record.get("corpus")
        if not isinstance(corpus, dict) or corpus.get("cases") != len(cases):
            raise ValueError(f"{case.case_id}: ABProbe corpus size mismatch")
        if corpus.get("sha256") != f"sha256:{corpus_digest}":
            raise ValueError(f"{case.case_id}: ABProbe corpus digest mismatch")

        candidates = record.get("candidates")
        if not isinstance(candidates, list) or not all(
            isinstance(candidate, str) for candidate in candidates
        ):
            raise ValueError(f"{case.case_id}: candidates must be a string array")
        top_k = record.get("top_k")
        measurement = record.get("measurement")
        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or top_k < 1
            or not isinstance(measurement, dict)
            or not isinstance(measurement.get("warmups"), int)
            or isinstance(measurement.get("warmups"), bool)
            or measurement["warmups"] < 0
            or not isinstance(measurement.get("iterations"), int)
            or isinstance(measurement.get("iterations"), bool)
            or measurement["iterations"] < 1
            or len(candidates) > top_k
        ):
            raise ValueError(f"{case.case_id}: invalid ABProbe acquisition policy")
        acquisition_policies.add(
            (top_k, measurement["warmups"], measurement["iterations"])
        )
        actual = candidates[0] if candidates else ""
        status = "OK:" if case.accepts(actual) else "FAILED:"

        source_ref = record.get("source_ref")
        resource = record.get("resource")
        backend_version = record.get("backend_version")
        if not isinstance(source_ref, str) or not source_ref:
            raise ValueError(f"{case.case_id}: missing source_ref")
        if (
            not isinstance(resource, dict)
            or not isinstance(resource.get("fingerprint"), str)
            or not resource["fingerprint"]
        ):
            raise ValueError(f"{case.case_id}: missing resource fingerprint")
        if not isinstance(backend_version, str) or not backend_version:
            raise ValueError(f"{case.case_id}: missing backend_version")
        source_refs.add(source_ref)
        resource_fingerprints.add(resource["fingerprint"])
        backend_versions.add(backend_version)
        normalized.append((status, actual))

    if (
        len(source_refs) != 1
        or len(resource_fingerprints) != 1
        or len(backend_versions) != 1
        or len(acquisition_policies) != 1
    ):
        raise ValueError("ABProbe artifact identity changes between rows")
    top_k, warmups, iterations = next(iter(acquisition_policies))
    evidence_id = (
        f"{args.evidence_id};"
        f"artifact_sha256={sha256_file(args.results)};"
        f"source_ref={next(iter(source_refs))};"
        f"resource={next(iter(resource_fingerprints))};"
        f"backend_version={next(iter(backend_versions))};"
        f"top_k={top_k};warmups={warmups};iterations={iterations}"
    )
    if any(character in evidence_id for character in "\t\r\n"):
        raise ValueError("evidence id components must not contain TSV delimiters")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        for case, (status, actual) in zip(cases, normalized, strict=True):
            stream.write(
                "\t".join(
                    [
                        status,
                        case.key,
                        actual,
                        case.mode,
                        case.expected,
                        evidence_id,
                    ]
                )
                + "\n"
            )
    print(f"normalized ABProbe result: {args.output}")
    print(f"evidence id: {evidence_id}")
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
    evaluate.add_argument(
        "--evidence-id",
        required=True,
        help="Stable engine/run label; executable and data digests are appended.",
    )
    evaluate.add_argument("--data-type", default="oss")
    evaluate.add_argument("--engine-type", default="desktop")
    evaluate.set_defaults(func=evaluate_engine)

    normalize = subparsers.add_parser(
        "normalize-ab-probe",
        help="Normalize one immutable Hazkey ABProbe v3 JSONL artifact.",
    )
    normalize.add_argument("--corpus", type=pathlib.Path, required=True)
    normalize.add_argument("--results", type=pathlib.Path, required=True)
    normalize.add_argument("--output", type=pathlib.Path, required=True)
    normalize.add_argument("--evidence-id", required=True)
    normalize.add_argument(
        "--expected-converter-backend",
        choices=("hazkey", "mozc"),
        required=True,
    )
    normalize.add_argument("--expected-backend")
    normalize.set_defaults(func=normalize_ab_probe_results)

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
