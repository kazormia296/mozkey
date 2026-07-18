import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mac import tweak_installer_files as target


class TweakInstallerFilesCodesignTest(unittest.TestCase):
    def test_signs_nested_runtime_before_enclosing_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "MozcConverter.app"
            resources = app / "Contents/Resources"
            resources.mkdir(parents=True)
            server = resources / "llama-server"
            scorer = resources / "mozc_zenz_scorer"
            for executable in (server, scorer):
                executable.write_bytes(b"mach-o fixture")
                executable.chmod(0o755)

            with mock.patch.object(target.util, "RunOrDie") as run:
                target.Codesign(temporary, "-")

            commands = [call.args[0] for call in run.call_args_list]
            signed_targets = [Path(command[-1]) for command in commands]
            self.assertEqual(set(signed_targets[:2]), {server, scorer})
            self.assertEqual(signed_targets[-1], app)

    def test_release_identity_enables_timestamped_hardened_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "MozcConverter.app"
            resources = app / "Contents/Resources"
            resources.mkdir(parents=True)
            server = resources / "llama-server"
            server.write_bytes(b"mach-o fixture")
            server.chmod(0o755)

            with mock.patch.object(target.util, "RunOrDie") as run:
                target.Codesign(temporary, "Developer ID Application: Example")

            for call in run.call_args_list:
                command = call.args[0]
                self.assertIn("--timestamp", command)
                self.assertIn("--options=runtime", command)

    def test_refuses_nested_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary) / "MozcConverter.app/Contents/Resources"
            resources.mkdir(parents=True)
            target_file = resources / "other"
            target_file.write_bytes(b"fixture")
            os.symlink(target_file.name, resources / "llama-server")

            with self.assertRaisesRegex(ValueError, "refusing to codesign symlink"):
                target.Codesign(temporary, "-")


if __name__ == "__main__":
    unittest.main()
