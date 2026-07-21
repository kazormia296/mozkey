from __future__ import annotations

import pathlib
import tempfile
import unittest

from tools.typing_correction import build_typing_correction_rules as builder
from tools.typing_correction import validate_typing_correction_corpus as target


class TypingCorrectionCorpusTest(unittest.TestCase):
    def _copy_corpus(self, root: pathlib.Path) -> None:
        for path in target.CORPUS_ROOT.iterdir():
            if path.is_file():
                (root / path.name).write_bytes(path.read_bytes())

    def test_repository_corpus_is_valid_and_has_expected_coverage(self) -> None:
        corpus = target.load_corpus()
        self.assertEqual(
            len(corpus.gold), target.EVALUATION_TARGETS["roman_gold"]
        )
        self.assertEqual(
            len(corpus.negative), target.EVALUATION_TARGETS["roman_negative"]
        )
        self.assertEqual(
            len(corpus.kana_gold), target.EVALUATION_TARGETS["kana_gold"]
        )
        self.assertEqual(
            len(corpus.kana_gold),
            target.EVALUATION_TARGETS["kana_gold_fixture"],
        )
        self.assertEqual(
            len(corpus.kana_evaluation), target.EVALUATION_TARGETS["kana_gold"]
        )
        self.assertEqual(
            len(corpus.kana_negative), target.EVALUATION_TARGETS["kana_negative"]
        )
        self.assertEqual(
            len(corpus.roman_holdout),
            target.EVALUATION_TARGETS["roman_composer_engine_holdout"],
        )
        self.assertEqual(
            len(corpus.kana_holdout), target.EVALUATION_TARGETS["kana_holdout"]
        )
        self.assertEqual(
            {case.row["operation"] for case in corpus.gold},
            set(target.ENUMS["roman_operations"]),
        )
        self.assertEqual(
            {case.row["behavior"] for case in corpus.gold}, {"auto", "suggest"}
        )
        self.assertEqual(
            {case.row["operation"] for case in corpus.kana_gold},
            set(target.ENUMS["kana_operations"]),
        )
        self.assertEqual(
            {case.row["behavior"] for case in corpus.kana_evaluation}, {"suggest"}
        )
        self.assertEqual(
            len(
                {
                    (case.row["typed_key_codes"], case.row["typed_key_strings"])
                    for case in corpus.kana_evaluation
                }
            ),
            len(corpus.kana_evaluation),
        )
        self.assertEqual(
            {case.row["operation"] for case in corpus.roman_holdout},
            set(target.ENUMS["roman_holdout_operations"]),
        )
        self.assertEqual(
            {case.row["operation"] for case in corpus.kana_holdout},
            set(target.ENUMS["kana_holdout_operations"]),
        )
        self.assertEqual(len(target.corpus_digest()), 64)

    def test_duplicate_case_id_fails_closed(self) -> None:
        corpus_root = pathlib.Path(target.CORPUS_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._copy_corpus(root)
            gold = root / target.GOLD_PATH.name
            contents = gold.read_text(encoding="utf-8")
            contents += contents.splitlines()[1] + "\n"
            gold.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(target.CorpusError, "duplicate"):
                target.load_corpus(root)
        self.assertTrue(corpus_root.is_dir())

    def test_malformed_gold_operation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._copy_corpus(root)
            gold = root / target.GOLD_PATH.name
            contents = gold.read_text(encoding="utf-8").replace(
                "R000001\tonegai\tonegia\tおねがい\ttranspose",
                "R000001\tonegai\tonegia\tおねがい\tunknown",
            )
            gold.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(target.CorpusError, "unknown Gold operation"):
                target.load_corpus(root)

    def test_contradictory_reading_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._copy_corpus(root)
            gold = root / target.GOLD_PATH.name
            contents = gold.read_text(encoding="utf-8")
            contents += (
                "R999999\tonegai\tonegia\tおねがえ\ttranspose\thigh\tauto\t"
                "length=short;position=medial;lexical=common;feature=none\tconflict\n"
            )
            gold.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(target.CorpusError, "contradictory"):
                target.load_corpus(root)

    def test_invalid_utf8_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._copy_corpus(root)
            (root / target.GOLD_PATH.name).write_bytes(b"\xff\n")
            with self.assertRaisesRegex(target.CorpusError, "UTF-8"):
                target.load_corpus(root)

    def test_generated_header_is_deterministic_and_contains_digest(self) -> None:
        first = builder.render_header()
        second = builder.render_header()
        self.assertEqual(first, second)
        self.assertIn(target.corpus_digest(), first)
        self.assertIn('"onegia", "onegai"', first)

    def test_checked_in_header_matches_generator(self) -> None:
        generated_contract = (
            pathlib.Path(target.REPO_ROOT)
            / "src"
            / "typing_correction"
            / "generated_contract.h"
        )
        self.assertEqual(
            generated_contract.read_text(encoding="utf-8"),
            builder.render_contract_header(),
        )

        generated = (
            pathlib.Path(target.REPO_ROOT)
            / "src"
            / "typing_correction"
            / "generated_roman_rules.h"
        )
        self.assertEqual(generated.read_text(encoding="utf-8"), builder.render_header())

        generated_kana = (
            pathlib.Path(target.REPO_ROOT)
            / "src"
            / "typing_correction"
            / "generated_kana_rules.h"
        )
        self.assertEqual(
            generated_kana.read_text(encoding="utf-8"),
            builder.render_kana_header(),
        )

        generated_holdout = (
            pathlib.Path(target.REPO_ROOT)
            / "src"
            / "typing_correction"
            / "generated_holdout_cases.h"
        )
        self.assertEqual(
            generated_holdout.read_text(encoding="utf-8"),
            builder.render_holdout_header(),
        )

    def test_holdout_changes_do_not_change_production_rule_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for path in target.CORPUS_ROOT.iterdir():
                if path.is_file():
                    (root / path.name).write_bytes(path.read_bytes())
            before = target.corpus_digest(root)
            holdout = root / target.ROMAN_HOLDOUT_PATH.name
            holdout.write_text(
                holdout.read_text(encoding="utf-8").replace(
                    "未登録の短語転置", "別の評価メモ"
                ),
                encoding="utf-8",
            )
            self.assertEqual(target.corpus_digest(root), before)


if __name__ == "__main__":
    unittest.main()
