import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
SCRIPT = Path(__file__).with_name("install_zenz_runtime")
NORMALIZED_MODEL = (
    REPOSITORY_ROOT / "dist/zenz/linux/zenz-v3.2-small-Q5_K_M.gguf"
)
SOURCE_MODEL = (
    SOURCE_ROOT
    / "win32/installer/zenz_runtime/models/zenz-v3.2-small-Q5_K_M.gguf"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class InstallZenzRuntimeTest(unittest.TestCase):
    def run_installer(
        self,
        destination: Path,
        target: str = "/usr/bin/llama-server",
        source: Path | None = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            {
                "DESTDIR": str(destination),
                "PREFIX": "/usr",
                "MOZKEY_ZENZ_LLAMA_SERVER_TARGET": target,
            }
        )
        if source is not None:
            env["MOZKEY_ZENZ_LLAMA_SERVER_SOURCE"] = str(source)
        return subprocess.run(
            [str(SCRIPT)],
            cwd=SOURCE_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_rejects_preexisting_directory_before_copying_product_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            link = destination / "usr/lib/mozkey-ibg/llama-server"
            link.mkdir(parents=True)
            result = self.run_installer(destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-symlink", result.stderr)
            self.assertFalse(
                (
                    destination
                    / "usr/lib/mozkey-ibg/models/zenz-v3.2-small-Q5_K_M.gguf"
                ).exists()
            )

    def test_installs_and_atomically_replaces_only_a_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            result = self.run_installer(destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            link = destination / "usr/lib/mozkey-ibg/llama-server"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "/usr/bin/llama-server")
            installed_model = (
                destination
                / "usr/lib/mozkey-ibg/models/zenz-v3.2-small-Q5_K_M.gguf"
            )
            self.assertEqual(sha256(installed_model), sha256(NORMALIZED_MODEL))
            self.assertNotEqual(sha256(installed_model), sha256(SOURCE_MODEL))

            result = self.run_installer(destination, "/usr/lib/llama/llama-server")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "/usr/lib/llama/llama-server")
            self.assertEqual(
                list(link.parent.glob(".mozkey-ibg-llama-link.*")),
                [],
            )

    def test_installs_and_atomically_replaces_bundled_server_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            source = root / "llama-server"
            source.write_bytes(b"#!/bin/sh\nexit 0\n")
            source.chmod(0o755)

            result = self.run_installer(destination, str(source), source)
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = destination / "usr/lib/mozkey-ibg/llama-server"
            self.assertTrue(installed.is_file())
            self.assertFalse(installed.is_symlink())
            self.assertEqual(installed.read_bytes(), source.read_bytes())
            self.assertEqual(installed.stat().st_mode & 0o777, 0o755)

            source.write_bytes(b"#!/bin/sh\necho updated\n")
            source.chmod(0o755)
            result = self.run_installer(destination, str(source), source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(installed.read_bytes(), source.read_bytes())
            self.assertEqual(list(installed.parent.glob(".mozkey-ibg-install.*")), [])

    def test_bundled_mode_rejects_symlink_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "llama-server"
            source.symlink_to("/bin/true")
            result = self.run_installer(root / "destination", "/bin/true", source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("regular executable", result.stderr)

    def test_bundled_mode_rejects_a_different_verified_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "llama-server"
            source.write_bytes(b"#!/bin/sh\nexit 0\n")
            source.chmod(0o755)
            result = self.run_installer(root / "destination", "/bin/true", source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source and verified target must match", result.stderr)


if __name__ == "__main__":
    unittest.main()
