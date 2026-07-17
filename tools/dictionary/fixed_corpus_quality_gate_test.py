#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from tools.dictionary import fixed_corpus_quality_gate as target


class FixedCorpusQualityGateTest(unittest.TestCase):
    def fixture_paths(self) -> dict[str, pathlib.Path]:
        return {
            "base_mozc": target.FIXTURE_DIR / "base_mozc.tsv",
            "hazkey": target.FIXTURE_DIR / "hazkey.tsv",
            "mozkey": target.FIXTURE_DIR / "mozkey.tsv",
        }

    def test_fixture_passes_union_oracle_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = target.run_gate(
                target.FIXTURE_DIR / "corpus.tsv",
                self.fixture_paths(),
                pathlib.Path(temp_dir),
            )
            self.assertEqual(summary["decision"], "pass")
            self.assertEqual(summary["counts"]["oracle_required"], 2)
            self.assertEqual(summary["counts"]["regressions"], 0)
            self.assertEqual(summary["counts"]["mozkey_improvements"], 1)
            self.assertTrue((pathlib.Path(temp_dir) / "comparison.tsv").is_file())
            self.assertTrue((pathlib.Path(temp_dir) / "summary.json").is_file())

    def test_hazkey_only_case_regression_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            mozkey_result = temp_root / "mozkey.tsv"
            lines = (target.FIXTURE_DIR / "mozkey.tsv").read_text(
                encoding="utf-8"
            ).splitlines()
            lines[1] = lines[1].replace("OK:", "FAILED:", 1).replace(
                "\t対人スキル\tConversion", "\t対人技能\tConversion", 1
            )
            mozkey_result.write_text("\n".join(lines) + "\n", encoding="utf-8")
            paths = self.fixture_paths()
            paths["mozkey"] = mozkey_result

            summary = target.run_gate(
                target.FIXTURE_DIR / "corpus.tsv",
                paths,
                temp_root / "artifacts",
            )
            self.assertEqual(summary["decision"], "fail")
            self.assertEqual(summary["counts"]["regressions"], 1)

    def test_reordered_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            result = temp_root / "reordered.tsv"
            lines = (target.FIXTURE_DIR / "mozkey.tsv").read_text(
                encoding="utf-8"
            ).splitlines()
            lines[0], lines[1] = lines[1], lines[0]
            result.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "misalignment"):
                target.load_engine_result(
                    result,
                    target.load_corpus(target.FIXTURE_DIR / "corpus.tsv"),
                    "mozkey",
                )

    def test_invalid_gate_input_keeps_error_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = pathlib.Path(temp_dir)
            result = temp_root / "truncated.tsv"
            result.write_text(
                (target.FIXTURE_DIR / "mozkey.tsv")
                .read_text(encoding="utf-8")
                .splitlines()[0]
                + "\n",
                encoding="utf-8",
            )
            out_dir = temp_root / "artifacts"
            with contextlib.redirect_stderr(io.StringIO()):
                exit_code = target.main(
                    [
                        "gate",
                        "--corpus",
                        str(target.FIXTURE_DIR / "corpus.tsv"),
                        "--base-mozc-result",
                        str(target.FIXTURE_DIR / "base_mozc.tsv"),
                        "--hazkey-result",
                        str(target.FIXTURE_DIR / "hazkey.tsv"),
                        "--mozkey-result",
                        str(result),
                        "--out-dir",
                        str(out_dir),
                    ]
                )
            self.assertEqual(exit_code, 2)
            summary = json.loads(
                (out_dir / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["decision"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
