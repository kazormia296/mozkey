#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tools.dictionary import daily_source_lock
from tools.dictionary import prepare_daily_dictionary_linux as target


class PrepareDailyDictionaryLinuxTest(unittest.TestCase):
    def test_auto_backend_uses_native_for_public_release(self) -> None:
        self.assertEqual(
            target.resolve_backend("auto", daily_source_lock.RELEASE_PROFILE),
            "native",
        )
        self.assertEqual(
            target.resolve_backend("auto", daily_source_lock.LOCAL_PROFILE),
            "powershell",
        )

    def test_native_command_does_not_require_pwsh(self) -> None:
        command = target.build_native_prepare_command(
            source_mode="download",
            sample_lines=321,
            prepare_script=pathlib.Path("prepare_native.py"),
        )
        self.assertEqual(command[1], "prepare_native.py")
        self.assertEqual(
            command[-4:], ["--source-mode", "download", "--sample-lines", "321"]
        )

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

    def test_release_profile_is_explicitly_forwarded(self) -> None:
        command = target.build_prepare_command(
            pwsh="pwsh",
            source_mode="download",
            sample_lines=5000,
            bash_path=None,
            prepare_script=pathlib.Path("prepare.ps1"),
            profile=daily_source_lock.RELEASE_PROFILE,
        )
        self.assertIn("-ReleaseApprovedOnly", command)

    def test_cached_sources_are_all_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            required = target.cached_sources(root)
            for path in required[:-1]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            self.assertEqual(target.missing_cached_sources(root), [required[-1]])

    def test_release_cached_sources_exclude_nico(self) -> None:
        paths = target.cached_source_paths(daily_source_lock.RELEASE_PROFILE)
        self.assertNotIn(target.NICO_INPUT_PATH, paths)
        self.assertIn(target.PERSONAL_NAMES_INPUT_PATH, paths)

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

    def test_release_output_manifest_is_an_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            for index, relative in enumerate(target.RELEASE_OUTPUT_PATHS):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"release-{index}\n", encoding="utf-8")
            stale_relative = target.NICO_INPUT_PATH.parent / "stale-local-only.txt"
            stale_nico = root / stale_relative
            stale_nico.parent.mkdir(parents=True, exist_ok=True)
            stale_nico.write_text("must not enter release manifest\n", encoding="utf-8")
            records = target.release_output_records(root)
            output = root / "release-output.json"
            target.write_release_output_manifest(records, output)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["files"], records)
            self.assertEqual(
                manifest["excluded_source_ids"], ["dic-nico-intersection-pixiv"]
            )
            self.assertTrue(
                all("nico" not in str(record["path"]) for record in records)
            )
            self.assertNotIn(stale_relative.as_posix(), {r["path"] for r in records})

    def test_release_outputs_are_captured_only_after_pipeline_completion(self) -> None:
        pipeline_completed = False
        source_records = [{"path": "cached.txt", "exists": True}]
        release_records = [
            {"path": "release.txt", "sha256": "a" * 64, "size_bytes": 1}
        ]

        def run_pipeline(*args: object, **kwargs: object) -> None:
            nonlocal pipeline_completed
            pipeline_completed = True

        def capture_outputs() -> list[dict[str, object]]:
            self.assertTrue(
                pipeline_completed,
                "release outputs were inspected before the preparation pipeline ran",
            )
            return release_records

        with (
            mock.patch.object(target, "resolve_pwsh", return_value="/bin/pwsh"),
            mock.patch.object(target.daily_source_lock, "load_lock", return_value={}),
            mock.patch.object(target, "source_records", return_value=source_records),
            mock.patch.object(target.subprocess, "run", side_effect=run_pipeline),
            mock.patch.object(
                target, "release_output_records", side_effect=capture_outputs
            ) as release_output_records,
            mock.patch.object(target, "write_source_manifest"),
            mock.patch.object(target, "write_release_output_manifest") as write_release,
        ):
            result = target.main(
                [
                    "--source-mode",
                    "download",
                    "--profile",
                    daily_source_lock.RELEASE_PROFILE,
                ]
            )

        self.assertEqual(result, 0)
        release_output_records.assert_called_once_with()
        write_release.assert_called_once_with(release_records)

    def test_missing_release_outputs_report_post_pipeline_records(self) -> None:
        after_records = [{"path": "cached.txt", "exists": True}]
        with (
            mock.patch.object(target, "resolve_pwsh", return_value="/bin/pwsh"),
            mock.patch.object(target.daily_source_lock, "load_lock", return_value={}),
            mock.patch.object(target, "source_records", return_value=after_records),
            mock.patch.object(target.subprocess, "run"),
            mock.patch.object(
                target,
                "release_output_records",
                side_effect=RuntimeError("missing release output"),
            ),
            mock.patch.object(target, "write_source_manifest") as write_source,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing release output"):
                target.main(
                    [
                        "--source-mode",
                        "download",
                        "--profile",
                        daily_source_lock.RELEASE_PROFILE,
                    ]
                )

        self.assertIn(
            mock.call(
                source_mode="download",
                status="release_output_incomplete",
                records=after_records,
                profile=daily_source_lock.RELEASE_PROFILE,
            ),
            write_source.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
