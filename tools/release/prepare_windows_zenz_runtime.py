#!/usr/bin/env python3
"""Build and verify the pinned Windows Zenz inference runtime.

The historical checked-in llama.cpp binaries do not carry enough source or
toolchain provenance to be release inputs.  Windows release jobs therefore
build a static llama-server from a checksum-pinned upstream archive and stage
it under an architecture-specific generated directory.  The generated
manifest travels in the MSI and records the exact binary digest and effective
CMake/toolchain contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.release import normalize_zenz_gguf  # noqa: E402


SCHEMA_VERSION = "mozkey.windows_zenz_runtime.v1"
LLAMA_CPP_TAG = "b9637"
LLAMA_CPP_COMMIT = "aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3"
LLAMA_CPP_ARCHIVE_URL = (
    "https://github.com/ggml-org/llama.cpp/archive/refs/tags/b9637.tar.gz"
)
LLAMA_CPP_ARCHIVE_SHA256 = (
    "762283319feb3de30886dc850d42f0e426b06600e7f9639d34e06506597309ca"
)
NORMALIZED_MODEL_SHA256 = (
    "601572033a0c231857864ab0a2ccf40fbd1abe6ee4ccecd5399bf82e3e559772"
)
MODEL_NAME = "zenz-v3.2-small-Q5_K_M.gguf"
ARCHITECTURES = {
    "x64": {"cmake_platform": "x64", "pe_machine": 0x8664},
    "arm64": {"cmake_platform": "ARM64", "pe_machine": 0xAA64},
}
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
REQUIRED_CLI_FLAGS = (
    b"--api-key",
    b"--host",
    b"--port",
    b"--model",
    b"--ctx-size",
    b"--threads",
)
REQUIRED_CMAKE_BOOL_OPTIONS = {
    "BUILD_SHARED_LIBS": "OFF",
    "GGML_ACCELERATE": "OFF",
    "GGML_BACKEND_DL": "OFF",
    "GGML_BLAS": "OFF",
    "GGML_CPU": "ON",
    "GGML_CUDA": "OFF",
    "GGML_HIP": "OFF",
    "GGML_LLAMAFILE": "OFF",
    "GGML_METAL": "OFF",
    "GGML_MUSA": "OFF",
    "GGML_NATIVE": "OFF",
    "GGML_OPENCL": "OFF",
    "GGML_OPENMP": "OFF",
    "GGML_OPENVINO": "OFF",
    "GGML_RPC": "OFF",
    "GGML_STATIC": "ON",
    "GGML_SYCL": "OFF",
    "GGML_VULKAN": "OFF",
    "GGML_WEBGPU": "OFF",
    "LLAMA_BUILD_APP": "OFF",
    "LLAMA_BUILD_EXAMPLES": "OFF",
    "LLAMA_BUILD_SERVER": "ON",
    "LLAMA_BUILD_TESTS": "OFF",
    "LLAMA_BUILD_TOOLS": "ON",
    "LLAMA_BUILD_UI": "OFF",
    "LLAMA_OPENSSL": "OFF",
    "LLAMA_USE_PREBUILT_UI": "OFF",
    "MTMD_VIDEO": "OFF",
}
LEGACY_CMAKE_OFF_OPTIONS = ("LLAMA_CURL",)
REQUIRED_CMAKE_STRING_OPTIONS = {
    "GGML_BUILD_COMMIT": LLAMA_CPP_COMMIT,
    "LLAMA_BUILD_COMMIT": LLAMA_CPP_COMMIT,
    "LLAMA_BUILD_NUMBER": LLAMA_CPP_TAG.removeprefix("b"),
}
REQUIRED_CMAKE_PATH_OPTIONS = {"GIT_EXE": "FALSE"}


class PreparationError(RuntimeError):
    """A redaction-safe Windows runtime preparation failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise PreparationError(f"{label}_missing") from error
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise PreparationError(f"{label}_invalid")
    return info


def _verify_archive(path: Path) -> None:
    info = _require_regular(path, "llama_archive")
    if info.st_size > MAX_ARCHIVE_BYTES:
        raise PreparationError("llama_archive_too_large")
    if sha256_file(path) != LLAMA_CPP_ARCHIVE_SHA256:
        raise PreparationError("llama_archive_checksum_mismatch")


def _download_archive(destination: Path) -> None:
    if not LLAMA_CPP_ARCHIVE_URL.startswith("https://"):
        raise PreparationError("llama_archive_url_invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            LLAMA_CPP_ARCHIVE_URL,
            headers={"User-Agent": "Mozkey-Windows-runtime-builder/1"},
        )
        total = 0
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise PreparationError("llama_archive_redirect_invalid")
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise PreparationError("llama_archive_too_large")
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        _verify_archive(temporary)
        os.replace(temporary, destination)
    except PreparationError:
        raise
    except (OSError, urllib.error.URLError) as error:
        raise PreparationError("llama_archive_download_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _ensure_archive(path: Path) -> Path:
    path = path.resolve()
    if path.exists() or path.is_symlink():
        _verify_archive(path)
    else:
        _download_archive(path)
    return path


def _extract_archive(archive: Path, destination: Path) -> None:
    """Extracts regular files/directories while rejecting archive links."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            roots = {
                Path(member.name).parts[0]
                for member in members
                if Path(member.name).parts
            }
            if len(roots) != 1:
                raise PreparationError("llama_archive_layout_invalid")
            root = next(iter(roots))
            for member in members:
                parts = Path(member.name).parts
                if not parts or parts[0] != root:
                    raise PreparationError("llama_archive_path_invalid")
                relative = Path(*parts[1:])
                if not relative.parts:
                    continue
                if relative.is_absolute() or ".." in relative.parts:
                    raise PreparationError("llama_archive_path_invalid")
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise PreparationError("llama_archive_entry_invalid")
                target.parent.mkdir(parents=True, exist_ok=True)
                input_stream = source.extractfile(member)
                if input_stream is None:
                    raise PreparationError("llama_archive_entry_invalid")
                with input_stream, target.open("wb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
    except PreparationError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise PreparationError("llama_archive_extract_failed") from error
    if not (destination / "CMakeLists.txt").is_file():
        raise PreparationError("llama_archive_layout_invalid")


def _run(
    command: Sequence[str], *, cwd: Path | None = None, timeout: int = 1800
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PreparationError("runtime_build_command_failed") from error


def _pe_machine(path: Path) -> int:
    _require_regular(path, "llama_server")
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise PreparationError("llama_server_pe_invalid")
            stream.seek(0x3C)
            offset_data = stream.read(4)
            if len(offset_data) != 4:
                raise PreparationError("llama_server_pe_invalid")
            pe_offset = struct.unpack("<I", offset_data)[0]
            if pe_offset < 0x40 or pe_offset > 16 * 1024 * 1024:
                raise PreparationError("llama_server_pe_invalid")
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                raise PreparationError("llama_server_pe_invalid")
            machine_data = stream.read(2)
            if len(machine_data) != 2:
                raise PreparationError("llama_server_pe_invalid")
            return struct.unpack("<H", machine_data)[0]
    except PreparationError:
        raise
    except OSError as error:
        raise PreparationError("llama_server_read_failed") from error


def _verify_cli_contract(path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PreparationError("llama_server_read_failed") from error
    if any(flag not in data for flag in REQUIRED_CLI_FLAGS):
        raise PreparationError("llama_server_cli_incompatible")


def _load_manifest(path: Path) -> Mapping[str, Any]:
    info = _require_regular(path, "runtime_manifest")
    if info.st_size > MAX_MANIFEST_BYTES:
        raise PreparationError("runtime_manifest_too_large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreparationError("runtime_manifest_invalid") from error
    if not isinstance(value, dict):
        raise PreparationError("runtime_manifest_invalid")
    return value


def verify_runtime(output: Path, architecture: str) -> None:
    if architecture not in ARCHITECTURES:
        raise PreparationError("architecture_invalid")
    output = output.resolve()
    server = output / "llama-server.exe"
    model = output / "models" / MODEL_NAME
    manifest_path = output / "runtime-manifest.json"
    server_info = _require_regular(server, "llama_server")
    model_info = _require_regular(model, "zenz_model")
    if _pe_machine(server) != ARCHITECTURES[architecture]["pe_machine"]:
        raise PreparationError("llama_server_architecture_mismatch")
    _verify_cli_contract(server)
    if sha256_file(model) != NORMALIZED_MODEL_SHA256:
        raise PreparationError("zenz_model_checksum_mismatch")

    manifest = _load_manifest(manifest_path)
    if (
        set(manifest)
        != {
            "architecture",
            "cmake_options",
            "cmake_path_options",
            "cmake_string_options",
            "legacy_cmake_options_disabled",
            "llama_cpp",
            "llama_server",
            "model",
            "schema_version",
            "toolchain",
        }
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("architecture") != architecture
        or manifest.get("cmake_options") != REQUIRED_CMAKE_BOOL_OPTIONS
        or manifest.get("cmake_path_options") != REQUIRED_CMAKE_PATH_OPTIONS
        or manifest.get("legacy_cmake_options_disabled")
        != list(LEGACY_CMAKE_OFF_OPTIONS)
        or manifest.get("cmake_string_options")
        != REQUIRED_CMAKE_STRING_OPTIONS
        or manifest.get("llama_cpp")
        != {
            "archive_sha256": LLAMA_CPP_ARCHIVE_SHA256,
            "archive_url": LLAMA_CPP_ARCHIVE_URL,
            "built_targets": ["llama-server"],
            "commit": LLAMA_CPP_COMMIT,
            "tag": LLAMA_CPP_TAG,
        }
        or manifest.get("llama_server")
        != {
            "filename": "llama-server.exe",
            "pe_machine": f"0x{ARCHITECTURES[architecture]['pe_machine']:04x}",
            "sha256": sha256_file(server),
            "size_bytes": server_info.st_size,
            "static_runtime": True,
        }
        or manifest.get("model")
        != {
            "filename": f"models/{MODEL_NAME}",
            "sha256": NORMALIZED_MODEL_SHA256,
            "size_bytes": model_info.st_size,
        }
    ):
        raise PreparationError("runtime_manifest_contract_mismatch")
    toolchain = manifest.get("toolchain")
    expected_toolchain_keys = {
        "cmake",
        "compiler_id",
        "compiler_version",
        "generator",
        "generator_platform",
        "windows_sdk_version",
    }
    if (
        not isinstance(toolchain, dict)
        or set(toolchain) != expected_toolchain_keys
        or not all(
            isinstance(toolchain.get(key), str) and toolchain[key]
            for key in expected_toolchain_keys
        )
    ):
        raise PreparationError("runtime_manifest_toolchain_invalid")
    if toolchain.get("compiler_id") != "MSVC":
        raise PreparationError("runtime_manifest_toolchain_invalid")


def _cache_value(cache: str, name: str) -> str:
    prefix = f"{name}:"
    for line in cache.splitlines():
        if line.startswith(prefix) and "=" in line:
            return line.split("=", 1)[1].strip()
    raise PreparationError("runtime_toolchain_metadata_missing")


def _cmake_cache_values(cache: str) -> dict[str, tuple[str, str]]:
    values: dict[str, tuple[str, str]] = {}
    for line in cache.splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        if ":" not in key_and_type:
            continue
        key, value_type = key_and_type.split(":", 1)
        values[key] = (value_type, value)
    return values


def _verify_cmake_cache(cache: str, architecture: str) -> None:
    values = _cmake_cache_values(cache)
    for key, expected in REQUIRED_CMAKE_BOOL_OPTIONS.items():
        if values.get(key) != ("BOOL", expected):
            raise PreparationError("cmake_backend_contract_invalid")
    for key in LEGACY_CMAKE_OFF_OPTIONS:
        if values.get(key) not in {("BOOL", "OFF"), ("UNINITIALIZED", "OFF")}:
            raise PreparationError("cmake_backend_contract_invalid")
    for key, expected in REQUIRED_CMAKE_STRING_OPTIONS.items():
        if values.get(key) not in {
            ("STRING", expected),
            ("UNINITIALIZED", expected),
        }:
            raise PreparationError("cmake_backend_contract_invalid")
    for key, expected in REQUIRED_CMAKE_PATH_OPTIONS.items():
        if values.get(key) != ("FILEPATH", expected):
            raise PreparationError("cmake_backend_contract_invalid")
    if _cache_value(cache, "CMAKE_GENERATOR_PLATFORM") != str(
        ARCHITECTURES[architecture]["cmake_platform"]
    ):
        raise PreparationError("cmake_architecture_contract_invalid")


def _find_server(build: Path) -> Path:
    candidates = [
        build / "bin" / "Release" / "llama-server.exe",
        build / "bin" / "llama-server.exe",
    ]
    existing = [path for path in candidates if path.is_file()]
    if len(existing) != 1:
        raise PreparationError("llama_server_build_output_invalid")
    return existing[0]


def _compiler_metadata(build: Path) -> dict[str, str]:
    candidates = list(build.glob("CMakeFiles/*/CMakeCXXCompiler.cmake"))
    if len(candidates) != 1:
        raise PreparationError("runtime_toolchain_metadata_missing")
    try:
        content = candidates[0].read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise PreparationError("runtime_toolchain_metadata_missing") from error
    values: dict[str, str] = {}
    for output_name, cmake_name in (
        ("compiler_id", "CMAKE_CXX_COMPILER_ID"),
        ("compiler_version", "CMAKE_CXX_COMPILER_VERSION"),
    ):
        match = re.search(
            rf'^set\({re.escape(cmake_name)} "([^"]+)"\)$',
            content,
            flags=re.MULTILINE,
        )
        if match is None:
            raise PreparationError("runtime_toolchain_metadata_missing")
        values[output_name] = match.group(1)
    if values["compiler_id"] != "MSVC":
        raise PreparationError("runtime_toolchain_invalid")
    return values


def _write_manifest(
    destination: Path,
    architecture: str,
    server: Path,
    model: Path,
    cache: str,
    cmake_version: str,
    compiler: Mapping[str, str],
) -> None:
    document = {
        "architecture": architecture,
        "cmake_options": REQUIRED_CMAKE_BOOL_OPTIONS,
        "cmake_path_options": REQUIRED_CMAKE_PATH_OPTIONS,
        "cmake_string_options": REQUIRED_CMAKE_STRING_OPTIONS,
        "legacy_cmake_options_disabled": list(LEGACY_CMAKE_OFF_OPTIONS),
        "llama_cpp": {
            "archive_sha256": LLAMA_CPP_ARCHIVE_SHA256,
            "archive_url": LLAMA_CPP_ARCHIVE_URL,
            "built_targets": ["llama-server"],
            "commit": LLAMA_CPP_COMMIT,
            "tag": LLAMA_CPP_TAG,
        },
        "llama_server": {
            "filename": "llama-server.exe",
            "pe_machine": f"0x{ARCHITECTURES[architecture]['pe_machine']:04x}",
            "sha256": sha256_file(server),
            "size_bytes": server.stat().st_size,
            "static_runtime": True,
        },
        "model": {
            "filename": f"models/{MODEL_NAME}",
            "sha256": sha256_file(model),
            "size_bytes": model.stat().st_size,
        },
        "schema_version": SCHEMA_VERSION,
        "toolchain": {
            "cmake": cmake_version.splitlines()[0].strip(),
            "compiler_id": compiler["compiler_id"],
            "compiler_version": compiler["compiler_version"],
            "generator": _cache_value(cache, "CMAKE_GENERATOR"),
            "generator_platform": _cache_value(
                cache, "CMAKE_GENERATOR_PLATFORM"
            ),
            "windows_sdk_version": _cache_value(
                cache, "CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION"
            ),
        },
    }
    payload = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    destination.write_text(payload, encoding="utf-8", newline="\n")


def prepare_runtime(
    root: Path, archive: Path, output: Path, architecture: str
) -> None:
    if sys.platform != "win32":
        raise PreparationError("windows_required")
    if architecture not in ARCHITECTURES:
        raise PreparationError("architecture_invalid")
    root = root.resolve(strict=True)
    archive = _ensure_archive(archive)
    normalized_model, digest, _ = normalize_zenz_gguf.create(root)
    normalize_zenz_gguf.verify(root)
    if digest != NORMALIZED_MODEL_SHA256:
        raise PreparationError("zenz_model_checksum_mismatch")

    with tempfile.TemporaryDirectory(prefix="mozkey-llama-b9637-") as name:
        work = Path(name)
        source = work / "source"
        build = work / "build"
        _extract_archive(archive, source)
        configure = [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-A",
            str(ARCHITECTURES[architecture]["cmake_platform"]),
            *(
                f"-D{name}={value}"
                for name, value in REQUIRED_CMAKE_BOOL_OPTIONS.items()
            ),
            *(
                f"-D{name}={value}"
                for name, value in REQUIRED_CMAKE_STRING_OPTIONS.items()
            ),
            *(
                f"-D{name}:FILEPATH={value}"
                for name, value in REQUIRED_CMAKE_PATH_OPTIONS.items()
            ),
            *(f"-D{name}=OFF" for name in LEGACY_CMAKE_OFF_OPTIONS),
        ]
        _run(configure)
        _run(
            [
                "cmake",
                "--build",
                str(build),
                "--config",
                "Release",
                "--target",
                "llama-server",
                "--parallel",
            ]
        )
        server = _find_server(build)
        if _pe_machine(server) != ARCHITECTURES[architecture]["pe_machine"]:
            raise PreparationError("llama_server_architecture_mismatch")
        _verify_cli_contract(server)

        staged = output.resolve()
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "models").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(server, staged / "llama-server.exe")
        shutil.copyfile(normalized_model, staged / "models" / MODEL_NAME)
        cache = (build / "CMakeCache.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        _verify_cmake_cache(cache, architecture)
        cmake_version = _run(["cmake", "--version"], timeout=30).stdout
        _write_manifest(
            staged / "runtime-manifest.json",
            architecture,
            staged / "llama-server.exe",
            staged / "models" / MODEL_NAME,
            cache,
            cmake_version,
            _compiler_metadata(build),
        )
        verify_runtime(staged, architecture)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the pinned Windows Zenz runtime"
    )
    parser.add_argument("--arch", choices=sorted(ARCHITECTURES), required=True)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--archive",
        type=Path,
        default=REPOSITORY_ROOT / "src/third_party_cache/llama.cpp-b9637.tar.gz",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    output = arguments.output or (
        arguments.root
        / "src/win32/installer/zenz_runtime/generated"
        / arguments.arch
    )
    try:
        if arguments.verify_only:
            verify_runtime(output, arguments.arch)
        else:
            prepare_runtime(
                arguments.root, arguments.archive, output, arguments.arch
            )
        print(f"Windows Zenz runtime verified: {output} ({arguments.arch})")
        return 0
    except (PreparationError, normalize_zenz_gguf.NormalizationError) as error:
        print(f"Windows Zenz runtime preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
