#!/usr/bin/env python3
"""Apply the schema-defined release thresholds to a metrics JSON report."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.typing_correction.validate_typing_correction_corpus import (
    EVALUATION_TARGETS,
    MODE_POLICIES,
    NEGATIVE_POLICY_TARGETS,
    RELEASE_THRESHOLDS,
    SCHEMA,
    STRICT_NEGATIVE_COVERAGE,
)


class MetricsError(ValueError):
    """The metrics report is malformed or misses a release metric."""


RATE_NAMES = {
    "candidate_precision",
    "gold_candidate_precision",
    "raw_hypothesis_candidate_precision",
    "candidate_recall",
    "raw_hypothesis_candidate_recall",
    "top1_accuracy",
    "raw_hypothesis_top1_accuracy",
    "candidate_false_positive_rate",
    "raw_hypothesis_candidate_false_positive_rate",
    "raw_policy_false_positive_rate",
    "display_policy_raw_hypothesis_violation_rate",
    "negative_candidate_rate",
    "negative_top1_wrong_rate",
    "auto_precision",
    "auto_recall",
    "suggest_recall",
    "auto_false_positive_rate",
    "suggest_auto_violation_rate",
    "recall",
    "engine_e2e_recall",
    "kana_engine_e2e_recall",
    "candidate_window_top1_accuracy",
    "candidate_window_topk_accuracy",
    "candidate_window_kana_top1_accuracy",
    "candidate_window_kana_topk_accuracy",
    "candidate_window_negative_false_positive_rate",
    "candidate_window_kana_negative_false_positive_rate",
}

METRICS_REPORT_CONTRACT = SCHEMA["metrics_report_contract"]


def _get(report: dict[str, Any], *path: str) -> Any:
    value: Any = report
    for name in path:
        if not isinstance(value, dict) or name not in value:
            raise MetricsError(f"missing metric: {'.'.join(path)}")
        value = value[name]
    return value


def _number(report: dict[str, Any], *path: str) -> float:
    value = _get(report, *path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricsError(f"metric is not numeric: {'.'.join(path)}")
    if not math.isfinite(value):
        raise MetricsError(f"metric is not finite: {'.'.join(path)}")
    return float(value)


def _rate(report: dict[str, Any], *path: str) -> float:
    value = _number(report, *path)
    if not 0.0 <= value <= 1.0:
        raise MetricsError(
            f"metric is outside [0, 1]: {'.'.join(path)}={value}"
        )
    return value


def _validate_ratio(
    report: dict[str, Any],
    numerator_path: tuple[str, ...],
    denominator_path: tuple[str, ...],
    rate_path: tuple[str, ...],
    errors: list[str],
) -> tuple[int, int, float] | None:
    """Validate a count-derived rate and return its counts/value when valid."""

    label = ".".join(rate_path)
    try:
        numerator = _get(report, *numerator_path)
        denominator = _get(report, *denominator_path)
    except MetricsError as error:
        errors.append(str(error))
        return None
    if isinstance(numerator, bool) or not isinstance(numerator, int):
        errors.append(f"{'.'.join(numerator_path)} is not an integer")
        return None
    if numerator < 0:
        errors.append(f"{'.'.join(numerator_path)} must not be negative")
        return None
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        errors.append(f"{'.'.join(denominator_path)} is not an integer")
        return None
    if denominator <= 0:
        errors.append(f"{'.'.join(denominator_path)} must be greater than zero")
        return None
    if numerator > denominator:
        errors.append(
            f"{'.'.join(numerator_path)}={numerator} exceeds "
            f"{'.'.join(denominator_path)}={denominator}"
        )
    try:
        actual = _rate(report, *rate_path)
    except MetricsError as error:
        errors.append(str(error))
        return None
    expected = numerator / denominator
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=5e-6):
        errors.append(
            f"{label}={actual:.6f} does not match "
            f"{'.'.join(numerator_path)}/{'.'.join(denominator_path)}="
            f"{expected:.6f}"
        )
    return numerator, denominator, actual


def _validate_optional_ratio(
    report: dict[str, Any],
    numerator_path: tuple[str, ...],
    denominator_path: tuple[str, ...],
    rate_path: tuple[str, ...],
    errors: list[str],
) -> None:
    """Validate a ratio whose zero denominator means not applicable."""

    try:
        numerator = _get(report, *numerator_path)
        denominator = _get(report, *denominator_path)
    except MetricsError as error:
        errors.append(str(error))
        return
    if isinstance(numerator, bool) or not isinstance(numerator, int):
        errors.append(f"{'.'.join(numerator_path)} is not an integer")
        return
    if numerator < 0:
        errors.append(f"{'.'.join(numerator_path)} must not be negative")
        return
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        errors.append(f"{'.'.join(denominator_path)} is not an integer")
        return
    if denominator < 0:
        errors.append(f"{'.'.join(denominator_path)} must not be negative")
        return
    if denominator == 0:
        if numerator != 0:
            errors.append(
                f"{'.'.join(numerator_path)}={numerator} must be zero when "
                f"{'.'.join(denominator_path)}=0"
            )
        try:
            actual = _rate(report, *rate_path)
        except MetricsError as error:
            errors.append(str(error))
        else:
            if actual != 0.0:
                errors.append(
                    f"{'.'.join(rate_path)}={actual:.6f} must be zero when "
                    f"{'.'.join(denominator_path)}=0"
                )
        return
    _validate_ratio(
        report, numerator_path, denominator_path, rate_path, errors
    )


def _nonnegative_count(
    report: dict[str, Any], path: tuple[str, ...], errors: list[str]
) -> int | None:
    """Read one non-negative integer count."""

    label = ".".join(path)
    try:
        value = _get(report, *path)
    except MetricsError as error:
        errors.append(str(error))
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} is not an integer")
        return None
    if value < 0:
        errors.append(f"{label} must not be negative")
        return None
    return value


def _validate_count_equals(
    report: dict[str, Any],
    path: tuple[str, ...],
    expected: int,
    authority: str,
    errors: list[str],
) -> None:
    """Require a reported count to equal an authoritative count."""

    label = ".".join(path)
    actual = _nonnegative_count(report, path, errors)
    if actual is None:
        return
    if actual != expected:
        errors.append(
            f"{label}={actual} does not match authoritative "
            f"{authority}={expected}"
        )


def _authority_total(
    report: dict[str, Any],
    scope: str,
    fields: list[str],
    errors: list[str],
) -> tuple[int, str] | None:
    """Read and sum the schema-declared authority fields for one mode."""

    values: list[int] = []
    for field in fields:
        value = _nonnegative_count(report, (scope, field), errors)
        if value is None:
            return None
        values.append(value)
    return sum(values), "+".join(f"{scope}.{field}" for field in fields)


def _validate_partition_total(
    buckets: dict[str, Any],
    field: str,
    expected: int,
    scope: str,
    group: str,
    authority: str,
    errors: list[str],
) -> None:
    """Require one stratum dimension to conserve an aggregate count."""

    total = 0
    valid = True
    for bucket, summary in buckets.items():
        label = f"{scope}.{group}.{bucket}.{field}"
        if not isinstance(summary, dict) or field not in summary:
            errors.append(f"missing metric: {label}")
            valid = False
            continue
        value = summary[field]
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{label} is not an integer")
            valid = False
            continue
        if value < 0:
            errors.append(f"{label} must not be negative")
            valid = False
            continue
        total += value
    if valid and total != expected:
        errors.append(
            f"{scope}.{group}.{field} total={total} does not match "
            f"authoritative {authority}={expected}"
        )


def _validate_rate_fields(value: Any, path: tuple[str, ...], errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (str(key),)
            if key in RATE_NAMES:
                if isinstance(child, bool) or not isinstance(child, (int, float)):
                    errors.append(f"metric is not numeric: {'.'.join(child_path)}")
                elif not math.isfinite(float(child)) or not 0.0 <= float(child) <= 1.0:
                    errors.append(
                        f"metric is outside [0, 1]: {'.'.join(child_path)}={child}"
                    )
            else:
                _validate_rate_fields(child, child_path, errors)


def _check_thresholds(
    report: dict[str, Any], scope: str, errors: list[str]
) -> None:
    for threshold_name, threshold in RELEASE_THRESHOLDS[scope].items():
        if threshold_name.endswith("_min"):
            metric_name = threshold_name[: -len("_min")]
            try:
                actual = (
                    _rate(report, scope, metric_name)
                    if metric_name in RATE_NAMES
                    else _number(report, scope, metric_name)
                )
            except MetricsError as error:
                errors.append(str(error))
                continue
            if actual < threshold:
                errors.append(
                    f"{scope}.{metric_name}={actual:.6f} is below "
                    f"minimum {threshold:.6f}"
                )
        elif threshold_name.endswith("_max"):
            metric_name = threshold_name[: -len("_max")]
            try:
                actual = _number(report, scope, metric_name)
            except MetricsError as error:
                errors.append(str(error))
                continue
            if actual > threshold:
                errors.append(
                    f"{scope}.{metric_name}={actual:.6f} is above "
                    f"maximum {threshold:.6f}"
                )
        else:
            errors.append(f"unsupported threshold name: {scope}.{threshold_name}")


def validate_metrics(report: dict[str, Any]) -> list[str]:
    """Return all release-gate failures in a metrics report."""

    errors: list[str] = []
    _validate_rate_fields(report, (), errors)
    target_fields = {
        "total_cases": ("corpus_cases", "total"),
        "roman_gold": ("corpus_cases", "roman_gold"),
        "roman_negative": ("corpus_cases", "roman_negative"),
        "kana_gold": ("corpus_cases", "kana_gold"),
        "kana_negative": ("corpus_cases", "kana_negative"),
        "roman_composer_engine_holdout": ("corpus_cases", "roman_holdout"),
        "kana_holdout": ("corpus_cases", "kana_holdout"),
    }
    for target_name, path in target_fields.items():
        try:
            actual = _get(report, *path)
        except MetricsError as error:
            errors.append(str(error))
            continue
        if actual != EVALUATION_TARGETS[target_name]:
            errors.append(
                f"{'.'.join(path)}={actual} does not match "
                f"schema target {EVALUATION_TARGETS[target_name]}"
            )

    for scope, gold_target, negative_target in (
        ("roman", "roman_gold", "roman_negative"),
        ("kana", "kana_gold", "kana_negative"),
    ):
        for metric_name, expected in (
            ("gold_cases", EVALUATION_TARGETS[gold_target]),
            ("negative_cases", EVALUATION_TARGETS[negative_target]),
        ):
            try:
                actual = _get(report, scope, metric_name)
            except MetricsError as error:
                errors.append(str(error))
                continue
            if actual != expected:
                errors.append(
                    f"{scope}.{metric_name}={actual} does not match "
                    f"schema target {expected}"
                )
        _check_thresholds(report, scope, errors)
        for numerator, denominator, rate in (
            (
                "raw_policy_forbidden_violations",
                "raw_policy_forbidden_cases",
                "candidate_false_positive_rate",
            ),
            (
                "raw_policy_forbidden_violations",
                "raw_policy_forbidden_cases",
                "raw_hypothesis_candidate_false_positive_rate",
            ),
            (
                "raw_policy_forbidden_violations",
                "raw_policy_forbidden_cases",
                "raw_policy_false_positive_rate",
            ),
            (
                "display_policy_forbidden_violations",
                "display_policy_forbidden_cases",
                "display_policy_raw_hypothesis_violation_rate",
            ),
            (
                "negative_rows_with_candidates",
                "negative_cases",
                "negative_candidate_rate",
            ),
            (
                "negative_rows_with_candidates",
                "negative_cases",
                "negative_top1_wrong_rate",
            ),
            (
                "auto_policy_violations",
                "negative_cases",
                "auto_false_positive_rate",
            ),
        ):
            _validate_ratio(
                report,
                (scope, numerator),
                (scope, denominator),
                (scope, rate),
                errors,
            )
        for numerator, denominator, rate in (
            (
                "candidate_precision_numerator",
                "candidate_precision_denominator",
                "candidate_precision",
            ),
            (
                "candidate_precision_numerator",
                "candidate_precision_denominator",
                "raw_hypothesis_candidate_precision",
            ),
            (
                "gold_candidate_precision_numerator",
                "gold_candidate_precision_denominator",
                "gold_candidate_precision",
            ),
            (
                "candidate_recall_numerator",
                "candidate_recall_denominator",
                "candidate_recall",
            ),
            (
                "candidate_recall_numerator",
                "candidate_recall_denominator",
                "raw_hypothesis_candidate_recall",
            ),
            (
                "top1_accuracy_numerator",
                "top1_accuracy_denominator",
                "top1_accuracy",
            ),
            (
                "top1_accuracy_numerator",
                "top1_accuracy_denominator",
                "raw_hypothesis_top1_accuracy",
            ),
            (
                "suggest_recall_numerator",
                "suggest_recall_denominator",
                "suggest_recall",
            ),
            (
                "suggest_auto_violation_rate_numerator",
                "suggest_auto_violation_rate_denominator",
                "suggest_auto_violation_rate",
            ),
        ):
            _validate_ratio(
                report,
                (scope, numerator),
                (scope, denominator),
                (scope, rate),
                errors,
            )
        for numerator, denominator, rate in (
            (
                "auto_precision_numerator",
                "auto_precision_denominator",
                "auto_precision",
            ),
            (
                "auto_recall_numerator",
                "auto_recall_denominator",
                "auto_recall",
            ),
        ):
            _validate_optional_ratio(
                report,
                (scope, numerator),
                (scope, denominator),
                (scope, rate),
                errors,
            )

        for field, authority_fields in METRICS_REPORT_CONTRACT[
            "mode_count_authorities"
        ].items():
            authority = _authority_total(
                report, scope, authority_fields, errors
            )
            if authority is not None:
                expected, authority_label = authority
                _validate_count_equals(
                    report,
                    (scope, field),
                    expected,
                    authority_label,
                    errors,
                )

        for authority_field, component_fields in METRICS_REPORT_CONTRACT[
            "mode_component_totals"
        ].items():
            authoritative = _nonnegative_count(
                report, (scope, authority_field), errors
            )
            components = _authority_total(
                report, scope, component_fields, errors
            )
            if authoritative is not None and components is not None:
                component_total, component_label = components
                if component_total != authoritative:
                    errors.append(
                        f"{component_label}={component_total} does not match "
                        f"authoritative {scope}.{authority_field}="
                        f"{authoritative}"
                    )
        for policy, expected in NEGATIVE_POLICY_TARGETS[scope].items():
            policy_field, policy_value = policy.split("_", 1)
            field = f"{policy_field}_policy_{policy_value}_cases"
            try:
                actual = _get(report, scope, field)
            except MetricsError as error:
                errors.append(str(error))
            else:
                if actual != expected:
                    errors.append(
                        f"{scope}.{field}={actual} does not match schema target {expected}"
                    )

        if MODE_POLICIES[scope]["auto_enabled"]:
            for field in ("auto_gold_cases", "gold_auto_candidates"):
                try:
                    denominator = _get(report, scope, field)
                except MetricsError as error:
                    errors.append(str(error))
                else:
                    if not isinstance(denominator, int) or isinstance(denominator, bool):
                        errors.append(f"{scope}.{field} is not an integer")
                    elif denominator <= 0:
                        errors.append(f"{scope}.{field} must be greater than zero")
        else:
            for field in ("auto_gold_cases", "gold_auto_candidates", "auto_candidates", "auto_rows"):
                try:
                    value = _get(report, scope, field)
                except MetricsError as error:
                    errors.append(str(error))
                else:
                    if value != 0:
                        errors.append(
                            f"{scope}.{field}={value} violates auto_disabled policy"
                        )
            try:
                violations = _get(report, scope, "suggest_auto_violation_rows")
            except MetricsError as error:
                errors.append(str(error))
            else:
                if violations != 0:
                    errors.append(
                        f"{scope}.suggest_auto_violation_rows={violations} is non-zero"
                    )

        try:
            suggest_cases = _get(report, scope, "suggest_gold_cases")
        except MetricsError as error:
            errors.append(str(error))
        else:
            if not isinstance(suggest_cases, int) or isinstance(suggest_cases, bool):
                errors.append(f"{scope}.suggest_gold_cases is not an integer")
            elif suggest_cases <= 0:
                errors.append(f"{scope}.suggest_gold_cases must be greater than zero")

        positive_partition_counts: list[tuple[str, int, str]] = []
        for field, authority_fields in METRICS_REPORT_CONTRACT[
            "positive_stratum_totals"
        ].items():
            authority = _authority_total(
                report, scope, authority_fields, errors
            )
            if authority is not None:
                expected, authority_label = authority
                positive_partition_counts.append(
                    (field, expected, authority_label)
                )

        for dimension in ("operation", "length", "position", "lexical", "feature"):
            try:
                buckets = _get(report, scope, f"by_{dimension}")
            except MetricsError as error:
                errors.append(str(error))
                continue
            if not isinstance(buckets, dict) or not buckets:
                errors.append(f"{scope}.by_{dimension} has no strata")
                continue
            expected_buckets = set(SCHEMA["metric_buckets"][scope][dimension])
            actual_buckets = set(buckets)
            for bucket in sorted(expected_buckets - actual_buckets):
                errors.append(
                    f"{scope}.by_{dimension} is missing bucket {bucket}"
                )
            for bucket in sorted(actual_buckets - expected_buckets):
                errors.append(
                    f"{scope}.by_{dimension} has unsupported bucket {bucket}"
                )
            for bucket, summary in buckets.items():
                if not isinstance(summary, dict):
                    errors.append(f"{scope}.by_{dimension}.{bucket} is not an object")
                    continue
                try:
                    cases = _get(summary, "cases")
                except MetricsError as error:
                    errors.append(
                        f"missing metric: {scope}.by_{dimension}.{bucket}.cases"
                    )
                else:
                    if not isinstance(cases, int) or cases <= 0:
                        errors.append(
                            f"{scope}.by_{dimension}.{bucket}.cases must be greater than zero"
                        )
                try:
                    total_candidates = _get(summary, "total_candidates")
                except MetricsError as error:
                    errors.append(
                        f"missing metric: {scope}.by_{dimension}.{bucket}.total_candidates"
                    )
                else:
                    if not isinstance(total_candidates, int) or total_candidates <= 0:
                        errors.append(
                            f"{scope}.by_{dimension}.{bucket}.total_candidates must be greater than zero"
                        )
                for metric_name in (
                    "raw_hypothesis_candidate_precision",
                    "raw_hypothesis_candidate_recall",
                    "raw_hypothesis_top1_accuracy",
                ):
                    try:
                        _rate(summary, metric_name)
                    except MetricsError as error:
                        errors.append(
                            f"{scope}.by_{dimension}.{bucket}: {error}"
                        )
                for numerator, denominator, rate in (
                    (
                        "correct_candidates",
                        "total_candidates",
                        "raw_hypothesis_candidate_precision",
                    ),
                    (
                        "correct_rows",
                        "cases",
                        "candidate_recall",
                    ),
                    (
                        "correct_rows",
                        "cases",
                        "raw_hypothesis_candidate_recall",
                    ),
                    (
                        "top1_correct",
                        "cases",
                        "top1_accuracy",
                    ),
                    (
                        "top1_correct",
                        "cases",
                        "raw_hypothesis_top1_accuracy",
                    ),
                ):
                    _validate_ratio(
                        summary,
                        (numerator,),
                        (denominator,),
                        (rate,),
                        errors,
                    )
                _validate_optional_ratio(
                    summary,
                    ("suggest_auto_violation_rows",),
                    ("suggest_cases",),
                    ("suggest_auto_violation_rate",),
                    errors,
                )
            for field, expected, authority in positive_partition_counts:
                _validate_partition_total(
                    buckets,
                    field,
                    expected,
                    scope,
                    f"by_{dimension}",
                    authority,
                    errors,
                )
            threshold = SCHEMA["stratum_thresholds"][scope][
                "raw_hypothesis_candidate_recall_min"
            ][dimension]
            for bucket, summary in buckets.items():
                try:
                    actual = _rate(summary, "raw_hypothesis_candidate_recall")
                except MetricsError as error:
                    errors.append(f"{scope}.by_{dimension}.{bucket}: {error}")
                    continue
                if actual < threshold:
                    errors.append(
                        f"{scope}.by_{dimension}.{bucket}."
                        f"raw_hypothesis_candidate_recall={actual:.6f} is below "
                        f"minimum {threshold:.6f}"
                    )

        negative_partition_counts: list[tuple[str, int, str]] = []
        for field, authority_fields in METRICS_REPORT_CONTRACT[
            "negative_stratum_totals"
        ].items():
            authority = _authority_total(
                report, scope, authority_fields, errors
            )
            if authority is not None:
                expected, authority_label = authority
                negative_partition_counts.append(
                    (field, expected, authority_label)
                )

        negative_fpr_max = RELEASE_THRESHOLDS[
            "candidate_false_positive_rate_max"
        ]
        for dimension in ("length", "position", "lexical", "feature"):
            try:
                buckets = _get(report, scope, f"negative_by_{dimension}")
            except MetricsError as error:
                errors.append(str(error))
                continue
            if not isinstance(buckets, dict):
                errors.append(f"{scope}.negative_by_{dimension} is not an object")
                continue
            expected_buckets = set(
                SCHEMA["negative_metric_buckets"][scope][dimension]
            )
            actual_buckets = set(buckets)
            for bucket in sorted(expected_buckets - actual_buckets):
                errors.append(
                    f"{scope}.negative_by_{dimension} is missing bucket {bucket}"
                )
            for bucket in sorted(actual_buckets - expected_buckets):
                errors.append(
                    f"{scope}.negative_by_{dimension} has unsupported bucket {bucket}"
                )
            for bucket, summary in buckets.items():
                try:
                    denominator = _get(summary, "raw_policy_forbidden_cases")
                except MetricsError as error:
                    errors.append(
                        f"{scope}.negative_by_{dimension}.{bucket}: {error}"
                    )
                    continue
                raw_denominator_required = (
                    STRICT_NEGATIVE_COVERAGE[scope]
                    .get("raw_forbidden", {})
                    .get(dimension, {})
                    .get(bucket, 0)
                    > 0
                )
                raw_denominator_valid = (
                    not isinstance(denominator, bool)
                    and isinstance(denominator, int)
                    and denominator > 0
                )
                if not raw_denominator_valid and raw_denominator_required:
                    errors.append(
                        f"{scope}.negative_by_{dimension}.{bucket}."
                        "raw_policy_forbidden_cases must be greater than zero"
                    )
                if raw_denominator_valid:
                    for rate_name in (
                        "candidate_false_positive_rate",
                        "raw_hypothesis_candidate_false_positive_rate",
                        "raw_policy_false_positive_rate",
                    ):
                        _validate_ratio(
                            summary,
                            ("raw_policy_forbidden_violations",),
                            ("raw_policy_forbidden_cases",),
                            (rate_name,),
                            errors,
                        )
                    actual = _rate(
                        summary, "raw_hypothesis_candidate_false_positive_rate"
                    )
                    if actual > negative_fpr_max:
                        errors.append(
                            f"{scope}.negative_by_{dimension}.{bucket}."
                            f"raw_hypothesis_candidate_false_positive_rate={actual:.6f} "
                            f"is above maximum {negative_fpr_max:.6f}"
                        )
                try:
                    display_denominator = _get(
                        summary, "display_policy_forbidden_cases"
                    )
                except MetricsError as error:
                    errors.append(
                        f"{scope}.negative_by_{dimension}.{bucket}: {error}"
                    )
                else:
                    if isinstance(display_denominator, bool) or not isinstance(
                        display_denominator, int
                    ):
                        errors.append(
                            f"{scope}.negative_by_{dimension}.{bucket}."
                            "display_policy_forbidden_cases is not an integer"
                        )
                    elif display_denominator < 0:
                        errors.append(
                            f"{scope}.negative_by_{dimension}.{bucket}."
                            "display_policy_forbidden_cases must not be negative"
                        )
                    elif display_denominator > 0:
                        _validate_ratio(
                            summary,
                            ("display_policy_forbidden_violations",),
                            ("display_policy_forbidden_cases",),
                            ("display_policy_raw_hypothesis_violation_rate",),
                            errors,
                        )
                _validate_ratio(
                    summary,
                    ("rows_with_candidates",),
                    ("cases",),
                    ("negative_candidate_rate",),
                    errors,
                )
                _validate_ratio(
                    summary,
                    ("rows_with_candidates",),
                    ("cases",),
                    ("negative_top1_wrong_rate",),
                    errors,
                )
                _validate_ratio(
                    summary,
                    ("auto_policy_violations",),
                    ("cases",),
                    ("auto_false_positive_rate",),
                    errors,
                )
            for policy, dimensions in STRICT_NEGATIVE_COVERAGE[scope].items():
                policy_field, policy_value = policy.split("_", 1)
                denominator_field = (
                    f"{policy_field}_policy_{policy_value}_cases"
                )
                for bucket, minimum in dimensions.get(dimension, {}).items():
                    try:
                        denominator = _get(
                            buckets, bucket, denominator_field
                        )
                    except MetricsError as error:
                        errors.append(
                            f"{scope}.negative_by_{dimension}.{bucket}: {error}"
                        )
                        continue
                    if not isinstance(denominator, int) or denominator < minimum:
                        errors.append(
                            f"{scope}.negative_by_{dimension}.{bucket}."
                            f"{denominator_field}={denominator} is below "
                            f"minimum {minimum}"
                        )
            for field, expected, authority in negative_partition_counts:
                _validate_partition_total(
                    buckets,
                    field,
                    expected,
                    scope,
                    f"negative_by_{dimension}",
                    authority,
                    errors,
                )

    candidate_fpr_max = RELEASE_THRESHOLDS["candidate_false_positive_rate_max"]
    for scope in ("roman", "kana"):
        try:
            actual = _rate(report, scope, "candidate_false_positive_rate")
        except MetricsError as error:
            errors.append(str(error))
            continue
        if actual > candidate_fpr_max:
            errors.append(
                f"{scope}.candidate_false_positive_rate={actual:.6f} is above "
                f"maximum {candidate_fpr_max:.6f}"
            )

    try:
        composer_cases = _get(report, "composer_replay", "cases")
        composer_recall = _rate(report, "composer_replay", "recall")
    except MetricsError as error:
        errors.append(str(error))
    else:
        if composer_cases != EVALUATION_TARGETS["roman_composer_engine_holdout"]:
            errors.append(
                f"composer_replay.cases={composer_cases} does not match schema target "
                f"{EVALUATION_TARGETS['roman_composer_engine_holdout']}"
            )
        _validate_ratio(
            report,
            ("composer_replay", "correct"),
            ("composer_replay", "cases"),
            ("composer_replay", "recall"),
            errors,
        )
        threshold = RELEASE_THRESHOLDS["composer_replay_recall_min"]
        if composer_recall < threshold:
            errors.append(
                f"composer_replay.recall={composer_recall:.6f} is below "
                f"minimum {threshold:.6f}"
            )

    try:
        engine_cases = _get(report, "engine_e2e", "cases")
        engine_recall = _rate(report, "engine_e2e", "engine_e2e_recall")
    except MetricsError as error:
        errors.append(str(error))
    else:
        if engine_cases != EVALUATION_TARGETS["roman_composer_engine_holdout"]:
            errors.append(
                f"engine_e2e.cases={engine_cases} does not match schema target "
                f"{EVALUATION_TARGETS['roman_composer_engine_holdout']}"
            )
        _validate_ratio(
            report,
            ("engine_e2e", "engine_correct"),
            ("engine_e2e", "cases"),
            ("engine_e2e", "engine_e2e_recall"),
            errors,
        )
        _validate_ratio(
            report,
            ("engine_e2e", "candidate_window_top1_correct"),
            ("engine_e2e", "cases"),
            ("engine_e2e", "candidate_window_top1_accuracy"),
            errors,
        )
        _validate_ratio(
            report,
            ("engine_e2e", "candidate_window_topk_correct"),
            ("engine_e2e", "cases"),
            ("engine_e2e", "candidate_window_topk_accuracy"),
            errors,
        )
        threshold = RELEASE_THRESHOLDS["engine_e2e_recall_min"]
        if engine_recall < threshold:
            errors.append(
                f"engine_e2e.engine_e2e_recall={engine_recall:.6f} is below "
                f"minimum {threshold:.6f}"
            )

        for metric_name, threshold, comparison in (
            (
                "candidate_window_top1_accuracy",
                RELEASE_THRESHOLDS["engine_candidate_window_top1_accuracy_min"],
                "min",
            ),
            (
                "candidate_window_topk_accuracy",
                RELEASE_THRESHOLDS["engine_candidate_window_topk_accuracy_min"],
                "min",
            ),
            (
                "kana_engine_e2e_recall",
                RELEASE_THRESHOLDS["kana_engine_e2e_recall_min"],
                "min",
            ),
            (
                "candidate_window_kana_top1_accuracy",
                RELEASE_THRESHOLDS[
                    "engine_candidate_window_kana_top1_accuracy_min"
                ],
                "min",
            ),
            (
                "candidate_window_kana_topk_accuracy",
                RELEASE_THRESHOLDS[
                    "engine_candidate_window_kana_topk_accuracy_min"
                ],
                "min",
            ),
            (
                "candidate_window_negative_false_positive_rate",
                RELEASE_THRESHOLDS[
                    "engine_candidate_window_negative_false_positive_rate_max"
                ],
                "max",
            ),
            (
                "candidate_window_kana_negative_false_positive_rate",
                RELEASE_THRESHOLDS[
                    "engine_candidate_window_negative_false_positive_rate_max"
                ],
                "max",
            ),
        ):
            try:
                actual = _rate(report, "engine_e2e", metric_name)
            except MetricsError as error:
                errors.append(str(error))
                continue
            if comparison == "min" and actual < threshold:
                errors.append(
                    f"engine_e2e.{metric_name}={actual:.6f} is below minimum "
                    f"{threshold:.6f}"
                )
            if comparison == "max" and actual > threshold:
                errors.append(
                    f"engine_e2e.{metric_name}={actual:.6f} is above maximum "
                    f"{threshold:.6f}"
                )
        for (
            cases_field,
            violations_field,
            rate_field,
            mode,
        ) in (
            (
                "candidate_window_negative_cases",
                "candidate_window_negative_violations",
                "candidate_window_negative_false_positive_rate",
                "roman",
            ),
            (
                "candidate_window_kana_negative_cases",
                "candidate_window_kana_negative_violations",
                "candidate_window_kana_negative_false_positive_rate",
                "kana",
            ),
        ):
            expected_cases = NEGATIVE_POLICY_TARGETS[mode]["display_forbidden"]
            try:
                actual_cases = _get(report, "engine_e2e", cases_field)
            except MetricsError as error:
                errors.append(str(error))
                continue
            if actual_cases != expected_cases:
                errors.append(
                    f"engine_e2e.{cases_field}={actual_cases} does not match "
                    f"schema target {expected_cases}"
                )
            _validate_ratio(
                report,
                ("engine_e2e", violations_field),
                ("engine_e2e", cases_field),
                ("engine_e2e", rate_field),
                errors,
            )
        try:
            kana_cases = _get(report, "engine_e2e", "kana_cases")
        except MetricsError as error:
            errors.append(str(error))
        else:
            expected_kana_cases = EVALUATION_TARGETS["kana_holdout"]
            if kana_cases != expected_kana_cases:
                errors.append(
                    f"engine_e2e.kana_cases={kana_cases} does not match schema target "
                    f"{expected_kana_cases}"
                )
            _validate_ratio(
                report,
                ("engine_e2e", "kana_engine_correct"),
                ("engine_e2e", "kana_cases"),
                ("engine_e2e", "kana_engine_e2e_recall"),
                errors,
            )
            _validate_ratio(
                report,
                ("engine_e2e", "candidate_window_kana_top1_correct"),
                ("engine_e2e", "kana_cases"),
                ("engine_e2e", "candidate_window_kana_top1_accuracy"),
                errors,
            )
            _validate_ratio(
                report,
                ("engine_e2e", "candidate_window_kana_topk_correct"),
                ("engine_e2e", "kana_cases"),
                ("engine_e2e", "candidate_window_kana_topk_accuracy"),
                errors,
            )

    return errors


def _read_report(path: pathlib.Path) -> dict[str, Any]:
    try:
        text = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
        report = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MetricsError(f"cannot read metrics JSON: {error}") from error
    if not isinstance(report, dict):
        raise MetricsError("metrics JSON root must be an object")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", type=pathlib.Path, help="metrics JSON file, or - for stdin")
    args = parser.parse_args(argv)
    try:
        errors = validate_metrics(_read_report(args.metrics))
    except MetricsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("ok: metrics satisfy schema release thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
