#!/usr/bin/python3 -I
"""Run the release-grade protected-surface probe against one fresh Fcitx."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator, Mapping, NoReturn, Sequence


EXPECTED_PYTHON = "/usr/bin/python3"
RELEASE_LAYOUT = "archlinux-x86_64"
PROBE_TIMEOUT_SECONDS = 45.0
BOOTSTRAP_MAX_BYTES = 4 << 20
BOOTSTRAP_GIT_OUTPUT_BYTES = 4096
EXPECTED_LITERAL = "RUST_LOG=debug"
EXPECTED_ROMANIZED_SUFFIX = "dekuwashiiloguwodasu"
EXPECTED_KANA_SUFFIX = "でくわしいろぐをだす"
EXPECTED_RAW_PREEDIT = EXPECTED_LITERAL + EXPECTED_KANA_SUFFIX
EXPECTED_CONVERTED_VALUE = "RUST_LOG=debugで詳しいログを出す"


@dataclass(frozen=True)
class BootstrapSnapshot:
    path: Path
    blob: str
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class SnapshotDirectory:
    path: Path
    mode: int
    device: int
    inode: int
    entries: tuple[str, ...]


class InotifyMutationGuard:
    """Record any mutation event, including a later-restored mutation."""

    _MUTATION_MASK = (
        0x00000002  # IN_MODIFY
        | 0x00000004  # IN_ATTRIB
        | 0x00000008  # IN_CLOSE_WRITE
        | 0x00000040  # IN_MOVED_FROM
        | 0x00000080  # IN_MOVED_TO
        | 0x00000100  # IN_CREATE
        | 0x00000200  # IN_DELETE
        | 0x00000400  # IN_DELETE_SELF
        | 0x00000800  # IN_MOVE_SELF
        | 0x00002000  # IN_UNMOUNT
        | 0x00004000  # IN_Q_OVERFLOW
        | 0x00008000  # IN_IGNORED
    )

    def __init__(self, paths: Sequence[Path]) -> None:
        if not paths or len(set(paths)) != len(paths):
            fail("snapshot mutation watch paths are empty or duplicated")
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
        self._descriptor: int | None = descriptor
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
                fail("snapshot mutation watches are not one-to-one")
        except BaseException:
            os.close(descriptor)
            self._descriptor = None
            raise

    def assert_clean(self) -> None:
        if self._descriptor is None:
            fail("snapshot mutation guard is already closed")
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
            fail("private snapshot tree was mutated during the product gate")

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


class SnapshotMutationGuard:
    """Combine inotify history with current snapshot identity verification."""

    def __init__(
        self, paths: Sequence[Path], verifier: Callable[[], None]
    ) -> None:
        self._verifier = verifier
        self._events = InotifyMutationGuard(paths)
        try:
            self.assert_clean()
        except BaseException:
            self._events.close()
            raise

    def assert_clean(self) -> None:
        self._events.assert_clean()
        self._verifier()
        self._events.assert_clean()

    @contextmanager
    def checked_execution(self) -> Iterator[None]:
        self.assert_clean()
        try:
            yield
        finally:
            self.assert_clean()

    def close(self) -> None:
        self._events.close()


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        fail(f"could not load tracked module: {path.name}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def bootstrap_git(root: Path, *arguments: str) -> str:
    """Run one bounded Git provenance query without imported gate code."""
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "HOME": "/var/empty",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("restart-runner bootstrap Git query failed") from error
    if (
        result.returncode != 0
        or result.stderr
        or len(result.stdout) > BOOTSTRAP_GIT_OUTPUT_BYTES
    ):
        fail("restart-runner bootstrap Git query was not exact and bounded")
    try:
        return result.stdout.decode("utf-8", "strict").strip()
    except UnicodeError as error:
        raise RuntimeError("restart-runner bootstrap Git output is invalid") from error


def bootstrap_read_regular(path: Path, expected_mode: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            fail("restart-runner bootstrap input identity is invalid")
        payload = bytearray()
        while True:
            chunk = os.read(
                descriptor,
                min(1 << 16, BOOTSTRAP_MAX_BYTES + 1 - len(payload)),
            )
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
            if len(payload) > BOOTSTRAP_MAX_BYTES:
                fail("restart-runner bootstrap input exceeds its cap")
    finally:
        os.close(descriptor)


def bootstrap_git_blob_id(payload: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(payload)}\0".encode("ascii"))
    digest.update(payload)
    return digest.hexdigest()


def bootstrap_verify_restart_runner(
    root: Path, path: Path, expected_head: str
) -> str:
    """Verify the support module against HEAD, index, and worktree."""
    if path.resolve(strict=True) != path:
        fail("restart runner is not a canonical regular file")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise RuntimeError("restart runner is outside the checkout") from error
    if (
        bootstrap_git(root, "rev-parse", "--show-toplevel") != str(root)
        or bootstrap_git(root, "rev-parse", "HEAD") != expected_head
    ):
        fail("restart-runner bootstrap checkout provenance is invalid")
    index = bootstrap_git(
        root, "ls-files", "--stage", "--", str(relative)
    ).split(maxsplit=3)
    head = bootstrap_git(root, "ls-tree", "HEAD", "--", str(relative)).split(
        maxsplit=3
    )
    if (
        len(index) != 4
        or index[0] != "100755"
        or index[2] != "0"
        or index[3] != str(relative)
        or re.fullmatch(r"[0-9a-f]{40}", index[1]) is None
        or len(head) != 4
        or head[0] != "100755"
        or head[1] != "blob"
        or head[2] != index[1]
        or head[3] != str(relative)
    ):
        fail("restart runner is not the exact executable HEAD/index blob")
    payload = bootstrap_read_regular(path, 0o755)
    if bootstrap_git_blob_id(payload) != index[1]:
        fail("restart runner worktree blob differs from HEAD/index")
    return index[1]


def bootstrap_verify_snapshot(snapshot: BootstrapSnapshot) -> None:
    metadata = snapshot.path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != snapshot.mode
        or metadata.st_dev != snapshot.device
        or metadata.st_ino != snapshot.inode
        or snapshot.path.resolve(strict=True) != snapshot.path
        or bootstrap_git_blob_id(
            bootstrap_read_regular(snapshot.path, snapshot.mode)
        )
        != snapshot.blob
    ):
        fail("private restart-runner bootstrap snapshot changed")


def bootstrap_snapshot_restart_runner(
    source: Path, destination: Path, expected_blob: str
) -> BootstrapSnapshot:
    payload = bootstrap_read_regular(source, 0o755)
    if bootstrap_git_blob_id(payload) != expected_blob:
        fail("restart runner changed before its bootstrap snapshot")
    descriptor = os.open(
        destination,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | os.O_NOFOLLOW,
        0o500,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("could not complete restart-runner bootstrap snapshot")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o500)
    finally:
        os.close(descriptor)
    metadata = destination.lstat()
    snapshot = BootstrapSnapshot(
        path=destination,
        blob=expected_blob,
        mode=0o500,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )
    bootstrap_verify_snapshot(snapshot)
    return snapshot


def capture_snapshot_directory(
    path: Path, expected_entries: Sequence[str], expected_mode: int
) -> SnapshotDirectory:
    before = path.lstat()
    entries = tuple(sorted(entry.name for entry in path.iterdir()))
    after = path.lstat()
    expected = tuple(sorted(expected_entries))
    if (
        path.is_symlink()
        or path.resolve(strict=True) != path
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_gid != os.getgid()
        or stat.S_IMODE(before.st_mode) != expected_mode
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or entries != expected
    ):
        fail("private snapshot directory identity or entries are invalid")
    return SnapshotDirectory(
        path=path,
        mode=expected_mode,
        device=before.st_dev,
        inode=before.st_ino,
        entries=expected,
    )


def verify_snapshot_directories(
    directories: Sequence[SnapshotDirectory],
) -> None:
    for evidence in directories:
        before = evidence.path.lstat()
        entries = tuple(sorted(entry.name for entry in evidence.path.iterdir()))
        after = evidence.path.lstat()
        if (
            evidence.path.is_symlink()
            or evidence.path.resolve(strict=True) != evidence.path
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_gid != os.getgid()
            or stat.S_IMODE(before.st_mode) != evidence.mode
            or before.st_dev != evidence.device
            or before.st_ino != evidence.inode
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or entries != evidence.entries
        ):
            fail("private snapshot directory changed during the product gate")


def verify_tracked_snapshot_tree(
    restart_gate: ModuleType,
    directories: Sequence[SnapshotDirectory],
    snapshots: Mapping[str, object],
) -> None:
    verify_snapshot_directories(directories)
    for name in sorted(snapshots):
        restart_gate.verify_snapshot(snapshots[name])
    verify_snapshot_directories(directories)


def snapshot_watch_paths(
    directories: Sequence[SnapshotDirectory], snapshots: Mapping[str, object]
) -> tuple[Path, ...]:
    return tuple(evidence.path for evidence in directories) + tuple(
        snapshots[name].path for name in sorted(snapshots)
    )


def verify_bootstrap_snapshot_tree(
    directory: SnapshotDirectory, snapshot: BootstrapSnapshot
) -> None:
    verify_snapshot_directories((directory,))
    bootstrap_verify_snapshot(snapshot)
    verify_snapshot_directories((directory,))


def restore_cleanup_directory_modes(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | os.O_NOFOLLOW,
            )
        except FileNotFoundError:
            continue
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)


def load_fixture(path: Path) -> dict[str, object]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            fail("protected-surface fixture is not a regular file")
        payload = os.read(descriptor, 4097)
        if len(payload) > 4096 or os.read(descriptor, 1):
            fail("protected-surface fixture exceeds its cap")
    finally:
        os.close(descriptor)
    try:
        document = json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("protected-surface fixture is invalid") from error
    expected: dict[str, object] = {
        "schema_version": 1,
        "literal": EXPECTED_LITERAL,
        "romanized_suffix": EXPECTED_ROMANIZED_SUFFIX,
        "kana_suffix": EXPECTED_KANA_SUFFIX,
        "raw_preedit": EXPECTED_RAW_PREEDIT,
        "converted_value": EXPECTED_CONVERTED_VALUE,
    }
    if document != expected:
        fail("protected-surface fixture is not the exact product case")
    return document


def expected_probe_result(
    fixture: dict[str, object], ibus_identity: object, fcitx: object
) -> str:
    literal = fixture["literal"]
    converted = fixture["converted_value"]
    assert isinstance(literal, str)
    assert isinstance(converted, str)
    literal_sha256 = hashlib.sha256(literal.encode("utf-8")).hexdigest()
    raw = fixture["raw_preedit"]
    assert isinstance(raw, str)
    raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    converted_sha256 = hashlib.sha256(converted.encode("utf-8")).hexdigest()
    return (
        "RESULT:protected_surface_exact_pass contexts=1 "
        "literal_bytes=preserved adjacent_kana=converted "
        f"cursor_boundary={len(literal)} "
        "resize_roundtrip=exact commit_count=2 deletion_count=1 "
        "reconversion=exact "
        f"literal_sha256={literal_sha256} "
        f"raw_sha256={raw_sha256} "
        f"converted_sha256={converted_sha256} "
        f"ibus_owner={ibus_identity.owner} fcitx_pid={fcitx.pid} "
        f"fcitx_start_time={fcitx.start_time}"
    )


def run_verified_gate(
    args: argparse.Namespace,
    release_root: Path,
    directory: Path,
    restart_runner_path: Path,
    restart_gate: ModuleType,
    restart_runner_blob: str,
    restart_snapshot: BootstrapSnapshot,
) -> str:
    if (
        restart_gate.run_git(release_root, "rev-parse", "--show-toplevel")
        != str(release_root)
        or restart_gate.run_git(release_root, "rev-parse", "HEAD")
        != args.expected_head
        or restart_gate.run_git(
            release_root, "status", "--porcelain", "--untracked-files=no"
        )
    ):
        fail("Mozkey protected-surface checkout provenance is invalid")

    source_paths = {
        "runner": Path(__file__).resolve(),
        "restart_runner": restart_runner_path,
        "probe": directory / "protected_surface_headless_probe.py",
        "protected_fixture": directory / "protected_surface_fixture.json",
        "release_fixture": directory / "release_fixture.json",
        "stopper": release_root / "tools/release/stop_mozkey_linux_runtime.py",
        "candidate_verifier": directory / "verify_installed_candidate.py",
        "official_verifier": release_root / "tools/release/linux_build_attestation.py",
        "zenz_normalizer": release_root / "tools/release/normalize_zenz_gguf.py",
    }
    expected_modes = {
        "runner": "100755",
        "restart_runner": "100755",
        "probe": "100755",
        "protected_fixture": "100644",
        "release_fixture": "100644",
        "stopper": "100755",
        "candidate_verifier": "100755",
        "official_verifier": "100755",
        "zenz_normalizer": "100644",
    }
    tracked_blobs = {
        name: restart_gate.verify_tracked_file(
            release_root, path, expected_modes[name]
        )
        for name, path in source_paths.items()
    }
    if tracked_blobs["restart_runner"] != restart_runner_blob:
        fail("imported restart support differs from bootstrap provenance")
    runtime_path = restart_gate.desktop_runtime_directory()
    expected_bus = f"unix:path={runtime_path}/bus"
    if (
        os.environ.get("XDG_RUNTIME_DIR") != str(runtime_path)
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS") != expected_bus
        or os.environ.get("MOZKEY_DOGFOOD_PROFILE_ROOT") != str(args.profile_root)
    ):
        fail("protected-surface desktop environment is not canonical")

    account = pwd.getpwuid(os.getuid())
    probe_environment = {
        "DBUS_SESSION_BUS_ADDRESS": expected_bus,
        "HOME": str(args.profile_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin",
        "USER": account.pw_name,
        "XDG_CONFIG_HOME": str(args.profile_root),
        "XDG_RUNTIME_DIR": str(runtime_path),
    }

    candidate_evidence: dict[str, object] | None = None
    profile_evidence = None
    fcitx = None
    ibus_identity = None
    protocol_evidence = None
    stopper = None
    server = None
    with tempfile.TemporaryDirectory(
        prefix="mozkey-protected-surface-gate."
    ) as raw_temp:
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
            "probe": restart_gate.snapshot_tracked_file(
                source_paths["probe"],
                snapshot_dogfood / "protected_surface_headless_probe.py",
                tracked_blobs["probe"],
                0o500,
            ),
            "protected_fixture": restart_gate.snapshot_tracked_file(
                source_paths["protected_fixture"],
                snapshot_dogfood / "protected_surface_fixture.json",
                tracked_blobs["protected_fixture"],
                0o400,
            ),
            "release_fixture": restart_gate.snapshot_tracked_file(
                source_paths["release_fixture"],
                snapshot_dogfood / "release_fixture.json",
                tracked_blobs["release_fixture"],
                0o400,
            ),
            "stopper": restart_gate.snapshot_tracked_file(
                source_paths["stopper"],
                snapshot_release / "stop_mozkey_linux_runtime.py",
                tracked_blobs["stopper"],
                0o400,
            ),
            "candidate_verifier": restart_gate.snapshot_tracked_file(
                source_paths["candidate_verifier"],
                snapshot_dogfood / "verify_installed_candidate.py",
                tracked_blobs["candidate_verifier"],
                0o500,
            ),
            "official_verifier": restart_gate.snapshot_tracked_file(
                source_paths["official_verifier"],
                snapshot_release / "linux_build_attestation.py",
                tracked_blobs["official_verifier"],
                0o500,
            ),
            "zenz_normalizer": restart_gate.snapshot_tracked_file(
                source_paths["zenz_normalizer"],
                snapshot_release / "normalize_zenz_gguf.py",
                tracked_blobs["zenz_normalizer"],
                0o400,
            ),
        }
        snapshot_directory_paths = (
            snapshot_root,
            snapshot_tools,
            snapshot_release,
            snapshot_dogfood,
        )
        snapshot_guard: SnapshotMutationGuard | None = None
        try:
            for path in reversed(snapshot_directory_paths):
                path.chmod(0o500)
            snapshot_directories = (
                capture_snapshot_directory(snapshot_root, ("tools",), 0o500),
                capture_snapshot_directory(snapshot_tools, ("release",), 0o500),
                capture_snapshot_directory(
                    snapshot_release,
                    (
                        "linux_build_attestation.py",
                        "linux_dogfood",
                        "normalize_zenz_gguf.py",
                        "stop_mozkey_linux_runtime.py",
                    ),
                    0o500,
                ),
                capture_snapshot_directory(
                    snapshot_dogfood,
                    (
                        "protected_surface_fixture.json",
                        "protected_surface_headless_probe.py",
                        "release_fixture.json",
                        "verify_installed_candidate.py",
                    ),
                    0o500,
                ),
            )
            snapshot_guard = SnapshotMutationGuard(
                snapshot_watch_paths(snapshot_directories, snapshots),
                lambda: verify_tracked_snapshot_tree(
                    restart_gate, snapshot_directories, snapshots
                ),
            )
            with snapshot_guard.checked_execution():
                protected_fixture = load_fixture(
                    snapshots["protected_fixture"].path
                )
                release_fixture, _reading, _custom, _default = (
                    restart_gate.load_fixture(snapshots["release_fixture"].path)
                )
            profile_evidence = restart_gate.load_fresh_profile_evidence(
                args.profile_root,
                runtime_path,
                args.expected_head,
                tracked_blobs["restart_runner"],
                require_unused=True,
            )
            protocol_evidence = restart_gate.verify_protocol_root(
                args.protocol_root, release_fixture
            )
            project = release_fixture["project"]
            assert isinstance(project, dict)
            project_id = project["project_id"]
            assert isinstance(project_id, str)
            protocol_guard = restart_gate.ProtocolMutationGuard(
                (
                    args.protocol_root,
                    args.protocol_root / "projects",
                    args.protocol_root / "state.json",
                    args.protocol_root / "projects" / f"{project_id}.json",
                )
            )
            try:
                fcitx = restart_gate.installed_fcitx()
                if (
                    fcitx.scope is not None
                    or fcitx.protocol_root != str(args.protocol_root)
                    or fcitx.profile_root != str(args.profile_root)
                    or fcitx.home_root != str(args.profile_root)
                ):
                    fail(
                        "installed Fcitx is not the exact fresh default-scope "
                        "lifetime"
                    )
                restart_gate.verify_fresh_profile_launch(
                    profile_evidence, fcitx.start_time
                )
                ibus_identity = restart_gate.ibus_owner_identity(
                    fcitx, runtime_path
                )
                with snapshot_guard.checked_execution():
                    stopper = restart_gate.load_stopper(
                        snapshots["stopper"].path
                    )
                    restart_gate.require_no_installed_server(stopper)

                expected_result = expected_probe_result(
                    protected_fixture, ibus_identity, fcitx
                )
                probe_environment.update(
                    {
                        "MOZKEY_DOGFOOD_EXPECTED_FCITX_PID": str(fcitx.pid),
                        "MOZKEY_DOGFOOD_EXPECTED_FCITX_START_TIME": (
                            fcitx.start_time
                        ),
                        "MOZKEY_DOGFOOD_EXPECTED_IBUS_OWNER": ibus_identity.owner,
                    }
                )
                with snapshot_guard.checked_execution():
                    status, output, error = restart_gate.run_bounded_command(
                        [EXPECTED_PYTHON, "-I", str(snapshots["probe"].path)],
                        probe_environment,
                        PROBE_TIMEOUT_SECONDS,
                        "protected-surface headless probe",
                    )
                if status != 0 or error or output != expected_result + "\n":
                    fail(
                        "protected-surface exact probe failed "
                        f"status={status} "
                        f"stdout_chars={len(output)} "
                        "stdout_sha256="
                        f"{hashlib.sha256(output.encode()).hexdigest()} "
                        f"stderr_chars={len(error)} "
                        "stderr_sha256="
                        f"{hashlib.sha256(error.encode()).hexdigest()}"
                    )
                with snapshot_guard.checked_execution():
                    server = restart_gate.unique_installed_server(stopper)
                    candidate_evidence = restart_gate.verify_installed_candidate(
                        snapshots["candidate_verifier"].path,
                        snapshots["official_verifier"].path,
                        release_root,
                        fcitx,
                        args.protocol_root,
                        args.profile_root,
                        args.expected_head,
                        server,
                    )
                if restart_gate.installed_fcitx() != fcitx:
                    fail(
                        "installed Fcitx identity changed during "
                        "protected-surface gate"
                    )
                if (
                    restart_gate.ibus_owner_identity(fcitx, runtime_path)
                    != ibus_identity
                ):
                    fail("IBus owner changed during protected-surface gate")
                restart_gate.verify_fresh_profile_evidence(
                    profile_evidence,
                    args.profile_root,
                    runtime_path,
                    args.expected_head,
                    tracked_blobs["restart_runner"],
                )
                if (
                    restart_gate.verify_protocol_root(
                        args.protocol_root, release_fixture
                    )
                    != protocol_evidence
                ):
                    fail("Protocol fixture changed during protected-surface gate")
                protocol_guard.assert_clean()
                snapshot_guard.assert_clean()
            finally:
                try:
                    protocol_guard.assert_clean()
                finally:
                    protocol_guard.close()

            if (
                restart_gate.run_git(release_root, "rev-parse", "HEAD")
                != args.expected_head
                or restart_gate.run_git(
                    release_root,
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                )
            ):
                fail("Mozkey checkout changed during protected-surface gate")
            final_blobs = {
                name: restart_gate.verify_tracked_file(
                    release_root, path, expected_modes[name]
                )
                for name, path in source_paths.items()
            }
            if final_blobs != tracked_blobs:
                fail(
                    "tracked protected-surface gate inputs changed during execution"
                )
            assert candidate_evidence is not None
            assert profile_evidence is not None
            assert fcitx is not None
            assert ibus_identity is not None
            assert protocol_evidence is not None
            assert stopper is not None
            assert server is not None
            with snapshot_guard.checked_execution():
                if restart_gate.unique_installed_server(stopper) != server:
                    fail(
                        "installed server identity changed before final protected "
                        "evidence"
                    )
            if restart_gate.installed_fcitx() != fcitx:
                fail("installed Fcitx identity changed before final evidence")
            if (
                restart_gate.ibus_owner_identity(fcitx, runtime_path)
                != ibus_identity
            ):
                fail("IBus owner changed before final protected evidence")
            restart_gate.verify_fresh_profile_evidence(
                profile_evidence,
                args.profile_root,
                runtime_path,
                args.expected_head,
                tracked_blobs["restart_runner"],
            )
            if (
                bootstrap_verify_restart_runner(
                    release_root, restart_runner_path, args.expected_head
                )
                != restart_runner_blob
            ):
                fail("restart runner changed before final protected evidence")
            bootstrap_verify_snapshot(restart_snapshot)
            snapshot_guard.assert_clean()
            result_line = (
                "RESULT:protected_surface_product_gate_pass "
                f"mozkey_head={args.expected_head} contexts=1 fresh_fcitx=exact "
                "literal_bytes=preserved adjacent_kana=converted cursor=exact "
                "resize_commit=exact reconversion=exact "
                f"fcitx_pid={fcitx.pid} fcitx_start_time={fcitx.start_time} "
                f"ibus_owner={ibus_identity.owner} "
                f"profile_marker_sha256={profile_evidence.marker_sha256} "
                f"fcitx_profile_sha256={profile_evidence.fcitx_profile_sha256} "
                "protocol_root_sha256="
                f"{candidate_evidence['protocolRootSha256']} "
                f"consumer_sha256={candidate_evidence['consumerSha256']} "
                f"attestation_sha256={candidate_evidence['attestationSha256']} "
                f"gate_blob={tracked_blobs['runner']} "
                f"probe_blob={tracked_blobs['probe']} "
                f"fixture_blob={tracked_blobs['protected_fixture']}"
            )
        finally:
            try:
                if snapshot_guard is not None:
                    snapshot_guard.assert_clean()
            finally:
                if snapshot_guard is not None:
                    snapshot_guard.close()
                restore_cleanup_directory_modes(snapshot_directory_paths)
    return result_line


def main() -> int:
    args = parse_args()
    if (
        Path(sys.executable).resolve(strict=True)
        != Path(EXPECTED_PYTHON).resolve(strict=True)
        or not sys.flags.isolated
    ):
        fail("protected-surface gate requires exact isolated system Python")
    if os.getuid() == 0:
        fail("protected-surface gate must run as the desktop user")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        fail("expected HEAD must be a full commit ID")

    invoked_runner = Path(__file__).absolute()
    if invoked_runner.is_symlink():
        fail("protected-surface gate must not be invoked through a symlink")
    runner_path = invoked_runner.resolve(strict=True)
    release_root = runner_path.parents[3]
    directory = runner_path.parent
    restart_runner_path = directory / "run_server_restart_gate.py"
    with tempfile.TemporaryDirectory(
        prefix="mozkey-protected-surface-bootstrap."
    ) as raw_bootstrap:
        bootstrap_root = Path(raw_bootstrap).resolve(strict=True)
        bootstrap_root.chmod(0o700)
        restart_runner_blob = bootstrap_verify_restart_runner(
            release_root, restart_runner_path, args.expected_head
        )
        restart_snapshot = bootstrap_snapshot_restart_runner(
            restart_runner_path,
            bootstrap_root / "verified_restart_support.py",
            restart_runner_blob,
        )
        if (
            bootstrap_verify_restart_runner(
                release_root, restart_runner_path, args.expected_head
            )
            != restart_runner_blob
        ):
            fail("restart runner changed before verified snapshot import")
        bootstrap_verify_snapshot(restart_snapshot)
        bootstrap_guard: SnapshotMutationGuard | None = None
        try:
            bootstrap_root.chmod(0o500)
            bootstrap_directory = capture_snapshot_directory(
                bootstrap_root, (restart_snapshot.path.name,), 0o500
            )
            bootstrap_guard = SnapshotMutationGuard(
                (bootstrap_root, restart_snapshot.path),
                lambda: verify_bootstrap_snapshot_tree(
                    bootstrap_directory, restart_snapshot
                ),
            )
            with bootstrap_guard.checked_execution():
                restart_gate = load_module(
                    "mozkey_protected_surface_restart_support",
                    restart_snapshot.path,
                )
                if (
                    bootstrap_verify_restart_runner(
                        release_root, restart_runner_path, args.expected_head
                    )
                    != restart_runner_blob
                ):
                    fail("restart runner changed during verified snapshot import")
                bootstrap_verify_snapshot(restart_snapshot)
            with bootstrap_guard.checked_execution():
                result_line = run_verified_gate(
                    args,
                    release_root,
                    directory,
                    restart_runner_path,
                    restart_gate,
                    restart_runner_blob,
                    restart_snapshot,
                )
            bootstrap_guard.assert_clean()
        finally:
            try:
                if bootstrap_guard is not None:
                    bootstrap_guard.assert_clean()
            finally:
                if bootstrap_guard is not None:
                    bootstrap_guard.close()
                restore_cleanup_directory_modes((bootstrap_root,))
    print(result_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
