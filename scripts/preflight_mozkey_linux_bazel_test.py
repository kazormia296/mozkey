import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("preflight_mozkey_linux_bazel")


class PreflightMozkeyLinuxBazelTest(unittest.TestCase):
    def write(self, root: Path, relative: str, content: bytes = b"fixture") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def make_harness(self, root: Path) -> tuple[Path, Path, Path]:
        scripts = root / "scripts"
        source = root / "src"
        bin_dir = root / "bin"
        scripts.mkdir()
        source.mkdir()
        bin_dir.mkdir()
        preflight = scripts / SCRIPT.name
        shutil.copy2(SCRIPT, preflight)
        preflight.chmod(0o755)

        helper = scripts / "fcitx5_install_paths"
        helper.write_text(
            textwrap.dedent(
                '''\
                validate_mozkey_install_environment() {
                  [ "${PREFIX:-/usr}" = /usr ]
                }
                validate_fcitx5_addon_dir() {
                  [ "$1" = /usr/lib/fcitx5 ]
                }
                resolve_fcitx5_addon_dir() {
                  printf '/usr/lib/fcitx5\\n'
                }
                read_installed_fcitx5_addon_dir() {
                  record="$1"
                  [ -f "$record" ] && [ ! -L "$record" ] || return 2
                  IFS= read -r value < "$record"
                  validate_fcitx5_addon_dir "$value" || return
                  printf '%s\\n' "$value"
                }
                '''
            ),
            encoding="utf-8",
        )

        verifier_log = root / "verifier.log"
        required_scripts = [
            "install_fcitx5_bazel",
            "install_server_bazel",
            "install_zenz_runtime",
            "smoke_test_mozkey_fcitx5_install",
            "uninstall_mozkey_fcitx5",
            "verify_mozkey_linux_build_attestation",
            "verify_zenz_gguf_normalization",
            "verify_llama_server_compatibility",
        ]
        for name in required_scripts:
            path = scripts / name
            if name == "verify_llama_server_compatibility":
                body = '#!/bin/sh\nprintf "verified\\n" >> "$MOZKEY_TEST_VERIFIER_LOG"\n'
            else:
                body = "#!/bin/sh\nexit 0\n"
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

        msgfmt = bin_dir / "msgfmt"
        msgfmt.write_text(
            textwrap.dedent(
                '''\
                #!/bin/sh
                output=
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = -o ]; then shift; output="$1"; fi
                  shift
                done
                mkdir -p "$(dirname -- "$output")"
                printf 'generated\\n' > "$output"
                '''
            ),
            encoding="utf-8",
        )
        msgfmt.chmod(0o755)
        appstreamcli = bin_dir / "appstreamcli"
        appstreamcli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        appstreamcli.chmod(0o755)

        for binary in [
            "bazel-bin/server/mozc_server",
            "bazel-bin/gui/tool/mozc_tool",
            "bazel-bin/unix/fcitx5/fcitx5-mozkey.so",
            "bazel-bin/unix/fcitx5/grimodex_consumer_tool",
            "bazel-bin/zenz_scorer/mozc_zenz_scorer",
        ]:
            self.write(source, binary, b"#!/bin/sh\nexit 0\n").chmod(0o755)

        metadata = [
            "data/images/product_icon_32bpp-128.png",
            "data/images/unix/ime_product_icon_opensource-32.png",
            "unix/fcitx5/po/ja.po",
            "data/installer/credits_en.html",
            "win32/installer/zenz_runtime/models/zenz-v3.2-small-Q5_K_M.gguf",
            "win32/installer/zenz_runtime/licenses/zenz-v3.2-small-gguf.txt",
            "win32/installer/zenz_runtime/licenses/llama.cpp-MIT.txt",
            "win32/installer/zenz_runtime/licenses/Apache-2.0.txt",
            "win32/installer/zenz_runtime/licenses/THIRD_PARTY_NOTICES.md",
        ]
        for relative in metadata:
            self.write(source, relative)
        self.write(
            root,
            "dist/zenz/linux/zenz-v3.2-small-Q5_K_M.gguf",
        )
        self.write(source, "unix/fcitx5/mozkey-addon.conf", b"Library=fcitx5-mozkey\n")
        self.write(source, "unix/fcitx5/mozkey.conf", b"Addon=mozkey\n")
        self.write(
            source,
            "unix/fcitx5/org.fcitx.Fcitx5.Addon.Mozkey.metainfo.xml.in",
            b"<component><id>org.fcitx.Fcitx5.Addon.Mozkey</id></component>\n",
        )

        repository_metadata = [
            "LICENSE",
            "LICENSES/Fcitx5-Mozc-BSD-3-Clause.txt",
            "LICENSES/LGPL-2.1-or-later.txt",
            "THIRD_PARTY_NOTICES.md",
            "tools/dictionary/RELEASE_THIRD_PARTY_NOTICES.md",
            "scripts/mozkey_fcitx5_install_manifest.txt",
        ]
        repository_metadata.extend(
            f"scripts/icons/{name}.png"
            for name in [
                "ui-alpha_full",
                "ui-alpha_half",
                "ui-direct",
                "ui-hiragana",
                "ui-katakana_full",
                "ui-katakana_half",
                "ui-dictionary",
                "ui-properties",
                "ui-tool",
            ]
        )
        for relative in repository_metadata:
            if not (root / relative).exists():
                self.write(root, relative)
        source_lock = {
            "profiles": {"release-approved-only": {"source_ids": ["approved"]}},
            "sources": {"approved": {"release_approved": True}},
        }
        self.write(
            root,
            "tools/dictionary/daily_sources.lock.json",
            (json.dumps(source_lock) + "\n").encode(),
        )
        verifier = self.write(
            root,
            "tools/dictionary/verify_release_dictionary_artifact.py",
            b"#!/usr/bin/env python3\nraise SystemExit(0)\n",
        )
        verifier.chmod(0o755)
        return preflight, source, verifier_log

    def run_preflight(
        self,
        preflight: Path,
        source: Path,
        verifier_log: Path,
        destination: Path,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{source.parent / 'bin'}:{env['PATH']}",
                "PREFIX": "/usr",
                "DESTDIR": str(destination),
                "FCITX5_ADDON_DIR": "/usr/lib/fcitx5",
                "MOZKEY_TEST_VERIFIER_LOG": str(verifier_log),
            }
        )
        return subprocess.run(
            [str(preflight)],
            cwd=source,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_validates_everything_before_llama_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, source, verifier_log = self.make_harness(root)
            result = self.run_preflight(
                preflight, source, verifier_log, root / "destination"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(verifier_log.read_text(encoding="utf-8"), "verified\n")

            verifier_log.unlink()
            model = (
                source
                / "win32/installer/zenz_runtime/models/zenz-v3.2-small-Q5_K_M.gguf"
            )
            model.unlink()
            result = self.run_preflight(
                preflight, source, verifier_log, root / "destination"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or empty", result.stderr)
            self.assertFalse(verifier_log.exists())

            self.write(source, model.relative_to(source))
            normalized = (
                root / "dist/zenz/linux/zenz-v3.2-small-Q5_K_M.gguf"
            )
            normalized.unlink()
            result = self.run_preflight(
                preflight, source, verifier_log, root / "destination"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or empty", result.stderr)
            self.assertFalse(verifier_log.exists())

    def test_rejects_preexisting_runtime_directory_before_llama_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, source, verifier_log = self.make_harness(root)
            destination = root / "destination"
            (destination / "usr/lib/mozkey/llama-server").mkdir(parents=True)
            result = self.run_preflight(preflight, source, verifier_log, destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-symlink", result.stderr)
            self.assertFalse(verifier_log.exists())

    def test_rejects_unsafe_addon_record_before_llama_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, source, verifier_log = self.make_harness(root)
            destination = root / "destination"
            record = destination / "usr/share/mozkey/fcitx5-addon-dir"
            record.parent.mkdir(parents=True)
            record.symlink_to("/etc/passwd")
            result = self.run_preflight(preflight, source, verifier_log, destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(verifier_log.exists())


if __name__ == "__main__":
    unittest.main()
