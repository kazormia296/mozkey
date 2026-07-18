#!/usr/bin/env python3
"""Verify the packaged macOS Zenz runtime and optionally exercise inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence


MODEL_NAME = "zenz-v3.2-small-Q5_K_M.gguf"
SCORER_NAME = "mozc_zenz_scorer"
MODEL_SHA256 = "601572033a0c231857864ab0a2ccf40fbd1abe6ee4ccecd5399bf82e3e559772"
LLAMA_ARCHIVE_SHA256 = (
    "762283319feb3de30886dc850d42f0e426b06600e7f9639d34e06506597309ca"
)
REQUIRED_LICENSES = (
    "Apache-2.0.txt",
    "THIRD_PARTY_NOTICES.md",
    "cpp-httplib-MIT.txt",
    "llama.cpp-MIT.txt",
    "nlohmann-json-MIT.txt",
    "zenz-v3.2-small-gguf.txt",
)
PINNED_DEPENDENCY_LICENSE_SHA256 = {
    "cpp-httplib-MIT.txt": (
        "f8c53951438545b8ed61176d9071bd1039e81502f9ec9590b85ccd5c71a08473"
    ),
    "nlohmann-json-MIT.txt": (
        "c0d068392ea65358b798b8c165103560f06e9e3b38c4ab4e2d8810a7b931af86"
    ),
}
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
MAX_WIRE_PAYLOAD_BYTES = 64 * 1024
WIRE_MAGIC = 0x315A4E5A
WIRE_VERSION = 2
WIRE_REQUEST = 1
WIRE_RESPONSE = 2
WIRE_STATUS_OK = 0
WIRE_STATUS_ERROR = 1
WIRE_STATUS_TIMEOUT = 2
WIRE_REQUEST_HEADER = struct.Struct("<IHHIIIII")
WIRE_RESPONSE_HEADER = struct.Struct("<IHHIIIII")
PROBE_PROMPT = "\uee02彼の動きは\uee00セイサイヲカイタ\uee01"
ARCHITECTURES = (("arm64", "12.0"),)
ARCHITECTURE_MANIFEST = {
    architecture: {"deployment_target": deployment_target}
    for architecture, deployment_target in ARCHITECTURES
}
_REGULAR_FILE_FAILURE_CODES = frozenset(
    {
        "package_layout_invalid",
        "runtime_license_layout_invalid",
        "runtime_manifest_layout_invalid",
        "runtime_model_layout_invalid",
        "runtime_scorer_layout_invalid",
        "runtime_server_layout_invalid",
    }
)


class ProbeFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WireFailureClass(Enum):
    """Fixed diagnostic classes for retryable wire probe failures."""

    SERVER_LOADING = "server_loading"
    TIMEOUT = "timeout"
    EMPTY_CONTENT = "empty_content"
    NON_OK = "non_ok"
    IO_FAILED = "io_failed"


_WIRE_FAILURE_REPORT_PRIORITY = (
    WireFailureClass.SERVER_LOADING,
    WireFailureClass.TIMEOUT,
    WireFailureClass.EMPTY_CONTENT,
    WireFailureClass.IO_FAILED,
    WireFailureClass.NON_OK,
)
_WIRE_IO_FAILURE_DEBUG = frozenset(
    {
        b"connect_failed",
        b"inet_pton_failed",
        b"recv_failed",
        b"send_failed",
        b"socket_failed",
    }
)


@dataclass(frozen=True)
class RuntimeLayout:
    converter_app: Path
    resources: Path
    scorer: Path
    server: Path
    model: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ProbeFailure("runtime_file_unreadable") from error
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    timeout: int = 30,
    failure_code: str = "runtime_command_failed",
) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProbeFailure(failure_code) from error
    if len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES:
        raise ProbeFailure("runtime_command_output_too_large")
    try:
        return result.stdout.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ProbeFailure("runtime_command_output_invalid") from error


def _require_regular(
    path: Path, *, executable: bool | None, failure_code: str
) -> None:
    if failure_code not in _REGULAR_FILE_FAILURE_CODES:
        raise ValueError("unsafe regular-file failure code")
    try:
        info = path.lstat()
    except OSError as error:
        raise ProbeFailure(failure_code) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
        or (
            executable is not None
            and bool(info.st_mode & stat.S_IXUSR) != executable
        )
        or info.st_mode & 0o022
    ):
        raise ProbeFailure(failure_code)


def _find_layout(expanded: Path) -> RuntimeLayout:
    scorers = list(
        expanded.glob(
            f"**/*Converter.app/Contents/Resources/{SCORER_NAME}"
        )
    )
    if len(scorers) != 1:
        raise ProbeFailure("runtime_scorer_count_invalid")
    scorer = scorers[0]
    resources = scorer.parent
    contents = resources.parent
    converter_app = contents.parent
    server = resources / "llama-server"
    model = resources / "models" / MODEL_NAME

    _require_regular(
        scorer,
        executable=True,
        failure_code="runtime_scorer_layout_invalid",
    )
    _require_regular(
        server,
        executable=True,
        failure_code="runtime_server_layout_invalid",
    )
    _require_regular(
        model,
        executable=False,
        failure_code="runtime_model_layout_invalid",
    )
    if sha256_file(model) != MODEL_SHA256:
        raise ProbeFailure("model_checksum_mismatch")

    manifest_path = resources / "zenz-runtime-manifest.json"
    _require_regular(
        manifest_path,
        executable=False,
        failure_code="runtime_manifest_layout_invalid",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProbeFailure("runtime_manifest_invalid") from error
    llama_contract = manifest.get("llama_cpp") if isinstance(manifest, dict) else None
    model_contract = manifest.get("model") if isinstance(manifest, dict) else None
    scorer_contract = manifest.get("scorer") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or not isinstance(llama_contract, dict)
        or not isinstance(model_contract, dict)
        or not isinstance(scorer_contract, dict)
        or manifest.get("schema_version") != "mozkey.macos_zenz_runtime.v1"
        or llama_contract.get("tag") != "b9637"
        or llama_contract.get("archive_sha256") != LLAMA_ARCHIVE_SHA256
        or llama_contract.get("built_targets") != ["llama-server"]
        or model_contract.get("sha256") != MODEL_SHA256
        or manifest.get("architectures") != ARCHITECTURE_MANIFEST
        or scorer_contract
        != {
            "architectures": ARCHITECTURE_MANIFEST,
            "filename": SCORER_NAME,
        }
        or manifest.get("backend")
        != {
            "accelerate": True,
            "cpu": True,
            "curl": False,
            "llama_build_app": False,
            "llama_build_tools": True,
            "metal": False,
            "multimodal_video": False,
            "rpc": False,
            "shared_libraries": False,
            "web_ui": False,
        }
    ):
        raise ProbeFailure("runtime_manifest_invalid")

    _verify_license_allowlist(resources / "licenses")

    return RuntimeLayout(
        converter_app=converter_app,
        resources=resources,
        scorer=scorer,
        server=server,
        model=model,
    )


def _verify_license_allowlist(licenses: Path) -> None:
    try:
        info = licenses.lstat()
        entries = tuple(sorted(path.name for path in licenses.iterdir()))
    except OSError as error:
        raise ProbeFailure("runtime_license_layout_invalid") from error
    if not stat.S_ISDIR(info.st_mode) or entries != tuple(sorted(REQUIRED_LICENSES)):
        raise ProbeFailure("runtime_license_layout_invalid")
    for name in REQUIRED_LICENSES:
        _require_regular(
            licenses / name,
            executable=False,
            failure_code="runtime_license_layout_invalid",
        )
    for name, expected_sha256 in PINNED_DEPENDENCY_LICENSE_SHA256.items():
        if sha256_file(licenses / name) != expected_sha256:
            raise ProbeFailure("runtime_license_checksum_mismatch")


def _verify_arm64_macho_contract(path: Path, error_prefix: str) -> None:
    arches = set(
        _run(
            ["/usr/bin/lipo", "-archs", str(path)],
            failure_code=f"{error_prefix}_architectures_invalid",
        ).split()
    )
    if arches != {"arm64"}:
        raise ProbeFailure(f"{error_prefix}_architectures_invalid")

    for architecture, expected_target in ARCHITECTURES:
        load_commands = _run(
            [
                "/usr/bin/otool",
                "-l",
                "-arch",
                architecture,
                str(path),
            ],
            failure_code=f"{error_prefix}_deployment_target_invalid",
        )
        minos_values = {
            fields[1]
            for line in load_commands.splitlines()
            if len(fields := line.split()) == 2 and fields[0] == "minos"
        }
        if minos_values != {expected_target}:
            raise ProbeFailure(f"{error_prefix}_deployment_target_invalid")


def _verify_macho_contract(layout: RuntimeLayout) -> None:
    _verify_arm64_macho_contract(layout.server, "llama")
    _verify_arm64_macho_contract(layout.scorer, "scorer")

    dependencies = _run(
        ["/usr/bin/otool", "-L", str(layout.server)],
        failure_code="llama_dependencies_invalid",
    )
    if "/Accelerate.framework/" not in dependencies or any(
        forbidden in dependencies
        for forbidden in (
            "@rpath",
            "libcurl",
            "libggml",
            "libllama",
            "/Metal.framework/",
        )
    ):
        raise ProbeFailure("llama_dependencies_invalid")

    help_output = _run(
        [str(layout.server), "--help"],
        failure_code="llama_cli_incompatible",
    )
    for required_flag in (
        "--api-key",
        "--host",
        "--port",
        "--model",
        "--ctx-size",
        "--threads",
    ):
        if required_flag not in help_output:
            raise ProbeFailure("llama_cli_incompatible")


def _verify_codesign(layout: RuntimeLayout) -> None:
    for target in (layout.server, layout.scorer, layout.converter_app):
        _run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--verbose=2",
                str(target),
            ],
            failure_code="nested_codesign_invalid",
        )


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    output: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ProbeFailure("wire_response_truncated")
        output.append(chunk)
        remaining -= len(chunk)
    return b"".join(output)


def _classify_wire_failure(
    status_code: int, debug: bytes
) -> WireFailureClass:
    """Maps scorer-owned wire diagnostics to a fixed, non-sensitive class."""
    if (
        status_code == WIRE_STATUS_TIMEOUT
        and debug == b"server_loading"
    ):
        return WireFailureClass.SERVER_LOADING
    if status_code == WIRE_STATUS_TIMEOUT or (
        status_code == WIRE_STATUS_ERROR and debug == b"timeout"
    ):
        return WireFailureClass.TIMEOUT
    if status_code == WIRE_STATUS_ERROR and debug == b"empty_content":
        return WireFailureClass.EMPTY_CONTENT
    if status_code == WIRE_STATUS_ERROR and debug in _WIRE_IO_FAILURE_DEBUG:
        return WireFailureClass.IO_FAILED
    return WireFailureClass.NON_OK


def _wire_completion_timeout_code(
    observations: dict[WireFailureClass, int],
) -> str:
    dominant = WireFailureClass.NON_OK
    dominant_count = 0
    for failure_class in _WIRE_FAILURE_REPORT_PRIORITY:
        count = observations.get(failure_class, 0)
        if count > dominant_count:
            dominant = failure_class
            dominant_count = count
    return f"wire_completion_timeout_{dominant.value}"


def _send_wire_request(
    socket_path: Path, generation: int, timeout: float
) -> WireFailureClass | None:
    prompt = PROBE_PROMPT.encode("utf-8")
    request = WIRE_REQUEST_HEADER.pack(
        WIRE_MAGIC,
        WIRE_VERSION,
        WIRE_REQUEST,
        generation,
        5000,
        16,
        len(prompt),
        0,
    )
    connection: socket.socket | None = None
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(max(0.1, min(timeout, 7.0)))
        connection.connect(str(socket_path))
        connection.sendall(request + prompt)
        response = WIRE_RESPONSE_HEADER.unpack(
            _recv_exact(connection, WIRE_RESPONSE_HEADER.size)
        )
        magic, version, kind, response_generation, status_code = response[:5]
        value_size, debug_size = response[6:]
        if (
            magic != WIRE_MAGIC
            or version != WIRE_VERSION
            or kind != WIRE_RESPONSE
            or response_generation != generation
            or value_size > MAX_WIRE_PAYLOAD_BYTES
            or debug_size > MAX_WIRE_PAYLOAD_BYTES
            or value_size + debug_size > MAX_WIRE_PAYLOAD_BYTES
        ):
            raise ProbeFailure("wire_response_invalid")
        value = _recv_exact(connection, value_size)
        debug = _recv_exact(connection, debug_size)
        if status_code != WIRE_STATUS_OK:
            return _classify_wire_failure(status_code, debug)
        try:
            if value.decode("utf-8", errors="strict").strip():
                return None
            return WireFailureClass.EMPTY_CONTENT
        except UnicodeError as error:
            raise ProbeFailure("wire_response_invalid") from error
    except (OSError, TimeoutError):
        return WireFailureClass.IO_FAILED
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def _validate_private_path(path: Path, *, socket_path: bool) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise ProbeFailure("private_endpoint_invalid") from error
    expected_type = stat.S_ISSOCK if socket_path else stat.S_ISREG
    if (
        not expected_type(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ProbeFailure("private_endpoint_invalid")


def _stop_scorer(scorer: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(scorer.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if scorer.poll() is None:
        try:
            scorer.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    try:
        os.killpg(scorer.pid, 0)
    except ProcessLookupError:
        return
    try:
        os.killpg(scorer.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if scorer.poll() is None:
        try:
            scorer.wait(timeout=3)
        except subprocess.TimeoutExpired as error:
            raise ProbeFailure("runtime_cleanup_failed") from error
    try:
        os.killpg(scorer.pid, 0)
    except ProcessLookupError:
        return
    raise ProbeFailure("runtime_cleanup_failed")


def _probe_live_runtime(layout: RuntimeLayout, timeout_seconds: float) -> None:
    with tempfile.TemporaryDirectory(prefix="mz-z-mac-", dir="/tmp") as temporary:
        home = Path(temporary)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "TMPDIR": str(home),
                "PATH": "/usr/bin:/bin",
            }
        )
        for name in list(environment):
            if name.startswith("DYLD_") or name.startswith("MOZC_ZENZ_"):
                environment.pop(name, None)

        try:
            scorer = subprocess.Popen(
                [str(layout.scorer)],
                cwd=layout.resources,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            raise ProbeFailure("scorer_launch_failed") from error

        socket_path = home / ".mozc_zenz_scorer_pipe"
        lock_path = home / ".mozc_zenz_scorer.lock"
        deadline = time.monotonic() + timeout_seconds
        failure: BaseException | None = None
        wire_failures = {failure_class: 0 for failure_class in WireFailureClass}
        try:
            while not socket_path.exists() and time.monotonic() < deadline:
                if scorer.poll() is not None:
                    raise ProbeFailure("scorer_exited_early")
                time.sleep(0.05)
            _validate_private_path(socket_path, socket_path=True)
            _validate_private_path(lock_path, socket_path=False)

            generation = 1
            while time.monotonic() < deadline:
                if scorer.poll() is not None:
                    raise ProbeFailure("scorer_exited_early")
                wire_failure = _send_wire_request(
                    socket_path, generation, deadline - time.monotonic()
                )
                if wire_failure is None:
                    break
                wire_failures[wire_failure] += 1
                generation += 1
                time.sleep(0.1)
            else:
                raise ProbeFailure(
                    _wire_completion_timeout_code(wire_failures)
                )
        except BaseException as error:
            failure = error
        try:
            _stop_scorer(scorer)
        except BaseException as error:
            if failure is None:
                failure = error
        if scorer.poll() is None:
            failure = failure or ProbeFailure("runtime_cleanup_failed")
        if failure is not None:
            raise failure


def run_probe(package: Path, *, live: bool, timeout_seconds: float) -> None:
    if sys.platform != "darwin":
        raise ProbeFailure("macos_required")
    if not 30.0 <= timeout_seconds <= 300.0:
        raise ProbeFailure("timeout_invalid")
    try:
        package = package.resolve(strict=True)
    except OSError as error:
        raise ProbeFailure("package_missing") from error
    # Bazel genrule outputs may carry the owner execute bit even when the
    # generated artifact is a package.  The package itself only needs to be a
    # nonempty, non-writable regular file; extracted runtime files retain exact
    # executable-mode validation in _find_layout().
    _require_regular(
        package,
        executable=None,
        failure_code="package_layout_invalid",
    )

    with tempfile.TemporaryDirectory(prefix="mozkey-pkg-probe-") as temporary:
        expanded = Path(temporary) / "expanded"
        _run(
            ["/usr/sbin/pkgutil", "--expand-full", str(package), str(expanded)],
            timeout=60,
            failure_code="package_expand_failed",
        )
        layout = _find_layout(expanded)
        _verify_macho_contract(layout)
        _verify_codesign(layout)
        if live:
            _probe_live_runtime(layout, timeout_seconds)


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe a packaged Mozkey macOS Zenz runtime"
    )
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_probe(
            arguments.package,
            live=not arguments.skip_live,
            timeout_seconds=arguments.timeout_seconds,
        )
    except ProbeFailure as error:
        print(f"macOS Zenz runtime probe failed: {error.code}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "macOS Zenz runtime probe failed: unexpected_runtime_error",
            file=sys.stderr,
        )
        return 1
    print("macOS Zenz runtime probe passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
