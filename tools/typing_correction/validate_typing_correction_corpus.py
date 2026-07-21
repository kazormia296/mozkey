#!/usr/bin/env python3
"""Validate the local, rule-generation typing-correction corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "src" / "data" / "typing_correction"
CORPUS_SCHEMA_PATH = CORPUS_ROOT / "corpus_schema.json"


def _load_schema(path: pathlib.Path = CORPUS_SCHEMA_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot load corpus schema {path}: {error}") from error


SCHEMA = _load_schema()
CORPUS_VERSION = SCHEMA["version"]
GOLD_PATH = CORPUS_ROOT / "roman_gold.tsv"
NEGATIVE_PATH = CORPUS_ROOT / "roman_negative.tsv"
OVERRIDES_PATH = CORPUS_ROOT / "roman_rule_overrides.tsv"
KANA_GOLD_PATH = CORPUS_ROOT / "kana_gold.tsv"
KANA_EVALUATION_PATH = CORPUS_ROOT / "kana_evaluation.tsv"
KANA_NEGATIVE_PATH = CORPUS_ROOT / "kana_negative.tsv"
KANA_LAYOUT_PATH = CORPUS_ROOT / "kana_jis_layout.tsv"
KANA_MODIFIER_PATH = CORPUS_ROOT / "kana_modifier_rules.tsv"
ROMAN_HOLDOUT_PATH = CORPUS_ROOT / "roman_holdout.tsv"
KANA_HOLDOUT_PATH = CORPUS_ROOT / "kana_holdout.tsv"

GOLD_COLUMNS = tuple(SCHEMA["roman_gold_columns"])
NEGATIVE_COLUMNS = tuple(SCHEMA["roman_negative_columns"])
OVERRIDE_COLUMNS = tuple(SCHEMA["roman_override_columns"])
KANA_GOLD_COLUMNS = tuple(SCHEMA["kana_gold_columns"])
KANA_EVALUATION_COLUMNS = tuple(SCHEMA["kana_evaluation_columns"])
KANA_NEGATIVE_COLUMNS = tuple(SCHEMA["kana_negative_columns"])
KANA_LAYOUT_COLUMNS = tuple(SCHEMA["kana_layout_columns"])
KANA_MODIFIER_COLUMNS = tuple(SCHEMA["kana_modifier_columns"])
ROMAN_HOLDOUT_COLUMNS = tuple(SCHEMA["roman_holdout_columns"])
KANA_HOLDOUT_COLUMNS = tuple(SCHEMA["kana_holdout_columns"])

ENUMS = SCHEMA["enums"]
OPERATIONS = set(ENUMS["roman_operations"])
KANA_OPERATIONS = set(ENUMS["kana_operations"])
ROMAN_HOLDOUT_OPERATIONS = set(ENUMS["roman_holdout_operations"])
KANA_HOLDOUT_OPERATIONS = set(ENUMS["kana_holdout_operations"])
CONFIDENCES = set(ENUMS["confidence"])
BEHAVIORS = set(ENUMS["behavior"])
NEGATIVE_RAW_POLICIES = set(ENUMS["negative_raw_policy"])
NEGATIVE_DISPLAY_POLICIES = set(ENUMS["negative_display_policy"])
NEGATIVE_AUTO_POLICIES = set(ENUMS["negative_auto_policy"])
OVERRIDE_SCOPES = set(ENUMS["override_scope"])
LAYOUT_ROWS = set(ENUMS["layout_row"])
RULE_ID = re.compile(r"^[A-Z][A-Z0-9-]+$")
ASCII_RAW = re.compile(r"^[a-z]+$")
NEGATIVE_RAW = re.compile(r"^[a-z0-9@:/._+?=-]+$")
STRATUM_FIELDS = tuple(SCHEMA["stratum_fields"]["required"])
STRATUM_KEYS = set(STRATUM_FIELDS)
STRATUM_SEPARATOR = SCHEMA["stratum_fields"]["separator"]
STRATUM_VALUES = {
    key: set(values)
    for key, values in SCHEMA["stratum_fields"]["values"].items()
}
EVALUATION_TARGETS = SCHEMA["evaluation_targets"]
NEGATIVE_POLICY_TARGETS = SCHEMA["negative_policy_targets"]
DATASET_CONSTRAINTS = SCHEMA["dataset_constraints"]
COVERAGE_MINIMUMS = SCHEMA["coverage_minimums"]
STRICT_NEGATIVE_COVERAGE = SCHEMA["strict_negative_coverage"]
RUNTIME_LIMITS = SCHEMA["runtime_limits"]
EXCLUSIVITY = SCHEMA["exclusivity"]
RELEASE_THRESHOLDS = SCHEMA["release_thresholds"]
MODE_POLICIES = SCHEMA["mode_policies"]
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
    kana_evaluation: tuple[KanaGoldCase, ...] = ()
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
    raw_bytes = len(value.encode("utf-8"))
    if not RUNTIME_LIMITS["min_raw_bytes"] <= raw_bytes <= RUNTIME_LIMITS["max_raw_bytes"]:
        raise CorpusError(
            f"{label} must be between {RUNTIME_LIMITS['min_raw_bytes']} and "
            f"{RUNTIME_LIMITS['max_raw_bytes']} bytes: {value!r}"
        )


def _require_negative_raw(value: str, label: str) -> None:
    if not NEGATIVE_RAW.fullmatch(value):
        raise CorpusError(
            f"{label} contains an unsupported raw character: {value!r}"
        )
    raw_bytes = len(value.encode("utf-8"))
    if not RUNTIME_LIMITS["min_raw_bytes"] <= raw_bytes <= RUNTIME_LIMITS["max_raw_bytes"]:
        raise CorpusError(
            f"{label} must be between {RUNTIME_LIMITS['min_raw_bytes']} and "
            f"{RUNTIME_LIMITS['max_raw_bytes']} bytes: {value!r}"
        )


def _require_ascii_fragment(value: str, label: str) -> None:
    if not ASCII_RAW.fullmatch(value):
        raise CorpusError(f"{label} must contain lower-case ASCII letters only: {value!r}")
    if not 1 <= len(value.encode("utf-8")) <= RUNTIME_LIMITS["max_raw_bytes"]:
        raise CorpusError(
            f"{label} must be between 1 and {RUNTIME_LIMITS['max_raw_bytes']} bytes: "
            f"{value!r}"
        )


def _validate_stratum(value: str, label: str) -> None:
    fields: dict[str, str] = {}
    for item in value.split(STRATUM_SEPARATOR):
        key, separator, field_value = item.partition("=")
        if not separator or not key or not field_value or key in fields:
            raise CorpusError(f"{label} must contain unique key=value fields")
        fields[key] = field_value
    if set(fields) != STRATUM_KEYS:
        raise CorpusError(
            f"{label} must contain exactly {sorted(STRATUM_KEYS)}: {value!r}"
        )
    for key, allowed in STRATUM_VALUES.items():
        if fields[key] not in allowed:
            raise CorpusError(
                f"{label}.{key} has unsupported value {fields[key]!r}"
            )


def _stratum_dict(value: str) -> dict[str, str]:
    return {
        item.partition("=")[0]: item.partition("=")[2]
        for item in value.split(STRATUM_SEPARATOR)
    }


def _validate_length_bucket(
    value: str, label: str, dataset: str, observed_length: int
) -> None:
    dimensions = SCHEMA["stratum_dimensions"][dataset]["length"]
    expected = None
    for bucket, bounds in dimensions["buckets"].items():
        if observed_length < bounds["min"]:
            continue
        if "max" not in bounds or observed_length <= bounds["max"]:
            expected = bucket
            break
    if expected is None or _stratum_dict(value)["length"] != expected:
        raise CorpusError(
            f"{label}.length does not match {dataset} {dimensions['unit']}="
            f"{observed_length}: {value!r}"
        )


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


def _is_one_event_change_trace(
    typed_codes: str,
    typed_strings: str,
    corrected_codes: str,
    corrected_strings: str,
) -> bool:
    if len(typed_codes) != len(corrected_codes):
        return False
    if len(typed_strings) != len(corrected_strings):
        return False
    mismatches = sum(
        typed_code != corrected_code or typed_string != corrected_string
        for typed_code, corrected_code, typed_string, corrected_string in zip(
            typed_codes, corrected_codes, typed_strings, corrected_strings
        )
    )
    return mismatches == 1


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
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "roman", len(row["typed_raw"])
        )
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
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
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
        _require_negative_raw(row["typed_raw"], f"{case_id}.typed_raw")
        if not row["expected_reading"] or not row["reason"]:
            raise CorpusError(f"Negative case has an empty expectation: {case_id}")
        if row["raw_policy"] not in NEGATIVE_RAW_POLICIES:
            raise CorpusError(f"unknown Negative raw policy: {case_id}")
        if row["display_policy"] not in NEGATIVE_DISPLAY_POLICIES:
            raise CorpusError(f"unknown Negative display policy: {case_id}")
        if row["auto_policy"] not in NEGATIVE_AUTO_POLICIES:
            raise CorpusError(f"unknown Negative auto policy: {case_id}")
        if row["display_policy"] == "forbidden" and row["raw_policy"] != "allowed":
            raise CorpusError(
                f"display-forbidden Negative must be raw-policy eligible: {case_id}"
            )
        if row["raw_policy"] == "forbidden" and row["display_policy"] != "allowed":
            raise CorpusError(
                f"raw-forbidden Negative cannot also be display-forbidden: {case_id}"
            )
        if row["raw_policy"] == "allowed":
            _require_ascii_raw(row["typed_raw"], f"{case_id}.typed_raw")
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "roman", len(row["typed_raw"])
        )
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
        _require_ascii_fragment(row["wrong"], f"{rule_id}.wrong")
        _require_ascii_fragment(row["corrected"], f"{rule_id}.corrected")
        if row["wrong"] == row["corrected"]:
            raise CorpusError(f"override must change raw input: {rule_id}")
        if row["operation"] not in OPERATIONS:
            raise CorpusError(f"unknown override operation: {rule_id}")
        try:
            cost = int(row["cost"])
        except ValueError as error:
            raise CorpusError(f"override cost is not an integer: {rule_id}") from error
        if not 1 <= cost <= RUNTIME_LIMITS["max_edit_cost"]:
            raise CorpusError(
                f"override cost must be in [1, {RUNTIME_LIMITS['max_edit_cost']}]: "
                f"{rule_id}"
            )
        if row["auto_applicable"] not in {"true", "false"}:
            raise CorpusError(f"override auto_applicable must be boolean: {rule_id}")
        if row["scope"] not in OVERRIDE_SCOPES:
            raise CorpusError(f"override scope must be local or full: {rule_id}")
        result.append(OverrideRule(row))
    return tuple(result)


def _validate_kana_gold_rows(
    rows: Iterable[dict[str, str]],
    dataset: str,
    case_id_pattern: str,
    label: str,
) -> tuple[KanaGoldCase, ...]:
    result: list[KanaGoldCase] = []
    seen_ids: set[str] = set()
    seen_inputs: set[tuple[str, str]] = set()
    constraints = DATASET_CONSTRAINTS[dataset]
    for row in rows:
        case_id = row["case_id"]
        if not re.fullmatch(case_id_pattern, case_id) or case_id in seen_ids:
            raise CorpusError(f"invalid or duplicate {label} case_id: {case_id}")
        seen_ids.add(case_id)
        if row["operation"] not in KANA_OPERATIONS:
            raise CorpusError(f"unknown {label} operation: {case_id}")
        if row["confidence"] not in CONFIDENCES:
            raise CorpusError(f"unknown {label} confidence: {case_id}")
        if row["behavior"] not in BEHAVIORS:
            raise CorpusError(f"unknown {label} behavior: {case_id}")
        if (
            not MODE_POLICIES["kana"]["auto_enabled"]
            and row["behavior"] == "auto"
        ):
            raise CorpusError(
                f"{label} automatic behavior is disabled by the Kana policy: "
                f"{case_id}"
            )
        if row["behavior"] == "auto" and row["confidence"] != "high":
            raise CorpusError(
                f"automatic {label} cases must be high confidence: {case_id}"
            )
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
        if len(row["typed_key_codes"]) != len(row["typed_key_strings"]):
            raise CorpusError(f"kana typed key/code count mismatch: {case_id}")
        if len(row["corrected_key_codes"]) != len(row["corrected_key_strings"]):
            raise CorpusError(f"kana corrected key/code count mismatch: {case_id}")
        if not (
            constraints["min_key_events"]
            <= len(row["typed_key_codes"])
            <= constraints.get("max_key_events", RUNTIME_LIMITS["max_key_events"])
        ):
            raise CorpusError(f"{label} key-event count is out of range: {case_id}")
        if not (
            constraints["min_key_events"]
            <= len(row["corrected_key_codes"])
            <= constraints.get("max_key_events", RUNTIME_LIMITS["max_key_events"])
        ):
            raise CorpusError(
                f"{label} corrected key-event count is out of range: {case_id}"
            )
        if (
            row["typed_key_codes"] == row["corrected_key_codes"]
            and row["typed_key_strings"] == row["corrected_key_strings"]
        ):
            raise CorpusError(f"{label} case must change the key trace: {case_id}")
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "kana", len(row["typed_key_codes"])
        )
        typed_trace = (row["typed_key_codes"], row["typed_key_strings"])
        if constraints.get("unique_typed_trace") and typed_trace in seen_inputs:
            raise CorpusError(
                f"{label} typed traces must be unique: {case_id}"
            )
        seen_inputs.add(typed_trace)
        result.append(KanaGoldCase(row))
    return tuple(result)


def _validate_optional_kana(
    root: pathlib.Path,
) -> tuple[
    tuple[KanaGoldCase, ...],
    tuple[KanaGoldCase, ...],
    tuple[KanaNegativeCase, ...],
]:
    gold_path = root / KANA_GOLD_PATH.name
    evaluation_path = root / KANA_EVALUATION_PATH.name
    negative_path = root / KANA_NEGATIVE_PATH.name
    layout_path = root / KANA_LAYOUT_PATH.name
    modifier_path = root / KANA_MODIFIER_PATH.name
    if not gold_path.exists() and not negative_path.exists():
        return (), (), ()
    if (
        not gold_path.exists()
        or not evaluation_path.exists()
        or not negative_path.exists()
        or not layout_path.exists()
        or not modifier_path.exists()
    ):
        raise CorpusError(
            "kana fixture, evaluation, and Negative files must be provided together"
        )
    gold_rows = _read_rows(gold_path, KANA_GOLD_COLUMNS)
    evaluation_rows = _read_rows(evaluation_path, KANA_EVALUATION_COLUMNS)
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
        if row["row"] not in LAYOUT_ROWS:
            raise CorpusError(f"unknown kana layout row: {row['row']}")
    gold = _validate_kana_gold_rows(
        gold_rows, "kana_gold", r"K[0-9]{6}", "kana Gold fixture"
    )
    evaluation = _validate_kana_gold_rows(
        evaluation_rows,
        "kana_gold_evaluation",
        r"KE[0-9]{6}",
        "kana Gold evaluation",
    )
    seen = {case.row["case_id"] for case in gold} | {
        case.row["case_id"] for case in evaluation
    }
    negative: list[KanaNegativeCase] = []
    for row in negative_rows:
        case_id = row["case_id"]
        if not re.fullmatch(r"KN[0-9]{6}", case_id) or case_id in seen:
            raise CorpusError(f"invalid or duplicate kana Negative case_id: {case_id}")
        seen.add(case_id)
        if len(row["typed_key_codes"]) != len(row["typed_key_strings"]):
            raise CorpusError(f"kana negative key/code count mismatch: {case_id}")
        negative_limits = DATASET_CONSTRAINTS["kana_negative"]
        if not (
            negative_limits["min_key_events"]
            <= len(row["typed_key_codes"])
            <= RUNTIME_LIMITS["max_key_events"]
        ):
            raise CorpusError(f"kana Negative key-event count is out of range: {case_id}")
        if not row["reason"]:
            raise CorpusError(f"kana Negative case has an empty reason: {case_id}")
        if row["raw_policy"] not in NEGATIVE_RAW_POLICIES:
            raise CorpusError(f"unknown kana Negative raw policy: {case_id}")
        if row["display_policy"] not in NEGATIVE_DISPLAY_POLICIES:
            raise CorpusError(f"unknown kana Negative display policy: {case_id}")
        if row["auto_policy"] not in NEGATIVE_AUTO_POLICIES:
            raise CorpusError(f"unknown kana Negative auto policy: {case_id}")
        if row["display_policy"] == "forbidden" and row["raw_policy"] != "allowed":
            raise CorpusError(
                f"display-forbidden kana Negative must be raw-policy eligible: {case_id}"
            )
        if row["raw_policy"] == "forbidden" and row["display_policy"] != "allowed":
            raise CorpusError(
                f"raw-forbidden kana Negative cannot also be display-forbidden: {case_id}"
            )
        if row["display_policy"] == "forbidden":
            if any(key_code not in layout_codes for key_code in row["typed_key_codes"]):
                raise CorpusError(
                    f"display-forbidden kana Negative must use JIS layout keys: {case_id}"
                )
        elif row["raw_policy"] == "forbidden" and all(
            key_code in layout_codes for key_code in row["typed_key_codes"]
        ):
            raise CorpusError(
                f"raw-forbidden kana Negative must exercise a gate-invalid key: {case_id}"
            )
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "kana", len(row["typed_key_codes"])
        )
        negative.append(KanaNegativeCase(row))
    gold_ids = {case.row["case_id"] for case in gold}
    modifier_ids: set[str] = set()
    for row in modifier_rows:
        rule_id = row["rule_id"]
        if rule_id in modifier_ids or rule_id not in gold_ids:
            raise CorpusError(f"kana modifier rule must reference one Gold case: {rule_id}")
        modifier_ids.add(rule_id)
        if row["operation"] != "kana_modifier" or row["operation"] not in KANA_OPERATIONS:
            raise CorpusError(f"unknown kana modifier operation: {rule_id}")
        try:
            cost = int(row["cost"])
        except ValueError as error:
            raise CorpusError(f"kana modifier cost is not an integer: {rule_id}") from error
        if not 1 <= cost <= RUNTIME_LIMITS["max_edit_cost"]:
            raise CorpusError(
                f"kana modifier cost must be in [1, {RUNTIME_LIMITS['max_edit_cost']}]: "
                f"{rule_id}"
            )
        if row["behavior"] not in BEHAVIORS:
            raise CorpusError(f"unknown kana modifier behavior: {rule_id}")
        if (
            not MODE_POLICIES["kana"]["auto_enabled"]
            and row["behavior"] == "auto"
        ):
            raise CorpusError(
                f"kana modifier automatic behavior is disabled by the Kana policy: "
                f"{rule_id}"
            )
    return gold, evaluation, tuple(negative)


def _validate_optional_holdout(
    root: pathlib.Path,
    gold: tuple[GoldCase, ...],
    negative: tuple[NegativeCase, ...],
    kana_gold: tuple[KanaGoldCase, ...],
    kana_evaluation: tuple[KanaGoldCase, ...],
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
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "roman", len(row["typed_raw"])
        )
        if not row["corrected_reading"]:
            raise CorpusError(f"Roman holdout has no corrected reading: {case_id}")
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
        if row["typed_raw"] == row["corrected_raw"]:
            raise CorpusError(f"Roman holdout must change raw input: {case_id}")
        if row["typed_raw"] in generated_typed_raw:
            raise CorpusError(
                f"Roman holdout overlaps generated corpus input: {case_id}"
            )
        if row["operation"] not in ROMAN_HOLDOUT_OPERATIONS:
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
        if operation == "neighbor":
            shape_ok = _is_one_neighbor(
                row["corrected_raw"], row["typed_raw"]
            )
        if not shape_ok:
            raise CorpusError(
                f"Roman holdout raw pair does not match {operation}: {case_id}"
            )
        roman_result.append(RomanHoldoutCase(row))

    generated_kana_inputs = {
        (case.row["typed_key_codes"], case.row["typed_key_strings"])
        for case in kana_gold
    } | {
        (case.row["typed_key_codes"], case.row["typed_key_strings"])
        for case in kana_evaluation
    } | {
        (case.row["typed_key_codes"], case.row["typed_key_strings"])
        for case in kana_negative
    }
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
        holdout_limits = DATASET_CONSTRAINTS["kana_holdout"]
        if not (
            holdout_limits["min_key_events"]
            <= len(row["typed_key_codes"])
            <= RUNTIME_LIMITS["max_key_events"]
        ):
            raise CorpusError(f"kana holdout must contain a multi-key trace: {case_id}")
        if (
            row["typed_key_codes"] == row["corrected_key_codes"]
            and row["typed_key_strings"] == row["corrected_key_strings"]
        ):
            raise CorpusError(f"kana holdout must change the key trace: {case_id}")
        if (
            row["typed_key_codes"], row["typed_key_strings"]
        ) in generated_kana_inputs:
            raise CorpusError(
                f"kana holdout overlaps generated corpus input: {case_id}"
            )
        _validate_stratum(row["stratum"], f"{case_id}.stratum")
        if row["operation"] not in KANA_HOLDOUT_OPERATIONS:
            raise CorpusError(f"unsupported kana holdout operation: {case_id}")
        if row["behavior"] != "test_only":
            raise CorpusError(f"kana holdout must be test_only: {case_id}")
        if row["operation"] == "duplicate":
            shape_ok = _is_one_duplicate_trace(
                row["typed_key_codes"],
                row["typed_key_strings"],
                row["corrected_key_codes"],
                row["corrected_key_strings"],
            )
        else:
            shape_ok = _is_one_event_change_trace(
                row["typed_key_codes"],
                row["typed_key_strings"],
                row["corrected_key_codes"],
                row["corrected_key_strings"],
            )
        if not shape_ok:
            raise CorpusError(f"kana holdout does not match its operation: {case_id}")
        _validate_length_bucket(
            row["stratum"], f"{case_id}.stratum", "kana", len(row["typed_key_codes"])
        )
        kana_result.append(KanaHoldoutCase(row))

    return tuple(roman_result), tuple(kana_result)


def _validate_coverage(corpus: Corpus) -> None:
    groups = {
        "roman_gold": corpus.gold,
        "roman_negative": corpus.negative,
        "roman_holdout": corpus.roman_holdout,
        "kana_gold_fixture": corpus.kana_gold,
        "kana_gold_evaluation": corpus.kana_evaluation,
        "kana_negative": corpus.kana_negative,
        "kana_holdout": corpus.kana_holdout,
    }
    target_names = {
        "roman_gold": "roman_gold",
        "roman_negative": "roman_negative",
        "roman_holdout": "roman_composer_engine_holdout",
        "kana_gold_fixture": "kana_gold_fixture",
        "kana_gold_evaluation": "kana_gold",
        "kana_negative": "kana_negative",
        "kana_holdout": "kana_holdout",
    }
    total = sum(
        len(rows)
        for name, rows in groups.items()
        if name != "kana_gold_fixture"
    )
    if total != EVALUATION_TARGETS["total_cases"]:
        raise CorpusError(
            f"evaluation corpus has {total} cases; expected "
            f"{EVALUATION_TARGETS['total_cases']}"
        )
    for name, rows in groups.items():
        expected = EVALUATION_TARGETS[target_names[name]]
        if len(rows) != expected:
            raise CorpusError(f"{name} has {len(rows)} cases; expected {expected}")
        for dimension, minimums in COVERAGE_MINIMUMS.get(name, {}).items():
            counts: dict[str, int] = {}
            for case in rows:
                value = (
                    case.row["operation"]
                    if dimension == "operation"
                    else case.row[dimension]
                    if dimension in case.row
                    else _stratum_dict(case.row["stratum"])[dimension]
                )
                counts[value] = counts.get(value, 0) + 1
            for value, minimum in minimums.items():
                if counts.get(value, 0) < minimum:
                    raise CorpusError(
                        f"{name}.{dimension}={value} has {counts.get(value, 0)} "
                        f"cases; minimum is {minimum}"
                    )


def _validate_strict_negative_coverage(corpus: Corpus) -> None:
    groups = {"roman": corpus.negative, "kana": corpus.kana_negative}
    for mode, rows in groups.items():
        for policy, dimensions in STRICT_NEGATIVE_COVERAGE[mode].items():
            policy_field, policy_value = policy.split("_", 1)
            for dimension, minimums in dimensions.items():
                counts: dict[str, int] = {}
                for case in rows:
                    if case.row[f"{policy_field}_policy"] != policy_value:
                        continue
                    value = _stratum_dict(case.row["stratum"])[dimension]
                    counts[value] = counts.get(value, 0) + 1
                for value, minimum in minimums.items():
                    if counts.get(value, 0) < minimum:
                        raise CorpusError(
                            f"{mode}.{policy}.{dimension}={value} has "
                            f"{counts.get(value, 0)} cases; minimum is {minimum}"
                        )


def _validate_negative_policy_targets(corpus: Corpus) -> None:
    groups = {"roman": corpus.negative, "kana": corpus.kana_negative}
    for mode, rows in groups.items():
        for policy, expected in NEGATIVE_POLICY_TARGETS[mode].items():
            policy_field, policy_value = policy.split("_", 1)
            actual = sum(
                case.row[f"{policy_field}_policy"] == policy_value for case in rows
            )
            if actual != expected:
                raise CorpusError(
                    f"{mode}.{policy} has {actual} cases; expected {expected}"
                )


def _validate_exclusivity(corpus: Corpus) -> None:
    if EXCLUSIVITY["global_case_ids"]:
        id_sets = [
            [case.row["case_id"] for case in corpus.gold],
            [case.row["case_id"] for case in corpus.negative],
            [case.row["case_id"] for case in corpus.kana_gold],
            [case.row["case_id"] for case in corpus.kana_evaluation],
            [case.row["case_id"] for case in corpus.kana_negative],
            [case.row["case_id"] for case in corpus.roman_holdout],
            [case.row["case_id"] for case in corpus.kana_holdout],
        ]
        ids = [case_id for group in id_sets for case_id in group]
        if len(set(ids)) != len(ids):
            raise CorpusError("case IDs must be globally unique across all datasets")

    if EXCLUSIVITY["roman_typed_raw_disjoint_across_datasets"]:
        roman_inputs = [
            [case.row["typed_raw"] for case in corpus.gold],
            [case.row["typed_raw"] for case in corpus.negative],
            [case.row["typed_raw"] for case in corpus.roman_holdout],
        ]
        flattened = [raw for group in roman_inputs for raw in group]
        if len(set(flattened)) != len(flattened):
            raise CorpusError(
                "Roman typed_raw inputs must be unique across Gold, Negative, "
                "and holdout datasets"
            )

    if EXCLUSIVITY["kana_typed_trace_disjoint_across_datasets"]:
        kana_fixture_inputs = {
            (case.row["typed_key_codes"], case.row["typed_key_strings"])
            for case in corpus.kana_gold
        }
        kana_evaluation_inputs = [
            (case.row["typed_key_codes"], case.row["typed_key_strings"])
            for case in corpus.kana_evaluation
        ]
        if len(set(kana_evaluation_inputs)) != len(kana_evaluation_inputs):
            raise CorpusError("Kana Gold evaluation typed traces must be unique")
        kana_inputs = [
            set(kana_evaluation_inputs),
            {
                (case.row["typed_key_codes"], case.row["typed_key_strings"])
                for case in corpus.kana_negative
            },
            {
                (case.row["typed_key_codes"], case.row["typed_key_strings"])
                for case in corpus.kana_holdout
            },
        ]
        if len(kana_inputs[1]) != len(corpus.kana_negative):
            raise CorpusError("Kana Negative typed traces must be unique")
        if len(kana_inputs[2]) != len(corpus.kana_holdout):
            raise CorpusError("Kana holdout typed traces must be unique")
        if kana_fixture_inputs & kana_inputs[0]:
            raise CorpusError(
                "Kana Gold fixture and evaluation typed traces must be disjoint"
            )
        for left_index, left in enumerate(kana_inputs):
            for right in kana_inputs[left_index + 1 :]:
                if left & right:
                    raise CorpusError(
                        "Kana typed key-code/string traces must be disjoint across "
                        "Gold, Negative, and holdout datasets"
                    )


def load_corpus(root: pathlib.Path = CORPUS_ROOT) -> Corpus:
    gold = _validate_gold(_read_rows(root / GOLD_PATH.name, GOLD_COLUMNS))
    negative = _validate_negative(
        _read_rows(root / NEGATIVE_PATH.name, NEGATIVE_COLUMNS)
    )
    overrides = _validate_overrides(
        _read_rows(root / OVERRIDES_PATH.name, OVERRIDE_COLUMNS)
    )
    kana_gold, kana_evaluation, kana_negative = _validate_optional_kana(root)
    roman_holdout, kana_holdout = _validate_optional_holdout(
        root, gold, negative, kana_gold, kana_evaluation, kana_negative
    )
    corpus = Corpus(
        gold,
        negative,
        overrides,
        kana_gold,
        kana_evaluation,
        kana_negative,
        roman_holdout,
        kana_holdout,
    )
    _validate_exclusivity(corpus)
    _validate_coverage(corpus)
    _validate_strict_negative_coverage(corpus)
    _validate_negative_policy_targets(corpus)
    return corpus


def corpus_digest(root: pathlib.Path = CORPUS_ROOT) -> str:
    # Holdout rows are intentionally excluded: they exercise generic
    # candidate generation without changing the production rule artifact.
    digest = hashlib.sha256()
    paths = [GOLD_PATH, NEGATIVE_PATH, OVERRIDES_PATH]
    for optional_path in (
        KANA_GOLD_PATH,
        KANA_EVALUATION_PATH,
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
        f"kana_evaluation={len(corpus.kana_evaluation)} "
        f"kana_negative={len(corpus.kana_negative)} "
        f"roman_holdout={len(corpus.roman_holdout)} "
        f"kana_holdout={len(corpus.kana_holdout)} "
        f"sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
