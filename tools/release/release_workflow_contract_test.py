from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import unittest


class ReleaseWorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[2]
        cls.workflow = (
            cls.repository / ".github" / "workflows" / "release.yaml"
        ).read_text(encoding="utf-8")
        cls.jobs = cls._split_job_blocks(cls.workflow)

    @staticmethod
    def _split_job_blocks(workflow: str) -> dict[str, str]:
        jobs = workflow.split("\njobs:\n", maxsplit=1)[1]
        matches = list(
            re.finditer(r"^  ([a-z][a-z0-9_-]*):\n", jobs, re.MULTILINE)
        )
        return {
            match.group(1): jobs[
                match.start() : matches[index + 1].start()
                if index + 1 < len(matches)
                else len(jobs)
            ]
            for index, match in enumerate(matches)
        }

    def test_release_is_tag_only_and_gate_validates_dispatch_refs(self) -> None:
        trigger = self.workflow.split("\nconcurrency:\n", maxsplit=1)[0]
        self.assertIn('      - "v*.*.*"', trigger)
        self.assertIn("  workflow_dispatch: {}", trigger)
        self.assertNotIn("branches:", trigger)

        gate = self.jobs["release-gate"]
        self.assertIn("fetch-depth: 0", gate)
        self.assertIn("RELEASE_REF_TYPE: ${{ github.ref_type }}", gate)
        self.assertIn("tools/release/validate_mozkey_release.py", gate)
        self.assertIn("--main-ref origin/main", gate)
        self.assertIn(
            "Refuse an already published release before product builds",
            gate,
        )

    def test_only_publish_has_write_permission(self) -> None:
        writers = [
            name
            for name, block in self.jobs.items()
            if re.search(r"(?m)^      contents: write$", block)
        ]
        self.assertEqual(writers, ["publish"])
        self.assertIn("permissions:\n  contents: read", self.workflow)

    def test_all_platform_release_jobs_are_gated(self) -> None:
        for platform in ("android", "linux", "macos", "windows"):
            with self.subTest(platform=platform):
                block = self.jobs[platform]
                self.assertIn("needs: release-gate", block)
                self.assertIn(
                    f"uses: ./.github/workflows/{platform}.yaml",
                    block,
                )
                self.assertIn("release: true", block)
                self.assertNotIn("secrets: inherit", block)

                called_workflow = (
                    self.repository / ".github" / "workflows" / f"{platform}.yaml"
                ).read_text(encoding="utf-8")
                self.assertRegex(
                    called_workflow,
                    r"(?ms)^  workflow_call:\n    inputs:\n      release:\n"
                    r".*?        type: boolean",
                )

        self.assertIn("needs: release-gate", self.jobs["release-notes"])
        self.assertIn(
            "needs: [release-gate, android, linux, macos, windows, release-notes]",
            self.jobs["publish"],
        )

    def test_release_artifact_names_are_unique(self) -> None:
        names: list[str] = []
        for platform in ("android", "linux", "macos", "windows"):
            called_workflow = (
                self.repository / ".github" / "workflows" / f"{platform}.yaml"
            ).read_text(encoding="utf-8")
            platform_names = re.findall(
                r"(?m)^\s+name: (release-[A-Za-z0-9_.-]+)\s*$",
                called_workflow,
            )
            self.assertTrue(platform_names, f"{platform} has no release-* artifact")
            names.extend(platform_names)

        duplicates = [name for name, count in Counter(names).items() if count > 1]
        self.assertEqual(duplicates, [])
        self.assertIn("pattern: release-*", self.jobs["publish"])

    def test_routine_ci_skips_all_product_build_jobs(self) -> None:
        gated_jobs = {
            "android": ("build_on_linux", "build_on_mac"),
            "linux": ("build", "package_archlinux"),
            "macos": ("prepare_macos_zenz_runtime", "build_arm64"),
            "windows": ("build_x64", "build_universal", "build_arm64"),
        }
        for platform, expected_jobs in gated_jobs.items():
            with self.subTest(platform=platform):
                workflow = (
                    self.repository / ".github" / "workflows" / f"{platform}.yaml"
                ).read_text(encoding="utf-8")
                jobs = self._split_job_blocks(workflow)
                for job in expected_jobs:
                    self.assertIn(
                        "if: ${{ inputs.release == true }}",
                        jobs[job],
                        f"{platform}/{job} must be release-only",
                    )

                if platform == "android":
                    trigger = workflow.split("\npermissions:\n", maxsplit=1)[0]
                    self.assertNotIn("  push:", trigger)
                    self.assertNotIn("  pull_request:", trigger)
                else:
                    trigger = workflow.split("\npermissions:\n", maxsplit=1)[0]
                    self.assertIn("  pull_request:", trigger)
                    self.assertIn("  push:\n    branches:\n      - main", trigger)

    def test_intermediate_artifacts_are_namespaced_and_short_lived(self) -> None:
        artifact_names: list[str] = []
        workflows: dict[str, str] = {}
        for platform in ("android", "linux", "macos", "windows"):
            workflow = (
                self.repository / ".github" / "workflows" / f"{platform}.yaml"
            ).read_text(encoding="utf-8")
            workflows[platform] = workflow
            artifact_names.extend(
                re.findall(
                    r"(?m)^\s+uses: actions/upload-artifact@v[0-9]+\n"
                    r"\s+with:\n\s+name: ([^\s]+)$",
                    workflow,
                )
            )
            upload_blocks = workflow.split("uses: actions/upload-artifact@")[1:]
            self.assertTrue(upload_blocks)
            for index, upload in enumerate(upload_blocks):
                with self.subTest(platform=platform, upload=index):
                    self.assertIn("overwrite: true", upload.split("\n\n", 1)[0])

        duplicates = [
            name for name, count in Counter(artifact_names).items() if count > 1
        ]
        self.assertEqual(duplicates, [])
        self.assertNotIn("name: mozc.zip", workflows["linux"])
        self.assertRegex(
            workflows["linux"],
            r"(?ms)name: linux-release-approved-dictionary\n.*?retention-days: 1",
        )
        self.assertRegex(
            workflows["macos"],
            r"(?ms)name: macos-daily-dictionary\n.*?retention-days: 1",
        )
        self.assertRegex(
            workflows["windows"],
            r"(?ms)name: windows-daily-dictionary\n.*?retention-days: 1",
        )

    def test_public_product_filenames_are_versioned(self) -> None:
        expected = {
            "android": "mozkey-v*-android-native-libs.zip",
            "linux": "dist/mozkey-v*-archlinux-x86_64.tar.xz",
            "macos": "Mozkey_v${{ needs.prepare_daily_dictionary.outputs.release_version }}_macos_arm64.pkg",
            "windows": "Mozkey_v${{ needs.prepare_daily_dictionary.outputs.release_version }}_x64.msi",
        }
        for platform, filename in expected.items():
            with self.subTest(platform=platform):
                workflow = (
                    self.repository / ".github" / "workflows" / f"{platform}.yaml"
                ).read_text(encoding="utf-8")
                self.assertIn(filename, workflow)

    def test_codex_is_last_read_only_step_and_publish_has_fallback(self) -> None:
        notes = self.jobs["release-notes"]
        self.assertIn("uses: openai/codex-action@v1", notes)
        self.assertIn("openai-api-key: ${{ secrets.OPENAI_API_KEY }}", notes)
        self.assertIn("continue-on-error: true", notes)
        self.assertIn("sandbox: read-only", notes)
        self.assertIn("safety-strategy: drop-sudo", notes)
        self.assertNotIn("OPENAI_API_KEY:", notes)
        self.assertIn("notes: ${{ steps.codex-notes.outputs.final-message }}", notes)
        self.assertIn("outcome: ${{ steps.codex-notes.outcome }}", notes)

        action_position = notes.index("uses: openai/codex-action@v1")
        self.assertNotRegex(notes[action_position:], r"(?m)^      - name:")

        publish = self.jobs["publish"]
        self.assertIn(
            "CODEX_RELEASE_NOTES: ${{ needs.release-notes.outputs.notes }}",
            publish,
        )
        self.assertIn(
            "CODEX_RELEASE_NOTES_OUTCOME: ${{ needs.release-notes.outputs.outcome }}",
            publish,
        )
        self.assertIn(
            '"$CODEX_RELEASE_NOTES_OUTCOME" = "success"',
            publish,
        )
        self.assertIn("printf '%s\\n' \"$CODEX_RELEASE_NOTES\"", publish)
        self.assertIn("releases/generate-notes", publish)
        self.assertIn("release-notes-generator:", publish)

    def test_publish_refuses_published_release_and_builds_checksums(self) -> None:
        publish = self.jobs["publish"]
        self.assertIn("Refusing to mutate published release", publish)
        self.assertIn("pattern: release-*", publish)
        self.assertIn("SHA256SUMS", publish)
        self.assertIn("--draft", publish)
        self.assertIn("--prerelease", publish)


if __name__ == "__main__":
    unittest.main()
