import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
SCRIPT = Path(__file__).with_name("install_zenz_runtime")


class InstallZenzRuntimeTest(unittest.TestCase):
    def run_installer(
        self, destination: Path, target: str = "/usr/bin/llama-server"
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            {
                "DESTDIR": str(destination),
                "PREFIX": "/usr",
                "MOZKEY_ZENZ_LLAMA_SERVER_TARGET": target,
            }
        )
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
            link = destination / "usr/lib/mozkey/llama-server"
            link.mkdir(parents=True)
            result = self.run_installer(destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-symlink", result.stderr)
            self.assertFalse(
                (
                    destination
                    / "usr/lib/mozkey/models/zenz-v3.2-small-Q5_K_M.gguf"
                ).exists()
            )

    def test_installs_and_atomically_replaces_only_a_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            result = self.run_installer(destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            link = destination / "usr/lib/mozkey/llama-server"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "/usr/bin/llama-server")

            result = self.run_installer(destination, "/usr/lib/llama/llama-server")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "/usr/lib/llama/llama-server")
            self.assertEqual(
                list(link.parent.glob(".mozkey-llama-link.*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
