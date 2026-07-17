#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from tools.dictionary import prepare_daily_dictionary_linux as target


class PrepareDailyDictionaryLinuxTest(unittest.TestCase):
    def test_cached_mode_forwards_skip_download(self) -> None:
        command = target.build_prepare_command(
            pwsh="/usr/bin/pwsh",
            source_mode="cached",
            sample_lines=123,
            bash_path="/bin/bash",
            prepare_script=pathlib.Path("prepare.ps1"),
        )
        self.assertIn("-SkipDownload", command)
        self.assertEqual(command[-2:], ["-BashPath", "/bin/bash"])
        self.assertEqual(command[command.index("-SampleLines") + 1], "123")

    def test_download_mode_does_not_forward_skip_download(self) -> None:
        command = target.build_prepare_command(
            pwsh="pwsh",
            source_mode="download",
            sample_lines=5000,
            bash_path=None,
            prepare_script=pathlib.Path("prepare.ps1"),
        )
        self.assertNotIn("-SkipDownload", command)

    def test_cached_sources_are_all_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            required = target.cached_sources(root)
            for path in required[:-1]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            self.assertEqual(target.missing_cached_sources(root), [required[-1]])

    def test_source_manifest_contains_stable_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            for index, path in enumerate(target.cached_sources(root)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"fixture-{index}\n", encoding="utf-8")

            records = target.source_records(root)
            self.assertTrue(all(record["exists"] for record in records))
            self.assertTrue(all(len(str(record["sha256"])) == 64 for record in records))

            output = root / "manifest.json"
            target.write_source_manifest("cached", "complete", records, output)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], target.SOURCE_MANIFEST_SCHEMA)
            self.assertEqual(manifest["files"], records)


if __name__ == "__main__":
    unittest.main()
