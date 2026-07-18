#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import tempfile
import unittest

from tools.dictionary import prepare_daily_dictionary_linux as prepare
from tools.dictionary import verify_release_dictionary_artifact as target


class VerifyReleaseDictionaryArtifactTest(unittest.TestCase):
    def create_artifact(self, root: pathlib.Path) -> pathlib.Path:
        for index, relative in enumerate(prepare.RELEASE_OUTPUT_PATHS):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"release-{index}\n", encoding="utf-8")
        manifest = root / "dist" / "dictionary" / "manifest.json"
        prepare.write_release_output_manifest(
            prepare.release_output_records(root), manifest
        )
        return manifest

    def test_accepts_exact_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            target.verify(root, self.create_artifact(root))

    def test_rejects_local_evaluation_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            manifest = self.create_artifact(root)
            nico = (
                root
                / "src/data/dictionary_koyasi/generated/profiled/"
                "dic-nico-pixiv-delta.txt"
            )
            nico.write_text("local only\n", encoding="utf-8")
            with self.assertRaisesRegex(
                target.ArtifactVerificationError, "non-allowlisted"
            ):
                target.verify(root, manifest)

    def test_generation_workspace_accepts_ignored_raw_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            manifest = self.create_artifact(root)
            raw = (
                root
                / "src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt"
            )
            raw.write_text("local generation input\n", encoding="utf-8")
            target.verify(root, manifest, require_transfer_allowlist=False)

    def test_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            manifest = self.create_artifact(root)
            (root / prepare.RELEASE_OUTPUT_PATHS[0]).write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                target.ArtifactVerificationError, "size mismatch|SHA-256 mismatch"
            ):
                target.verify(root, manifest)


if __name__ == "__main__":
    unittest.main()
