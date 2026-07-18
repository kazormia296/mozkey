from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.release.validate_mozkey_release import (
    ReleaseValidationError,
    main,
    parse_release_tag,
    parse_version_file,
    validate_release,
)


def _run_git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _write_version(path: Path, version: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"MOZKEY_RELEASE_VERSION_MAJOR = {version[0]}",
                f"MOZKEY_RELEASE_VERSION_MINOR = {version[1]}",
                f"MOZKEY_RELEASE_VERSION_PATCH = {version[2]}",
                "",
            ]
        ),
        encoding="utf-8",
    )


class VersionParsingTest(unittest.TestCase):
    def test_accepts_canonical_tag(self) -> None:
        self.assertEqual(parse_release_tag("v0.8.0"), (0, 8, 0))
        self.assertEqual(parse_release_tag("v12.34.56"), (12, 34, 56))

    def test_rejects_noncanonical_or_prerelease_tags(self) -> None:
        for tag in ("0.8.0", "v0.8", "v00.8.0", "v0.8.0-beta.1", "v0.8.0.1"):
            with self.subTest(tag=tag), self.assertRaises(ReleaseValidationError):
                parse_release_tag(tag)

    def test_reads_exact_literal_version_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            version_file = Path(temporary_directory) / "version.bzl"
            _write_version(version_file, (0, 8, 1))
            self.assertEqual(parse_version_file(version_file), (0, 8, 1))

    def test_rejects_missing_duplicate_and_computed_values(self) -> None:
        invalid_contents = (
            "MOZKEY_RELEASE_VERSION_MAJOR = 0\n"
            "MOZKEY_RELEASE_VERSION_MINOR = 8\n",
            "MOZKEY_RELEASE_VERSION_MAJOR = 0\n"
            "MOZKEY_RELEASE_VERSION_MAJOR = 1\n"
            "MOZKEY_RELEASE_VERSION_MINOR = 8\n"
            "MOZKEY_RELEASE_VERSION_PATCH = 0\n",
            "MOZKEY_RELEASE_VERSION_MAJOR = 0\n"
            "MOZKEY_RELEASE_VERSION_MINOR = 4 + 4\n"
            "MOZKEY_RELEASE_VERSION_PATCH = 0\n",
            "MOZKEY_RELEASE_VERSION_MAJOR = 0\n"
            "MOZKEY_RELEASE_VERSION_MINOR = True\n"
            "MOZKEY_RELEASE_VERSION_PATCH = 0\n",
        )
        for index, contents in enumerate(invalid_contents):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                version_file = Path(directory) / "version.bzl"
                version_file.write_text(contents, encoding="utf-8")
                with self.assertRaises(ReleaseValidationError):
                    parse_version_file(version_file)


class GitBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name)
        _run_git(self.repository, "init", "--initial-branch=main")
        _run_git(self.repository, "config", "user.name", "Mozkey Release Test")
        _run_git(self.repository, "config", "user.email", "release-test@example.invalid")
        self.version_file = self.repository / "src" / "version.bzl"
        _write_version(self.version_file, (0, 8, 0))
        _run_git(self.repository, "add", "src/version.bzl")
        _run_git(self.repository, "commit", "-m", "release identity")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_accepts_tagged_commit_reachable_from_main(self) -> None:
        _run_git(self.repository, "tag", "v0.8.0")
        identity = validate_release(
            tag="v0.8.0",
            ref_type="tag",
            version_file=self.version_file,
            repository=self.repository,
            main_ref="main",
        )
        self.assertEqual(identity.version, "0.8.0")
        self.assertEqual(identity.commit, _run_git(self.repository, "rev-parse", "HEAD"))

    def test_rejects_tag_not_reachable_from_main(self) -> None:
        _run_git(self.repository, "switch", "-c", "release-side")
        (self.repository / "side.txt").write_text("side\n", encoding="utf-8")
        _run_git(self.repository, "add", "side.txt")
        _run_git(self.repository, "commit", "-m", "side release")
        _run_git(self.repository, "tag", "v0.8.0")
        with self.assertRaisesRegex(ReleaseValidationError, "not an ancestor"):
            validate_release(
                tag="v0.8.0",
                ref_type="tag",
                version_file=self.version_file,
                repository=self.repository,
                main_ref="main",
            )

    def test_rejects_checkout_that_does_not_match_tag(self) -> None:
        _run_git(self.repository, "tag", "v0.8.0")
        (self.repository / "later.txt").write_text("later\n", encoding="utf-8")
        _run_git(self.repository, "add", "later.txt")
        _run_git(self.repository, "commit", "-m", "later commit")
        with self.assertRaisesRegex(ReleaseValidationError, "does not match"):
            validate_release(
                tag="v0.8.0",
                ref_type="tag",
                version_file=self.version_file,
                repository=self.repository,
                main_ref="main",
            )

    def test_rejects_branch_ref_and_version_mismatch(self) -> None:
        _run_git(self.repository, "tag", "v0.8.0")
        with self.assertRaisesRegex(ReleaseValidationError, "tag ref"):
            validate_release(
                tag="v0.8.0",
                ref_type="branch",
                version_file=self.version_file,
                repository=self.repository,
                main_ref="main",
            )

        _run_git(self.repository, "tag", "v0.8.1")
        with self.assertRaisesRegex(ReleaseValidationError, "does not match"):
            validate_release(
                tag="v0.8.1",
                ref_type="tag",
                version_file=self.version_file,
                repository=self.repository,
                main_ref="main",
            )

    def test_cli_writes_github_outputs(self) -> None:
        _run_git(self.repository, "tag", "v0.8.0")
        output_file = self.repository / "github-output.txt"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):
            result = main(
                [
                    "--tag",
                    "v0.8.0",
                    "--ref-type",
                    "tag",
                    "--version-file",
                    str(self.version_file),
                    "--repository",
                    str(self.repository),
                    "--main-ref",
                    "main",
                ]
            )
        self.assertEqual(result, 0)
        output = output_file.read_text(encoding="utf-8")
        self.assertIn("tag=v0.8.0\n", output)
        self.assertIn("version=0.8.0\n", output)
        self.assertRegex(output, r"commit=[0-9a-f]{40}\n")


if __name__ == "__main__":
    unittest.main()
