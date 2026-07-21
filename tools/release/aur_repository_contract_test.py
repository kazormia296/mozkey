from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_URL = "https://github.com/kazormia296/mozkey-ibg"
LEGACY_URL = "https://github.com/kazormia296/mozkey"


class AurRepositoryContractTest(unittest.TestCase):
    def test_public_repository_metadata_uses_the_renamed_repository(self) -> None:
        paths = (
            ROOT / ".github/ISSUE_TEMPLATE/config.yml",
            ROOT / ".github/ISSUE_TEMPLATE/build_error.yml",
            ROOT / "README.md",
            ROOT / "docs/build_mozc_in_osx.md",
            ROOT / "docs/build_mozc_in_windows.md",
            ROOT / "packaging/aur/PKGBUILD",
            ROOT / "packaging/aur/.SRCINFO",
            ROOT / "scripts/package_mozkey_linux_deb",
            ROOT / "scripts/package_mozkey_linux_rpm",
            ROOT / "scripts/stage_mozkey_linux_native_package",
            ROOT / "tools/release/generate_linux_spdx_sbom.py",
        )
        for path in paths:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn(CANONICAL_URL, content)
                self.assertNotIn(LEGACY_URL + "/", content)
                self.assertNotIn(LEGACY_URL + ".git", content)

    def test_aur_publish_is_limited_to_the_renamed_repository(self) -> None:
        workflow = (ROOT / ".github/workflows/aur.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "if: ${{ github.repository == 'kazormia296/mozkey-ibg' }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
