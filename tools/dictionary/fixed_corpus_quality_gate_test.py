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

    def test_exact_mcnemar_matches_frozen_corpus_reference_counts(self) -> None:
        self.assertAlmostEqual(
            target.exact_mcnemar_two_sided(146, 138),
            0.6779427203063515,
        )
        self.assertAlmostEqual(
            target.exact_mcnemar_two_sided(2, 17),
            0.000728607177734375,
        )
        self.assertEqual(target.exact_mcnemar_two_sided(0, 0), 1.0)

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
            self.assertEqual(
                summary["categories"]["uncategorized"]["engines"]["mozkey"],
                {"passed": 3, "failed": 1},
            )
            paired = summary["paired_diagnostics"]["scopes"]["all_cases"][
                "comparisons"
            ]
            self.assertEqual(
                paired["hazkey_vs_mozkey"]["left_only"],
                0,
            )
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

    def test_ab_probe_corpus_header_uses_quality_regression_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            corpus = pathlib.Path(temp_dir) / "corpus.tsv"
            corpus.write_text(
                "id\treading\texpected\tcategory\n"
                "case-1\tよみ\t読み|ヨミ\tgeneral\n",
                encoding="utf-8",
            )
            cases = target.load_corpus(corpus)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].mode, "Conversion Expected")
            self.assertEqual(cases[0].category, "general")
            self.assertTrue(cases[0].accepts("ヨミ"))

    def test_normalizes_immutable_ab_probe_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            corpus = root / "corpus.tsv"
            corpus.write_text(
                "id\treading\texpected\tcategory\n"
                "case-1\tよみ\t読み\tgeneral\n"
                "case-2\tしっぱい\t成功\tgeneral\n",
                encoding="utf-8",
            )
            corpus_identity = {
                "cases": 2,
                "sha256": f"sha256:{target.sha256_file(corpus)}",
            }
            common = {
                "backend": "Hazkey",
                "backend_version": "0.2.1",
                "category": "general",
                "converter_backend": "hazkey",
                "corpus": corpus_identity,
                "measurement": {"warmups": 0, "iterations": 1},
                "resource": {"fingerprint": "sha256:dictionary"},
                "schema": target.AB_PROBE_SCHEMA,
                "source_ref": "source-revision",
                "top_k": 10,
            }
            records = [
                {**common, "id": "case-1", "reading": "よみ", "candidates": ["読み"]},
                {
                    **common,
                    "id": "case-2",
                    "reading": "しっぱい",
                    "candidates": ["失敗"],
                },
            ]
            results = root / "hazkey.jsonl"
            results.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            output = root / "hazkey.tsv"
            exit_code = target.main(
                [
                    "normalize-ab-probe",
                    "--corpus",
                    str(corpus),
                    "--results",
                    str(results),
                    "--output",
                    str(output),
                    "--evidence-id",
                    "hazkey-frozen",
                    "--expected-converter-backend",
                    "hazkey",
                    "--expected-backend",
                    "Hazkey",
                ]
            )
            self.assertEqual(exit_code, 0)
            rows = [
                target.parse_row(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row.status for row in rows if row], ["OK", "FAILED"])
            self.assertTrue(all(row.version.startswith("hazkey-frozen;") for row in rows if row))
            self.assertTrue(
                all(
                    row.version.endswith("top_k=10;warmups=0;iterations=1")
                    for row in rows
                    if row
                )
            )

    def test_reused_result_artifact_is_not_independent_evidence(self) -> None:
        paths = self.fixture_paths()
        paths["hazkey"] = paths["base_mozc"]
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "three distinct files"):
                target.run_gate(
                    target.FIXTURE_DIR / "corpus.tsv",
                    paths,
                    pathlib.Path(temp_dir),
                )

    def test_copied_result_artifact_is_not_independent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            copied = root / "copied-hazkey.tsv"
            copied.write_bytes((target.FIXTURE_DIR / "base_mozc.tsv").read_bytes())
            paths = self.fixture_paths()
            paths["hazkey"] = copied
            with self.assertRaisesRegex(ValueError, "distinct SHA-256"):
                target.run_gate(
                    target.FIXTURE_DIR / "corpus.tsv",
                    paths,
                    root / "artifacts",
                )


if __name__ == "__main__":
    unittest.main()
