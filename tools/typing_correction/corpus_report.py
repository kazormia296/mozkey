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
    corpus_digest,
    load_corpus,
)


def report(root: pathlib.Path = CORPUS_ROOT) -> dict[str, object]:
    corpus = load_corpus(root)
    return {
        "gold": len(corpus.gold),
        "negative": len(corpus.negative),
        "overrides": len(corpus.overrides),
        "kana_gold": len(corpus.kana_gold),
        "kana_negative": len(corpus.kana_negative),
        "roman_holdout": len(corpus.roman_holdout),
        "kana_holdout": len(corpus.kana_holdout),
        "gold_by_operation": dict(
            sorted(Counter(case.row["operation"] for case in corpus.gold).items())
        ),
        "gold_by_behavior": dict(
            sorted(Counter(case.row["behavior"] for case in corpus.gold).items())
        ),
        "negative_by_reason": dict(
            sorted(Counter(case.row["reason"] for case in corpus.negative).items())
        ),
        "kana_gold_by_operation": dict(
            sorted(
                Counter(case.row["operation"] for case in corpus.kana_gold).items()
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
