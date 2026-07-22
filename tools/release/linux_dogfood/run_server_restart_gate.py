#!/usr/bin/python3 -I
"""Prove installed Mozkey restart recovery and ordinary fallback through IBus."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import pwd
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import NoReturn, Sequence

import dbus


EXPECTED_FCITX = "/usr/bin/fcitx5"
EXPECTED_PYTHON = "/usr/bin/python3"
RELEASE_LAYOUT = "archlinux-x86_64"
IBUS_BUS_NAME = "org.freedesktop.IBus"
DBUS_BUS_NAME = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"
PROFILE_MARKER_NAME = ".mozkey-ibg-dogfood-fresh-profile.json"
PROFILE_MARKER_SCHEMA = 2
FCITX_PROFILE_PAYLOAD = b"""[Groups/0]
Name=Mozkey Dogfood
Default Layout=jp
DefaultIM=mozkey-ibg

[Groups/0/Items/0]
Name=keyboard-jp
Layout=

[Groups/0/Items/1]
Name=mozkey-ibg
Layout=

[GroupOrder]
0=Mozkey Dogfood
"""
MAX_PROFILE_LAUNCH_DELAY_SECONDS = 300
MAX_PROC_BYTES = 1 << 20
MAX_STREAM_BYTES = 1 << 16
MAX_TRACKED_BYTES = 4 << 20
PROBE_TIMEOUT_SECONDS = 35.0
COMMAND_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class FcitxIdentity:
    pid: int
    start_time: str
    scope: str | None
    protocol_root: str
    profile_root: str
    home_root: str


@dataclass(frozen=True)
class IBusOwnerIdentity:
    owner: str
    pid: int
    start_time: str
    runtime_device: int
    runtime_inode: int
    socket_device: int
    socket_inode: int
    socket_mode: int


@dataclass(frozen=True)
class FreshProfileEvidence:
    root_device: int
    root_inode: int
    marker_device: int
    marker_inode: int
    marker_sha256: str
    nonce: str
    created_boottime_ticks: int
    clock_ticks_per_second: int
    fcitx_directory_device: int
    fcitx_directory_inode: int
    fcitx_profile_device: int
    fcitx_profile_inode: int
    fcitx_profile_sha256: str


@dataclass(frozen=True)
class TrackedSnapshot:
    path: Path
    blob: str
    mode: int
    device: int
    inode: int


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-fresh-profile", action="store_true")
    parser.add_argument("--launch-fresh-fcitx", action="store_true")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--pause-after", type=int, default=3)
    parser.add_argument("--protocol-root", type=Path)
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def remaining(deadline: float, label: str) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        fail(f"timeout while waiting for {label}")
    return value


def clean_command_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def run_git(root: Path, *arguments: str) -> str:
    status, output, error = run_bounded_command(
        ["/usr/bin/git", "-C", str(root), *arguments],
        clean_command_environment(),
        10.0,
        "Git provenance command",
    )
    if status != 0 or error:
        fail(f"Git provenance command failed: {arguments[0]}")
    return output.strip()


def verify_tracked_file(root: Path, path: Path, mode: str) -> str:
    relative = path.relative_to(root)
    index = run_git(root, "ls-files", "--stage", "--", str(relative)).split()
    head = run_git(root, "ls-tree", "HEAD", "--", str(relative)).split()
    if (
        len(index) != 4
        or index[0] != mode
        or index[3] != str(relative)
        or len(head) != 4
        or head[0] != mode
        or head[1] != "blob"
        or head[2] != index[1]
        or head[3] != str(relative)
        or run_git(root, "hash-object", "--", str(relative)) != index[1]
    ):
        fail(f"dogfood input is not the committed HEAD blob: {path.name}")
    return index[1]


def read_regular_bounded(path: Path, limit: int = MAX_TRACKED_BYTES) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            fail("bounded input is not a regular file")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(1 << 16, limit + 1 - len(payload)))
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
            if len(payload) > limit:
                fail("bounded regular input exceeded its cap")
    finally:
        os.close(descriptor)


def git_blob_id(payload: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(payload)}\0".encode("ascii"))
    digest.update(payload)
    return digest.hexdigest()


def snapshot_tracked_file(
    source: Path, destination: Path, expected_blob: str, mode: int
) -> TrackedSnapshot:
    payload = read_regular_bounded(source)
    if git_blob_id(payload) != expected_blob:
        fail("tracked source changed before its private snapshot")
    descriptor = os.open(
        destination,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | os.O_NOFOLLOW,
        mode,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("could not complete tracked input snapshot")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
    metadata = destination.lstat()
    evidence = TrackedSnapshot(
        destination,
        expected_blob,
        mode,
        metadata.st_dev,
        metadata.st_ino,
    )
    verify_snapshot(evidence)
    return evidence


def verify_snapshot(snapshot: TrackedSnapshot) -> None:
    metadata = snapshot.path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != snapshot.mode
        or metadata.st_dev != snapshot.device
        or metadata.st_ino != snapshot.inode
        or snapshot.path.resolve(strict=True) != snapshot.path
        or git_blob_id(read_regular_bounded(snapshot.path)) != snapshot.blob
    ):
        fail("private tracked input snapshot changed during the gate")


def desktop_runtime_directory() -> Path:
    runtime = Path(f"/run/user/{os.getuid()}")
    metadata = runtime.lstat()
    if (
        runtime.is_symlink()
        or runtime.resolve(strict=True) != runtime
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("desktop runtime directory identity is invalid")
    return runtime


def private_profile_metadata(root: Path, runtime: Path) -> os.stat_result:
    if (
        not root.is_absolute()
        or root.is_symlink()
        or root.resolve(strict=True) != root
        or root.parent != runtime
    ):
        fail("profile root must be a canonical direct child of the user runtime")
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("profile root identity is invalid")
    return metadata


def current_boot_id() -> str:
    raw = proc_value(Path("/proc/sys/kernel/random/boot_id"))
    value = raw.decode("ascii", "strict").strip()
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        fail("kernel boot ID is invalid")
    return value


def clock_ticks_per_second() -> int:
    value = os.sysconf("SC_CLK_TCK")
    if not isinstance(value, int) or value < 1 or value > 1_000_000:
        fail("kernel clock tick frequency is invalid")
    return value


def boottime_ticks(frequency: int) -> int:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        fail("CLOCK_BOOTTIME is unavailable")
    value = time.clock_gettime_ns(clock) * frequency // 1_000_000_000
    if value < 1:
        fail("kernel boot clock is invalid")
    return value


def write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            fail("private evidence write was incomplete")
        view = view[written:]


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
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(1 << 16, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > limit:
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
    if (
        path.is_symlink()
        or path.resolve(strict=True) != path
        or linked.st_size != len(payload)
    ):
        fail("private file path or size is invalid")
    return after, bytes(payload)


def validate_fcitx_profile(root: Path) -> tuple[int, int, int, int, str]:
    directory = root / "fcitx5"
    profile = directory / "profile"
    directory_metadata = directory.lstat()
    observed = read_private_regular_identity(profile, 4096, 0o600)
    if observed is None:
        fail("fresh Fcitx profile changed during verification")
    profile_metadata, payload = observed
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
    return (
        directory_metadata.st_dev,
        directory_metadata.st_ino,
        profile_metadata.st_dev,
        profile_metadata.st_ino,
        hashlib.sha256(payload).hexdigest(),
    )


def validate_no_mozkey_config_override(root: Path) -> None:
    override = root / "fcitx5" / "conf" / "mozkey-ibg.conf"
    try:
        override.lstat()
    except FileNotFoundError:
        return
    fail("fresh Fcitx profile contains a Mozkey configuration override")


def validate_prelaunch_profile_manifest(root: Path) -> None:
    validate_fcitx_profile(root)
    validate_no_mozkey_config_override(root)
    if sorted(path.name for path in root.iterdir()) != [
        PROFILE_MARKER_NAME,
        "fcitx5",
    ]:
        fail("fresh profile root changed before Fcitx launch")
    if sorted(path.name for path in (root / "fcitx5").iterdir()) != ["profile"]:
        fail("fresh Fcitx configuration changed before launch")


def validate_prelaunch_protocol_manifest(
    root: Path, runtime: Path, fixture: dict[str, object]
) -> None:
    metadata = root.lstat()
    if (
        not root.is_absolute()
        or root.is_symlink()
        or root.resolve(strict=True) != root
        or root.parent != runtime
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_gid != os.getgid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or sorted(path.name for path in root.iterdir())
        != ["consumers", "projects", "state.json"]
    ):
        fail("Protocol root is not the exact fresh release fixture")
    consumers = root / "consumers"
    consumers_metadata = consumers.lstat()
    if (
        consumers.is_symlink()
        or consumers.resolve(strict=True) != consumers
        or not stat.S_ISDIR(consumers_metadata.st_mode)
        or consumers_metadata.st_uid != os.getuid()
        or consumers_metadata.st_gid != os.getgid()
        or stat.S_IMODE(consumers_metadata.st_mode) != 0o700
        or consumers_metadata.st_dev != metadata.st_dev
        or any(consumers.iterdir())
    ):
        fail("Protocol consumers directory is not private and empty before launch")
    verify_protocol_root(root, fixture)
    if any(consumers.iterdir()):
        fail("Protocol consumers directory changed before Fcitx exec")


def write_fcitx_profile(root: Path) -> tuple[int, int, int, int, str]:
    directory = root / "fcitx5"
    profile = directory / "profile"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    descriptor = os.open(
        profile,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | os.O_NOFOLLOW,
        0o600,
    )
    try:
        write_all(descriptor, FCITX_PROFILE_PAYLOAD)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    for path in (directory, root):
        directory_descriptor = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return validate_fcitx_profile(root)


def prepare_fresh_profile(
    root: Path, runtime: Path, head: str, runner_blob: str
) -> FreshProfileEvidence:
    metadata = private_profile_metadata(root, runtime)
    if any(root.iterdir()):
        fail("fresh profile root must start empty")
    frequency = clock_ticks_per_second()
    document = {
        "boot_id": current_boot_id(),
        "clock_ticks_per_second": frequency,
        "created_boottime_ticks": boottime_ticks(frequency),
        "git_head": head,
        "home_root": str(root),
        "nonce": secrets.token_hex(32),
        "root_device": metadata.st_dev,
        "root_inode": metadata.st_ino,
        "runner_blob": runner_blob,
        "schema_version": PROFILE_MARKER_SCHEMA,
    }
    payload = (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    marker = root / PROFILE_MARKER_NAME
    descriptor = os.open(
        marker,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | os.O_NOFOLLOW,
        0o400,
    )
    try:
        write_all(descriptor, payload)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    write_fcitx_profile(root)
    return load_fresh_profile_evidence(
        root,
        runtime,
        head,
        runner_blob,
        require_unused=True,
    )


def launch_fresh_fcitx(
    profile_root: Path,
    protocol_root: Path,
    runtime: Path,
    head: str,
    runner_blob: str,
    fixture: dict[str, object],
) -> NoReturn:
    load_fresh_profile_evidence(
        profile_root,
        runtime,
        head,
        runner_blob,
        require_unused=True,
    )
    validate_prelaunch_profile_manifest(profile_root)
    validate_prelaunch_protocol_manifest(protocol_root, runtime, fixture)
    expected_bus = f"unix:path={runtime}/bus"
    if (
        os.environ.get("XDG_RUNTIME_DIR") != str(runtime)
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS") != expected_bus
        or os.environ.get("HOME") != str(profile_root)
        or os.environ.get("XDG_CONFIG_HOME") != str(profile_root)
        or os.environ.get("GRIMODEX_IME_ROOT") != str(protocol_root)
    ):
        fail("fresh Fcitx launch environment is not canonical")
    scope = os.environ.get("MOZKEY_GRIMODEX_SCOPE")
    if scope is not None and (
        not 1 <= len(scope) <= 64
        or re.fullmatch(r"[a-z0-9-]+", scope) is None
    ):
        fail("fresh Fcitx scope is invalid")
    account = pwd.getpwuid(os.getuid())
    environment = {
        "DBUS_SESSION_BUS_ADDRESS": expected_bus,
        "GRIMODEX_IME_ROOT": str(protocol_root),
        "HOME": str(profile_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin",
        "USER": account.pw_name,
        "XDG_CONFIG_HOME": str(profile_root),
        "XDG_RUNTIME_DIR": str(runtime),
    }
    for name in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_SESSION_TYPE",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    if scope is not None:
        environment["MOZKEY_GRIMODEX_SCOPE"] = scope
    os.execve(EXPECTED_FCITX, [EXPECTED_FCITX, "-d"], environment)


def load_fresh_profile_evidence(
    root: Path,
    runtime: Path,
    head: str,
    runner_blob: str,
    *,
    require_unused: bool,
) -> FreshProfileEvidence:
    root_metadata = private_profile_metadata(root, runtime)
    marker = root / PROFILE_MARKER_NAME
    marker_metadata = marker.lstat()
    if (
        marker.is_symlink()
        or marker.resolve(strict=True) != marker
        or not stat.S_ISREG(marker_metadata.st_mode)
        or marker_metadata.st_uid != os.getuid()
        or stat.S_IMODE(marker_metadata.st_mode) != 0o400
    ):
        fail("fresh profile marker identity is invalid")
    payload = read_regular_bounded(marker, 4096)
    try:
        document = json.loads(payload.decode("ascii", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("fresh profile marker is invalid") from error
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
        or document.get("boot_id") != current_boot_id()
        or document.get("git_head") != head
        or document.get("home_root") != str(root)
        or document.get("runner_blob") != runner_blob
        or document.get("root_device") != root_metadata.st_dev
        or document.get("root_inode") != root_metadata.st_ino
        or document.get("clock_ticks_per_second") != clock_ticks_per_second()
        or not isinstance(document.get("created_boottime_ticks"), int)
        or document["created_boottime_ticks"] < 1
        or not isinstance(document.get("nonce"), str)
        or re.fullmatch(r"[0-9a-f]{64}", document["nonce"]) is None
    ):
        fail("fresh profile marker does not bind this release run")
    if require_unused:
        mozkey_profile = root / "mozkey-ibg"
        try:
            mozkey_profile.lstat()
        except FileNotFoundError:
            pass
        else:
            fail("fresh profile already contains Mozkey state")
    legacy_profile = root / ".mozkey-ibg"
    try:
        legacy_profile.lstat()
    except FileNotFoundError:
        pass
    else:
        fail("fresh dogfood HOME contains a legacy Mozkey profile")
    fcitx_profile = validate_fcitx_profile(root)
    validate_no_mozkey_config_override(root)
    return FreshProfileEvidence(
        root_device=root_metadata.st_dev,
        root_inode=root_metadata.st_ino,
        marker_device=marker_metadata.st_dev,
        marker_inode=marker_metadata.st_ino,
        marker_sha256=hashlib.sha256(payload).hexdigest(),
        nonce=document["nonce"],
        created_boottime_ticks=document["created_boottime_ticks"],
        clock_ticks_per_second=document["clock_ticks_per_second"],
        fcitx_directory_device=fcitx_profile[0],
        fcitx_directory_inode=fcitx_profile[1],
        fcitx_profile_device=fcitx_profile[2],
        fcitx_profile_inode=fcitx_profile[3],
        fcitx_profile_sha256=fcitx_profile[4],
    )


def verify_fresh_profile_evidence(
    expected: FreshProfileEvidence,
    root: Path,
    runtime: Path,
    head: str,
    runner_blob: str,
) -> None:
    if load_fresh_profile_evidence(
        root,
        runtime,
        head,
        runner_blob,
        require_unused=False,
    ) != expected:
        fail("fresh profile evidence changed during the gate")


def verify_fresh_profile_launch(
    evidence: FreshProfileEvidence, fcitx_start_time: str
) -> None:
    if not fcitx_start_time.isdigit() or fcitx_start_time.startswith("0"):
        fail("Fcitx start time is invalid for fresh profile verification")
    fcitx_start_ticks = int(fcitx_start_time)
    if (
        evidence.created_boottime_ticks > fcitx_start_ticks + 1
        or fcitx_start_ticks - evidence.created_boottime_ticks
        > MAX_PROFILE_LAUNCH_DELAY_SECONDS * evidence.clock_ticks_per_second
    ):
        fail("Fcitx was not launched directly from this fresh profile setup")


def proc_value(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(MAX_PROC_BYTES + 1)
    if len(value) > MAX_PROC_BYTES:
        fail("proc identity exceeds its cap")
    return value


def scan_visible_executable(proc: Path, uid: int) -> str | None:
    try:
        metadata = proc.stat()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as error:
        raise RuntimeError(
            "could not inspect process metadata during Fcitx discovery"
        ) from error
    if metadata.st_uid != uid:
        return None
    try:
        return os.readlink(proc / "exe")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as executable_error:
        # Kernel comm is a refusal-only hint: it never authorizes a candidate,
        # but an unreadable process named fcitx5 must not disappear from the
        # exactly-one check.  Normal protected services (for example systemd
        # --user) remain harmless scan misses.
        try:
            raw_comm = proc_value(proc / "comm")
        except (FileNotFoundError, ProcessLookupError) as comm_error:
            try:
                proc.stat()
            except (FileNotFoundError, ProcessLookupError):
                return None
            raise RuntimeError(
                "protected process comm is unreadable during Fcitx discovery"
            ) from comm_error
        except OSError as comm_error:
            raise RuntimeError(
                "protected process comm is unreadable during Fcitx discovery"
            ) from comm_error
        if (
            not raw_comm.endswith(b"\n")
            or not 1 <= len(raw_comm) - 1 <= 15
            or b"\n" in raw_comm[:-1]
            or b"\0" in raw_comm[:-1]
            or any(byte < 0x20 or byte > 0x7E for byte in raw_comm[:-1])
        ):
            fail("protected process comm is invalid during Fcitx discovery")
        if raw_comm[:-1] == b"fcitx5":
            raise RuntimeError(
                "an Fcitx candidate executable is unreadable"
            ) from executable_error
        return None


def proc_start_time(proc: Path) -> str:
    raw = proc_value(proc / "stat")
    close = raw.rfind(b")")
    fields = raw[close + 2 :].decode("ascii", "strict").split()
    if close < 0 or len(fields) < 20 or not fields[19].isdigit():
        fail("Fcitx process stat is invalid")
    return fields[19]


def bus_socket_evidence(runtime: Path) -> tuple[int, int, int, int, int]:
    runtime_metadata = runtime.lstat()
    if (
        runtime.is_symlink()
        or runtime.resolve(strict=True) != runtime
        or not stat.S_ISDIR(runtime_metadata.st_mode)
        or runtime_metadata.st_uid != os.getuid()
        or stat.S_IMODE(runtime_metadata.st_mode) != 0o700
    ):
        fail("desktop runtime directory changed during the gate")
    bus_socket = runtime / "bus"
    socket_metadata = bus_socket.lstat()
    if (
        bus_socket.is_symlink()
        or bus_socket.resolve(strict=True) != bus_socket
        or not stat.S_ISSOCK(socket_metadata.st_mode)
        or socket_metadata.st_uid != os.getuid()
        or stat.S_IMODE(socket_metadata.st_mode) != 0o666
    ):
        fail("D-Bus session socket identity or mode is invalid")
    return (
        runtime_metadata.st_dev,
        runtime_metadata.st_ino,
        socket_metadata.st_dev,
        socket_metadata.st_ino,
        stat.S_IMODE(socket_metadata.st_mode),
    )


def ibus_owner_identity(
    expected_fcitx: FcitxIdentity, runtime: Path
) -> IBusOwnerIdentity:
    socket_identity = bus_socket_evidence(runtime)
    address = f"unix:path={runtime}/bus"
    try:
        bus = dbus.bus.BusConnection(address)
        try:
            daemon = dbus.Interface(
                bus.get_object(DBUS_BUS_NAME, DBUS_PATH),
                DBUS_INTERFACE,
            )
            first_owner = str(daemon.GetNameOwner(IBUS_BUS_NAME))
            owner_pid = int(daemon.GetConnectionUnixProcessID(first_owner))
            owner_uid = int(daemon.GetConnectionUnixUser(first_owner))
            second_owner = str(daemon.GetNameOwner(IBUS_BUS_NAME))
            second_pid = int(daemon.GetConnectionUnixProcessID(second_owner))
            second_uid = int(daemon.GetConnectionUnixUser(second_owner))
        finally:
            bus.close()
    except dbus.DBusException as error:
        raise RuntimeError("could not resolve the exact IBus D-Bus owner") from error
    if (
        re.fullmatch(r":[0-9]+\.[0-9]+", first_owner) is None
        or second_owner != first_owner
        or second_pid != owner_pid
        or owner_uid != os.getuid()
        or second_uid != owner_uid
        or owner_pid != expected_fcitx.pid
    ):
        fail("IBus well-known name belongs to an unexpected D-Bus connection")
    proc = Path("/proc") / str(owner_pid)
    try:
        if proc.stat().st_uid != os.getuid() or os.readlink(proc / "exe") != EXPECTED_FCITX:
            fail("IBus D-Bus owner is not the installed Fcitx executable")
        start_time = proc_start_time(proc)
    except (FileNotFoundError, ProcessLookupError) as error:
        raise RuntimeError("IBus D-Bus owner disappeared during verification") from error
    if start_time != expected_fcitx.start_time:
        fail("IBus D-Bus owner lifetime differs from the installed Fcitx lifetime")
    if bus_socket_evidence(runtime) != socket_identity:
        fail("D-Bus session socket changed while resolving the IBus owner")
    return IBusOwnerIdentity(
        owner=first_owner,
        pid=owner_pid,
        start_time=start_time,
        runtime_device=socket_identity[0],
        runtime_inode=socket_identity[1],
        socket_device=socket_identity[2],
        socket_inode=socket_identity[3],
        socket_mode=socket_identity[4],
    )


def installed_fcitx() -> FcitxIdentity:
    uid = os.getuid()
    candidates: list[FcitxIdentity] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal() or entry.name.startswith("0"):
            continue
        if scan_visible_executable(entry, uid) != EXPECTED_FCITX:
            continue
        environment = proc_value(entry / "environ")
        if environment and not environment.endswith(b"\0"):
            fail("Fcitx environment is not NUL terminated")
        values = environment.split(b"\0")
        scope_prefix = b"MOZKEY_GRIMODEX_SCOPE="
        root_prefix = b"GRIMODEX_IME_ROOT="
        profile_prefix = b"XDG_CONFIG_HOME="
        home_prefix = b"HOME="
        scopes = [value[len(scope_prefix) :] for value in values if value.startswith(scope_prefix)]
        roots = [value[len(root_prefix) :] for value in values if value.startswith(root_prefix)]
        profiles = [
            value[len(profile_prefix) :]
            for value in values
            if value.startswith(profile_prefix)
        ]
        homes = [
            value[len(home_prefix) :]
            for value in values
            if value.startswith(home_prefix)
        ]
        if (
            len(scopes) > 1
            or len(roots) != 1
            or len(profiles) != 1
            or len(homes) != 1
        ):
            fail("Fcitx dogfood environment is ambiguous")
        candidates.append(
            FcitxIdentity(
                pid=int(entry.name),
                start_time=proc_start_time(entry),
                scope=scopes[0].decode("utf-8", "strict") if scopes else None,
                protocol_root=roots[0].decode("utf-8", "strict"),
                profile_root=profiles[0].decode("utf-8", "strict"),
                home_root=homes[0].decode("utf-8", "strict"),
            )
        )
    if len(candidates) != 1:
        fail(f"expected exactly one installed Fcitx, got {len(candidates)}")
    return candidates[0]


def verify_protocol_root(
    root: Path, fixture: dict[str, object]
) -> tuple[int, int, str, str]:
    if not root.is_absolute() or root.is_symlink() or root.resolve(strict=True) != root:
        fail("Protocol fixture root must be canonical and non-symlink")
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_gid != os.getgid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("Protocol fixture root identity is invalid")
    if sorted(path.name for path in root.iterdir()) != [
        "consumers",
        "projects",
        "state.json",
    ]:
        fail("Protocol fixture root entries are invalid")
    consumers = root / "consumers"
    consumers_metadata = consumers.lstat()
    if (
        consumers.is_symlink()
        or consumers.resolve(strict=True) != consumers
        or not stat.S_ISDIR(consumers_metadata.st_mode)
        or consumers_metadata.st_uid != os.getuid()
        or consumers_metadata.st_gid != os.getgid()
        or stat.S_IMODE(consumers_metadata.st_mode) != 0o700
        or consumers_metadata.st_dev != metadata.st_dev
    ):
        fail("Protocol fixture consumers directory is invalid")
    consumer_entries = stable_consumer_entries(consumers, allow_empty=True)
    if consumer_entries:
        consumer = consumers / consumer_entries[0]
        consumer_metadata = consumer.lstat()
        if (
            consumer.is_symlink()
            or consumer.resolve(strict=True) != consumer
            or not stat.S_ISREG(consumer_metadata.st_mode)
            or consumer_metadata.st_uid != os.getuid()
            or stat.S_IMODE(consumer_metadata.st_mode) != 0o600
            or consumer_metadata.st_nlink != 1
        ):
            fail("Protocol fixture consumer marker identity is invalid")
    state = root / "state.json"
    project_value = fixture.get("project")
    state_value = fixture.get("state")
    if not isinstance(project_value, dict) or not isinstance(state_value, dict):
        fail("tracked Protocol fixture is invalid")
    project_id = project_value.get("project_id")
    if not isinstance(project_id, str) or re.fullmatch(r"[a-z0-9-]+", project_id) is None:
        fail("tracked Protocol fixture project ID is invalid")
    projects = root / "projects"
    project = projects / f"{project_id}.json"
    projects_metadata = projects.lstat()
    if (
        projects.is_symlink()
        or projects.resolve(strict=True) != projects
        or not stat.S_ISDIR(projects_metadata.st_mode)
        or projects_metadata.st_uid != os.getuid()
        or projects_metadata.st_gid != os.getgid()
        or stat.S_IMODE(projects_metadata.st_mode) != 0o700
        or projects_metadata.st_dev != metadata.st_dev
        or sorted(path.name for path in projects.iterdir()) != [project.name]
    ):
        fail("Protocol fixture projects directory is invalid")
    state_observed = read_private_regular_identity(state, MAX_TRACKED_BYTES, 0o600)
    project_observed = read_private_regular_identity(
        project, MAX_TRACKED_BYTES, 0o600
    )
    if state_observed is None or project_observed is None:
        fail("Protocol fixture documents changed during verification")
    state_metadata, state_payload = state_observed
    project_metadata, project_payload = project_observed
    if (
        state_metadata.st_dev != metadata.st_dev
        or project_metadata.st_dev != metadata.st_dev
    ):
        fail("Protocol fixture documents cross filesystem boundaries")
    if json.loads(state_payload) != state_value or json.loads(project_payload) != project_value:
        fail("Protocol root differs from the tracked fixture")
    final_root = root.lstat()
    final_projects = projects.lstat()
    if (
        (final_root.st_dev, final_root.st_ino) != (metadata.st_dev, metadata.st_ino)
        or (final_projects.st_dev, final_projects.st_ino)
        != (projects_metadata.st_dev, projects_metadata.st_ino)
    ):
        fail("Protocol fixture directory identity changed during verification")
    return (
        metadata.st_dev,
        metadata.st_ino,
        hashlib.sha256(state_payload).hexdigest(),
        hashlib.sha256(project_payload).hexdigest(),
    )


class ProtocolMutationGuard:
    """Detect writes and atomic replacement of the fixed Protocol documents."""

    _MUTATION_MASK = (
        0x00000002
        | 0x00000004
        | 0x00000008
        | 0x00000040
        | 0x00000080
        | 0x00000100
        | 0x00000200
        | 0x00000400
        | 0x00000800
        | 0x00002000
        | 0x00004000
        | 0x00008000
    )

    def __init__(self, paths: Sequence[Path]) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        init = libc.inotify_init1
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add = libc.inotify_add_watch
        add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add.restype = ctypes.c_int
        descriptor = init(os.O_NONBLOCK | os.O_CLOEXEC)
        if descriptor < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        self._descriptor = descriptor
        try:
            watches = []
            for path in paths:
                watch = add(
                    descriptor,
                    os.fsencode(path),
                    ctypes.c_uint32(self._MUTATION_MASK),
                )
                if watch < 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error), path)
                watches.append(watch)
            if len(set(watches)) != len(paths):
                fail("Protocol mutation watches are not one-to-one")
        except BaseException:
            os.close(descriptor)
            raise

    def assert_clean(self) -> None:
        mutated = False
        while True:
            try:
                event = os.read(self._descriptor, 1 << 16)
            except BlockingIOError:
                break
            if not event:
                break
            mutated = True
        if mutated:
            fail("Protocol fixture was mutated during the restart gate")

    def close(self) -> None:
        os.close(self._descriptor)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    def group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    if not group_exists():
        if process.poll() is None:
            process.wait(timeout=1)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        if not group_exists():
            return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    if not group_exists():
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        process.wait(timeout=2)
    deadline = time.monotonic() + 2
    while group_exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if group_exists():
        fail("dogfood subprocess group did not terminate")


class BoundedPipeCapture:
    """Nonblocking, deadline-aware 64 KiB capture for stdout and stderr."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None or process.stderr is None:
            fail("bounded capture requires stdout and stderr pipes")
        self._selector = selectors.DefaultSelector()
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._open_streams: set[str] = set()
        self._stdout_cursor = 0
        try:
            for name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                descriptor = stream.fileno()
                os.set_blocking(descriptor, False)
                self._selector.register(descriptor, selectors.EVENT_READ, name)
                self._open_streams.add(name)
        except BaseException:
            self._selector.close()
            raise

    def close(self) -> None:
        self._selector.close()

    def _size(self) -> int:
        return sum(len(buffer) for buffer in self._buffers.values())

    def pump(self, deadline: float, label: str) -> None:
        events = self._selector.select(remaining(deadline, label))
        for key, _ in events:
            capacity = MAX_STREAM_BYTES - self._size()
            try:
                chunk = os.read(key.fd, max(1, min(1 << 14, capacity + 1)))
            except BlockingIOError:
                continue
            if not chunk:
                self._selector.unregister(key.fd)
                self._open_streams.discard(key.data)
                continue
            self._buffers[key.data].extend(chunk)
            if self._size() > MAX_STREAM_BYTES:
                fail("subprocess output exceeded its 64 KiB cap")

    def next_stdout_line(self, deadline: float, label: str) -> str:
        while True:
            if self._buffers["stderr"]:
                fail("subprocess wrote to stderr")
            stdout = self._buffers["stdout"]
            newline = stdout.find(b"\n", self._stdout_cursor)
            if newline >= 0:
                raw = bytes(stdout[self._stdout_cursor : newline])
                self._stdout_cursor = newline + 1
                return raw.decode("utf-8", "strict")
            if "stdout" not in self._open_streams:
                fail(f"subprocess exited before {label}")
            self.pump(deadline, label)

    def drain_to_eof(self, deadline: float, label: str) -> None:
        while self._open_streams:
            self.pump(deadline, label)

    def decoded(self) -> tuple[str, str]:
        return (
            bytes(self._buffers["stdout"]).decode("utf-8", "strict"),
            bytes(self._buffers["stderr"]).decode("utf-8", "strict"),
        )


def run_bounded_command(
    command: Sequence[str], environment: dict[str, str], timeout: float, label: str
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    capture: BoundedPipeCapture | None = None
    deadline = time.monotonic() + timeout
    try:
        capture = BoundedPipeCapture(process)
        capture.drain_to_eof(deadline, f"{label} output")
        if process.poll() is None:
            try:
                process.wait(timeout=remaining(deadline, f"{label} exit"))
            except subprocess.TimeoutExpired:
                fail(f"{label} timed out")
        output, error = capture.decoded()
    finally:
        try:
            stop_process(process)
        finally:
            if capture is not None:
                capture.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    assert process.returncode is not None
    return process.returncode, output, error


def load_stopper(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "mozkey_dogfood_runtime_stopper", path
    )
    if specification is None or specification.loader is None:
        fail("could not load the runtime identity verifier snapshot")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def unique_installed_server(stopper: ModuleType):
    candidates = [
        identity
        for identity in stopper.Procfs().scan(os.getuid())
        if identity.exe == stopper.MOZKEY_SERVER
    ]
    if len(candidates) != 1:
        fail(f"expected exactly one installed server, got {len(candidates)}")
    return candidates[0]


def require_no_installed_server(stopper: ModuleType) -> None:
    candidates = [
        identity
        for identity in stopper.Procfs().scan(os.getuid())
        if identity.exe == stopper.MOZKEY_SERVER
    ]
    if candidates:
        fail("fresh profile gate requires no pre-existing installed server")


def wait_for_replacement(
    stopper: ModuleType, killed_pid: int, killed_start_time: str, deadline: float
):
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            replacement = unique_installed_server(stopper)
        except RuntimeError as error:
            last_error = error
            time.sleep(0.05)
            continue
        if replacement.pid != killed_pid or replacement.start_time != killed_start_time:
            return replacement
        time.sleep(0.05)
    if last_error is not None:
        raise last_error
    fail("a distinct installed server lifetime was not observed")


def verify_installed_candidate(
    verifier: Path,
    official_verifier: Path,
    repository_root: Path,
    fcitx: FcitxIdentity,
    protocol_root: Path,
    profile_root: Path,
    head: str,
    replacement,
) -> dict[str, object]:
    attestation = (
        repository_root
        / "dist"
        / "linux"
        / RELEASE_LAYOUT
        / "build-attestation.json"
    )
    command = [
        EXPECTED_PYTHON,
        "-I",
        str(verifier),
        "--repository-root",
        str(repository_root),
        "--layout",
        RELEASE_LAYOUT,
        "--attestation",
        str(attestation),
        "--official-verifier",
        str(official_verifier),
        "--fcitx-pid",
        str(fcitx.pid),
        "--protocol-root",
        str(protocol_root),
        "--profile-root",
        str(profile_root),
        "--scope-kind",
        "absent",
    ]
    status, output, error = run_bounded_command(
        command,
        clean_command_environment(),
        COMMAND_TIMEOUT_SECONDS,
        "installed candidate verifier",
    )
    if (
        status != 0
        or error
        or not output.startswith("RESULT:")
        or not output.endswith("\n")
        or "\n" in output[:-1]
    ):
        fail("installed candidate verifier did not emit an exact success record")
    try:
        result = json.loads(output[len("RESULT:") : -1])
    except json.JSONDecodeError as parse_error:
        raise RuntimeError("installed candidate verifier result is invalid") from parse_error
    expected_keys = {
        "addonSha256",
        "attestationSha256",
        "consumerSha256",
        "fcitxPid",
        "fcitxStartTime",
        "gitHead",
        "layout",
        "fcitxProfileSha256",
        "profileMarkerSha256",
        "profileRootSha256",
        "protocolRootSha256",
        "result",
        "schemaVersion",
        "scope",
        "serverPid",
        "serverSha256",
        "serverStartTime",
    }
    if (
        not isinstance(result, dict)
        or set(result) != expected_keys
        or result.get("result") != "pass"
        or result.get("gitHead") != head
        or result.get("layout") != RELEASE_LAYOUT
        or result.get("fcitxPid") != fcitx.pid
        or result.get("fcitxStartTime") != fcitx.start_time
        or result.get("scope") is not None
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(result.get("consumerSha256", ""))
        )
        or result.get("profileRootSha256")
        != hashlib.sha256(str(profile_root).encode("utf-8")).hexdigest()
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(result.get("fcitxProfileSha256", ""))
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(result.get("profileMarkerSha256", ""))
        )
        or result.get("protocolRootSha256")
        != hashlib.sha256(str(protocol_root).encode("utf-8")).hexdigest()
        or result.get("serverPid") != replacement.pid
        or result.get("serverStartTime") != replacement.start_time
    ):
        fail("installed candidate verifier result identity mismatch")
    return result


def run_restart_iteration(
    iteration: int,
    pause_after: int,
    expected_result: str,
    probe: Path,
    injector: Path,
    environment: dict[str, str],
    stopper: ModuleType,
    candidate_verifier: Path,
    official_verifier: Path,
    repository_root: Path,
    fcitx: FcitxIdentity,
    protocol_root: Path,
    profile_root: Path,
    ibus_identity: IBusOwnerIdentity,
    runtime: Path,
    head: str,
) -> tuple[object, dict[str, object]]:
    process = subprocess.Popen(
        [EXPECTED_PYTHON, "-I", str(probe)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    capture: BoundedPipeCapture | None = None
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    try:
        capture = BoundedPipeCapture(process)
        expected_ready = (
            f"READY:partial_keys={pause_after} consumed={pause_after}/{pause_after} "
            f"ibus_owner={ibus_identity.owner} fcitx_pid={fcitx.pid} "
            f"fcitx_start_time={fcitx.start_time}"
        )
        ready = capture.next_stdout_line(
            min(deadline, time.monotonic() + 10), "restart probe readiness"
        )
        if ready != expected_ready:
            fail("restart probe readiness transcript was not exact")
        if ibus_owner_identity(fcitx, runtime) != ibus_identity:
            fail("IBus owner changed after restart probe readiness")
        killed = unique_installed_server(stopper)
        status, injected_output, injected_error = run_bounded_command(
            [EXPECTED_PYTHON, "-I", str(injector)],
            environment,
            min(5.0, remaining(deadline, "fault injection")),
            "fault injector",
        )
        if status != 0 or injected_error:
            fail("fault injector did not emit an exact success record")
        match = re.fullmatch(
            r"RESULT:exact_pidfd_sigkill pid=([1-9][0-9]*) "
            r"start_time=([1-9][0-9]*)\n",
            injected_output,
        )
        if (
            match is None
            or int(match.group(1)) != killed.pid
            or match.group(2) != killed.start_time
        ):
            fail("fault injector killed an unexpected server lifetime")
        if ibus_owner_identity(fcitx, runtime) != ibus_identity:
            fail("IBus owner changed during server fault injection")
        if process.stdin is None:
            fail("restart probe control pipe is unavailable")
        try:
            if os.write(process.stdin.fileno(), b"\n") != 1:
                fail("restart probe control write was incomplete")
        finally:
            process.stdin.close()
        capture.drain_to_eof(deadline, "restart probe result")
        if process.poll() is None:
            try:
                process.wait(timeout=remaining(deadline, "restart probe exit"))
            except subprocess.TimeoutExpired:
                fail("restart probe timed out")
        output, error = capture.decoded()
        if (
            process.returncode != 0
            or error
            or output != expected_ready + "\n" + expected_result + "\n"
        ):
            fail("restart probe exact result failed")
        replacement = wait_for_replacement(
            stopper,
            killed.pid,
            killed.start_time,
            time.monotonic() + 5,
        )
        candidate = verify_installed_candidate(
            candidate_verifier,
            official_verifier,
            repository_root,
            fcitx,
            protocol_root,
            profile_root,
            head,
            replacement,
        )
        if ibus_owner_identity(fcitx, runtime) != ibus_identity:
            fail("IBus owner changed during candidate verification")
        print(
            f"iteration={iteration} status=PASS "
            f"replacement_pid={replacement.pid} "
            f"replacement_start_time={replacement.start_time}"
        )
        return replacement, candidate
    finally:
        try:
            stop_process(process)
        finally:
            if capture is not None:
                capture.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def run_default_expectation(
    probe: Path,
    environment: dict[str, str],
    expected_result: str,
    fcitx: FcitxIdentity,
    ibus_identity: IBusOwnerIdentity,
    runtime: Path,
) -> None:
    if ibus_owner_identity(fcitx, runtime) != ibus_identity:
        fail("IBus owner changed before ordinary fallback")
    status, output, error = run_bounded_command(
        [EXPECTED_PYTHON, "-I", str(probe)],
        environment,
        PROBE_TIMEOUT_SECONDS,
        "ordinary fallback probe",
    )
    if status != 0 or error or output != expected_result + "\n":
        fail(
            "ordinary Mozkey fallback exact result failed "
            f"status={status} "
            f"stdout_chars={len(output)} "
            f"stdout_sha256={hashlib.sha256(output.encode()).hexdigest()} "
            f"stderr_chars={len(error)} "
            f"stderr_sha256={hashlib.sha256(error.encode()).hexdigest()}"
        )
    if ibus_owner_identity(fcitx, runtime) != ibus_identity:
        fail("IBus owner changed during ordinary fallback")


def load_fixture(path: Path) -> tuple[dict[str, object], str, str, str]:
    fixture = json.loads(read_regular_bounded(path).decode("utf-8", "strict"))
    if not isinstance(fixture, dict) or set(fixture) != {
        "schema_version",
        "reading",
        "custom_value",
        "default_value",
        "state",
        "project",
    } or fixture.get("schema_version") != 1:
        fail("tracked release fixture shape is invalid")
    reading = fixture.get("reading")
    custom = fixture.get("custom_value")
    default = fixture.get("default_value")
    project = fixture.get("project")
    if (
        not isinstance(reading, str)
        or re.fullmatch(r"[a-z]+", reading) is None
        or len(reading) > 128
        or not isinstance(custom, str)
        or not custom
        or not isinstance(default, str)
        or not default
        or custom == default
        or custom.strip() == reading
        or default.strip() == reading
        or not isinstance(project, dict)
        or not isinstance(project.get("entries"), list)
        or len(project["entries"]) != 1
        or project["entries"][0].get("surface") != custom
    ):
        fail("tracked release fixture expectations are invalid")
    return fixture, reading, custom, default


def stable_consumer_entries(
    consumers: Path, *, allow_empty: bool
) -> list[str]:
    temporary_pattern = re.compile(
        r"^\.fcitx5-mozkey-ibg\.[1-9][0-9]*\.[0-9]+\.tmp$"
    )
    accepted = ([], ["fcitx5-mozkey-ibg.json"]) if allow_empty else (
        ["fcitx5-mozkey-ibg.json"],
    )
    for _ in range(50):
        entries = sorted(path.name for path in consumers.iterdir())
        if entries in accepted:
            return entries
        temporary = [name for name in entries if temporary_pattern.fullmatch(name)]
        if (
            len(temporary) == 1
            and len(entries) in (1, 2)
            and all(
                name == "fcitx5-mozkey-ibg.json" or name in temporary
                for name in entries
            )
        ):
            time.sleep(0.01)
            continue
        fail("Protocol fixture consumers directory entries are invalid")
    fail("Protocol consumer heartbeat refresh did not settle")


def main() -> int:
    args = parse_args()
    if (
        Path(sys.executable).resolve(strict=True)
        != Path(EXPECTED_PYTHON).resolve(strict=True)
        or not sys.flags.isolated
    ):
        fail("restart gate requires exact isolated system Python")
    if os.getuid() == 0:
        fail("restart gate must run as the desktop user")
    if args.iterations < 1 or args.iterations > 20:
        fail("iterations must be between 1 and 20")
    if args.pause_after < 1:
        fail("pause-after must be positive")
    release_root = Path(__file__).resolve().parents[3]
    runner_path = Path(__file__).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        fail("expected HEAD must be a full commit ID")
    if (
        run_git(release_root, "rev-parse", "--show-toplevel") != str(release_root)
        or run_git(release_root, "rev-parse", "HEAD") != args.expected_head
        or run_git(release_root, "status", "--porcelain", "--untracked-files=no")
    ):
        fail("Mozkey dogfood checkout provenance is invalid")
    runner_blob = verify_tracked_file(release_root, runner_path, "100755")
    runtime_path = desktop_runtime_directory()
    if args.prepare_fresh_profile and args.launch_fresh_fcitx:
        fail("profile preparation and Fcitx launch modes are mutually exclusive")
    if args.prepare_fresh_profile:
        if args.protocol_root is not None:
            fail("profile preparation does not accept a Protocol root")
        evidence = prepare_fresh_profile(
            args.profile_root,
            runtime_path,
            args.expected_head,
            runner_blob,
        )
        print(
            "RESULT:fresh_profile_ready initial_mozkey_profile=absent "
            f"marker_sha256={evidence.marker_sha256} "
            f"fcitx_profile_sha256={evidence.fcitx_profile_sha256} "
            f"profile_root_sha256={hashlib.sha256(str(args.profile_root).encode('utf-8')).hexdigest()}"
        )
        return 0
    if args.launch_fresh_fcitx:
        if args.protocol_root is None:
            fail("--protocol-root is required to launch fresh Fcitx")
        fixture_path = runner_path.with_name("release_fixture.json")
        verify_tracked_file(release_root, fixture_path, "100644")
        fixture, _reading, _custom, _default = load_fixture(fixture_path)
        launch_fresh_fcitx(
            args.profile_root,
            args.protocol_root,
            runtime_path,
            args.expected_head,
            runner_blob,
            fixture,
        )
    if os.environ.get("MOZKEY_DOGFOOD_PROFILE_ROOT") != str(args.profile_root):
        fail("MOZKEY_DOGFOOD_PROFILE_ROOT does not match --profile-root")
    if args.protocol_root is None:
        fail("--protocol-root is required for the restart gate")

    directory = runner_path.parent
    source_paths = {
        "runner": runner_path,
        "probe": directory / "ibus_headless_probe.py",
        "injector": directory / "kill_installed_mozkey_server.py",
        "fixture": directory / "release_fixture.json",
        "stopper": release_root / "tools/release/stop_mozkey_linux_runtime.py",
        "candidate_verifier": directory / "verify_installed_candidate.py",
        "official_verifier": release_root / "tools/release/linux_build_attestation.py",
        "zenz_normalizer": release_root / "tools/release/normalize_zenz_gguf.py",
    }
    expected_modes = {
        "runner": "100755",
        "probe": "100755",
        "injector": "100755",
        "fixture": "100644",
        "stopper": "100755",
        "candidate_verifier": "100755",
        "official_verifier": "100755",
        "zenz_normalizer": "100644",
    }
    tracked_blobs = {
        name: verify_tracked_file(release_root, path, expected_modes[name])
        for name, path in source_paths.items()
    }
    if tracked_blobs["runner"] != runner_blob:
        fail("restart runner provenance changed during setup")

    uid = os.getuid()
    account = pwd.getpwuid(uid)
    runtime = str(runtime_path)
    expected_bus = f"unix:path={runtime_path}/bus"
    if (
        os.environ.get("XDG_RUNTIME_DIR") != runtime
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS") != expected_bus
    ):
        fail("desktop D-Bus environment is not canonical")
    base_environment = {
        "DBUS_SESSION_BUS_ADDRESS": expected_bus,
        "HOME": account.pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin",
        "USER": account.pw_name,
        "XDG_RUNTIME_DIR": runtime,
        "XDG_CONFIG_HOME": str(args.profile_root),
    }

    with tempfile.TemporaryDirectory(prefix="mozkey-restart-gate.") as raw_temp:
        temp_root = Path(raw_temp).resolve(strict=True)
        temp_root.chmod(0o700)
        snapshot_root = temp_root / "candidate-inputs"
        snapshot_tools = snapshot_root / "tools"
        snapshot_release = snapshot_tools / "release"
        snapshot_dogfood = snapshot_release / "linux_dogfood"
        for path in (snapshot_root, snapshot_tools, snapshot_release, snapshot_dogfood):
            path.mkdir(mode=0o700)
            path.chmod(0o700)

        snapshots = {
            "probe": snapshot_tracked_file(
                source_paths["probe"],
                snapshot_dogfood / "ibus_headless_probe.py",
                tracked_blobs["probe"],
                0o500,
            ),
            "injector": snapshot_tracked_file(
                source_paths["injector"],
                snapshot_dogfood / "kill_installed_mozkey_server.py",
                tracked_blobs["injector"],
                0o500,
            ),
            "fixture": snapshot_tracked_file(
                source_paths["fixture"],
                snapshot_dogfood / "release_fixture.json",
                tracked_blobs["fixture"],
                0o400,
            ),
            "stopper": snapshot_tracked_file(
                source_paths["stopper"],
                snapshot_release / "stop_mozkey_linux_runtime.py",
                tracked_blobs["stopper"],
                0o400,
            ),
            "candidate_verifier": snapshot_tracked_file(
                source_paths["candidate_verifier"],
                snapshot_dogfood / "verify_installed_candidate.py",
                tracked_blobs["candidate_verifier"],
                0o500,
            ),
            "official_verifier": snapshot_tracked_file(
                source_paths["official_verifier"],
                snapshot_release / "linux_build_attestation.py",
                tracked_blobs["official_verifier"],
                0o500,
            ),
            "zenz_normalizer": snapshot_tracked_file(
                source_paths["zenz_normalizer"],
                snapshot_release / "normalize_zenz_gguf.py",
                tracked_blobs["zenz_normalizer"],
                0o400,
            ),
        }
        fixture, reading, _custom, _default = load_fixture(snapshots["fixture"].path)
        if args.pause_after >= len(reading):
            fail("pause-after must be smaller than the reading")
        total_keys = len(reading) + 2
        profile_evidence = load_fresh_profile_evidence(
            args.profile_root,
            runtime_path,
            args.expected_head,
            tracked_blobs["runner"],
            require_unused=True,
        )
        project = fixture["project"]
        assert isinstance(project, dict)
        project_id = project["project_id"]
        assert isinstance(project_id, str)
        guard = ProtocolMutationGuard(
            (
                args.protocol_root,
                args.protocol_root / "projects",
                args.protocol_root / "state.json",
                args.protocol_root / "projects" / f"{project_id}.json",
            )
        )
        try:
            protocol_evidence = verify_protocol_root(args.protocol_root, fixture)
            guard.assert_clean()
            fcitx = installed_fcitx()
            if fcitx.scope is not None:
                fail("restart gate requires default Grimodex scope")
            if fcitx.protocol_root != str(args.protocol_root):
                fail("installed Fcitx Protocol root does not match the gate")
            if fcitx.profile_root != str(args.profile_root):
                fail("installed Fcitx profile root does not match the fresh gate root")
            if fcitx.home_root != str(args.profile_root):
                fail("installed Fcitx HOME does not match the fresh gate root")
            verify_fresh_profile_launch(profile_evidence, fcitx.start_time)
            ibus_identity = ibus_owner_identity(fcitx, runtime_path)
            stopper = load_stopper(snapshots["stopper"].path)
            require_no_installed_server(stopper)
            identity_suffix = (
                f" ibus_owner={ibus_identity.owner} fcitx_pid={fcitx.pid} "
                f"fcitx_start_time={fcitx.start_time}"
            )
            custom_result = (
                "RESULT:match=true expectation=custom commit_count=1 "
                f"consumed={total_keys}/{total_keys} forwarded=0"
                + identity_suffix
            )
            default_result = (
                "RESULT:match=true expectation=default commit_count=1 "
                f"consumed={total_keys}/{total_keys} forwarded=0"
                + identity_suffix
            )
            default_environment = dict(base_environment)
            default_environment.update(
                {
                    "MOZKEY_DOGFOOD_EXPECTATION": "default",
                    "MOZKEY_DOGFOOD_EXPECTED_FCITX_PID": str(fcitx.pid),
                    "MOZKEY_DOGFOOD_EXPECTED_FCITX_START_TIME": fcitx.start_time,
                    "MOZKEY_DOGFOOD_EXPECTED_IBUS_OWNER": ibus_identity.owner,
                }
            )
            run_default_expectation(
                snapshots["probe"].path,
                default_environment,
                default_result,
                fcitx,
                ibus_identity,
                runtime_path,
            )
            baseline_server = unique_installed_server(stopper)
            candidate_evidence = verify_installed_candidate(
                snapshots["candidate_verifier"].path,
                snapshots["official_verifier"].path,
                release_root,
                fcitx,
                args.protocol_root,
                args.profile_root,
                args.expected_head,
                baseline_server,
            )
            latest_consumer_sha256 = candidate_evidence["consumerSha256"]
            if (
                candidate_evidence.get("profileMarkerSha256")
                != profile_evidence.marker_sha256
                or candidate_evidence.get("profileRootSha256")
                != hashlib.sha256(str(args.profile_root).encode("utf-8")).hexdigest()
                or candidate_evidence.get("fcitxProfileSha256")
                != profile_evidence.fcitx_profile_sha256
            ):
                fail("candidate verifier did not bind the fresh profile evidence")
            if installed_fcitx() != fcitx:
                fail("installed Fcitx identity changed during fresh fallback gate")
            if ibus_owner_identity(fcitx, runtime_path) != ibus_identity:
                fail("IBus owner changed during fresh fallback gate")
            verify_fresh_profile_evidence(
                profile_evidence,
                args.profile_root,
                runtime_path,
                args.expected_head,
                tracked_blobs["runner"],
            )
            if verify_protocol_root(args.protocol_root, fixture) != protocol_evidence:
                fail("Protocol fixture changed during fresh fallback gate")
            guard.assert_clean()
            for snapshot in snapshots.values():
                verify_snapshot(snapshot)

            custom_environment = dict(base_environment)
            custom_environment.update(
                {
                    "MOZKEY_DOGFOOD_EXPECTED_FCITX_PID": str(fcitx.pid),
                    "MOZKEY_DOGFOOD_EXPECTED_FCITX_START_TIME": fcitx.start_time,
                    "MOZKEY_DOGFOOD_EXPECTED_IBUS_OWNER": ibus_identity.owner,
                    "MOZKEY_DOGFOOD_RELEASE_ROOT": str(snapshot_root),
                    "MOZKEY_DOGFOOD_PAUSE_AFTER": str(args.pause_after),
                    "MOZKEY_DOGFOOD_EXPECTATION": "custom",
                }
            )
            last_replacement = None
            for iteration in range(1, args.iterations + 1):
                if ibus_owner_identity(fcitx, runtime_path) != ibus_identity:
                    fail("IBus owner changed before restart iteration")
                verify_fresh_profile_evidence(
                    profile_evidence,
                    args.profile_root,
                    runtime_path,
                    args.expected_head,
                    tracked_blobs["runner"],
                )
                last_replacement, current_candidate = run_restart_iteration(
                    iteration,
                    args.pause_after,
                    custom_result,
                    snapshots["probe"].path,
                    snapshots["injector"].path,
                    custom_environment,
                    stopper,
                    snapshots["candidate_verifier"].path,
                    snapshots["official_verifier"].path,
                    release_root,
                    fcitx,
                    args.protocol_root,
                    args.profile_root,
                    ibus_identity,
                    runtime_path,
                    args.expected_head,
                )
                for key in (
                    "addonSha256",
                    "attestationSha256",
                    "fcitxPid",
                    "fcitxStartTime",
                    "gitHead",
                    "layout",
                    "fcitxProfileSha256",
                    "profileMarkerSha256",
                    "profileRootSha256",
                    "protocolRootSha256",
                    "schemaVersion",
                    "scope",
                    "serverSha256",
                ):
                    if current_candidate.get(key) != candidate_evidence.get(key):
                        fail("candidate provenance changed between restart iterations")
                latest_consumer_sha256 = current_candidate["consumerSha256"]
                if installed_fcitx() != fcitx:
                    fail("installed Fcitx identity changed during restart gate")
                if ibus_owner_identity(fcitx, runtime_path) != ibus_identity:
                    fail("IBus owner changed after restart iteration")
                verify_fresh_profile_evidence(
                    profile_evidence,
                    args.profile_root,
                    runtime_path,
                    args.expected_head,
                    tracked_blobs["runner"],
                )
                if verify_protocol_root(args.protocol_root, fixture) != protocol_evidence:
                    fail("Protocol fixture changed during restart gate")
                guard.assert_clean()
                for snapshot in snapshots.values():
                    verify_snapshot(snapshot)

            if last_replacement is None:
                fail("custom restart evidence was not produced")
            current_server = unique_installed_server(stopper)
            if current_server != last_replacement:
                fail("final custom restart lifetime changed unexpectedly")
            if installed_fcitx() != fcitx:
                fail("installed Fcitx identity changed after custom restart gate")
            if ibus_owner_identity(fcitx, runtime_path) != ibus_identity:
                fail("IBus owner changed after custom restart gate")
            verify_fresh_profile_evidence(
                profile_evidence,
                args.profile_root,
                runtime_path,
                args.expected_head,
                tracked_blobs["runner"],
            )
            if verify_protocol_root(args.protocol_root, fixture) != protocol_evidence:
                fail("Protocol fixture changed after custom restart gate")
            guard.assert_clean()
            for snapshot in snapshots.values():
                verify_snapshot(snapshot)
        finally:
            try:
                guard.assert_clean()
            finally:
                guard.close()

    if (
        run_git(release_root, "rev-parse", "HEAD") != args.expected_head
        or run_git(release_root, "status", "--porcelain", "--untracked-files=no")
    ):
        fail("Mozkey checkout changed during restart gate")
    final_blobs = {
        name: verify_tracked_file(release_root, path, expected_modes[name])
        for name, path in source_paths.items()
    }
    if final_blobs != tracked_blobs:
        fail("tracked restart gate inputs changed during execution")
    assert candidate_evidence is not None
    assert latest_consumer_sha256 is not None
    if installed_fcitx() != fcitx:
        fail("installed Fcitx identity changed before final restart evidence")
    if ibus_owner_identity(fcitx, runtime_path) != ibus_identity:
        fail("IBus owner changed before final restart evidence")
    verify_fresh_profile_evidence(
        profile_evidence,
        args.profile_root,
        runtime_path,
        args.expected_head,
        tracked_blobs["runner"],
    )
    print(
        f"RESULT:server_restart_exact_pass iterations={args.iterations} "
        "fallback=default_exact fallback_order=fresh_before_custom "
        f"mozkey_head={args.expected_head} fcitx_pid={fcitx.pid} "
        f"ibus_owner={ibus_identity.owner} initial_mozkey_profile=absent "
        f"profile_marker_sha256={profile_evidence.marker_sha256} "
        f"fcitx_profile_sha256={profile_evidence.fcitx_profile_sha256} "
        f"consumer_sha256={latest_consumer_sha256} "
        f"profile_root_sha256={hashlib.sha256(str(args.profile_root).encode('utf-8')).hexdigest()} "
        f"attestation_sha256={candidate_evidence['attestationSha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
