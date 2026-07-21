#!/usr/bin/env python3
"""Print deterministic coverage statistics for the typing-correction corpus."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.typing_correction.validate_typing_correction_corpus import (
    CORPUS_ROOT,
    COVERAGE_MINIMUMS,
    EVALUATION_TARGETS,
    SCHEMA,
    STRATUM_FIELDS,
    corpus_digest,
    load_corpus,
)


def _stratum_counts(cases: object) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for dimension in STRATUM_FIELDS:
        values = Counter()
        for case in cases:  # type: ignore[union-attr]
            fields = dict(item.split("=", 1) for item in case.row["stratum"].split(";"))
            values[fields[dimension]] += 1
        result[dimension] = dict(sorted(values.items()))
    return result


def report(root: pathlib.Path = CORPUS_ROOT) -> dict[str, object]:
    corpus = load_corpus(root)
    total = (
        len(corpus.gold)
        + len(corpus.negative)
        + len(corpus.kana_evaluation)
        + len(corpus.kana_negative)
        + len(corpus.roman_holdout)
        + len(corpus.kana_holdout)
    )
    return {
        "schema_version": SCHEMA["version"],
        "evaluation_targets": EVALUATION_TARGETS,
        "coverage_minimums": COVERAGE_MINIMUMS,
        "total_evaluation_cases": total,
        "gold": len(corpus.gold),
        "negative": len(corpus.negative),
        "overrides": len(corpus.overrides),
        "kana_gold": len(corpus.kana_gold),
        "kana_gold_fixture": len(corpus.kana_gold),
        "kana_gold_evaluation": len(corpus.kana_evaluation),
        "kana_negative": len(corpus.kana_negative),
        "roman_holdout": len(corpus.roman_holdout),
        "kana_holdout": len(corpus.kana_holdout),
        "gold_by_operation": dict(
            sorted(Counter(case.row["operation"] for case in corpus.gold).items())
        ),
        "gold_by_behavior": dict(
            sorted(Counter(case.row["behavior"] for case in corpus.gold).items())
        ),
        "gold_by_stratum": _stratum_counts(corpus.gold),
        "negative_by_reason": dict(
            sorted(Counter(case.row["reason"] for case in corpus.negative).items())
        ),
        "negative_by_raw_policy": dict(
            sorted(
                Counter(
                    case.row["raw_policy"] for case in corpus.negative
                ).items()
            )
        ),
        "negative_by_display_policy": dict(
            sorted(
                Counter(
                    case.row["display_policy"] for case in corpus.negative
                ).items()
            )
        ),
        "negative_by_stratum": _stratum_counts(corpus.negative),
        "kana_gold_by_operation": dict(
            sorted(
                Counter(case.row["operation"] for case in corpus.kana_gold).items()
            )
        ),
        "kana_gold_by_stratum": _stratum_counts(corpus.kana_gold),
        "kana_gold_fixture_by_stratum": _stratum_counts(corpus.kana_gold),
        "kana_gold_evaluation_by_stratum": _stratum_counts(corpus.kana_evaluation),
        "kana_gold_evaluation_by_behavior": dict(
            sorted(
                Counter(case.row["behavior"] for case in corpus.kana_evaluation).items()
            )
        ),
        "kana_negative_by_stratum": _stratum_counts(corpus.kana_negative),
        "kana_negative_by_raw_policy": dict(
            sorted(
                Counter(
                    case.row["raw_policy"]
                    for case in corpus.kana_negative
                ).items()
            )
        ),
        "kana_negative_by_display_policy": dict(
            sorted(
                Counter(
                    case.row["display_policy"]
                    for case in corpus.kana_negative
                ).items()
            )
        ),
        "roman_holdout_by_operation": dict(
            sorted(
                Counter(case.row["operation"] for case in corpus.roman_holdout).items()
            )
        ),
        "kana_holdout_by_operation": dict(
            sorted(
                Counter(case.row["operation"] for case in corpus.kana_holdout).items()
            )
        ),
        "roman_holdout_by_stratum": _stratum_counts(corpus.roman_holdout),
        "kana_holdout_by_stratum": _stratum_counts(corpus.kana_holdout),
        "composer_engine_e2e_cases": len(corpus.roman_holdout),
        "corpus_sha256": corpus_digest(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=pathlib.Path, default=CORPUS_ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(report(args.corpus_root), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
