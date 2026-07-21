#!/usr/bin/env python3
"""Validate the local, rule-generation typing-correction corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "src" / "data" / "typing_correction"
CORPUS_VERSION = "v1"
GOLD_PATH = CORPUS_ROOT / "roman_gold.tsv"
NEGATIVE_PATH = CORPUS_ROOT / "roman_negative.tsv"
OVERRIDES_PATH = CORPUS_ROOT / "roman_rule_overrides.tsv"
KANA_GOLD_PATH = CORPUS_ROOT / "kana_gold.tsv"
KANA_NEGATIVE_PATH = CORPUS_ROOT / "kana_negative.tsv"
KANA_LAYOUT_PATH = CORPUS_ROOT / "kana_jis_layout.tsv"
KANA_MODIFIER_PATH = CORPUS_ROOT / "kana_modifier_rules.tsv"
ROMAN_HOLDOUT_PATH = CORPUS_ROOT / "roman_holdout.tsv"
KANA_HOLDOUT_PATH = CORPUS_ROOT / "kana_holdout.tsv"

GOLD_COLUMNS = (
    "case_id",
    "intended_raw",
    "typed_raw",
    "intended_reading",
    "operation",
    "confidence",
    "behavior",
    "note",
)
NEGATIVE_COLUMNS = ("case_id", "typed_raw", "expected_reading", "reason")
OVERRIDE_COLUMNS = (
    "rule_id",
    "wrong",
    "corrected",
    "operation",
    "cost",
    "auto_applicable",
    "scope",
    "note",
)
KANA_GOLD_COLUMNS = (
    "case_id",
    "typed_key_codes",
    "typed_key_strings",
    "corrected_key_codes",
    "corrected_key_strings",
    "operation",
    "confidence",
    "behavior",
    "note",
)
KANA_NEGATIVE_COLUMNS = (
    "case_id",
    "typed_key_codes",
    "typed_key_strings",
    "reason",
)
KANA_LAYOUT_COLUMNS = ("key_code", "key_string", "row")
KANA_MODIFIER_COLUMNS = (
    "rule_id",
    "wrong_key_code",
    "wrong_key_string",
    "corrected_key_code",
    "corrected_key_string",
    "operation",
    "cost",
    "behavior",
    "note",
)
ROMAN_HOLDOUT_COLUMNS = (
    "case_id",
    "typed_raw",
    "corrected_raw",
    "operation",
    "behavior",
    "note",
)
KANA_HOLDOUT_COLUMNS = (
    "case_id",
    "typed_key_codes",
    "typed_key_strings",
    "corrected_key_codes",
    "corrected_key_strings",
    "operation",
    "behavior",
    "note",
)

OPERATIONS = {"transpose", "omission", "duplicate", "replacement", "neighbor"}
CONFIDENCES = {"high", "medium", "low"}
BEHAVIORS = {"auto", "suggest", "test_only"}
RULE_ID = re.compile(r"^[A-Z][A-Z0-9-]+$")
ASCII_RAW = re.compile(r"^[a-z]+$")
QWERTY_NEIGHBORS = {
    "q": "was",
    "w": "qesa",
    "e": "wsdr",
    "r": "edft",
    "t": "rfgy",
    "y": "tghu",
    "u": "yhji",
    "i": "ujko",
    "o": "iklp",
    "p": "ol",
    "a": "qwsz",
    "s": "awedxz",
    "d": "serfcx",
    "f": "drtgvc",
    "g": "ftyhbv",
    "h": "gyujnb",
    "j": "huikmn",
    "k": "jiolm",
    "l": "kop",
    "z": "asx",
    "x": "zsdc",
    "c": "xdfv",
    "v": "cfgb",
    "b": "vghn",
    "n": "bhjm",
    "m": "njk",
}


class CorpusError(ValueError):
    """The corpus is malformed or internally inconsistent."""


@dataclass(frozen=True)
class GoldCase:
    row: dict[str, str]


@dataclass(frozen=True)
class NegativeCase:
    row: dict[str, str]


@dataclass(frozen=True)
class OverrideRule:
    row: dict[str, str]


@dataclass(frozen=True)
class KanaGoldCase:
    row: dict[str, str]


@dataclass(frozen=True)
class KanaNegativeCase:
    row: dict[str, str]


@dataclass(frozen=True)
class RomanHoldoutCase:
    row: dict[str, str]


@dataclass(frozen=True)
class KanaHoldoutCase:
    row: dict[str, str]


@dataclass(frozen=True)
class Corpus:
    gold: tuple[GoldCase, ...]
    negative: tuple[NegativeCase, ...]
    overrides: tuple[OverrideRule, ...]
    kana_gold: tuple[KanaGoldCase, ...] = ()
    kana_negative: tuple[KanaNegativeCase, ...] = ()
    roman_holdout: tuple[RomanHoldoutCase, ...] = ()
    kana_holdout: tuple[KanaHoldoutCase, ...] = ()


def _read_rows(path: pathlib.Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CorpusError(f"cannot read UTF-8 corpus file {path}: {error}") from error
    if not text.endswith("\n"):
        raise CorpusError(f"corpus file must end with a newline: {path}")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    if tuple(reader.fieldnames or ()) != columns:
        raise CorpusError(
            f"unexpected columns in {path}: {reader.fieldnames!r}; expected {columns!r}"
        )
    rows: list[dict[str, str]] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise CorpusError(f"malformed TSV row at {path}:{line_number}")
        normalized = {key: value for key, value in row.items() if key is not None}
        if any(value == "" for value in normalized.values() if value is not None):
            raise CorpusError(f"empty field at {path}:{line_number}")
        rows.append(normalized)
    if not rows:
        raise CorpusError(f"corpus file has no cases: {path}")
    return rows


def _require_ascii_raw(value: str, label: str) -> None:
    if not ASCII_RAW.fullmatch(value):
        raise CorpusError(f"{label} must contain lower-case ASCII letters only: {value!r}")
    if not 2 <= len(value) <= 64:
        raise CorpusError(f"{label} must be between 2 and 64 bytes: {value!r}")


def _is_one_insertion(longer: str, shorter: str) -> bool:
    if len(longer) != len(shorter) + 1:
        return False
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return True
    return False


def _is_one_duplicate(typed: str, intended: str) -> bool:
    if len(typed) != len(intended) + 1:
        return False
    return any(typed[:index] + typed[index + 1 :] == intended for index in range(len(typed)))


def _is_one_neighbor(intended: str, typed: str) -> bool:
    if len(intended) != len(typed):
        return False
    mismatches = [
        (expected, actual)
        for expected, actual in zip(intended, typed)
        if expected != actual
    ]
    return (
        len(mismatches) == 1
        and mismatches[0][1] in QWERTY_NEIGHBORS.get(mismatches[0][0], "")
    )


def _is_one_duplicate_trace(
    typed_codes: str,
    typed_strings: str,
    corrected_codes: str,
    corrected_strings: str,
) -> bool:
    if len(typed_codes) != len(corrected_codes) + 1:
        return False
    return any(
        typed_codes[:index] + typed_codes[index + 1 :] == corrected_codes
        and typed_strings[:index] + typed_strings[index + 1 :] == corrected_strings
        for index in range(len(typed_codes))
    )


def _is_adjacent_transpose(intended: str, typed: str) -> bool:
    if len(intended) != len(typed):
        return False
    for index in range(len(intended) - 1):
        if (
            intended[:index] == typed[:index]
            and intended[index] == typed[index + 1]
            and intended[index + 1] == typed[index]
            and intended[index + 2 :] == typed[index + 2 :]
        ):
            return True
    return False


def _validate_gold(rows: Iterable[dict[str, str]]) -> tuple[GoldCase, ...]:
    result: list[GoldCase] = []
    seen: set[str] = set()
    reading_by_typed_raw: dict[str, str] = {}
    for row in rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"R[0-9]{6}", case_id) or case_id in seen:
            raise CorpusError(f"invalid or duplicate Gold case_id: {case_id}")
        seen.add(case_id)
        _require_ascii_raw(row["intended_raw"], f"{case_id}.intended_raw")
        _require_ascii_raw(row["typed_raw"], f"{case_id}.typed_raw")
        if row["intended_raw"] == row["typed_raw"]:
            raise CorpusError(f"Gold case must change raw input: {case_id}")
        if row["operation"] not in OPERATIONS:
            raise CorpusError(f"unknown Gold operation: {case_id}")
        if row["confidence"] not in CONFIDENCES:
            raise CorpusError(f"unknown Gold confidence: {case_id}")
        if row["behavior"] not in BEHAVIORS:
            raise CorpusError(f"unknown Gold behavior: {case_id}")
        if row["behavior"] == "auto" and row["confidence"] != "high":
            raise CorpusError(f"automatic Gold cases must be high confidence: {case_id}")
        operation = row["operation"]
        intended = row["intended_raw"]
        typed = row["typed_raw"]
        shape_ok = {
            "transpose": _is_adjacent_transpose(intended, typed),
            "omission": _is_one_insertion(intended, typed),
            "duplicate": _is_one_duplicate(typed, intended),
            "replacement": len(intended) == len(typed)
            and sum(lhs != rhs for lhs, rhs in zip(intended, typed)) == 1,
            "neighbor": _is_one_neighbor(intended, typed),
        }[operation]
        if not shape_ok:
            raise CorpusError(f"Gold raw pair does not match {operation}: {case_id}")
        previous_reading = reading_by_typed_raw.setdefault(
            row["typed_raw"], row["intended_reading"]
        )
        if previous_reading != row["intended_reading"]:
            raise CorpusError(
                "contradictory intended readings for typed_raw: "
                f"{row['typed_raw']}"
            )
        result.append(GoldCase(row))
    return tuple(result)


def _validate_negative(rows: Iterable[dict[str, str]]) -> tuple[NegativeCase, ...]:
    result: list[NegativeCase] = []
    seen: set[str] = set()
    expected_reading_by_raw: dict[str, str] = {}
    for row in rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"N[0-9]{6}", case_id) or case_id in seen:
            raise CorpusError(f"invalid or duplicate Negative case_id: {case_id}")
        seen.add(case_id)
        _require_ascii_raw(row["typed_raw"], f"{case_id}.typed_raw")
        if not row["expected_reading"] or not row["reason"]:
            raise CorpusError(f"Negative case has an empty expectation: {case_id}")
        previous_reading = expected_reading_by_raw.setdefault(
            row["typed_raw"], row["expected_reading"]
        )
        if previous_reading != row["expected_reading"]:
            raise CorpusError(
                "contradictory negative readings for typed_raw: "
                f"{row['typed_raw']}"
            )
        result.append(NegativeCase(row))
    return tuple(result)


def _validate_overrides(rows: Iterable[dict[str, str]]) -> tuple[OverrideRule, ...]:
    result: list[OverrideRule] = []
    seen: set[str] = set()
    for row in rows:
        rule_id = row["rule_id"]
        if not RULE_ID.fullmatch(rule_id) or rule_id in seen:
            raise CorpusError(f"invalid or duplicate rule_id: {rule_id}")
        seen.add(rule_id)
        _require_ascii_raw(row["wrong"], f"{rule_id}.wrong")
        _require_ascii_raw(row["corrected"], f"{rule_id}.corrected")
        if row["wrong"] == row["corrected"]:
            raise CorpusError(f"override must change raw input: {rule_id}")
        if row["operation"] not in OPERATIONS:
            raise CorpusError(f"unknown override operation: {rule_id}")
        try:
            cost = int(row["cost"])
        except ValueError as error:
            raise CorpusError(f"override cost is not an integer: {rule_id}") from error
        if not 1 <= cost <= 300:
            raise CorpusError(f"override cost must be in [1, 300]: {rule_id}")
        if row["auto_applicable"] not in {"true", "false"}:
            raise CorpusError(f"override auto_applicable must be boolean: {rule_id}")
        if row["scope"] not in {"local", "full"}:
            raise CorpusError(f"override scope must be local or full: {rule_id}")
        result.append(OverrideRule(row))
    return tuple(result)


def _validate_optional_kana(
    root: pathlib.Path,
) -> tuple[tuple[KanaGoldCase, ...], tuple[KanaNegativeCase, ...]]:
    gold_path = root / KANA_GOLD_PATH.name
    negative_path = root / KANA_NEGATIVE_PATH.name
    layout_path = root / KANA_LAYOUT_PATH.name
    modifier_path = root / KANA_MODIFIER_PATH.name
    if not gold_path.exists() and not negative_path.exists():
        return (), ()
    if (
        not gold_path.exists()
        or not negative_path.exists()
        or not layout_path.exists()
        or not modifier_path.exists()
    ):
        raise CorpusError("kana Gold and Negative files must be provided together")
    gold_rows = _read_rows(gold_path, KANA_GOLD_COLUMNS)
    negative_rows = _read_rows(negative_path, KANA_NEGATIVE_COLUMNS)
    layout_rows = _read_rows(layout_path, KANA_LAYOUT_COLUMNS)
    modifier_rows = _read_rows(modifier_path, KANA_MODIFIER_COLUMNS)
    layout_codes: set[str] = set()
    for row in layout_rows:
        if len(row["key_code"]) != 1 or ord(row["key_code"]) > 0x7F:
            raise CorpusError("kana layout key_code must be one ASCII character")
        if row["key_code"] in layout_codes:
            raise CorpusError(f"duplicate kana layout key_code: {row['key_code']}")
        layout_codes.add(row["key_code"])
        if row["row"] not in {"top", "qwerty", "home", "bottom"}:
            raise CorpusError(f"unknown kana layout row: {row['row']}")
    seen: set[str] = set()
    gold: list[KanaGoldCase] = []
    for row in gold_rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"K[0-9]{6}", case_id) or case_id in seen:
            raise CorpusError(f"invalid or duplicate kana Gold case_id: {case_id}")
        seen.add(case_id)
        if row["operation"] not in {"neighbor", "kana_modifier"}:
            raise CorpusError(f"unknown kana Gold operation: {case_id}")
        if row["confidence"] not in CONFIDENCES:
            raise CorpusError(f"unknown kana Gold confidence: {case_id}")
        if row["behavior"] not in {"auto", "suggest", "test_only"}:
            raise CorpusError(f"unknown kana Gold behavior: {case_id}")
        if row["behavior"] == "auto" and row["confidence"] != "high":
            raise CorpusError(f"automatic kana Gold cases must be high confidence: {case_id}")
        if len(row["typed_key_codes"]) != len(row["typed_key_strings"]):
            raise CorpusError(f"kana typed key/code count mismatch: {case_id}")
        if len(row["corrected_key_codes"]) != len(row["corrected_key_strings"]):
            raise CorpusError(f"kana corrected key/code count mismatch: {case_id}")
        if len(row["typed_key_codes"]) != 1 or len(row["corrected_key_codes"]) != 1:
            raise CorpusError(
                f"kana Gold currently supports exactly one key event: {case_id}"
            )
        if row["typed_key_codes"] == row["corrected_key_codes"] and row["typed_key_strings"] == row["corrected_key_strings"]:
            raise CorpusError(f"kana Gold case must change the key trace: {case_id}")
        gold.append(KanaGoldCase(row))
    negative: list[KanaNegativeCase] = []
    for row in negative_rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"KN[0-9]{6}", case_id) or case_id in seen:
            raise CorpusError(f"invalid or duplicate kana Negative case_id: {case_id}")
        seen.add(case_id)
        if len(row["typed_key_codes"]) != len(row["typed_key_strings"]):
            raise CorpusError(f"kana negative key/code count mismatch: {case_id}")
        if not row["reason"]:
            raise CorpusError(f"kana Negative case has an empty reason: {case_id}")
        negative.append(KanaNegativeCase(row))
    gold_ids = {case.row["case_id"] for case in gold}
    modifier_ids: set[str] = set()
    for row in modifier_rows:
        rule_id = row["rule_id"]
        if rule_id in modifier_ids or rule_id not in gold_ids:
            raise CorpusError(f"kana modifier rule must reference one Gold case: {rule_id}")
        modifier_ids.add(rule_id)
        if row["operation"] != "kana_modifier":
            raise CorpusError(f"unknown kana modifier operation: {rule_id}")
        try:
            cost = int(row["cost"])
        except ValueError as error:
            raise CorpusError(f"kana modifier cost is not an integer: {rule_id}") from error
        if not 1 <= cost <= 300:
            raise CorpusError(f"kana modifier cost must be in [1, 300]: {rule_id}")
        if row["behavior"] not in {"auto", "suggest", "test_only"}:
            raise CorpusError(f"unknown kana modifier behavior: {rule_id}")
    return tuple(gold), tuple(negative)


def _validate_optional_holdout(
    root: pathlib.Path,
    gold: tuple[GoldCase, ...],
    negative: tuple[NegativeCase, ...],
    kana_gold: tuple[KanaGoldCase, ...],
    kana_negative: tuple[KanaNegativeCase, ...],
) -> tuple[tuple[RomanHoldoutCase, ...], tuple[KanaHoldoutCase, ...]]:
    roman_path = root / ROMAN_HOLDOUT_PATH.name
    kana_path = root / KANA_HOLDOUT_PATH.name
    if not roman_path.exists() and not kana_path.exists():
        return (), ()
    if not roman_path.exists() or not kana_path.exists():
        raise CorpusError("Roman and kana holdout files must be provided together")

    roman_rows = _read_rows(roman_path, ROMAN_HOLDOUT_COLUMNS)
    kana_rows = _read_rows(kana_path, KANA_HOLDOUT_COLUMNS)

    generated_typed_raw = {
        case.row["typed_raw"]
        for case in gold
    } | {case.row["typed_raw"] for case in negative}
    roman_result: list[RomanHoldoutCase] = []
    seen_roman: set[str] = set()
    for row in roman_rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"H[0-9]{6}", case_id) or case_id in seen_roman:
            raise CorpusError(f"invalid or duplicate Roman holdout case_id: {case_id}")
        seen_roman.add(case_id)
        _require_ascii_raw(row["typed_raw"], f"{case_id}.typed_raw")
        _require_ascii_raw(row["corrected_raw"], f"{case_id}.corrected_raw")
        if row["typed_raw"] == row["corrected_raw"]:
            raise CorpusError(f"Roman holdout must change raw input: {case_id}")
        if row["typed_raw"] in generated_typed_raw:
            raise CorpusError(
                f"Roman holdout overlaps generated corpus input: {case_id}"
            )
        if row["operation"] not in {"transpose", "duplicate", "neighbor"}:
            raise CorpusError(f"unsupported Roman holdout operation: {case_id}")
        if row["behavior"] != "test_only":
            raise CorpusError(f"Roman holdout must be test_only: {case_id}")
        operation = row["operation"]
        shape_ok = {
            "transpose": _is_adjacent_transpose(
                row["corrected_raw"], row["typed_raw"]
            ),
            "duplicate": _is_one_duplicate(
                row["typed_raw"], row["corrected_raw"]
            ),
            "neighbor": len(row["typed_raw"]) == len(row["corrected_raw"]),
        }[operation]
        if not shape_ok:
            raise CorpusError(
                f"Roman holdout raw pair does not match {operation}: {case_id}"
            )
        roman_result.append(RomanHoldoutCase(row))

    generated_kana_inputs = {
        case.row["typed_key_codes"]
        for case in kana_gold
    } | {case.row["typed_key_codes"] for case in kana_negative}
    kana_result: list[KanaHoldoutCase] = []
    seen_kana: set[str] = set()
    for row in kana_rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"KH[0-9]{6}", case_id) or case_id in seen_kana:
            raise CorpusError(f"invalid or duplicate kana holdout case_id: {case_id}")
        seen_kana.add(case_id)
        if len(row["typed_key_codes"]) != len(row["typed_key_strings"]):
            raise CorpusError(f"kana holdout key/code count mismatch: {case_id}")
        if len(row["corrected_key_codes"]) != len(row["corrected_key_strings"]):
            raise CorpusError(f"kana holdout corrected key/code count mismatch: {case_id}")
        if len(row["typed_key_codes"]) < 2:
            raise CorpusError(f"kana holdout must contain a multi-key trace: {case_id}")
        if row["typed_key_codes"] == row["corrected_key_codes"]:
            raise CorpusError(f"kana holdout must change the key trace: {case_id}")
        if row["typed_key_codes"] in generated_kana_inputs:
            raise CorpusError(
                f"kana holdout overlaps generated corpus input: {case_id}"
            )
        if row["operation"] != "duplicate":
            raise CorpusError(f"unsupported kana holdout operation: {case_id}")
        if row["behavior"] != "test_only":
            raise CorpusError(f"kana holdout must be test_only: {case_id}")
        if not _is_one_duplicate_trace(
            row["typed_key_codes"],
            row["typed_key_strings"],
            row["corrected_key_codes"],
            row["corrected_key_strings"],
        ):
            raise CorpusError(f"kana holdout is not one duplicate removal: {case_id}")
        kana_result.append(KanaHoldoutCase(row))

    return tuple(roman_result), tuple(kana_result)


def load_corpus(root: pathlib.Path = CORPUS_ROOT) -> Corpus:
    gold = _validate_gold(_read_rows(root / GOLD_PATH.name, GOLD_COLUMNS))
    negative = _validate_negative(
        _read_rows(root / NEGATIVE_PATH.name, NEGATIVE_COLUMNS)
    )
    overrides = _validate_overrides(
        _read_rows(root / OVERRIDES_PATH.name, OVERRIDE_COLUMNS)
    )
    kana_gold, kana_negative = _validate_optional_kana(root)
    roman_holdout, kana_holdout = _validate_optional_holdout(
        root, gold, negative, kana_gold, kana_negative
    )
    gold_readings = {case.row["typed_raw"]: case.row["intended_reading"] for case in gold}
    for case in negative:
        if case.row["typed_raw"] in gold_readings and case.row["expected_reading"] != gold_readings[case.row["typed_raw"]]:
            raise CorpusError(
                "Gold and Negative readings contradict for typed_raw: "
                f"{case.row['typed_raw']}"
            )
    ids = {case.row["case_id"] for case in gold}
    ids.update(case.row["case_id"] for case in negative)
    if len(ids) != len(gold) + len(negative):
        raise CorpusError("Gold and Negative case IDs must be globally unique")
    return Corpus(
        gold,
        negative,
        overrides,
        kana_gold,
        kana_negative,
        roman_holdout,
        kana_holdout,
    )


def corpus_digest(root: pathlib.Path = CORPUS_ROOT) -> str:
    # Holdout rows are intentionally excluded: they exercise generic
    # candidate generation without changing the production rule artifact.
    digest = hashlib.sha256()
    paths = [GOLD_PATH, NEGATIVE_PATH, OVERRIDES_PATH]
    for optional_path in (
        KANA_GOLD_PATH,
        KANA_NEGATIVE_PATH,
        KANA_LAYOUT_PATH,
        KANA_MODIFIER_PATH,
    ):
        if (root / optional_path.name).exists():
            paths.append(optional_path)
    for path in paths:
        relative = path.relative_to(CORPUS_ROOT).as_posix().encode("utf-8")
        digest.update(relative + b"\0")
        digest.update((root / path.name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=pathlib.Path, default=CORPUS_ROOT)
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.corpus_root)
        digest = corpus_digest(args.corpus_root)
    except CorpusError as error:
        print(f"ERROR: {error}")
        return 1
    print(
        f"ok: version={CORPUS_VERSION} gold={len(corpus.gold)} "
        f"negative={len(corpus.negative)} overrides={len(corpus.overrides)} "
        f"kana_gold={len(corpus.kana_gold)} "
        f"kana_negative={len(corpus.kana_negative)} "
        f"roman_holdout={len(corpus.roman_holdout)} "
        f"kana_holdout={len(corpus.kana_holdout)} "
        f"sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
