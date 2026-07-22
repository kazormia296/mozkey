from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.release.preflight_release_identity import validate_preflight_identity
from tools.release.validate_mozkey_release import ReleaseValidationError


def _git(repository: Path, *arguments: str) -> str:
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
            (
                f"MOZKEY_RELEASE_VERSION_MAJOR = {version[0]}",
                f"MOZKEY_RELEASE_VERSION_MINOR = {version[1]}",
                f"MOZKEY_RELEASE_VERSION_PATCH = {version[2]}",
                "",
            )
        ),
        encoding="utf-8",
    )


class PreflightReleaseIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name)
        _git(self.repository, "init", "--initial-branch=main")
        _git(self.repository, "config", "user.name", "Mozkey Preflight Test")
        _git(
            self.repository,
            "config",
            "user.email",
            "preflight-test@example.invalid",
        )
        self.version_file = self.repository / "src" / "version.bzl"
        _write_version(self.version_file, (0, 9, 4))
        _git(self.repository, "add", "src/version.bzl")
        _git(self.repository, "commit", "-m", "main release identity")
        _git(self.repository, "tag", "v0.9.4")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _validate(
        self, phase: str, candidate_tag: str | None = None
    ):
        return validate_preflight_identity(
            phase=phase,
            candidate_tag=candidate_tag,
            version_file=self.version_file,
            repository=self.repository,
            main_ref="main",
        )

    def test_pull_request_accepts_ordinary_change_at_current_version(self) -> None:
        _git(self.repository, "switch", "-c", "feature")
        (self.repository / "feature.txt").write_text("feature\n", encoding="utf-8")
        _git(self.repository, "add", "feature.txt")
        _git(self.repository, "commit", "-m", "feature")

        identity = self._validate("pull-request")

        self.assertEqual(identity.candidate_tag, "v0.9.4")

    def test_pull_request_accepts_new_version_only_when_tag_is_new(self) -> None:
        _git(self.repository, "switch", "-c", "release")
        _write_version(self.version_file, (0, 9, 5))
        _git(self.repository, "add", "src/version.bzl")
        _git(self.repository, "commit", "-m", "version 0.9.5")

        self.assertEqual(
            self._validate("pull-request").candidate_tag,
            "v0.9.5",
        )
        _git(self.repository, "tag", "v0.9.5")
        with self.assertRaisesRegex(ReleaseValidationError, "reuses existing"):
            self._validate("pull-request")

    def test_pull_request_rejects_version_older_than_main(self) -> None:
        _git(self.repository, "switch", "-c", "old-release")
        _write_version(self.version_file, (0, 9, 3))
        _git(self.repository, "add", "src/version.bzl")
        _git(self.repository, "commit", "-m", "old version")

        with self.assertRaisesRegex(ReleaseValidationError, "older than"):
            self._validate("pull-request")

    def test_pre_tag_requires_clean_main_and_absent_matching_tag(self) -> None:
        _git(self.repository, "tag", "-d", "v0.9.4")
        identity = self._validate("pre-tag", "v0.9.4")
        self.assertEqual(identity.commit, identity.main_commit)

        (self.repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseValidationError, "must be clean"):
            self._validate("pre-tag", "v0.9.4")

    def test_pre_tag_rejects_existing_or_mismatched_tag(self) -> None:
        with self.assertRaisesRegex(ReleaseValidationError, "already exists"):
            self._validate("pre-tag", "v0.9.4")
        with self.assertRaisesRegex(ReleaseValidationError, "does not match"):
            self._validate("pre-tag", "v0.9.5")

    def test_tag_phase_reuses_release_boundary_validation(self) -> None:
        identity = self._validate("tag", "v0.9.4")
        self.assertEqual(identity.commit, _git(self.repository, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
