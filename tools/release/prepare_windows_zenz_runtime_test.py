#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.release import prepare_windows_zenz_runtime as target


def _write_fake_server(path: Path, machine: int) -> None:
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    data.extend(b"\0".join(target.REQUIRED_CLI_FLAGS))
    path.write_bytes(data)


class PrepareWindowsZenzRuntimeTest(unittest.TestCase):
    def test_pe_machine_accepts_x64_and_arm64(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for architecture, contract in target.ARCHITECTURES.items():
                server = root / f"{architecture}.exe"
                _write_fake_server(server, contract["pe_machine"])
                self.assertEqual(target._pe_machine(server), contract["pe_machine"])

    def test_pe_machine_rejects_non_pe(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            server = Path(name) / "server.exe"
            server.write_bytes(b"not a PE")
            with self.assertRaises(target.PreparationError):
                target._pe_machine(server)

    def test_verify_rejects_cross_architecture_server(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            output = Path(name)
            (output / "models").mkdir()
            _write_fake_server(output / "llama-server.exe", 0x8664)
            (output / "models" / target.MODEL_NAME).write_bytes(b"model")
            (output / "runtime-manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                target.PreparationError, "architecture_mismatch"
            ):
                target.verify_runtime(output, "arm64")

    def test_manifest_contract_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            server = root / "llama-server.exe"
            model = root / "model.gguf"
            manifest = root / "runtime-manifest.json"
            _write_fake_server(server, 0x8664)
            model.write_bytes(b"model")
            cache = "\n".join(
                (
                    "CMAKE_GENERATOR:INTERNAL=Visual Studio 18 2026",
                    "CMAKE_GENERATOR_PLATFORM:INTERNAL=x64",
                    "CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION:STRING=10.0.26100.0",
                )
            )
            target._write_manifest(
                manifest,
                "x64",
                server,
                model,
                cache,
                "cmake version 4.0.0",
                {"compiler_id": "MSVC", "compiler_version": "19.50.12345"},
            )
            document = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], target.SCHEMA_VERSION)
            self.assertEqual(document["llama_cpp"]["tag"], "b9637")
            self.assertEqual(
                document["llama_cpp"]["commit"],
                "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3",
            )
            self.assertEqual(
                document["cmake_string_options"]["LLAMA_BUILD_COMMIT"],
                "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3",
            )
            self.assertEqual(
                document["cmake_path_options"],
                {"GIT_EXE": "FALSE"},
            )
            self.assertEqual(document["architecture"], "x64")
            self.assertEqual(document["llama_server"]["pe_machine"], "0x8664")
            self.assertTrue(document["llama_server"]["static_runtime"])
            self.assertEqual(
                document["toolchain"]["compiler_version"], "19.50.12345"
            )
            self.assertEqual(
                document["toolchain"]["windows_sdk_version"],
                "10.0.26100.0",
            )

    def test_compiler_metadata_reads_exact_msvc_version(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            compiler_file = (
                Path(name) / "CMakeFiles/4.0.0/CMakeCXXCompiler.cmake"
            )
            compiler_file.parent.mkdir(parents=True)
            compiler_file.write_text(
                'set(CMAKE_CXX_COMPILER_ID "MSVC")\n'
                'set(CMAKE_CXX_COMPILER_VERSION "19.50.12345")\n',
                encoding="utf-8",
            )
            self.assertEqual(
                target._compiler_metadata(Path(name)),
                {"compiler_id": "MSVC", "compiler_version": "19.50.12345"},
            )

    def test_cmake_cache_contract_rejects_wrong_architecture(self) -> None:
        entries = [
            *(
                f"{name}:BOOL={value}"
                for name, value in target.REQUIRED_CMAKE_BOOL_OPTIONS.items()
            ),
            *(
                f"{name}:UNINITIALIZED=OFF"
                for name in target.LEGACY_CMAKE_OFF_OPTIONS
            ),
            *(
                f"{name}:UNINITIALIZED={value}"
                for name, value in target.REQUIRED_CMAKE_STRING_OPTIONS.items()
            ),
            *(
                f"{name}:FILEPATH={value}"
                for name, value in target.REQUIRED_CMAKE_PATH_OPTIONS.items()
            ),
            "CMAKE_GENERATOR_PLATFORM:INTERNAL=ARM64",
        ]
        cache = "\n".join(entries)
        target._verify_cmake_cache(cache, "arm64")
        with self.assertRaisesRegex(
            target.PreparationError, "architecture_contract"
        ):
            target._verify_cmake_cache(cache, "x64")

    def test_release_build_uses_only_generated_runtime(self) -> None:
        build = (
            target.REPOSITORY_ROOT
            / "src/win32/installer/zenz_runtime/BUILD.bazel"
        ).read_text(encoding="utf-8")
        installer = (
            target.REPOSITORY_ROOT / "src/win32/installer/BUILD.bazel"
        ).read_text(encoding="utf-8")
        self.assertIn("generated/x64/llama-server.exe", build)
        self.assertIn("generated/arm64/llama-server.exe", build)
        self.assertNotIn('"llama-server.exe",', build)
        self.assertNotIn("zenz_runtime:ggml.dll", installer)


if __name__ == "__main__":
    unittest.main()
