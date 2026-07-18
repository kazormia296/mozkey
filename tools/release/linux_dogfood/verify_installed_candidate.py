#!/usr/bin/python3 -I
"""Bind a live Fcitx/Mozkey session to one attested Linux candidate.

This verifier is intentionally independent from the GUI automation.  It
proves that the candidate attestation was installed byte-for-byte, that the
installed addon is mapped by the exact Fcitx lifetime under test, and that the
live server was started by that Fcitx lifetime with the requested Protocol and
scope environment.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NoReturn


SCHEMA = "mozkey.linux_build_attestation.v2"
EXPECTED_FCITX = Path("/usr/bin/fcitx5")
EXPECTED_ADDON = Path("/usr/lib/fcitx5/fcitx5-mozkey.so")
EXPECTED_SERVER = Path("/usr/lib/mozkey/mozc_server")
PROFILE_MARKER_NAME = ".mozkey-dogfood-fresh-profile.json"
PROFILE_MARKER_SCHEMA = 2
FCITX_PROFILE_PAYLOAD = b"""[Groups/0]
Name=Mozkey Dogfood
Default Layout=jp
DefaultIM=mozkey

[Groups/0/Items/0]
Name=keyboard-jp
Layout=

[Groups/0/Items/1]
Name=mozkey
Layout=

[GroupOrder]
0=Mozkey Dogfood
"""
PROFILE_RUNNER_PATH = "tools/release/linux_dogfood/run_server_restart_gate.py"
MAX_PROFILE_LAUNCH_DELAY_SECONDS = 300
MAX_CONSUMER_AGE_SECONDS = 20 * 60
MAX_CONSUMER_BYTES = 8 * 1024
CONSUMER_START_TOLERANCE_SECONDS = 2
INSTALLED_ATTESTATION = Path(
    "/usr/share/doc/mozkey/linux-build-attestation.json"
)
ADDON_PATH_RECORD = Path("/usr/share/mozkey/fcitx5-addon-dir")
ATTESTED_INSTALLS = {
    "src/bazel-bin/unix/fcitx5/fcitx5-mozkey.so": EXPECTED_ADDON,
    "src/bazel-bin/server/mozc_server": EXPECTED_SERVER,
}
EXPECTED_BINARY_PATHS = frozenset(
    (
        "src/bazel-bin/unix/fcitx5/fcitx5-mozkey.so",
        "src/bazel-bin/unix/fcitx5/grimodex_consumer_tool",
        "src/bazel-bin/server/mozc_server",
        "src/bazel-bin/gui/tool/mozc_tool",
        "src/bazel-bin/zenz_scorer/mozc_zenz_scorer",
    )
)
EXPECTED_TOP_LEVEL_KEYS = frozenset(
    (
        "bazel",
        "binaries",
        "dictionary",
        "git_head",
        "layout",
        "schema_version",
        "zenz_runtime",
    )
)
MAX_DOCUMENT_BYTES = 4 << 20
MAX_PROC_BYTES = 4 << 20
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CONSUMER_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    parent_pid: int
    start_time: str
    executable: str
    command: tuple[str, ...]


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one live installed Mozkey candidate lifetime."
    )
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument(
        "--layout", required=True, choices=("archlinux-x86_64", "ubuntu-layout")
    )
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--official-verifier", required=True, type=Path)
    parser.add_argument("--fcitx-pid", required=True, type=int)
    parser.add_argument("--protocol-root", required=True, type=Path)
    parser.add_argument("--profile-root", required=True, type=Path)
    parser.add_argument("--scope-kind", required=True, choices=("absent", "value"))
    parser.add_argument("--scope-value")
    parser.add_argument("--profile-only", action="store_true")
    return parser.parse_args()


def read_bounded(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1 << 20, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                fail(f"bounded read exceeded for {path.name}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def scan_visible_executable(proc: Path, uid: int) -> str | None:
    try:
        metadata = proc.stat()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as error:
        raise RuntimeError(
            "could not inspect process metadata during server discovery"
        ) from error
    if metadata.st_uid != uid:
        return None
    try:
        return os.readlink(proc / "exe")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as executable_error:
        try:
            raw_comm = read_bounded(proc / "comm", 16)
        except (FileNotFoundError, ProcessLookupError) as comm_error:
            try:
                proc.stat()
            except (FileNotFoundError, ProcessLookupError):
                return None
            raise RuntimeError(
                "protected process comm is unreadable during server discovery"
            ) from comm_error
        except OSError as comm_error:
            raise RuntimeError(
                "protected process comm is unreadable during server discovery"
            ) from comm_error
        if (
            not raw_comm.endswith(b"\n")
            or not 1 <= len(raw_comm) - 1 <= 15
            or b"\n" in raw_comm[:-1]
            or b"\0" in raw_comm[:-1]
            or any(byte < 0x20 or byte > 0x7E for byte in raw_comm[:-1])
        ):
            fail("protected process comm is invalid during server discovery")
        if raw_comm[:-1] == EXPECTED_SERVER.name.encode("ascii"):
            raise RuntimeError(
                "a Mozkey server candidate executable is unreadable"
            ) from executable_error
        return None


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def git_blob_id(payload: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(payload)}\0".encode("ascii"))
    digest.update(payload)
    return digest.hexdigest()


def require_regular_file(
    path: Path,
    *,
    owner: tuple[int, int],
    mode: int,
) -> os.stat_result:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != owner
    ):
        fail(f"unsafe file identity: {path}")
    if stat.S_IMODE(metadata.st_mode) != mode:
        fail(f"file mode is not {mode:04o}: {path}")
    return metadata


def read_private_regular_identity(
    path: Path,
    limit: int,
    mode: int,
    *,
    allow_replacement: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_gid != os.getgid()
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
        ):
            fail("private file descriptor identity is invalid")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1 << 16, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                fail("private file payload exceeded its cap")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    signature = lambda metadata: (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    if signature(before) != signature(after):
        if allow_replacement:
            return None
        fail("private file changed while it was read")
    linked = path.lstat()
    if (
        not stat.S_ISREG(linked.st_mode)
        or linked.st_uid != os.getuid()
        or linked.st_gid != os.getgid()
        or stat.S_IMODE(linked.st_mode) != mode
        or linked.st_nlink != 1
    ):
        fail("private file path identity is invalid")
    if (linked.st_dev, linked.st_ino) != (after.st_dev, after.st_ino):
        if allow_replacement:
            return None
        fail("private file path was replaced while it was read")
    payload = b"".join(chunks)
    if (
        path.is_symlink()
        or path.resolve(strict=True) != path
        or linked.st_size != len(payload)
    ):
        fail("private file path or size is invalid")
    return after, payload


def trusted_system_owner() -> tuple[int, int]:
    anchors = (EXPECTED_FCITX, Path("/usr/bin/git"))
    identities = []
    for anchor in anchors:
        metadata = anchor.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid == os.getuid()
            or metadata.st_mode & 0o022
        ):
            fail("trusted system executable ownership is invalid")
        identities.append((metadata.st_uid, metadata.st_gid))
    if len(set(identities)) != 1:
        fail("trusted system executable ownership views disagree")
    return identities[0]


def clean_git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        env=clean_git_environment(),
    )
    if result.returncode != 0 or result.stderr:
        fail("Git candidate inspection failed")
    return result.stdout.strip()


def verify_tracked_snapshot(
    root: Path, snapshot: Path, relative: str, expected_head_mode: str, mode: int
) -> None:
    if (
        not snapshot.is_absolute()
        or snapshot.resolve(strict=True) != snapshot
        or snapshot.parent.stat().st_uid != os.getuid()
        or stat.S_IMODE(snapshot.parent.stat().st_mode) not in (0o500, 0o700)
    ):
        fail("official verifier snapshot directory identity is invalid")
    require_regular_file(
        snapshot, owner=(os.getuid(), os.getgid()), mode=mode
    )
    tree = git(root, "ls-tree", "HEAD", "--", relative).split()
    if (
        len(tree) != 4
        or tree[0] != expected_head_mode
        or tree[1] != "blob"
        or tree[3] != relative
    ):
        fail("official verifier source is not the expected HEAD blob")
    payload = read_bounded(snapshot, MAX_DOCUMENT_BYTES)
    if git_blob_id(payload) != tree[2]:
        fail("official verifier snapshot differs from its HEAD blob")


def run_official_verifier(
    root: Path, layout: str, attestation: Path, verifier: Path
) -> Mapping[str, Any]:
    normalizer = verifier.with_name("normalize_zenz_gguf.py")
    verify_tracked_snapshot(
        root,
        verifier,
        "tools/release/linux_build_attestation.py",
        "100755",
        0o500,
    )
    verify_tracked_snapshot(
        root,
        normalizer,
        "tools/release/normalize_zenz_gguf.py",
        "100644",
        0o400,
    )
    module_name = "mozkey_dogfood_linux_build_attestation"
    if module_name in sys.modules or "normalize_zenz_gguf" in sys.modules:
        fail("official verifier module namespace is already populated")
    specification = importlib.util.spec_from_file_location(module_name, verifier)
    if specification is None or specification.loader is None:
        fail("could not load the official attestation verifier snapshot")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    sys.path.insert(0, str(verifier.parent))
    try:
        if importlib.util.find_spec("tools") is not None:
            fail("an unexpected tools package could shadow the verifier snapshot")
        specification.loader.exec_module(module)
        loaded_normalizer = sys.modules.get("normalize_zenz_gguf")
        loaded_normalizer_path = getattr(loaded_normalizer, "__file__", None)
        if (
            loaded_normalizer is None
            or not isinstance(loaded_normalizer_path, str)
            or Path(loaded_normalizer_path).resolve(strict=True) != normalizer
        ):
            fail("official verifier did not load the pinned normalizer snapshot")
        verify_function = getattr(module, "verify", None)
        if not callable(verify_function):
            fail("official attestation verifier API is unavailable")
        verified = verify_function(root, layout, attestation)
        if not isinstance(verified, Mapping):
            fail("official attestation verifier returned an invalid document")
    except Exception as error:
        raise RuntimeError("official Linux build attestation verification failed") from error
    finally:
        if not sys.path or sys.path[0] != str(verifier.parent):
            fail("official verifier import path identity changed")
        del sys.path[0]
        sys.modules.pop(module_name, None)
        sys.modules.pop("normalize_zenz_gguf", None)
    verify_tracked_snapshot(
        root,
        verifier,
        "tools/release/linux_build_attestation.py",
        "100755",
        0o500,
    )
    verify_tracked_snapshot(
        root,
        normalizer,
        "tools/release/normalize_zenz_gguf.py",
        "100644",
        0o400,
    )
    return verified


def load_attestation(
    repository_root: Path, layout: str, attestation: Path, official_verifier: Path
) -> tuple[dict[str, Any], bytes, dict[str, Mapping[str, Any]]]:
    root = repository_root.resolve(strict=True)
    if root != repository_root or root.is_symlink() or not (root / ".git").exists():
        fail("repository root identity is invalid")
    expected_attestation = root / "dist" / "linux" / layout / "build-attestation.json"
    if attestation != expected_attestation or attestation.resolve(strict=True) != attestation:
        fail("candidate attestation is not the canonical layout attestation")
    require_regular_file(
        attestation, owner=(os.getuid(), os.getgid()), mode=0o644
    )
    system_owner = trusted_system_owner()
    require_regular_file(
        INSTALLED_ATTESTATION, owner=system_owner, mode=0o644
    )
    require_regular_file(
        ADDON_PATH_RECORD, owner=system_owner, mode=0o644
    )
    if read_bounded(ADDON_PATH_RECORD, 128) != b"/usr/lib/fcitx5\n":
        fail("installed Fcitx addon path record is invalid")
    candidate_payload = read_bounded(attestation, MAX_DOCUMENT_BYTES)
    installed_payload = read_bounded(INSTALLED_ATTESTATION, MAX_DOCUMENT_BYTES)
    if not candidate_payload or candidate_payload != installed_payload:
        fail("installed attestation differs from the candidate attestation")
    try:
        document = json.loads(candidate_payload.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("candidate attestation is not strict JSON") from error
    if not isinstance(document, dict) or set(document) != EXPECTED_TOP_LEVEL_KEYS:
        fail("candidate attestation top-level shape is invalid")
    if document.get("schema_version") != SCHEMA or document.get("layout") != layout:
        fail("candidate attestation schema or layout mismatch")
    head = git(root, "rev-parse", "HEAD")
    if not HEX40.fullmatch(head) or document.get("git_head") != head:
        fail("candidate attestation Git HEAD mismatch")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        fail("candidate checkout has tracked changes")
    officially_verified = run_official_verifier(
        root, layout, attestation, official_verifier
    )
    if dict(officially_verified) != document:
        fail("official verifier document differs from the parsed attestation")
    if git(root, "rev-parse", "HEAD") != head or git(
        root, "status", "--porcelain", "--untracked-files=no"
    ):
        fail("candidate checkout changed during official verification")
    if read_bounded(attestation, MAX_DOCUMENT_BYTES) != candidate_payload:
        fail("candidate attestation changed during official verification")
    raw_records = document.get("binaries")
    if not isinstance(raw_records, list) or len(raw_records) != len(
        EXPECTED_BINARY_PATHS
    ):
        fail("candidate attestation binary set is invalid")
    records: dict[str, Mapping[str, Any]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping) or set(raw_record) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            fail("candidate binary record shape is invalid")
        path = raw_record.get("path")
        digest = raw_record.get("sha256")
        size = raw_record.get("size_bytes")
        if (
            not isinstance(path, str)
            or path in records
            or path not in EXPECTED_BINARY_PATHS
            or not isinstance(digest, str)
            or not HEX64.fullmatch(digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            fail("candidate binary record value is invalid")
        records[path] = raw_record
    if set(records) != EXPECTED_BINARY_PATHS:
        fail("candidate attestation binary allowlist mismatch")
    for source_path, installed_path in ATTESTED_INSTALLS.items():
        record = records[source_path]
        installed_metadata = require_regular_file(
            installed_path, owner=system_owner, mode=0o755
        )
        if (
            installed_metadata.st_size != record["size_bytes"]
            or sha256_file(installed_path) != record["sha256"]
        ):
            fail("installed Mozkey binary differs from the candidate attestation")
    return document, candidate_payload, records


def recheck_bound_files(
    root: Path,
    layout: str,
    attestation: Path,
    official_verifier: Path,
    head: str,
    expected_payload: bytes,
    expected_document: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
) -> None:
    final_verified = run_official_verifier(
        root, layout, attestation, official_verifier
    )
    if dict(final_verified) != dict(expected_document):
        fail("candidate build state changed during live verification")
    require_regular_file(
        attestation, owner=(os.getuid(), os.getgid()), mode=0o644
    )
    system_owner = trusted_system_owner()
    require_regular_file(
        INSTALLED_ATTESTATION, owner=system_owner, mode=0o644
    )
    require_regular_file(
        ADDON_PATH_RECORD, owner=system_owner, mode=0o644
    )
    if (
        read_bounded(attestation, MAX_DOCUMENT_BYTES) != expected_payload
        or read_bounded(INSTALLED_ATTESTATION, MAX_DOCUMENT_BYTES)
        != expected_payload
        or read_bounded(ADDON_PATH_RECORD, 128) != b"/usr/lib/fcitx5\n"
    ):
        fail("attestation or installed path record changed during verification")
    for source_path, installed_path in ATTESTED_INSTALLS.items():
        record = records[source_path]
        metadata = require_regular_file(
            installed_path, owner=system_owner, mode=0o755
        )
        if (
            metadata.st_size != record["size_bytes"]
            or sha256_file(installed_path) != record["sha256"]
        ):
            fail("installed Mozkey binary changed during verification")
    if git(root, "rev-parse", "HEAD") != head or git(
        root, "status", "--porcelain", "--untracked-files=no"
    ):
        fail("candidate checkout changed during live verification")


def proc_parent_and_start_time(proc: Path) -> tuple[int, str]:
    raw = read_bounded(proc / "stat", MAX_PROC_BYTES)
    close = raw.rfind(b")")
    if close < 0:
        fail("invalid process stat")
    fields = raw[close + 2 :].decode("ascii", "strict").split()
    if (
        len(fields) < 20
        or not fields[1].isdecimal()
        or not fields[19].isdecimal()
    ):
        fail("invalid process parent or start time")
    return int(fields[1]), fields[19]


def proc_uids(proc: Path) -> tuple[int, ...]:
    raw = read_bounded(proc / "status", MAX_PROC_BYTES).decode("ascii", "strict")
    line = next((item for item in raw.splitlines() if item.startswith("Uid:")), None)
    if line is None:
        fail("process status lacks Uid identity")
    values = tuple(int(value) for value in line.split()[1:])
    if len(values) != 4:
        fail("process Uid identity is invalid")
    return values


def process_record(pid: int, expected_executable: Path) -> ProcessRecord:
    if pid <= 1:
        fail("process pid is invalid")
    proc = Path("/proc") / str(pid)
    uid = os.getuid()
    if proc.stat().st_uid != uid or any(value != uid for value in proc_uids(proc)):
        fail("process owner identity mismatch")
    executable = os.readlink(proc / "exe")
    if executable != str(expected_executable):
        fail("process executable identity mismatch")
    raw_command = read_bounded(proc / "cmdline", MAX_PROC_BYTES)
    if not raw_command.endswith(b"\0"):
        fail("process command line is not NUL terminated")
    command = tuple(
        item.decode("utf-8", "strict") for item in raw_command[:-1].split(b"\0")
    )
    if not command or command[0] != str(expected_executable):
        fail("process argv[0] identity mismatch")
    parent_pid, start_time = proc_parent_and_start_time(proc)
    return ProcessRecord(pid, parent_pid, start_time, executable, command)


def verify_same_process(record: ProcessRecord, expected_executable: Path) -> None:
    if process_record(record.pid, expected_executable) != record:
        fail("live process identity changed during verification")


def environment(proc: Path) -> dict[str, str]:
    raw = read_bounded(proc / "environ", MAX_PROC_BYTES)
    if raw and not raw.endswith(b"\0"):
        fail("process environment is not NUL terminated")
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        if b"=" not in item:
            fail("process environment entry is invalid")
        raw_name, raw_value = item.split(b"=", 1)
        name = raw_name.decode("utf-8", "strict")
        value = raw_value.decode("utf-8", "strict")
        if name in result:
            fail("process environment has a duplicate key")
        result[name] = value
    return result


def verify_loaded_addon(fcitx: ProcessRecord) -> None:
    maps = read_bounded(Path("/proc") / str(fcitx.pid) / "maps", MAX_PROC_BYTES)
    expected_metadata = EXPECTED_ADDON.stat()
    matches: list[tuple[int, int, int]] = []
    executable_mapping = False
    for raw_line in maps.splitlines():
        fields = raw_line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        try:
            pathname = fields[5].decode("utf-8", "strict")
        except UnicodeError:
            fail("Fcitx memory map path is not UTF-8")
        if pathname != str(EXPECTED_ADDON):
            continue
        try:
            permissions = fields[1]
            if re.fullmatch(rb"[r-][w-][x-][ps]", permissions) is None:
                fail("Fcitx addon memory map permissions are invalid")
            major_raw, minor_raw = fields[3].split(b":", 1)
            matches.append(
                (int(major_raw, 16), int(minor_raw, 16), int(fields[4], 10))
            )
            executable_mapping = executable_mapping or permissions[2:3] == b"x"
        except (ValueError, TypeError):
            fail("Fcitx addon memory map identity is invalid")
    expected = (
        os.major(expected_metadata.st_dev),
        os.minor(expected_metadata.st_dev),
        expected_metadata.st_ino,
    )
    if (
        not matches
        or not executable_mapping
        or any(match != expected for match in matches)
    ):
        fail("exact installed addon is not mapped by the Fcitx lifetime")


def find_servers() -> list[ProcessRecord]:
    uid = os.getuid()
    records: list[ProcessRecord] = []
    for proc in sorted(Path("/proc").iterdir(), key=lambda path: path.name):
        if not proc.name.isdecimal() or proc.name.startswith("0"):
            continue
        if scan_visible_executable(proc, uid) != str(EXPECTED_SERVER):
            continue
        records.append(process_record(int(proc.name), EXPECTED_SERVER))
    return records


def find_server() -> ProcessRecord:
    records = find_servers()
    if len(records) != 1:
        fail(f"expected one live installed Mozkey server, got {len(records)}")
    return records[0]


def validate_scope(args: argparse.Namespace) -> str | None:
    if args.scope_kind == "absent":
        if args.scope_value is not None:
            fail("absent scope must not have a value")
        return None
    if args.scope_value is None or not args.scope_value or "\0" in args.scope_value:
        fail("value scope requires a non-empty safe value")
    return args.scope_value


def validate_server_identity(
    server: ProcessRecord,
    fcitx: ProcessRecord,
    requested_scope: str | None,
    protocol_root: Path,
    profile_root: Path,
) -> None:
    if int(server.start_time) <= int(fcitx.start_time):
        fail("Mozkey server predates the Fcitx lifetime under test")
    if server.parent_pid != fcitx.pid or server.command != (str(EXPECTED_SERVER),):
        fail("Mozkey server is not the exact direct child of the Fcitx lifetime")
    server_environment = environment(Path("/proc") / str(server.pid))
    actual_scope = server_environment.get("MOZKEY_GRIMODEX_SCOPE")
    if actual_scope != requested_scope or (
        requested_scope is None and "MOZKEY_GRIMODEX_SCOPE" in server_environment
    ):
        fail("live Mozkey server scope environment mismatch")
    if server_environment.get("GRIMODEX_IME_ROOT") != str(protocol_root):
        fail("live Mozkey server Protocol root mismatch")
    if server_environment.get("XDG_CONFIG_HOME") != str(profile_root):
        fail("live Mozkey server profile root environment mismatch")
    if server_environment.get("HOME") != str(profile_root):
        fail("live Mozkey server HOME mismatch")
    server_executable_metadata = (Path("/proc") / str(server.pid) / "exe").stat()
    installed_server_metadata = EXPECTED_SERVER.stat()
    if (
        server_executable_metadata.st_dev != installed_server_metadata.st_dev
        or server_executable_metadata.st_ino != installed_server_metadata.st_ino
    ):
        fail("live Mozkey server inode differs from the installed candidate")


def validate_profile_root(path: Path) -> Path:
    runtime = Path(f"/run/user/{os.getuid()}")
    runtime_metadata = runtime.lstat()
    if (
        runtime.is_symlink()
        or runtime.resolve(strict=True) != runtime
        or not stat.S_ISDIR(runtime_metadata.st_mode)
        or runtime_metadata.st_uid != os.getuid()
        or stat.S_IMODE(runtime_metadata.st_mode) != 0o700
        or not path.is_absolute()
        or path.is_symlink()
        or path.resolve(strict=True) != path
        or path.parent != runtime
    ):
        fail("profile root must be a canonical direct child of the user runtime")
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("profile root identity is invalid")
    return path


def validate_fcitx_profile(root: Path) -> str:
    directory = root / "fcitx5"
    profile = directory / "profile"
    directory_metadata = directory.lstat()
    observed = read_private_regular_identity(profile, 4096, 0o600)
    if observed is None:
        fail("fresh Fcitx profile changed during verification")
    _, payload = observed
    final_directory_metadata = directory.lstat()
    if (
        directory.is_symlink()
        or directory.resolve(strict=True) != directory
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != os.getuid()
        or directory_metadata.st_gid != os.getgid()
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        or (directory_metadata.st_dev, directory_metadata.st_ino)
        != (final_directory_metadata.st_dev, final_directory_metadata.st_ino)
        or payload != FCITX_PROFILE_PAYLOAD
    ):
        fail("fresh Fcitx profile identity or content is invalid")
    return sha256_bytes(payload)


def validate_no_mozkey_config_override(root: Path) -> None:
    override = root / "fcitx5" / "conf" / "mozkey.conf"
    try:
        override.lstat()
    except FileNotFoundError:
        return
    fail("fresh Fcitx profile contains a Mozkey configuration override")


def expected_mozkey_release_version(repository_root: Path) -> str:
    source = git(repository_root, "show", "HEAD:src/version.bzl")
    values: list[int] = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        matches = re.findall(
            rf"^MOZKEY_RELEASE_VERSION_{name} = ([0-9]+)$",
            source,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            fail("Mozkey release version source is invalid")
        values.append(int(matches[0]))
    return f"v{values[0]}.{values[1]}.{values[2]}"


def validate_consumer_payload(
    payload: bytes,
    expected_version: str,
    *,
    now: datetime.datetime | None = None,
    not_before: datetime.datetime | None = None,
) -> str:
    if not payload or len(payload) > MAX_CONSUMER_BYTES:
        fail("Mozkey consumer handshake size is invalid")
    try:
        document = json.loads(payload.decode("ascii", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Mozkey consumer handshake is invalid JSON") from error
    expected_keys = {
        "capabilities",
        "consumer_id",
        "format_version",
        "last_seen",
        "name",
        "platform",
        "version",
    }
    expected_capabilities = {
        "application_scoping": True,
        "dynamic_dictionary": True,
        "profile": True,
        "zenzai_v3_conditions": True,
    }
    timestamp = document.get("last_seen") if isinstance(document, dict) else None
    capabilities = (
        document.get("capabilities") if isinstance(document, dict) else None
    )
    if (
        not isinstance(document, dict)
        or set(document) != expected_keys
        or not isinstance(capabilities, dict)
        or set(capabilities) != set(expected_capabilities)
        or any(capabilities[name] is not True for name in expected_capabilities)
        or document.get("consumer_id") != "fcitx5-mozkey"
        or type(document.get("format_version")) is not int
        or document.get("format_version") != 1
        or document.get("name") != "Mozkey for Grimodex on Linux"
        or document.get("platform") != "linux"
        or document.get("version") != expected_version
        or not isinstance(timestamp, str)
        or CONSUMER_TIMESTAMP.fullmatch(timestamp) is None
    ):
        fail("Mozkey consumer handshake semantics are invalid")
    canonical = (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    if payload != canonical:
        fail("Mozkey consumer handshake encoding is not canonical")
    try:
        observed = datetime.datetime.strptime(
            timestamp, "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=datetime.timezone.utc)
    except ValueError as error:
        raise RuntimeError("Mozkey consumer heartbeat timestamp is invalid") from error
    current = now or datetime.datetime.now(datetime.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        fail("consumer verification clock must be timezone-aware")
    if not_before is not None and (
        not_before.tzinfo is None or not_before.utcoffset() is None
    ):
        fail("consumer lifetime boundary must be timezone-aware")
    age_seconds = (current.astimezone(datetime.timezone.utc) - observed).total_seconds()
    if age_seconds < -60 or age_seconds > MAX_CONSUMER_AGE_SECONDS:
        fail("Mozkey consumer heartbeat is not fresh")
    if (
        not_before is not None
        and observed
        < not_before.astimezone(datetime.timezone.utc)
        - datetime.timedelta(seconds=CONSUMER_START_TOLERANCE_SECONDS)
    ):
        fail("Mozkey consumer heartbeat predates this Fcitx lifetime")
    return sha256_bytes(payload)


def fcitx_wall_clock_window(start_time: str) -> tuple[datetime.datetime, datetime.datetime]:
    if not start_time.isdigit() or start_time.startswith("0"):
        fail("Fcitx start time is invalid for consumer verification")
    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        boot_clock = getattr(time, "CLOCK_BOOTTIME")
        boottime_seconds = time.clock_gettime(boot_clock)
    except (AttributeError, OSError, ValueError) as error:
        raise RuntimeError("kernel boot clock is unavailable") from error
    if (
        not isinstance(ticks_per_second, int)
        or ticks_per_second < 1
        or ticks_per_second > 1_000_000
    ):
        fail("kernel clock tick frequency is invalid")
    age_seconds = boottime_seconds - int(start_time) / ticks_per_second
    if age_seconds < -1:
        fail("Fcitx start time is in the future")
    current = datetime.datetime.now(datetime.timezone.utc)
    return current, current - datetime.timedelta(seconds=max(age_seconds, 0))


def validate_consumer_handshake(
    protocol_root: Path,
    repository_root: Path,
    fcitx_pid: int,
    fcitx_start_time: str,
) -> str:
    root_metadata = protocol_root.lstat()
    consumers = protocol_root / "consumers"
    consumers_metadata = consumers.lstat()
    if (
        protocol_root.is_symlink()
        or protocol_root.resolve(strict=True) != protocol_root
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.getuid()
        or root_metadata.st_gid != os.getgid()
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
        or consumers.is_symlink()
        or consumers.resolve(strict=True) != consumers
        or not stat.S_ISDIR(consumers_metadata.st_mode)
        or consumers_metadata.st_uid != os.getuid()
        or consumers_metadata.st_gid != os.getgid()
        or stat.S_IMODE(consumers_metadata.st_mode) != 0o700
        or consumers_metadata.st_dev != root_metadata.st_dev
    ):
        fail("Mozkey consumer directory identity is invalid")
    if fcitx_pid <= 1:
        fail("Fcitx pid is invalid for consumer verification")
    expected_entries = ["fcitx5-mozkey.json"]
    temporary_pattern = re.compile(
        rf"^\.fcitx5-mozkey\.{fcitx_pid}\.[0-9]+\.tmp$"
    )

    def refresh_in_progress(entries: list[str]) -> bool:
        if not entries:
            return True
        temporary = [name for name in entries if temporary_pattern.fullmatch(name)]
        return (
            len(temporary) == 1
            and len(entries) in (1, 2)
            and all(
                name == "fcitx5-mozkey.json" or name in temporary
                for name in entries
            )
        )

    consumer = consumers / "fcitx5-mozkey.json"
    payload: bytes | None = None
    for _ in range(50):
        entries = sorted(path.name for path in consumers.iterdir())
        if entries != expected_entries:
            if refresh_in_progress(entries):
                time.sleep(0.01)
                continue
            fail("Mozkey consumer directory entries are invalid")
        observed = read_private_regular_identity(
            consumer,
            MAX_CONSUMER_BYTES,
            0o600,
            allow_replacement=True,
        )
        if observed is None:
            continue
        metadata, candidate = observed
        if metadata.st_dev != consumers_metadata.st_dev:
            fail("Mozkey consumer marker filesystem identity is invalid")
        final_entries = sorted(path.name for path in consumers.iterdir())
        if final_entries != expected_entries:
            if refresh_in_progress(final_entries):
                time.sleep(0.01)
                continue
            fail("Mozkey consumer directory changed unexpectedly")
        payload = candidate
        break
    if payload is None:
        fail("Mozkey consumer marker changed continuously during verification")
    current, not_before = fcitx_wall_clock_window(fcitx_start_time)
    return validate_consumer_payload(
        payload,
        expected_mozkey_release_version(repository_root),
        now=current,
        not_before=not_before,
    )


def validate_fresh_profile_evidence(
    root: Path,
    repository_root: Path,
    git_head: str,
    fcitx_start_time: str,
) -> tuple[str, str, str]:
    root = validate_profile_root(root)
    root_metadata = root.lstat()
    marker = root / PROFILE_MARKER_NAME
    marker_metadata = require_regular_file(
        marker, owner=(os.getuid(), os.getgid()), mode=0o400
    )
    if marker.is_symlink() or marker.resolve(strict=True) != marker:
        fail("fresh profile marker identity is invalid")
    payload = read_bounded(marker, 4096)
    try:
        document = json.loads(payload.decode("ascii", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("fresh profile marker is invalid") from error
    runner_tree = git(
        repository_root, "ls-tree", "HEAD", "--", PROFILE_RUNNER_PATH
    ).split()
    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError) as error:
        raise RuntimeError("kernel clock tick frequency is unavailable") from error
    boot_id = read_bounded(Path("/proc/sys/kernel/random/boot_id"), 128).decode(
        "ascii", "strict"
    ).strip()
    expected_keys = {
        "boot_id",
        "clock_ticks_per_second",
        "created_boottime_ticks",
        "git_head",
        "home_root",
        "nonce",
        "root_device",
        "root_inode",
        "runner_blob",
        "schema_version",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_keys
        or document.get("schema_version") != PROFILE_MARKER_SCHEMA
        or document.get("git_head") != git_head
        or document.get("home_root") != str(root)
        or document.get("boot_id") != boot_id
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            boot_id,
        )
        is None
        or len(runner_tree) != 4
        or runner_tree[0] != "100755"
        or runner_tree[1] != "blob"
        or runner_tree[3] != PROFILE_RUNNER_PATH
        or document.get("runner_blob") != runner_tree[2]
        or document.get("root_device") != root_metadata.st_dev
        or document.get("root_inode") != root_metadata.st_ino
        or not isinstance(ticks_per_second, int)
        or ticks_per_second < 1
        or ticks_per_second > 1_000_000
        or document.get("clock_ticks_per_second") != ticks_per_second
        or not isinstance(document.get("created_boottime_ticks"), int)
        or document["created_boottime_ticks"] < 1
        or not isinstance(document.get("nonce"), str)
        or re.fullmatch(r"[0-9a-f]{64}", document["nonce"]) is None
        or marker_metadata.st_dev != root_metadata.st_dev
    ):
        fail("fresh profile marker does not bind this installed candidate")
    fcitx_profile_sha256 = validate_fcitx_profile(root)
    validate_no_mozkey_config_override(root)
    legacy_profile = root / ".mozkey"
    try:
        legacy_profile.lstat()
    except FileNotFoundError:
        pass
    else:
        fail("fresh dogfood HOME contains a legacy Mozkey profile")
    if not fcitx_start_time.isdigit() or fcitx_start_time.startswith("0"):
        fail("Fcitx start time is invalid for fresh profile verification")
    fcitx_start_ticks = int(fcitx_start_time)
    created_ticks = document["created_boottime_ticks"]
    if (
        created_ticks > fcitx_start_ticks + 1
        or fcitx_start_ticks - created_ticks
        > MAX_PROFILE_LAUNCH_DELAY_SECONDS * ticks_per_second
    ):
        fail("Fcitx was not launched from the attested fresh profile setup")
    return (
        sha256_bytes(payload),
        hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        fcitx_profile_sha256,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if (
        Path(sys.executable).resolve(strict=True)
        != Path("/usr/bin/python3").resolve(strict=True)
        or not sys.flags.isolated
    ):
        fail("installed candidate verification requires exact isolated Python")
    if os.getuid() == 0:
        fail("installed candidate verification must run as the desktop user")
    root = args.repository_root
    if not root.is_absolute() or root.resolve(strict=True) != root:
        fail("repository root must be canonical and absolute")
    if not args.attestation.is_absolute():
        fail("candidate attestation must be absolute")
    protocol_root = args.protocol_root
    if not protocol_root.is_absolute() or protocol_root.resolve(strict=True) != protocol_root:
        fail("Protocol root must be canonical and absolute")
    profile_root = validate_profile_root(args.profile_root)
    requested_scope = validate_scope(args)
    document, attestation_payload, records = load_attestation(
        root, args.layout, args.attestation, args.official_verifier
    )
    fcitx = process_record(args.fcitx_pid, EXPECTED_FCITX)
    (
        profile_marker_sha256,
        profile_root_sha256,
        fcitx_profile_sha256,
    ) = validate_fresh_profile_evidence(
        profile_root, root, document["git_head"], fcitx.start_time
    )
    fcitx_environment = environment(Path("/proc") / str(fcitx.pid))
    actual_fcitx_scope = fcitx_environment.get("MOZKEY_GRIMODEX_SCOPE")
    if (
        actual_fcitx_scope != requested_scope
        or (
            requested_scope is None
            and "MOZKEY_GRIMODEX_SCOPE" in fcitx_environment
        )
        or fcitx_environment.get("GRIMODEX_IME_ROOT") != str(protocol_root)
        or fcitx_environment.get("XDG_CONFIG_HOME") != str(profile_root)
        or fcitx_environment.get("HOME") != str(profile_root)
    ):
        fail("live Fcitx dogfood environment mismatch")
    verify_loaded_addon(fcitx)
    consumer_sha256 = validate_consumer_handshake(
        protocol_root, root, fcitx.pid, fcitx.start_time
    )
    if args.profile_only:
        existing_servers = find_servers()
        if len(existing_servers) > 1:
            fail("profile preflight found multiple installed Mozkey servers")
        if existing_servers:
            validate_server_identity(
                existing_servers[0],
                fcitx,
                requested_scope,
                protocol_root,
                profile_root,
            )
            verify_same_process(existing_servers[0], EXPECTED_SERVER)
        recheck_bound_files(
            root,
            args.layout,
            args.attestation,
            args.official_verifier,
            document["git_head"],
            attestation_payload,
            document,
            records,
        )
        verify_same_process(fcitx, EXPECTED_FCITX)
        verify_loaded_addon(fcitx)
        consumer_sha256 = validate_consumer_handshake(
            protocol_root, root, fcitx.pid, fcitx.start_time
        )
        final_existing_servers = find_servers()
        if final_existing_servers != existing_servers:
            fail("Mozkey server set changed during profile preflight")
        if final_existing_servers:
            validate_server_identity(
                final_existing_servers[0],
                fcitx,
                requested_scope,
                protocol_root,
                profile_root,
            )
            verify_same_process(final_existing_servers[0], EXPECTED_SERVER)
        if validate_fresh_profile_evidence(
            profile_root, root, document["git_head"], fcitx.start_time
        ) != (
            profile_marker_sha256,
            profile_root_sha256,
            fcitx_profile_sha256,
        ):
            fail("fresh profile evidence changed during profile preflight")
        final_fcitx_environment = environment(Path("/proc") / str(fcitx.pid))
        if final_fcitx_environment != fcitx_environment:
            fail("Fcitx environment changed during profile preflight")
        return {
            "schemaVersion": SCHEMA,
            "layout": args.layout,
            "gitHead": document["git_head"],
            "attestationSha256": sha256_bytes(attestation_payload),
            "addonSha256": records[
                "src/bazel-bin/unix/fcitx5/fcitx5-mozkey.so"
            ]["sha256"],
            "serverSha256": records["src/bazel-bin/server/mozc_server"][
                "sha256"
            ],
            "fcitxPid": fcitx.pid,
            "fcitxStartTime": fcitx.start_time,
            "serverPid": None,
            "serverStartTime": None,
            "scope": requested_scope,
            "protocolRootSha256": hashlib.sha256(
                str(protocol_root).encode("utf-8")
            ).hexdigest(),
            "consumerSha256": consumer_sha256,
            "profileMarkerSha256": profile_marker_sha256,
            "profileRootSha256": profile_root_sha256,
            "fcitxProfileSha256": fcitx_profile_sha256,
            "result": "profile-pass",
        }
    server = find_server()
    validate_server_identity(
        server, fcitx, requested_scope, protocol_root, profile_root
    )
    verify_same_process(fcitx, EXPECTED_FCITX)
    verify_same_process(server, EXPECTED_SERVER)
    verify_loaded_addon(fcitx)
    recheck_bound_files(
        root,
        args.layout,
        args.attestation,
        args.official_verifier,
        document["git_head"],
        attestation_payload,
        document,
        records,
    )
    verify_same_process(fcitx, EXPECTED_FCITX)
    verify_same_process(server, EXPECTED_SERVER)
    verify_loaded_addon(fcitx)
    final_server_environment = environment(Path("/proc") / str(server.pid))
    final_fcitx_environment = environment(Path("/proc") / str(fcitx.pid))
    if (
        final_server_environment.get("MOZKEY_GRIMODEX_SCOPE") != requested_scope
        or (
            requested_scope is None
            and "MOZKEY_GRIMODEX_SCOPE" in final_server_environment
        )
        or final_server_environment.get("GRIMODEX_IME_ROOT")
        != str(protocol_root)
        or final_server_environment.get("XDG_CONFIG_HOME") != str(profile_root)
        or final_fcitx_environment.get("XDG_CONFIG_HOME") != str(profile_root)
        or final_server_environment.get("HOME") != str(profile_root)
        or final_fcitx_environment.get("HOME") != str(profile_root)
    ):
        fail("live Mozkey server environment changed during verification")
    final_server_executable = (Path("/proc") / str(server.pid) / "exe").stat()
    final_installed_server = EXPECTED_SERVER.stat()
    if (
        final_server_executable.st_dev != final_installed_server.st_dev
        or final_server_executable.st_ino != final_installed_server.st_ino
    ):
        fail("live Mozkey server no longer matches the installed candidate")
    fresh_server = find_server()
    if fresh_server != server:
        fail("live Mozkey server uniqueness changed during verification")
    if validate_fresh_profile_evidence(
        profile_root, root, document["git_head"], fcitx.start_time
    ) != (
        profile_marker_sha256,
        profile_root_sha256,
        fcitx_profile_sha256,
    ):
        fail("fresh profile evidence changed during candidate verification")
    verify_same_process(fcitx, EXPECTED_FCITX)
    verify_loaded_addon(fcitx)
    consumer_sha256 = validate_consumer_handshake(
        protocol_root, root, fcitx.pid, fcitx.start_time
    )
    return {
        "schemaVersion": SCHEMA,
        "layout": args.layout,
        "gitHead": document["git_head"],
        "attestationSha256": sha256_bytes(attestation_payload),
        "addonSha256": records[
            "src/bazel-bin/unix/fcitx5/fcitx5-mozkey.so"
        ]["sha256"],
        "serverSha256": records["src/bazel-bin/server/mozc_server"]["sha256"],
        "fcitxPid": fcitx.pid,
        "fcitxStartTime": fcitx.start_time,
        "serverPid": server.pid,
        "serverStartTime": server.start_time,
        "scope": requested_scope,
        "protocolRootSha256": hashlib.sha256(
            str(protocol_root).encode("utf-8")
        ).hexdigest(),
        "consumerSha256": consumer_sha256,
        "profileMarkerSha256": profile_marker_sha256,
        "profileRootSha256": profile_root_sha256,
        "fcitxProfileSha256": fcitx_profile_sha256,
        "result": "pass",
    }


def main() -> int:
    try:
        result = run(parse_args())
    except (
        FileNotFoundError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
    ) as error:
        print(f"RESULT:fail reason={type(error).__name__}:{error}", file=os.sys.stderr)
        return 1
    print("RESULT:" + json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
