import unittest

from tools.typing_correction import validate_typing_correction_corpus as corpus
from tools.typing_correction import validate_typing_correction_metrics as metrics


def _passing_report() -> dict[str, object]:
    def distribute(total: int, bucket_count: int) -> list[int]:
        quotient, remainder = divmod(total, bucket_count)
        return [
            quotient + (1 if index < remainder else 0)
            for index in range(bucket_count)
        ]

    def bounded_distribution(total: int, capacities: list[int]) -> list[int]:
        result: list[int] = []
        remaining = total
        for capacity in capacities:
            value = min(capacity, remaining)
            result.append(value)
            remaining -= value
        if remaining:
            raise AssertionError("test fixture count exceeds bucket capacity")
        return result

    def strata(
        mode_name: str, gold: int, *, auto_enabled: bool, top1: float
    ) -> dict[str, dict[str, dict[str, object]]]:
        result: dict[str, dict[str, dict[str, object]]] = {}
        for dimension in ("operation", "length", "position", "lexical", "feature"):
            buckets = metrics.SCHEMA["metric_buckets"][mode_name][dimension]
            case_counts = distribute(gold, len(buckets))
            top1_counts = bounded_distribution(int(gold * top1), case_counts)
            auto_counts = bounded_distribution(
                1 if auto_enabled else 0, case_counts
            )
            result[f"by_{dimension}"] = {
                bucket: {
                    "cases": cases,
                    "rows_with_candidates": cases,
                    "total_candidates": cases,
                    "auto_candidates": auto_count,
                    "correct_candidates": cases,
                    "correct_rows": cases,
                    "top1_correct": top1_correct,
                    "correct_auto_candidates": auto_count,
                    "correct_auto_rows": auto_count,
                    "suggest_cases": cases - auto_count,
                    "suggest_auto_violation_rows": 0,
                    "candidate_recall": 1.0,
                    "raw_hypothesis_candidate_precision": 1.0,
                    "raw_hypothesis_candidate_recall": 1.0,
                    "top1_accuracy": top1_correct / cases,
                    "raw_hypothesis_top1_accuracy": top1_correct / cases,
                    "suggest_auto_violation_rate": 0.0,
                }
                for bucket, cases, top1_correct, auto_count in zip(
                    buckets, case_counts, top1_counts, auto_counts
                )
            }
        return result

    def negative_strata(
        mode_name: str, negative: int
    ) -> dict[str, dict[str, dict[str, object]]]:
        result: dict[str, dict[str, dict[str, object]]] = {}
        policy_targets = metrics.NEGATIVE_POLICY_TARGETS[mode_name]
        for dimension in ("length", "position", "lexical", "feature"):
            buckets = metrics.SCHEMA["negative_metric_buckets"][mode_name][
                dimension
            ]
            raw_minimums = (
                metrics.STRICT_NEGATIVE_COVERAGE[mode_name]
                .get("raw_forbidden", {})
                .get(dimension, {})
            )
            display_minimums = (
                metrics.STRICT_NEGATIVE_COVERAGE[mode_name]
                .get("display_forbidden", {})
                .get(dimension, {})
            )
            case_counts = [
                max(
                    1,
                    raw_minimums.get(bucket, 0),
                    display_minimums.get(bucket, 0),
                )
                for bucket in buckets
            ]
            case_counts[0] += negative - sum(case_counts)

            def allocate_policy(total: int, minimums: dict[str, int]) -> list[int]:
                counts = [minimums.get(bucket, 0) for bucket in buckets]
                remaining = total - sum(counts)
                for index, capacity in enumerate(case_counts):
                    addition = min(capacity - counts[index], remaining)
                    counts[index] += addition
                    remaining -= addition
                if remaining:
                    raise AssertionError(
                        "test fixture policy count exceeds bucket capacity"
                    )
                return counts

            raw_counts = allocate_policy(
                policy_targets["raw_forbidden"], raw_minimums
            )
            display_counts = allocate_policy(
                policy_targets["display_forbidden"], display_minimums
            )
            result[f"negative_by_{dimension}"] = {
                bucket: {
                    "cases": cases,
                    "rows_with_candidates": 0,
                    "raw_policy_forbidden_cases": raw_cases,
                    "raw_policy_forbidden_violations": 0,
                    "raw_policy_forbidden_candidates": 0,
                    "auto_candidates": 0,
                    "display_policy_forbidden_cases": display_cases,
                    "display_policy_forbidden_violations": 0,
                    "display_policy_raw_hypothesis_violation_rate": 0.0,
                    "negative_candidate_rate": 0.0,
                    "negative_top1_wrong_rate": 0.0,
                    "negative_rows_with_candidates": 0,
                    "auto_false_positive_rate": 0.0,
                    "auto_policy_violations": 0,
                    "raw_hypothesis_candidate_false_positive_rate": 0.0,
                    "candidate_false_positive_rate": 0.0,
                    "raw_policy_false_positive_rate": 0.0,
                }
                for bucket, cases, raw_cases, display_cases in zip(
                    buckets, case_counts, raw_counts, display_counts
                )
            }
        return result

    def mode(
        gold: int,
        negative: int,
        *,
        auto_enabled: bool,
        top1: float = 1.0,
    ) -> dict[str, object]:
        return {
            "gold_cases": gold,
            "negative_cases": negative,
            "candidate_precision": 1.0,
            "gold_candidate_precision": 1.0,
            "raw_hypothesis_candidate_precision": 1.0,
            "candidate_recall": 1.0,
            "raw_hypothesis_candidate_recall": 1.0,
            "top1_accuracy": top1,
            "raw_hypothesis_top1_accuracy": top1,
            "candidate_false_positive_rate": 0.0,
            "raw_hypothesis_candidate_false_positive_rate": 0.0,
            "raw_policy_false_positive_rate": 0.0,
            "display_policy_raw_hypothesis_violation_rate": 0.0,
            "negative_candidate_rate": 0.0,
            "negative_top1_wrong_rate": 0.0,
            "negative_rows_with_candidates": 0,
            "raw_policy_forbidden_cases": metrics.NEGATIVE_POLICY_TARGETS[
                "roman" if auto_enabled else "kana"
            ]["raw_forbidden"],
            "raw_policy_forbidden_violations": 0,
            "raw_policy_forbidden_candidates": 0,
            "display_policy_forbidden_cases": metrics.NEGATIVE_POLICY_TARGETS[
                "roman" if auto_enabled else "kana"
            ]["display_forbidden"],
            "display_policy_forbidden_violations": 0,
            "gold_total_candidates": gold,
            "negative_auto_candidates": 0,
            "suggest_recall": 1.0,
            "auto_recall": 1.0 if auto_enabled else 0.0,
            "auto_precision": 1.0 if auto_enabled else 0.0,
            "auto_false_positive_rate": 0.0,
            "auto_policy_violations": 0,
            "gold_auto_candidates": 1 if auto_enabled else 0,
            "auto_candidates": 1 if auto_enabled else 0,
            "auto_rows": 1 if auto_enabled else 0,
            "auto_gold_cases": 1 if auto_enabled else 0,
            "suggest_gold_cases": gold - (1 if auto_enabled else 0),
            "suggest_auto_violation_rows": 0,
            "suggest_auto_violation_rate": 0.0,
            "candidate_precision_numerator": gold,
            "candidate_precision_denominator": gold,
            "gold_candidate_precision_numerator": gold,
            "gold_candidate_precision_denominator": gold,
            "candidate_recall_numerator": gold,
            "candidate_recall_denominator": gold,
            "top1_accuracy_numerator": int(gold * top1),
            "top1_accuracy_denominator": gold,
            "auto_precision_numerator": 1 if auto_enabled else 0,
            "auto_precision_denominator": 1 if auto_enabled else 0,
            "auto_recall_numerator": 1 if auto_enabled else 0,
            "auto_recall_denominator": 1 if auto_enabled else 0,
            "suggest_recall_numerator": gold - (1 if auto_enabled else 0),
            "suggest_recall_denominator": gold - (1 if auto_enabled else 0),
            "suggest_auto_violation_rate_numerator": 0,
            "suggest_auto_violation_rate_denominator": gold
            - (1 if auto_enabled else 0),
            **negative_strata(
                "roman" if auto_enabled else "kana", negative
            ),
            **strata(
                "roman" if auto_enabled else "kana",
                gold,
                auto_enabled=auto_enabled,
                top1=top1,
            ),
        }

    return {
        "corpus_cases": {
            "total": corpus.EVALUATION_TARGETS["total_cases"],
            "roman_gold": corpus.EVALUATION_TARGETS["roman_gold"],
            "roman_negative": corpus.EVALUATION_TARGETS["roman_negative"],
            "kana_gold": corpus.EVALUATION_TARGETS["kana_gold"],
            "kana_negative": corpus.EVALUATION_TARGETS["kana_negative"],
            "roman_holdout": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "kana_holdout": corpus.EVALUATION_TARGETS["kana_holdout"],
        },
        "roman": mode(
            corpus.EVALUATION_TARGETS["roman_gold"],
            corpus.EVALUATION_TARGETS["roman_negative"],
            auto_enabled=True,
        ),
        "kana": mode(
            corpus.EVALUATION_TARGETS["kana_gold"],
            corpus.EVALUATION_TARGETS["kana_negative"],
            auto_enabled=False,
            top1=0.15,
        ),
        "composer_replay": {
            "cases": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "correct": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "recall": 1.0,
        },
        "engine_e2e": {
            "cases": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "engine_e2e_recall": 1.0,
            "engine_correct": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "candidate_window_top1_accuracy": 1.0,
            "candidate_window_topk_accuracy": 1.0,
            "candidate_window_top1_correct": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "candidate_window_topk_correct": corpus.EVALUATION_TARGETS[
                "roman_composer_engine_holdout"
            ],
            "kana_cases": corpus.EVALUATION_TARGETS["kana_holdout"],
            "kana_engine_e2e_recall": 1.0,
            "kana_engine_correct": corpus.EVALUATION_TARGETS["kana_holdout"],
            "candidate_window_kana_top1_accuracy": 1.0,
            "candidate_window_kana_top1_correct": corpus.EVALUATION_TARGETS[
                "kana_holdout"
            ],
            "candidate_window_kana_topk_accuracy": 1.0,
            "candidate_window_kana_topk_correct": corpus.EVALUATION_TARGETS[
                "kana_holdout"
            ],
            "candidate_window_negative_cases": metrics.NEGATIVE_POLICY_TARGETS[
                "roman"
            ]["display_forbidden"],
            "candidate_window_negative_violations": 0,
            "candidate_window_negative_false_positive_rate": 0.0,
            "candidate_window_kana_negative_cases": metrics.NEGATIVE_POLICY_TARGETS[
                "kana"
            ]["display_forbidden"],
            "candidate_window_kana_negative_violations": 0,
            "candidate_window_kana_negative_false_positive_rate": 0.0,
        },
    }


class TypingCorrectionMetricsTest(unittest.TestCase):
    def test_passing_report_uses_schema_targets(self) -> None:
        self.assertEqual(metrics.validate_metrics(_passing_report()), [])

    def test_missing_engine_recall_is_not_a_release_report(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["engine_e2e_recall"] = None  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("engine_e2e_recall" in error for error in errors))

    def test_composer_recall_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["composer_replay"]["correct"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "composer_replay.recall" in error and "does not match" in error
                for error in errors
            )
        )

    def test_gold_recall_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["kana"]["candidate_recall_numerator"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "kana.candidate_recall" in error and "does not match" in error
                for error in errors
            )
        )

    def test_gold_denominators_must_match_authoritative_counts(self) -> None:
        for field in (
            "candidate_recall_denominator",
            "top1_accuracy_denominator",
            "gold_candidate_precision_denominator",
            "candidate_precision_denominator",
            "auto_recall_denominator",
            "suggest_recall_denominator",
            "suggest_auto_violation_rate_denominator",
            "auto_precision_denominator",
        ):
            with self.subTest(field=field):
                report = _passing_report()
                report["roman"][field] -= 1  # type: ignore[index,operator]
                errors = metrics.validate_metrics(report)
                self.assertTrue(
                    any(
                        f"roman.{field}=" in error
                        and "authoritative" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_gold_recall_cannot_be_reduced_to_self_reported_one_of_one(
        self,
    ) -> None:
        report = _passing_report()
        report["roman"]["candidate_recall_numerator"] = 1  # type: ignore[index]
        report["roman"]["candidate_recall_denominator"] = 1  # type: ignore[index]
        report["roman"]["candidate_recall"] = 1.0  # type: ignore[index]
        report["roman"]["raw_hypothesis_candidate_recall"] = 1.0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.candidate_recall_denominator=1" in error
                and "roman.gold_cases=250" in error
                for error in errors
            )
        )

    def test_each_gold_dimension_must_partition_all_gold_cases(self) -> None:
        for dimension in ("operation", "length", "position", "lexical", "feature"):
            with self.subTest(dimension=dimension):
                report = _passing_report()
                buckets = report["roman"][f"by_{dimension}"]  # type: ignore[index]
                summary = next(reversed(buckets.values()))  # type: ignore[union-attr]
                for field in (
                    "cases",
                    "rows_with_candidates",
                    "total_candidates",
                    "correct_candidates",
                    "correct_rows",
                    "top1_correct",
                    "suggest_cases",
                ):
                    summary[field] -= 1  # type: ignore[index,operator]
                errors = metrics.validate_metrics(report)
                self.assertTrue(
                    any(
                        f"roman.by_{dimension}.cases total=249" in error
                        and "roman.gold_cases=250" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_three_reported_length_cases_cannot_stand_in_for_roman_gold(
        self,
    ) -> None:
        report = _passing_report()
        for summary in report["roman"]["by_length"].values():  # type: ignore[index,union-attr]
            summary.update(  # type: ignore[union-attr]
                {
                    "cases": 1,
                    "rows_with_candidates": 1,
                    "total_candidates": 1,
                    "auto_candidates": 0,
                    "correct_candidates": 1,
                    "correct_rows": 1,
                    "top1_correct": 1,
                    "correct_auto_candidates": 0,
                    "correct_auto_rows": 0,
                    "suggest_cases": 1,
                    "candidate_recall": 1.0,
                    "raw_hypothesis_candidate_precision": 1.0,
                    "raw_hypothesis_candidate_recall": 1.0,
                    "top1_accuracy": 1.0,
                    "raw_hypothesis_top1_accuracy": 1.0,
                }
            )
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.by_length.cases total=3" in error
                and "roman.gold_cases=250" in error
                for error in errors
            )
        )

    def test_gold_strata_candidate_totals_must_match_aggregate(self) -> None:
        report = _passing_report()
        summary = report["roman"]["by_length"]["short"]  # type: ignore[index]
        summary["total_candidates"] -= 1  # type: ignore[index,operator]
        summary["correct_candidates"] -= 1  # type: ignore[index,operator]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.by_length.total_candidates total=249" in error
                and "roman.gold_total_candidates=250" in error
                for error in errors
            )
        )

    def test_gold_strata_correct_auto_rows_must_match_auto_recall(self) -> None:
        report = _passing_report()
        report["roman"]["by_length"]["short"]["correct_auto_rows"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.by_length.correct_auto_rows total=0" in error
                and "roman.auto_recall_numerator=1" in error
                for error in errors
            )
        )

    def test_each_negative_dimension_must_partition_all_negative_cases(
        self,
    ) -> None:
        for dimension in ("length", "position", "lexical", "feature"):
            with self.subTest(dimension=dimension):
                report = _passing_report()
                buckets = report["kana"][f"negative_by_{dimension}"]  # type: ignore[index]
                summary = next(reversed(buckets.values()))  # type: ignore[union-attr]
                summary["cases"] -= 1  # type: ignore[index,operator]
                errors = metrics.validate_metrics(report)
                self.assertTrue(
                    any(
                        f"kana.negative_by_{dimension}.cases total=149"
                        in error
                        and "kana.negative_cases=150" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_gold_precision_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["roman"]["gold_candidate_precision_numerator"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.gold_candidate_precision" in error
                and "does not match" in error
                for error in errors
            )
        )

    def test_gold_stratum_recall_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["roman"]["by_length"]["short"]["correct_rows"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "candidate_recall=1.000000" in error
                and "does not match" in error
                for error in errors
            )
        )

    def test_gold_stratum_top1_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["kana"]["by_feature"]["kana_neighbor"]["top1_correct"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "top1_accuracy=" in error and "does not match" in error
                for error in errors
            )
        )

    def test_kana_engine_recall_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["kana_engine_correct"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "kana_engine_e2e_recall" in error and "does not match" in error
                for error in errors
            )
        )

    def test_engine_topk_must_match_correct_count(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["candidate_window_kana_topk_correct"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "candidate_window_kana_topk_accuracy" in error
                and "does not match" in error
                for error in errors
            )
        )

    def test_threshold_failure_is_reported(self) -> None:
        report = _passing_report()
        report["roman"]["raw_hypothesis_top1_accuracy"] = 0.0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("roman.raw_hypothesis_top1_accuracy" in error for error in errors))

    def test_rate_outside_unit_interval_is_rejected(self) -> None:
        report = _passing_report()
        report["roman"]["raw_hypothesis_candidate_recall"] = 2.0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("outside [0, 1]" in error for error in errors))

    def test_negative_rate_is_rejected(self) -> None:
        report = _passing_report()
        report["kana"]["candidate_false_positive_rate"] = -0.1  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("outside [0, 1]" in error for error in errors))

    def test_zero_strict_negative_denominator_is_rejected(self) -> None:
        report = _passing_report()
        report["kana"]["raw_policy_forbidden_cases"] = 0  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("raw_policy_forbidden_cases" in error for error in errors))

    def test_zero_strict_negative_stratum_denominator_is_rejected(self) -> None:
        report = _passing_report()
        report["kana"]["negative_by_length"]["medium"][  # type: ignore[index]
            "raw_policy_forbidden_cases"
        ] = 0
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any("negative_by_length.medium" in error for error in errors)
        )

    def test_missing_stratum_bucket_is_rejected(self) -> None:
        report = _passing_report()
        del report["roman"]["by_length"]["short"]  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(any("missing bucket short" in error for error in errors))

    def test_engine_negative_case_count_must_match_schema_target(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["candidate_window_negative_cases"] -= 1  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any("candidate_window_negative_cases" in error for error in errors)
        )

    def test_kana_engine_negative_case_count_must_match_schema_target(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["candidate_window_kana_negative_cases"] -= 1  # type: ignore[index]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "candidate_window_kana_negative_cases" in error
                for error in errors
            )
        )

    def test_engine_negative_rate_must_match_violation_count(self) -> None:
        report = _passing_report()
        report["engine_e2e"]["candidate_window_negative_violations"] = (  # type: ignore[index]
            report["engine_e2e"]["candidate_window_negative_cases"]  # type: ignore[index]
        )
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "candidate_window_negative_false_positive_rate" in error
                and "does not match" in error
                for error in errors
            )
        )

    def test_raw_negative_rate_must_match_violation_count(self) -> None:
        report = _passing_report()
        report["roman"]["raw_policy_forbidden_violations"] = report["roman"][  # type: ignore[index]
            "raw_policy_forbidden_cases"
        ]
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any(
                "roman.candidate_false_positive_rate" in error
                and "does not match" in error
                for error in errors
            )
        )

    def test_rate_numerator_cannot_exceed_denominator(self) -> None:
        report = _passing_report()
        report["kana"]["auto_policy_violations"] = (  # type: ignore[index]
            report["kana"]["negative_cases"] + 1  # type: ignore[index]
        )
        errors = metrics.validate_metrics(report)
        self.assertTrue(
            any("auto_policy_violations" in error and "exceeds" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
