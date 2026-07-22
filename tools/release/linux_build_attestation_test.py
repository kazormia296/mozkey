#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

from tools.release import linux_build_attestation as target
from tools.release import normalize_zenz_gguf as zenz_normalizer
from tools.release.normalize_zenz_gguf_test import build_gguf, fixture_entries


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPOSITORY_ROOT / "scripts/build_mozkey_linux_bazel"
ATTESTATION_TOOL = REPOSITORY_ROOT / "tools/release/linux_build_attestation.py"
ZENZ_NORMALIZER = REPOSITORY_ROOT / "tools/release/normalize_zenz_gguf.py"
DICTIONARY_VERIFIER = (
    REPOSITORY_ROOT
    / "tools/dictionary/verify_release_dictionary_artifact.py"
)
DAILY_SOURCE_LOCK = REPOSITORY_ROOT / "tools/dictionary/daily_source_lock.py"


class LinuxBuildAttestationTest(unittest.TestCase):
    def write(self, root: Path, relative: str, content: bytes) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def make_repository(
        self, root: Path, *, include_binaries: bool = True, include_script: bool = False
    ) -> Path:
        repository = root / "repository"
        repository.mkdir()
        self.write(
            repository,
            ".gitignore",
            b"/dist/\n/src/bazel-bin\n/src/data/dictionary_koyasi/generated/\n"
            b"__pycache__/\n*.pyc\n",
        )
        lock = {
            "schema_version": target.SOURCE_LOCK_SCHEMA,
            "profiles": {
                target.RELEASE_PROFILE: {
                    "source_ids": ["merge", "place", "sudachi", "personal"]
                },
                "local-evaluation": {
                    "source_ids": [
                        "merge",
                        "place",
                        "sudachi",
                        "personal",
                        target.NICO_SOURCE_ID,
                    ]
                },
            },
            "sources": {
                "merge": {"release_approved": True},
                "place": {"release_approved": True},
                "sudachi": {"release_approved": True},
                "personal": {"release_approved": True},
                target.NICO_SOURCE_ID: {"release_approved": False},
            },
        }
        lock_path = self.write(
            repository,
            target.SOURCE_LOCK_PATH.as_posix(),
            (json.dumps(lock, sort_keys=True) + "\n").encode(),
        )
        for index, relative in enumerate(target.DICTIONARY_OUTPUT_PATHS):
            self.write(repository, relative.as_posix(), f"dictionary-{index}\n".encode())
        if include_binaries:
            self.write_binaries(repository, b"binary-v1\n")
        if include_script:
            script = repository / "scripts" / BUILD_SCRIPT.name
            script.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BUILD_SCRIPT, script)
            release_tools = repository / "tools/release"
            release_tools.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ATTESTATION_TOOL, release_tools / ATTESTATION_TOOL.name)
            shutil.copy2(ZENZ_NORMALIZER, release_tools / ZENZ_NORMALIZER.name)
            dictionary_tools = repository / "tools/dictionary"
            dictionary_tools.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                DICTIONARY_VERIFIER,
                dictionary_tools / DICTIONARY_VERIFIER.name,
            )
            shutil.copy2(
                DAILY_SOURCE_LOCK,
                dictionary_tools / DAILY_SOURCE_LOCK.name,
            )
            self.write_fake_dictionary_preparer(dictionary_tools)

        source_model = build_gguf(fixture_entries())
        normalized_model = zenz_normalizer.normalized_bytes(source_model)
        source_path = self.write(
            repository,
            target.ZENZ_SOURCE_MODEL_PATH.as_posix(),
            source_model,
        )
        normalized_metadata = zenz_normalizer.inspect_metadata(normalized_model)
        zenz_lock = {
            "normalized": {
                "metadata_changes": {
                    "tokenizer.ggml.bos_token_id": 2,
                    "tokenizer.ggml.eos_token_id": 3,
                    "tokenizer.ggml.pre": "gpt-2",
                    "tokenizer.ggml.unknown_token_id": 0,
                },
                "path": target.ZENZ_NORMALIZED_MODEL_PATH.as_posix(),
                "sha256": hashlib.sha256(normalized_model).hexdigest(),
                "size_bytes": len(normalized_model),
                "tensor_payload_sha256": normalized_metadata[
                    "tensor_payload_sha256"
                ],
            },
            "schema_version": zenz_normalizer.SCHEMA,
            "source": {
                "path": target.ZENZ_SOURCE_MODEL_PATH.as_posix(),
                "repository": "https://example.invalid/synthetic-zenz",
                "repository_commit": "1" * 40,
                "sha256": hashlib.sha256(source_model).hexdigest(),
                "size_bytes": source_path.stat().st_size,
                "source_filename": "synthetic-zenz.gguf",
            },
        }
        self.write(
            repository,
            target.ZENZ_NORMALIZATION_LOCK_PATH.as_posix(),
            (json.dumps(zenz_lock, sort_keys=True) + "\n").encode(),
        )
        normalized_path = repository.joinpath(*target.ZENZ_NORMALIZED_MODEL_PATH.parts)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_bytes(normalized_model)
        bundled_server = self.write(
            repository,
            target.BUNDLED_LLAMA_SERVER_PATH.as_posix(),
            b"#!/bin/sh\nexit 0\n",
        )
        bundled_server.chmod(0o755)
        self.write(
            repository,
            target.BUNDLED_LLAMA_MANIFEST_PATH.as_posix(),
            b'{"schema_version":"mozkey.linux_llama_server.v1"}\n',
        )

        output_records = [
            self.record(repository, relative) for relative in target.DICTIONARY_OUTPUT_PATHS
        ]
        manifest = {
            "excluded_source_ids": [target.NICO_SOURCE_ID],
            "files": output_records,
            "profile": target.RELEASE_PROFILE,
            "schema_version": target.RELEASE_DICTIONARY_MANIFEST_SCHEMA,
            "source_lock_path": target.SOURCE_LOCK_PATH.as_posix(),
            "source_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        }
        self.write(
            repository,
            target.RELEASE_MANIFEST_PATH.as_posix(),
            (json.dumps(manifest, sort_keys=True) + "\n").encode(),
        )

        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        commit_env = os.environ.copy()
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "Mozkey Attestation Test",
                "GIT_AUTHOR_EMAIL": "attestation@example.invalid",
                "GIT_COMMITTER_NAME": "Mozkey Attestation Test",
                "GIT_COMMITTER_EMAIL": "attestation@example.invalid",
            }
        )
        subprocess.run(
            ["git", "commit", "-qm", "fixture"],
            cwd=repository,
            env=commit_env,
            check=True,
        )
        return repository

    def write_fake_dictionary_preparer(self, dictionary_tools: Path) -> None:
        script = dictionary_tools / "prepare_daily_dictionary_linux.py"
        script.write_text(
            textwrap.dedent(
                '''\
                #!/usr/bin/env python3
                import hashlib
                import json
                import pathlib

                REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
                RELEASE_OUTPUT_MANIFEST_SCHEMA = "mozkey.release_dictionary_outputs.v1"
                RELEASE_OUTPUT_MANIFEST = (
                    REPOSITORY_ROOT
                    / "dist/dictionary/linux-release-approved-output-manifest.json"
                )
                RELEASE_OUTPUT_PATHS = (
                    pathlib.Path("src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-daily.txt"),
                    pathlib.Path("src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-personal-names-daily.txt"),
                    pathlib.Path("src/data/dictionary_koyasi/generated/profiled/koyasi-syntax-guard.txt"),
                )

                def sha256_file(path):
                    return hashlib.sha256(path.read_bytes()).hexdigest()

                def main():
                    records = []
                    for index, relative in enumerate(RELEASE_OUTPUT_PATHS):
                        path = REPOSITORY_ROOT / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(f"bootstrapped-{index}\\n", encoding="utf-8")
                        records.append({
                            "path": relative.as_posix(),
                            "sha256": sha256_file(path),
                            "size_bytes": path.stat().st_size,
                        })
                    lock = REPOSITORY_ROOT / "tools/dictionary/daily_sources.lock.json"
                    manifest = {
                        "excluded_source_ids": ["dic-nico-intersection-pixiv"],
                        "files": records,
                        "profile": "release-approved-only",
                        "schema_version": RELEASE_OUTPUT_MANIFEST_SCHEMA,
                        "source_lock_path": "tools/dictionary/daily_sources.lock.json",
                        "source_lock_sha256": sha256_file(lock),
                    }
                    RELEASE_OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
                    RELEASE_OUTPUT_MANIFEST.write_text(
                        json.dumps(manifest, sort_keys=True) + "\\n", encoding="utf-8"
                    )
                    (RELEASE_OUTPUT_MANIFEST.parent / "bootstrap-called").write_text(
                        "yes\\n", encoding="utf-8"
                    )
                    return 0

                if __name__ == "__main__":
                    raise SystemExit(main())
                '''
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)

    def record(self, repository: Path, relative: Path) -> dict[str, object]:
        path = repository.joinpath(*relative.parts)
        return {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }

    def write_binaries(self, repository: Path, content: bytes) -> None:
        for relative in target.BINARY_PATHS:
            path = self.write(repository, relative.as_posix(), content)
            path.chmod(0o755)

    def attestation_path(self, repository: Path, layout: str) -> Path:
        return target.default_attestation_path(repository, layout)

    def test_create_and_verify_pin_exact_arch_release_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.make_repository(Path(temporary))
            output = self.attestation_path(repository, "archlinux-x86_64")
            document = target.create(
                repository, "archlinux-x86_64", output, "npx-bazelisk"
            )
            self.assertEqual(
                document["bazel"]["driver_argv"],
                ["npx", "--yes", "@bazel/bazelisk@1.28.1"],
            )
            self.assertEqual(
                document["bazel"]["targets"],
                list(target.COMMON_TARGETS),
            )
            self.assertIn("--config=no_sframe", document["bazel"]["flags"])
            self.assertEqual(len(document["dictionary"]["outputs"]), 3)
            self.assertEqual(len(document["binaries"]), 5)
            self.assertEqual(
                document["zenz_runtime"]["tensor_payload_sha256"],
                zenz_normalizer.inspect_metadata(
                    repository.joinpath(
                        *target.ZENZ_NORMALIZED_MODEL_PATH.parts
                    ).read_bytes()
                )["tensor_payload_sha256"],
            )
            target.verify(repository, "archlinux-x86_64", output)

    def test_native_package_layouts_attest_the_bundled_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.make_repository(Path(temporary))
            for layout in ("ubuntu-layout", "fedora-x86_64"):
                output = self.attestation_path(repository, layout)
                document = target.create(repository, layout, output, "bazelisk")
                self.assertEqual(
                    document["zenz_runtime"]["bundled_llama_server"]["path"],
                    target.BUNDLED_LLAMA_SERVER_PATH.as_posix(),
                )
                target.verify(repository, layout, output)

    def test_sframe_workaround_is_limited_to_the_arch_toolchain(self) -> None:
        self.assertIn(
            "--config=no_sframe",
            target.LAYOUTS["archlinux-x86_64"]["flags"],
        )
        self.assertNotIn(
            "--config=no_sframe",
            target.LAYOUTS["fedora-x86_64"]["flags"],
        )

    def test_verify_rejects_stale_binary_and_dictionary_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.make_repository(Path(temporary))
            output = self.attestation_path(repository, "archlinux-x86_64")
            target.create(repository, "archlinux-x86_64", output, "bazelisk")
            stale_binary = repository.joinpath(*target.BINARY_PATHS[0].parts)
            stale_binary.write_bytes(b"stale other-profile output\n")
            stale_binary.chmod(0o755)
            with self.assertRaisesRegex(target.AttestationError, "binary digest"):
                target.verify(repository, "archlinux-x86_64", output)

            self.write_binaries(repository, b"binary-v1\n")
            dictionary = repository.joinpath(*target.DICTIONARY_OUTPUT_PATHS[0].parts)
            dictionary.write_bytes(b"tampered dictionary\n")
            with self.assertRaisesRegex(target.AttestationError, "dictionary output"):
                target.verify(repository, "archlinux-x86_64", output)

    def test_verify_rejects_normalized_zenz_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.make_repository(Path(temporary))
            output = self.attestation_path(repository, "archlinux-x86_64")
            target.create(repository, "archlinux-x86_64", output, "bazelisk")
            model = repository.joinpath(*target.ZENZ_NORMALIZED_MODEL_PATH.parts)
            model.write_bytes(model.read_bytes() + b"tampered")
            with self.assertRaisesRegex(target.AttestationError, "Zenz normalization"):
                target.verify(repository, "archlinux-x86_64", output)

    def test_verify_rejects_wrong_target_profile_layout_and_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.make_repository(Path(temporary))
            output = self.attestation_path(repository, "ubuntu-layout")
            target.create(repository, "ubuntu-layout", output, "bazelisk")
            original = json.loads(output.read_text(encoding="utf-8"))

            tampered = json.loads(json.dumps(original))
            tampered["bazel"]["targets"][0] = "//server:wrong_server"
            output.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(target.AttestationError, "targets"):
                target.verify(repository, "ubuntu-layout", output)

            tampered = json.loads(json.dumps(original))
            tampered["bazel"]["flags"][2] = "--define=server=1"
            output.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(target.AttestationError, "flags"):
                target.verify(repository, "ubuntu-layout", output)

            output.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(target.AttestationError, "layout mismatch"):
                target.verify(repository, "archlinux-x86_64", output)

            tracked = repository / "tracked.txt"
            tracked.write_text("new head\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
            commit_env = os.environ.copy()
            commit_env.update(
                {
                    "GIT_AUTHOR_NAME": "Mozkey Attestation Test",
                    "GIT_AUTHOR_EMAIL": "attestation@example.invalid",
                    "GIT_COMMITTER_NAME": "Mozkey Attestation Test",
                    "GIT_COMMITTER_EMAIL": "attestation@example.invalid",
                }
            )
            subprocess.run(
                ["git", "commit", "-qm", "new head"],
                cwd=repository,
                env=commit_env,
                check=True,
            )
            with self.assertRaisesRegex(target.AttestationError, "Git HEAD mismatch"):
                target.verify(repository, "ubuntu-layout", output)

    def test_create_and_verify_reject_dirty_or_untracked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracked_root = root / "tracked"
            tracked_root.mkdir()
            repository = self.make_repository(tracked_root)
            lock = repository.joinpath(*target.SOURCE_LOCK_PATH.parts)
            lock.write_text(lock.read_text(encoding="utf-8") + " ", encoding="utf-8")
            output = self.attestation_path(repository, "ubuntu-layout")
            with self.assertRaisesRegex(target.AttestationError, "tracked changes"):
                target.create(repository, "ubuntu-layout", output, "bazelisk")
            self.assertFalse(output.exists())

            untracked_root = root / "untracked"
            untracked_root.mkdir()
            repository = self.make_repository(untracked_root)
            output = self.attestation_path(repository, "ubuntu-layout")
            target.create(repository, "ubuntu-layout", output, "bazelisk")
            self.write(repository, "src/release-only.BUILD", b"untracked source\n")
            with self.assertRaisesRegex(target.AttestationError, "untracked files"):
                target.verify(repository, "ubuntu-layout", output)

    def write_fake_build_command(self, script: Path) -> None:
        script.write_text(
            textwrap.dedent(
                '''\
                #!/bin/sh
                set -eu
                : > "$MOZKEY_TEST_BUILD_LOG"
                for argument in "$@"; do printf '%s\n' "$argument" >> "$MOZKEY_TEST_BUILD_LOG"; done
                if [ "${MOZKEY_TEST_BUILD_FAIL:-0}" = 1 ]; then exit 19; fi
                for output in \
                  src/bazel-bin/unix/fcitx5/fcitx5-mozkey-ibg.so \
                  src/bazel-bin/unix/fcitx5/grimodex_consumer_tool \
                  src/bazel-bin/server/mozc_server \
                  src/bazel-bin/gui/tool/mozc_tool \
                  src/bazel-bin/zenz_scorer/mozc_zenz_scorer
                do
                  mkdir -p "$MOZKEY_TEST_REPOSITORY/$(dirname -- "$output")"
                  printf 'built:%s\n' "$output" > "$MOZKEY_TEST_REPOSITORY/$output"
                  chmod 755 "$MOZKEY_TEST_REPOSITORY/$output"
                done
                '''
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)

    def make_fake_bazelisk(self, root: Path) -> Path:
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        self.write_fake_build_command(fake_bin / "bazelisk")
        return fake_bin

    def make_isolated_fake_npx(self, root: Path) -> Path:
        fake_bin = root / "fake-npx-bin"
        fake_bin.mkdir()
        for command in ("chmod", "dirname", "git", "mkdir", "python3", "rm"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, f"test prerequisite missing: {command}")
            (fake_bin / command).symlink_to(Path(executable).resolve())
        self.write_fake_build_command(fake_bin / "npx")
        return fake_bin

    def run_build_script(
        self,
        repository: Path,
        fake_bin: Path,
        log: Path,
        layout: str,
        **extra_environment: str,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "MOZKEY_TEST_BUILD_LOG": str(log),
                "MOZKEY_TEST_REPOSITORY": str(repository),
            }
        )
        environment.update(extra_environment)
        return subprocess.run(
            [str(repository / "scripts/build_mozkey_linux_bazel"), layout],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_build_wrapper_attests_only_successful_exact_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for layout in target.LAYOUTS:
                with self.subTest(layout=layout):
                    layout_root = root / layout
                    layout_root.mkdir()
                    repository = self.make_repository(
                        layout_root, include_binaries=False, include_script=True
                    )
                    fake_bin = self.make_fake_bazelisk(layout_root)
                    log = layout_root / "build.log"
                    result = self.run_build_script(
                        repository, fake_bin, log, layout
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected_argv = [
                        "build",
                        *target.LAYOUTS[layout]["targets"],
                        *target.LAYOUTS[layout]["flags"],
                    ]
                    self.assertEqual(
                        log.read_text(encoding="utf-8").splitlines(), expected_argv
                    )
                    output = self.attestation_path(repository, layout)
                    target.verify(repository, layout, output)

                    failed = self.run_build_script(
                        repository,
                        fake_bin,
                        log,
                        layout,
                        MOZKEY_TEST_BUILD_FAIL="1",
                    )
                    self.assertEqual(failed.returncode, 19)
                    self.assertFalse(
                        output.exists(), "failed build retained a stale attestation"
                    )

    def test_build_wrapper_rejects_nonignored_untracked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self.make_repository(
                root, include_binaries=False, include_script=True
            )
            self.write(repository, "src/untracked.BUILD", b"untracked source\n")
            fake_bin = self.make_fake_bazelisk(root)
            log = root / "build.log"
            result = self.run_build_script(
                repository, fake_bin, log, "ubuntu-layout"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("non-ignored untracked files", result.stderr)
            self.assertFalse(log.exists(), "dirty worktree reached Bazel")
            self.assertFalse(
                self.attestation_path(repository, "ubuntu-layout").exists()
            )

    def test_build_wrapper_bootstraps_fresh_clone_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self.make_repository(
                root, include_binaries=False, include_script=True
            )
            for relative in target.DICTIONARY_OUTPUT_PATHS:
                repository.joinpath(*relative.parts).unlink()
            repository.joinpath(*target.RELEASE_MANIFEST_PATH.parts).unlink()
            fake_bin = self.make_fake_bazelisk(root)
            log = root / "build.log"

            result = self.run_build_script(
                repository, fake_bin, log, "archlinux-x86_64"
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Preparing missing Mozkey release dictionary", result.stdout)
            self.assertTrue(
                (repository / "dist/dictionary/bootstrap-called").is_file()
            )
            target.verify(
                repository,
                "archlinux-x86_64",
                self.attestation_path(repository, "archlinux-x86_64"),
            )

    def test_build_wrapper_uses_pinned_npx_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self.make_repository(
                root, include_binaries=False, include_script=True
            )
            fake_bin = self.make_isolated_fake_npx(root)
            log = root / "build.log"
            layout = "archlinux-x86_64"
            result = self.run_build_script(
                repository,
                fake_bin,
                log,
                layout,
                PATH=str(fake_bin),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected_argv = [
                "--yes",
                "@bazel/bazelisk@1.28.1",
                "build",
                *target.LAYOUTS[layout]["targets"],
                *target.LAYOUTS[layout]["flags"],
            ]
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), expected_argv)
            output = self.attestation_path(repository, layout)
            document = target.verify(repository, layout, output)
            self.assertEqual(document["bazel"]["driver"], "npx-bazelisk")


if __name__ == "__main__":
    unittest.main()
