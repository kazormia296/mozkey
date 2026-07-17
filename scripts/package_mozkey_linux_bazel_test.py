import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("package_mozkey_linux_bazel")
SBOM_GENERATOR = REPOSITORY_ROOT / "tools/release/generate_linux_spdx_sbom.py"


class PackageMozkeyLinuxBazelTest(unittest.TestCase):
    def make_repository(self, root: Path) -> tuple[Path, Path]:
        repository = root / "repository"
        scripts = repository / "scripts"
        source = repository / "src"
        release_tools = repository / "tools/release"
        scripts.mkdir(parents=True)
        source.mkdir()
        release_tools.mkdir(parents=True)
        package_script = scripts / SCRIPT.name
        shutil.copy2(SCRIPT, package_script)
        package_script.chmod(0o755)
        shutil.copy2(SBOM_GENERATOR, release_tools / SBOM_GENERATOR.name)
        verifier = scripts / "verify_mozkey_linux_build_attestation"
        verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verifier.chmod(0o755)

        (source / "version.bzl").write_text(
            textwrap.dedent(
                '''\
                MOZKEY_RELEASE_VERSION_MAJOR = 0
                MOZKEY_RELEASE_VERSION_MINOR = 8
                MOZKEY_RELEASE_VERSION_PATCH = 0
                '''
            ),
            encoding="utf-8",
        )
        installer = scripts / "install_mozkey_linux_bazel"
        installer.write_text(
            textwrap.dedent(
                '''\
                #!/bin/sh
                set -eu
                install -D -m 755 /bin/true "${DESTDIR}/usr/lib/mozkey/mozc_server"
                install -D -m 644 /dev/null "${DESTDIR}/usr/share/doc/mozkey/marker.txt"
                install -d "${DESTDIR}/usr/lib/mozkey"
                ln -s /usr/bin/llama-server "${DESTDIR}/usr/lib/mozkey/llama-server"
                '''
            ),
            encoding="utf-8",
        )
        installer.chmod(0o755)
        attestation = repository / "dist/linux/archlinux-x86_64/build-attestation.json"
        attestation.parent.mkdir(parents=True)
        attestation.write_text(
            '{"schema_version":"mozkey.linux_build_attestation.v1"}\n',
            encoding="utf-8",
        )
        packages = repository / "dist/archlinux-build-packages.txt"
        packages.write_text("fcitx5 5.1.21-1\nllama-cpp b9859-1\n", encoding="utf-8")

        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        commit_env = os.environ.copy()
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "Mozkey Test",
                "GIT_AUTHOR_EMAIL": "mozkey-test@example.invalid",
                "GIT_COMMITTER_NAME": "Mozkey Test",
                "GIT_COMMITTER_EMAIL": "mozkey-test@example.invalid",
            }
        )
        subprocess.run(
            ["git", "commit", "-qm", "fixture"],
            cwd=repository,
            env=commit_env,
            check=True,
        )
        return repository, package_script

    def run_package(
        self,
        repository: Path,
        package_script: Path,
        output: Path,
        **environment: str,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            {
                "MOZKEY_LINUX_ALLOW_NON_ARCH_FOR_TESTS": "1",
                "MOZKEY_LINUX_ARCH_FOR_TESTS": "x86_64",
                "MOZKEY_LINUX_OUTPUT_DIR": str(output),
                "SOURCE_DATE_EPOCH": "1",
            }
        )
        env.update(environment)
        return subprocess.run(
            [str(package_script)],
            cwd=repository / "src",
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_archive_target_checksum_namespace_and_rerun_are_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, package_script = self.make_repository(root)
            output = root / "output"
            first = self.run_package(repository, package_script, output)
            self.assertEqual(first.returncode, 0, first.stderr)

            base = "mozkey-v0.8.0-archlinux-x86_64"
            archive = output / f"{base}.tar.xz"
            checksum = output / f"{base}.tar.xz.sha256"
            sbom = output / f"{base}.spdx.json"
            attestation = output / f"{base}.build-attestation.json"
            self.assertTrue(archive.is_file())
            self.assertTrue(attestation.is_file())
            checksum_parts = checksum.read_text(encoding="utf-8").split()
            self.assertEqual(checksum_parts[1], archive.name)
            self.assertEqual(
                checksum_parts[0], hashlib.sha256(archive.read_bytes()).hexdigest()
            )
            document = json.loads(sbom.read_text(encoding="utf-8"))
            verification = document["packages"][0]["packageVerificationCode"]
            self.assertIn(
                "/linux/archlinux-x86_64/0.8.0/",
                document["documentNamespace"],
            )
            self.assertTrue(
                document["documentNamespace"].endswith(
                    verification["packageVerificationCodeValue"]
                )
            )
            first_archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            first_sbom = sbom.read_bytes()
            first_attestation = attestation.read_bytes()

            second = self.run_package(repository, package_script, output)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(), first_archive_digest
            )
            self.assertEqual(sbom.read_bytes(), first_sbom)
            self.assertEqual(attestation.read_bytes(), first_attestation)
            archive_listing = subprocess.run(
                ["tar", "-tJf", str(archive)],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()
            self.assertIn(
                "usr/share/doc/mozkey/linux-build-attestation.json",
                archive_listing,
            )
            self.assertIn(
                "usr/share/doc/mozkey/archlinux-build-packages.txt",
                archive_listing,
            )

    def test_rejects_unsupported_target_and_dirty_tree_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, package_script = self.make_repository(root)
            output = root / "output"

            wrong_arch = self.run_package(
                repository,
                package_script,
                output,
                MOZKEY_LINUX_ARCH_FOR_TESTS="aarch64",
            )
            self.assertNotEqual(wrong_arch.returncode, 0)
            self.assertIn("archlinux-x86_64", wrong_arch.stderr)
            invalid_host_override = self.run_package(
                repository,
                package_script,
                output,
                MOZKEY_LINUX_ALLOW_NON_ARCH_FOR_TESTS="yes",
            )
            self.assertNotEqual(invalid_host_override.returncode, 0)
            self.assertIn("accepts only 1", invalid_host_override.stderr)

            version_file = repository / "src/version.bzl"
            version_file.write_text(
                version_file.read_text(encoding="utf-8") + "# dirty\n",
                encoding="utf-8",
            )
            dirty = self.run_package(repository, package_script, output)
            self.assertNotEqual(dirty.returncode, 0)
            self.assertIn("dirty Mozkey worktree", dirty.stderr)
            test_override = self.run_package(
                repository,
                package_script,
                output,
                MOZKEY_LINUX_ALLOW_DIRTY_TREE_FOR_TESTS="1",
            )
            self.assertEqual(test_override.returncode, 0, test_override.stderr)
            self.assertIn("explicit test override", test_override.stderr)


if __name__ == "__main__":
    unittest.main()
