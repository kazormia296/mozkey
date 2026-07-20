from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.release import verify_staged_linux_payload as target


class VerifyStagedLinuxPayloadTest(unittest.TestCase):
    def _trees(self, root: Path) -> tuple[Path, Path]:
        expected = root / "expected"
        actual = root / "actual"
        for tree in (expected, actual):
            (tree / "lib/mozkey").mkdir(parents=True)
            server = tree / "lib/mozkey/llama-server"
            server.write_bytes(b"runtime\n")
            server.chmod(0o755)
            (tree / "runtime-link").symlink_to("lib/mozkey/llama-server")
        return expected, actual

    def test_accepts_identical_regular_files_modes_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected, actual = self._trees(Path(temporary))
            target.verify_payload(expected, actual)

    def test_rejects_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected, actual = self._trees(Path(temporary))
            (actual / "lib/mozkey/llama-server").write_bytes(b"stripped\n")
            with self.assertRaisesRegex(
                target.PayloadError, "package_payload_mismatch"
            ):
                target.verify_payload(expected, actual)

    def test_rejects_mode_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected, actual = self._trees(Path(temporary))
            (actual / "lib/mozkey/llama-server").chmod(0o644)
            with self.assertRaisesRegex(
                target.PayloadError, "package_payload_mismatch"
            ):
                target.verify_payload(expected, actual)

    def test_rejects_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected, actual = self._trees(Path(temporary))
            (actual / "runtime-link").unlink()
            with self.assertRaisesRegex(
                target.PayloadError, "package_payload_mismatch"
            ):
                target.verify_payload(expected, actual)


if __name__ == "__main__":
    unittest.main()
