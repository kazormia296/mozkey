import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("install_mozkey_linux_bazel")


class InstallMozkeyLinuxBazelTest(unittest.TestCase):
    def make_harness(self, root: Path, fail_preflight: bool = False) -> tuple[Path, Path]:
        scripts = root / "scripts"
        source = root / "src"
        scripts.mkdir()
        source.mkdir()
        entrypoint = scripts / SCRIPT.name
        shutil.copy2(SCRIPT, entrypoint)
        entrypoint.chmod(0o755)
        log = root / "order.log"

        helper = scripts / "fcitx5_install_paths"
        helper.write_text(
            textwrap.dedent(
                '''\
                validate_mozkey_install_environment() {
                  [ "${PREFIX:-/usr}" = /usr ]
                }
                resolve_fcitx5_addon_dir() {
                  printf 'resolve\\n' >> "${MOZKEY_TEST_LOG}"
                  printf '/usr/lib/test-triplet/fcitx5\\n'
                }
                '''
            ),
            encoding="utf-8",
        )

        commands = [
            ("preflight_mozkey_linux_bazel", "preflight", 9 if fail_preflight else 0),
            ("smoke_test_mozkey_fcitx5_install", "smoke", 0),
            ("install_server_bazel", "server", 0),
            ("install_fcitx5_bazel", "addon", 0),
        ]
        for name, marker, status in commands:
            command = scripts / name
            command.write_text(
                textwrap.dedent(
                    f'''\
                    #!/bin/sh
                    marker='{marker}'
                    if [ "$marker" = addon ]; then
                      marker="addon-${{MOZKEY_FCITX_INSTALL_PHASE:-all}}"
                    fi
                    printf '%s:%s:%s:%s:%s\\n' "$marker" "${{FCITX5_ADDON_DIR:-}}" "${{PREFIX:-}}" "${{DESTDIR:-}}" "${{MOZKEY_LINUX_PREFLIGHT_DONE:-}}" >> "${{MOZKEY_TEST_LOG}}"
                    exit {status}
                    '''
                ),
                encoding="utf-8",
            )
            command.chmod(0o755)
        return entrypoint, log

    def run_entrypoint(
        self, entrypoint: Path, source: Path, log: Path
    ) -> subprocess.CompletedProcess:
        destination = source.parent / "stage"
        env = os.environ.copy()
        env.update(
            {
                "PREFIX": "/usr",
                "DESTDIR": str(destination),
                "MOZKEY_TEST_LOG": str(log),
            }
        )
        return subprocess.run(
            [str(entrypoint)],
            cwd=source,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_preflight_and_smoke_precede_server_and_addon_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entrypoint, log = self.make_harness(root)
            result = self.run_entrypoint(entrypoint, root / "src", log)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "resolve")
            self.assertEqual(
                [line.split(":", 1)[0] for line in lines[1:]],
                ["preflight", "smoke", "addon-data", "server", "addon-entry"],
            )
            addon_paths = {line.split(":", 2)[1] for line in lines[1:]}
            self.assertEqual(addon_paths, {"/usr/lib/test-triplet/fcitx5"})
            self.assertEqual(lines.count("resolve"), 1)
            self.assertEqual(lines[1].rsplit(":", 1)[1], "")
            self.assertTrue(all(line.rsplit(":", 1)[1] == "1" for line in lines[2:]))

    def test_preflight_failure_prevents_smoke_and_target_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entrypoint, log = self.make_harness(root, fail_preflight=True)
            result = self.run_entrypoint(entrypoint, root / "src", log)
            self.assertEqual(result.returncode, 9)
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "resolve")
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[1].startswith("preflight:"))


if __name__ == "__main__":
    unittest.main()
