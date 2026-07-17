#!/usr/bin/python3 -I
"""Run a fail-closed GTK/Qt installed-product scope gate.

The gate deliberately builds the adjacent, tracked probe source itself.  It
then verifies the live probe and the one installed Fcitx process by exact
``/proc`` identity before sending input, and verifies that the Fcitx identity
and scope environment did not change before accepting the result.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Sequence


EXPECTED_FCITX = Path("/usr/bin/fcitx5")
EXPECTED_PYTHON = Path("/usr/bin/python3")
EXPECTED_COMPILERS = {"gtk": Path("/usr/bin/cc"), "qt": Path("/usr/bin/c++")}
EXPECTED_SOURCES = {"gtk": "gtk_probe.c", "qt": "qt_probe.cc"}
EXPECTED_BINARY_NAMES = {
    "gtk": "mozkey-gtk-scope-probe",
    "qt": "mozkey-qt-scope-probe",
}
EXPECTED_PROGRAM_IDENTITIES = {
    "gtk": "com.miyakey.mozkey.GtkDogfood",
    "qt": "mozkey-qt-scope-probe",
}
KNOWN_SCOPES = frozenset(("all", "off"))
RECOGNIZED_SCOPE_ALIASES = frozenset(
    ("", "all", "all-applications", "grimodex", "grimodex-only", "off")
)
MAX_PROC_BYTES = 1 << 20
MAX_STREAM_BYTES = 1 << 16
MAX_TRACKED_BYTES = 1 << 20
HELPER_SUCCESS_LINE = "ydotool socket verified: exact_private_owner"
RELEASE_LAYOUT = "archlinux-x86_64"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: str
    executable: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class FcitxIdentity:
    process: ProcessIdentity
    scope: str | None
    protocol_root: str
    profile_root: str
    home_root: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one installed Mozkey GTK/Qt application-scope gate."
    )
    parser.add_argument("--toolkit", choices=("gtk", "qt"), required=True)
    parser.add_argument(
        "--scope-mode", choices=("default", "all", "off", "unknown"), required=True
    )
    parser.add_argument("--unknown-scope-value")
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    return parser.parse_args()


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def read_limited(path: Path, *, binary: bool = True) -> bytes | str:
    mode = "rb" if binary else "r"
    kwargs = {} if binary else {"encoding": "utf-8", "errors": "strict"}
    with path.open(mode, **kwargs) as stream:  # type: ignore[arg-type]
        data = stream.read(MAX_PROC_BYTES + 1)
    if len(data) > MAX_PROC_BYTES:
        fail(f"bounded read exceeded for {path.name}")
    return data


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
        try:
            raw_comm = read_limited(proc / "comm")
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
        if not isinstance(raw_comm, bytes):
            fail("protected process comm was not read as bytes")
        if (
            not raw_comm.endswith(b"\n")
            or not 1 <= len(raw_comm) - 1 <= 15
            or b"\n" in raw_comm[:-1]
            or b"\0" in raw_comm[:-1]
            or any(byte < 0x20 or byte > 0x7E for byte in raw_comm[:-1])
        ):
            fail("protected process comm is invalid during Fcitx discovery")
        if raw_comm[:-1] == EXPECTED_FCITX.name.encode("ascii"):
            raise RuntimeError(
                "an Fcitx candidate executable is unreadable"
            ) from executable_error
        return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_id(payload: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(payload)}\0".encode("ascii"))
    digest.update(payload)
    return digest.hexdigest()


def snapshot_tracked_file(
    source: Path, destination: Path, expected_blob: str, mode: int
) -> Path:
    source_descriptor = os.open(
        source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            fail("tracked snapshot source is not a regular file")
        payload = bytearray()
        while True:
            chunk = os.read(
                source_descriptor,
                min(1 << 16, MAX_TRACKED_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_TRACKED_BYTES:
                fail("tracked snapshot source exceeded its cap")
    finally:
        os.close(source_descriptor)
    if git_blob_id(bytes(payload)) != expected_blob:
        fail("tracked source changed before its immutable snapshot")
    destination_descriptor = os.open(
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
            written = os.write(destination_descriptor, view)
            if written <= 0:
                fail("could not complete tracked input snapshot")
            view = view[written:]
        os.fsync(destination_descriptor)
        os.fchmod(destination_descriptor, mode)
    finally:
        os.close(destination_descriptor)
    metadata = destination.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != mode
        or destination.resolve(strict=True) != destination
        or git_blob_id(read_limited(destination)) != expected_blob
    ):
        fail("immutable tracked input snapshot identity is invalid")
    return destination


class ProtocolMutationGuard:
    """Detect write/replace activity against the Protocol fixture in-gate."""

    _IN_NONBLOCK = os.O_NONBLOCK
    _IN_CLOEXEC = os.O_CLOEXEC
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
        libc = ctypes.CDLL(None, use_errno=True)
        init = libc.inotify_init1
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add = libc.inotify_add_watch
        add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add.restype = ctypes.c_int
        descriptor = init(self._IN_NONBLOCK | self._IN_CLOEXEC)
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
            fail("Protocol fixture was mutated during the GUI gate")

    def close(self) -> None:
        os.close(self._descriptor)


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


def run_checked(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
        env=environment or clean_command_environment(),
    )
    if result.returncode != 0:
        fail(f"command failed: {Path(command[0]).name} status={result.returncode}")
    return result


def git_root(script: Path) -> Path:
    result = run_checked(
        ["/usr/bin/git", "-C", str(script.parent), "rev-parse", "--show-toplevel"],
        timeout=10,
    )
    root = Path(result.stdout.strip()).resolve(strict=True)
    try:
        script.relative_to(root)
    except ValueError:
        fail("gate runner is outside its Git checkout")
    return root


def verify_tracked_file(root: Path, path: Path, expected_mode: str) -> str:
    if path.is_symlink() or not path.is_file():
        fail(f"{path.name} must be a regular non-symlink")
    canonical = path.resolve(strict=True)
    try:
        relative = canonical.relative_to(root)
    except ValueError:
        fail(f"{path.name} is outside the gate checkout")
    index = run_checked(
        ["/usr/bin/git", "-C", str(root), "ls-files", "--stage", "--", str(relative)],
        timeout=10,
    ).stdout.strip()
    fields = index.split()
    if len(fields) != 4 or fields[0] != expected_mode or fields[3] != str(relative):
        fail(f"{path.name} is not the expected tracked file mode")
    head = run_checked(
        ["/usr/bin/git", "-C", str(root), "ls-tree", "HEAD", "--", str(relative)],
        timeout=10,
    ).stdout.strip()
    head_fields = head.split()
    if (
        len(head_fields) != 4
        or head_fields[0] != expected_mode
        or head_fields[1] != "blob"
        or head_fields[2] != fields[1]
        or head_fields[3] != str(relative)
    ):
        fail(f"{path.name} is not the expected committed HEAD blob")
    working_blob = run_checked(
        ["/usr/bin/git", "-C", str(root), "hash-object", "--", str(canonical)],
        timeout=10,
    ).stdout.strip()
    if working_blob != fields[1]:
        fail(f"{path.name} differs from its tracked index blob")
    return fields[1]


def proc_start_time(proc: Path) -> str:
    raw = read_limited(proc / "stat")
    assert isinstance(raw, bytes)
    # The comm field is parenthesized and may itself contain spaces.  Fields
    # after the last ')' begin with field 3; starttime is field 22.
    close = raw.rfind(b")")
    if close < 0:
        fail("invalid process stat")
    fields_after_comm = raw[close + 2 :].decode("ascii", "strict").split()
    if len(fields_after_comm) < 20:
        fail("invalid process stat field count")
    return fields_after_comm[19]


def proc_uids(proc: Path) -> tuple[int, ...]:
    raw = read_limited(proc / "status")
    assert isinstance(raw, bytes)
    uid_line = next(
        (line for line in raw.decode("ascii", "strict").splitlines() if line.startswith("Uid:")),
        None,
    )
    if uid_line is None:
        fail("process status has no Uid line")
    values = tuple(int(value) for value in uid_line.split()[1:])
    if len(values) != 4:
        fail("process status has invalid Uid fields")
    return values


def process_identity(pid: int, expected_executable: Path) -> ProcessIdentity:
    if pid <= 1:
        fail("process pid is invalid")
    proc = Path("/proc") / str(pid)
    uid = os.getuid()
    if proc.stat().st_uid != uid or any(value != uid for value in proc_uids(proc)):
        fail("process uid identity mismatch")
    executable = os.readlink(proc / "exe")
    if executable != str(expected_executable):
        fail("process executable identity mismatch")
    raw_command = read_limited(proc / "cmdline")
    assert isinstance(raw_command, bytes)
    if not raw_command.endswith(b"\0"):
        fail("process command line is not NUL terminated")
    command = tuple(
        item.decode("utf-8", "strict") for item in raw_command[:-1].split(b"\0")
    )
    if not command:
        fail("process command line is empty")
    if command[0] != str(expected_executable):
        fail("process argv[0] identity mismatch")
    return ProcessIdentity(
        pid=pid,
        start_time=proc_start_time(proc),
        executable=executable,
        command=command,
    )


def installed_fcitx() -> FcitxIdentity:
    uid = os.getuid()
    candidates: list[FcitxIdentity] = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda path: path.name):
        if not entry.name.isdecimal() or entry.name.startswith("0"):
            continue
        executable = scan_visible_executable(entry, uid)
        if executable != str(EXPECTED_FCITX):
            continue
        # Once an exact executable candidate is found, every identity read is
        # mandatory.  Silently skipping it could turn an ambiguous process set
        # into a false unique match.
        identity = process_identity(int(entry.name), EXPECTED_FCITX)
        environment_raw = read_limited(entry / "environ")
        assert isinstance(environment_raw, bytes)
        if environment_raw and not environment_raw.endswith(b"\0"):
            fail("Fcitx environment is not NUL terminated")
        prefix = b"MOZKEY_GRIMODEX_SCOPE="
        scope_entries = [
            item[len(prefix) :]
            for item in environment_raw.split(b"\0")
            if item.startswith(prefix)
        ]
        if len(scope_entries) > 1:
            fail("Fcitx environment has duplicate scope entries")
        protocol_prefix = b"GRIMODEX_IME_ROOT="
        protocol_entries = [
            item[len(protocol_prefix) :]
            for item in environment_raw.split(b"\0")
            if item.startswith(protocol_prefix)
        ]
        if len(protocol_entries) != 1:
            fail("Fcitx environment must have one Protocol root")
        profile_prefix = b"XDG_CONFIG_HOME="
        profile_entries = [
            item[len(profile_prefix) :]
            for item in environment_raw.split(b"\0")
            if item.startswith(profile_prefix)
        ]
        if len(profile_entries) != 1:
            fail("Fcitx environment must have one profile root")
        home_prefix = b"HOME="
        home_entries = [
            item[len(home_prefix) :]
            for item in environment_raw.split(b"\0")
            if item.startswith(home_prefix)
        ]
        if len(home_entries) != 1:
            fail("Fcitx environment must have one HOME root")
        try:
            scope = scope_entries[0].decode("utf-8", "strict") if scope_entries else None
            protocol_root = protocol_entries[0].decode("utf-8", "strict")
            profile_root = profile_entries[0].decode("utf-8", "strict")
            home_root = home_entries[0].decode("utf-8", "strict")
        except UnicodeDecodeError:
            fail("Fcitx dogfood environment is not valid UTF-8")
        candidates.append(
            FcitxIdentity(
                process=identity,
                scope=scope,
                protocol_root=protocol_root,
                profile_root=profile_root,
                home_root=home_root,
            )
        )
    if len(candidates) != 1:
        fail(f"expected exactly one installed Fcitx process, got {len(candidates)}")
    return candidates[0]


def expected_scope(args: argparse.Namespace) -> str | None:
    if args.scope_mode == "default":
        if args.unknown_scope_value is not None:
            fail("--unknown-scope-value is valid only for unknown scope")
        return None
    if args.scope_mode in KNOWN_SCOPES:
        if args.unknown_scope_value is not None:
            fail("--unknown-scope-value is valid only for unknown scope")
        return args.scope_mode
    value = args.unknown_scope_value
    if value is None or not value or value in KNOWN_SCOPES:
        fail("unknown scope requires an exact non-empty unknown value")
    if "\x00" in value or "=" in value:
        fail("unknown scope value contains a forbidden byte")
    if value.strip().lower() in RECOGNIZED_SCOPE_ALIASES:
        fail("unknown scope value normalizes to a recognized scope alias")
    return value


def load_release_fixture(path: Path) -> tuple[str, str, str]:
    try:
        with path.open(encoding="utf-8") as stream:
            fixture = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("could not read the tracked release fixture") from error
    if set(fixture) != {
        "schema_version",
        "reading",
        "custom_value",
        "default_value",
        "state",
        "project",
    } or fixture.get("schema_version") != 1:
        fail("tracked release fixture fields changed")
    reading = fixture.get("reading")
    custom = fixture.get("custom_value")
    baseline = fixture.get("default_value")
    project = fixture.get("project")
    if (
        not isinstance(reading, str)
        or re.fullmatch(r"[a-z]+", reading) is None
        or len(reading) > 128
        or not isinstance(custom, str)
        or not isinstance(baseline, str)
        or not custom
        or not baseline
        or custom == baseline
        or custom.strip() == reading
        or baseline.strip() == reading
        or not isinstance(project, dict)
        or not isinstance(project.get("entries"), list)
        or len(project["entries"]) != 1
        or project["entries"][0].get("surface") != custom
    ):
        fail("tracked release fixture expectations are invalid")
    return reading, custom, baseline


def stable_consumer_entries(consumers: Path) -> list[str]:
    temporary_pattern = re.compile(
        r"^\.fcitx5-mozkey\.[1-9][0-9]*\.[0-9]+\.tmp$"
    )
    for _ in range(50):
        entries = sorted(path.name for path in consumers.iterdir())
        if entries == ["fcitx5-mozkey.json"]:
            return entries
        temporary = [name for name in entries if temporary_pattern.fullmatch(name)]
        if (
            len(temporary) == 1
            and len(entries) in (1, 2)
            and all(
                name == "fcitx5-mozkey.json" or name in temporary
                for name in entries
            )
        ):
            time.sleep(0.01)
            continue
        fail("Protocol fixture consumers directory entries are invalid")
    fail("Protocol consumer heartbeat refresh did not settle")


def verify_protocol_root(
    root: Path, fixture_path: Path
) -> tuple[Path, tuple[int, int, int], str, str]:
    if not root.is_absolute() or root.is_symlink():
        fail("Protocol fixture root must be absolute and non-symlink")
    canonical = root.resolve(strict=True)
    if canonical != root:
        fail("Protocol fixture root must be canonical")
    metadata = canonical.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("Protocol fixture root identity is invalid")
    if sorted(path.name for path in canonical.iterdir()) != [
        "consumers",
        "projects",
        "state.json",
    ]:
        fail("Protocol fixture root entries are invalid")
    consumers = canonical / "consumers"
    consumers_metadata = consumers.lstat()
    if (
        consumers.is_symlink()
        or consumers.resolve(strict=True) != consumers
        or not stat.S_ISDIR(consumers_metadata.st_mode)
        or consumers_metadata.st_uid != os.getuid()
        or stat.S_IMODE(consumers_metadata.st_mode) != 0o700
    ):
        fail("Protocol fixture consumers directory is invalid")
    consumer_entries = stable_consumer_entries(consumers)
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
    with fixture_path.open(encoding="utf-8") as stream:
        fixture = json.load(stream)
    project_id = fixture.get("project", {}).get("project_id")
    if (
        not isinstance(project_id, str)
        or re.fullmatch(r"[a-z0-9-]+", project_id) is None
        or fixture.get("state", {}).get("active_project_id") != project_id
    ):
        fail("tracked Protocol fixture project identity is invalid")
    projects = canonical / "projects"
    projects_metadata = projects.stat()
    if (
        projects.is_symlink()
        or not stat.S_ISDIR(projects_metadata.st_mode)
        or projects_metadata.st_uid != os.getuid()
        or stat.S_IMODE(projects_metadata.st_mode) != 0o700
        or sorted(path.name for path in projects.iterdir()) != [f"{project_id}.json"]
    ):
        fail("Protocol fixture projects directory is invalid")
    state = canonical / "state.json"
    project = projects / f"{project_id}.json"
    for document in (state, project):
        document_metadata = document.stat()
        if (
            document.is_symlink()
            or not stat.S_ISREG(document_metadata.st_mode)
            or document_metadata.st_uid != os.getuid()
            or stat.S_IMODE(document_metadata.st_mode) & 0o077
        ):
            fail("Protocol fixture document identity is invalid")
    with state.open(encoding="utf-8") as stream:
        state_value = json.load(stream)
    with project.open(encoding="utf-8") as stream:
        project_value = json.load(stream)
    if state_value != fixture["state"] or project_value != fixture["project"]:
        fail("Protocol root differs from the tracked fixture")
    identity = (metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode))
    return canonical, identity, sha256(state), sha256(project)


def validate_inputs(
    args: argparse.Namespace, reading: str, custom: str, baseline: str
) -> tuple[str | None, str, bool]:
    if not 15 <= args.timeout_seconds <= 300:
        fail("--timeout-seconds must be between 15 and 300")
    scope = expected_scope(args)
    custom_expected = not args.secure and args.scope_mode == "all"
    expected = reading if args.secure else custom if custom_expected else baseline
    return scope, expected, custom_expected


def pkg_config(package: str) -> list[str]:
    binary = Path("/usr/bin/pkg-config")
    if not binary.is_file():
        fail("exact /usr/bin/pkg-config is unavailable")
    result = run_checked(
        [str(binary), "--cflags", "--libs", package],
        timeout=30,
        environment=clean_command_environment(),
    )
    # pkg-config emits compiler arguments, not shell syntax.  Whitespace in
    # these system paths is unsupported deliberately to avoid shell parsing.
    flags = result.stdout.split()
    if any(any(character in flag for character in "'\"`$;|&<>") for flag in flags):
        fail("pkg-config emitted an unsafe compiler argument")
    return flags


def build_probe(toolkit: str, source: Path, build_dir: Path) -> Path:
    compiler = EXPECTED_COMPILERS[toolkit]
    if not compiler.exists():
        fail(f"exact compiler is unavailable: {compiler}")
    output = build_dir / EXPECTED_BINARY_NAMES[toolkit]
    package = "gtk4" if toolkit == "gtk" else "Qt6Widgets"
    language = "-std=c11" if toolkit == "gtk" else "-std=c++17"
    command = [
        str(compiler),
        language,
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
    ]
    if toolkit == "qt":
        command.append("-fPIC")
    command.extend((str(source), *pkg_config(package), "-o", str(output)))
    run_checked(command, timeout=90, environment=clean_command_environment())
    output.chmod(0o700)
    output_stat = output.lstat()
    if (
        output.is_symlink()
        or not stat.S_ISREG(output_stat.st_mode)
        or output_stat.st_uid != os.getuid()
        or stat.S_IMODE(output_stat.st_mode) != 0o700
        or output.resolve(strict=True) != output
    ):
        fail("compiled probe identity is invalid")
    return output


def desktop_environment(toolkit: str, profile_root: Path) -> dict[str, str]:
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    runtime = Path(f"/run/user/{uid}")
    if os.environ.get("XDG_RUNTIME_DIR") != str(runtime):
        fail("XDG_RUNTIME_DIR is not the desktop user's canonical runtime")
    runtime_metadata = runtime.stat()
    if (
        runtime.resolve(strict=True) != runtime
        or runtime_metadata.st_uid != uid
        or not stat.S_ISDIR(runtime_metadata.st_mode)
        or stat.S_IMODE(runtime_metadata.st_mode) != 0o700
    ):
        fail("desktop runtime directory identity is invalid")
    expected_bus = f"unix:path={runtime}/bus"
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") != expected_bus:
        fail("D-Bus session address is not the canonical user bus")
    bus_metadata = (runtime / "bus").stat()
    if not stat.S_ISSOCK(bus_metadata.st_mode) or bus_metadata.st_uid != uid:
        fail("D-Bus session socket identity is invalid")
    profile_metadata = profile_root.lstat()
    if (
        not profile_root.is_absolute()
        or profile_root.is_symlink()
        or profile_root.resolve(strict=True) != profile_root
        or profile_root.parent != runtime
        or not stat.S_ISDIR(profile_metadata.st_mode)
        or profile_metadata.st_uid != uid
        or stat.S_IMODE(profile_metadata.st_mode) != 0o700
    ):
        fail("fresh dogfood profile root identity is invalid")
    if os.environ.get("MOZKEY_DOGFOOD_PROFILE_ROOT") != str(profile_root):
        fail("MOZKEY_DOGFOOD_PROFILE_ROOT does not match --profile-root")
    wayland_name = os.environ.get("WAYLAND_DISPLAY")
    if not wayland_name or "/" in wayland_name or wayland_name in {".", ".."}:
        fail("a canonical Wayland display is required for this release gate")
    wayland_socket = runtime / wayland_name
    wayland_metadata = wayland_socket.stat()
    if (
        wayland_socket.is_symlink()
        or not stat.S_ISSOCK(wayland_metadata.st_mode)
        or wayland_metadata.st_uid != uid
    ):
        fail("Wayland display socket identity is invalid")

    environment = {
        "DBUS_SESSION_BUS_ADDRESS": expected_bus,
        "HOME": account.pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin",
        "USER": account.pw_name,
        "WAYLAND_DISPLAY": wayland_name,
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CONFIG_HOME": str(profile_root),
        "MOZKEY_DOGFOOD_PROFILE_ROOT": str(profile_root),
        "XDG_SESSION_TYPE": "wayland",
        "XMODIFIERS": "@im=fcitx",
    }
    if toolkit == "gtk":
        environment.update({"GDK_BACKEND": "wayland", "GTK_IM_MODULE": "fcitx"})
    else:
        environment.update(
            {"QT_IM_MODULE": "fcitx", "QT_QPA_PLATFORM": "wayland"}
        )
    for required_name in (
        "HOME",
        "LOGNAME",
        "USER",
        "YDOTOOL_SOCKET",
        "MOZKEY_DOGFOOD_YDOTOOLD_PID",
    ):
        value = (
            environment.get(required_name)
            if required_name in environment
            else os.environ.get(required_name)
        )
        if not value:
            fail(f"{required_name} is required for the desktop gate")
        environment[required_name] = value
    return environment


def remaining(deadline: float, label: str) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        fail(f"timeout while waiting for {label}")
    return value


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
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    if not group_exists():
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        process.wait(timeout=3)
    deadline = time.monotonic() + 3
    while group_exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if group_exists():
        fail("GUI helper process group did not terminate")


class BoundedPipeCapture:
    """Nonblocking, deadline-aware capture for one subprocess pipe pair."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None or process.stderr is None:
            fail("bounded capture requires stdout and stderr pipes")
        self._selector = selectors.DefaultSelector()
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._open_streams: set[str] = set()
        self._stdout_cursor = 0
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            descriptor = stream.fileno()
            os.set_blocking(descriptor, False)
            self._selector.register(descriptor, selectors.EVENT_READ, name)
            self._open_streams.add(name)

    def close(self) -> None:
        self._selector.close()

    def _captured_size(self) -> int:
        return sum(len(buffer) for buffer in self._buffers.values())

    def pump(self, deadline: float, label: str) -> None:
        events = self._selector.select(remaining(deadline, label))
        if not events:
            return
        for key, _ in events:
            remaining_capacity = MAX_STREAM_BYTES - self._captured_size()
            read_size = max(1, min(1 << 14, remaining_capacity + 1))
            try:
                chunk = os.read(key.fd, read_size)
            except BlockingIOError:
                continue
            if not chunk:
                self._selector.unregister(key.fd)
                self._open_streams.discard(key.data)
                continue
            self._buffers[key.data].extend(chunk)
            if self._captured_size() > MAX_STREAM_BYTES:
                fail("subprocess output exceeded its cap")

    def next_stdout_line(self, deadline: float, label: str) -> str:
        while True:
            if self._buffers["stderr"]:
                fail("subprocess wrote to stderr")
            stdout = self._buffers["stdout"]
            newline = stdout.find(b"\n", self._stdout_cursor)
            if newline >= 0:
                raw_line = bytes(stdout[self._stdout_cursor : newline])
                self._stdout_cursor = newline + 1
                return raw_line.decode("utf-8", "strict")
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


def run_sequence_helper(
    helper: Path,
    reading: str,
    mode: str,
    environment: dict[str, str],
    timeout: float,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        [str(helper), reading, mode],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    capture: BoundedPipeCapture | None = None
    try:
        capture = BoundedPipeCapture(process)
        capture.drain_to_eof(deadline, "canonical IME sequence output")
        if process.poll() is None:
            try:
                process.wait(timeout=remaining(deadline, "canonical IME sequence exit"))
            except subprocess.TimeoutExpired:
                fail("canonical IME sequence timed out")
        output, error = capture.decoded()
    finally:
        try:
            stop_process(process)
        finally:
            if capture is not None:
                capture.close()
    assert process.returncode is not None
    return process.returncode, output, error


def run_bounded_command(
    command: Sequence[str], environment: dict[str, str], timeout: float
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    capture: BoundedPipeCapture | None = None
    try:
        capture = BoundedPipeCapture(process)
        capture.drain_to_eof(deadline, "candidate verifier output")
        if process.poll() is None:
            try:
                process.wait(timeout=remaining(deadline, "candidate verifier exit"))
            except subprocess.TimeoutExpired:
                fail("candidate verifier timed out")
        output, error = capture.decoded()
    finally:
        try:
            stop_process(process)
        finally:
            if capture is not None:
                capture.close()
    assert process.returncode is not None
    return process.returncode, output, error


def verify_installed_candidate(
    verifier: Path,
    official_verifier: Path,
    repository_root: Path,
    attestation: Path,
    fcitx: FcitxIdentity,
    protocol_root: Path,
    profile_root: Path,
    scope: str | None,
    head: str,
    timeout: float,
    *,
    profile_only: bool,
) -> dict[str, object]:
    command = [
        str(EXPECTED_PYTHON),
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
        str(fcitx.process.pid),
        "--protocol-root",
        str(protocol_root),
        "--profile-root",
        str(profile_root),
        "--scope-kind",
        "absent" if scope is None else "value",
    ]
    if scope is not None:
        command.extend(("--scope-value", scope))
    if profile_only:
        command.append("--profile-only")
    status, output, error = run_bounded_command(
        command, clean_command_environment(), timeout
    )
    lines = output.splitlines()
    if status != 0 or error or len(lines) != 1 or not lines[0].startswith("RESULT:"):
        fail("installed candidate verifier did not emit an exact success record")
    try:
        result = json.loads(lines[0][len("RESULT:") :])
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
        or result.get("result")
        != ("profile-pass" if profile_only else "pass")
        or result.get("gitHead") != head
        or result.get("layout") != RELEASE_LAYOUT
        or result.get("fcitxPid") != fcitx.process.pid
        or result.get("fcitxStartTime") != fcitx.process.start_time
        or result.get("scope") != scope
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
        or (
            profile_only
            and (
                result.get("serverPid") is not None
                or result.get("serverStartTime") is not None
            )
        )
        or (
            not profile_only
            and (
                not isinstance(result.get("serverPid"), int)
                or result["serverPid"] <= 1
                or not isinstance(result.get("serverStartTime"), str)
                or not result["serverStartTime"].isdigit()
            )
        )
    ):
        fail("installed candidate verifier result identity mismatch")
    return result


def stable_candidate_evidence(evidence: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in evidence.items()
        if key != "consumerSha256"
    }


def run_gate(args: argparse.Namespace) -> dict[str, object]:
    invoked_script = Path(__file__).absolute()
    if invoked_script.is_symlink():
        fail("gate runner must not be invoked through a symlink")
    script = invoked_script.resolve(strict=True)
    root = git_root(script)
    directory = script.parent
    source = directory / EXPECTED_SOURCES[args.toolkit]
    helper = directory / "send_ime_sequence.sh"
    socket_verifier = directory / "verify_ydotool_socket.py"
    verified_ydotool = directory / "run_verified_ydotool.py"
    candidate_verifier = directory / "verify_installed_candidate.py"
    official_attestation_verifier = root / "tools/release/linux_build_attestation.py"
    zenz_normalizer = root / "tools/release/normalize_zenz_gguf.py"
    fixture_path = directory / "release_fixture.json"
    runner_blob = verify_tracked_file(root, script, "100755")
    source_blob = verify_tracked_file(root, source, "100644")
    helper_blob = verify_tracked_file(root, helper, "100755")
    socket_verifier_blob = verify_tracked_file(root, socket_verifier, "100755")
    verified_ydotool_blob = verify_tracked_file(root, verified_ydotool, "100755")
    candidate_verifier_blob = verify_tracked_file(
        root, candidate_verifier, "100755"
    )
    official_attestation_verifier_blob = verify_tracked_file(
        root, official_attestation_verifier, "100755"
    )
    zenz_normalizer_blob = verify_tracked_file(root, zenz_normalizer, "100644")
    fixture_blob = verify_tracked_file(root, fixture_path, "100644")
    head = run_checked(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"], timeout=10
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        fail("gate checkout HEAD is invalid")
    if run_checked(
        [
            "/usr/bin/git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        timeout=10,
    ).stdout:
        fail("gate checkout tracked worktree is not clean")
    deadline = time.monotonic() + args.timeout_seconds
    with tempfile.TemporaryDirectory(prefix="mozkey-gui-scope-gate.") as raw_build:
        build_dir = Path(raw_build).resolve(strict=True)
        if build_dir.stat().st_uid != os.getuid():
            fail("temporary build directory owner mismatch")
        build_dir.chmod(0o700)
        snapshot_dir = build_dir / "tracked-inputs"
        snapshot_dir.mkdir(mode=0o700)
        source_snapshot = snapshot_tracked_file(
            source, snapshot_dir / source.name, source_blob, 0o400
        )
        helper_snapshot = snapshot_tracked_file(
            helper, snapshot_dir / helper.name, helper_blob, 0o500
        )
        snapshot_tracked_file(
            socket_verifier,
            snapshot_dir / socket_verifier.name,
            socket_verifier_blob,
            0o500,
        )
        snapshot_tracked_file(
            verified_ydotool,
            snapshot_dir / verified_ydotool.name,
            verified_ydotool_blob,
            0o500,
        )
        candidate_verifier_snapshot = snapshot_tracked_file(
            candidate_verifier,
            snapshot_dir / candidate_verifier.name,
            candidate_verifier_blob,
            0o500,
        )
        official_attestation_verifier_snapshot = snapshot_tracked_file(
            official_attestation_verifier,
            snapshot_dir / official_attestation_verifier.name,
            official_attestation_verifier_blob,
            0o500,
        )
        snapshot_tracked_file(
            zenz_normalizer,
            snapshot_dir / zenz_normalizer.name,
            zenz_normalizer_blob,
            0o400,
        )
        fixture_snapshot = snapshot_tracked_file(
            fixture_path, snapshot_dir / fixture_path.name, fixture_blob, 0o400
        )
        reading, custom, baseline = load_release_fixture(fixture_snapshot)
        if (
            not args.protocol_root.is_absolute()
            or args.protocol_root.is_symlink()
            or args.protocol_root.resolve(strict=True) != args.protocol_root
        ):
            fail("Protocol fixture root must be canonical before watch setup")
        fixture_document = json.loads(fixture_snapshot.read_text(encoding="utf-8"))
        project_id = fixture_document["project"]["project_id"]
        mutation_guard = ProtocolMutationGuard(
            (
                args.protocol_root,
                args.protocol_root / "projects",
                args.protocol_root / "state.json",
                args.protocol_root / "projects" / f"{project_id}.json",
            )
        )
        protocol_root, protocol_identity, state_digest, project_digest = (
            verify_protocol_root(args.protocol_root, fixture_snapshot)
        )
        mutation_guard.assert_clean()
        intended_scope, expected_value, custom_expected = validate_inputs(
            args, reading, custom, baseline
        )
        before = installed_fcitx()
        if before.scope != intended_scope:
            fail("installed Fcitx scope does not exactly match the requested gate")
        if before.protocol_root != str(protocol_root):
            fail("installed Fcitx Protocol root does not match the gate")
        if before.profile_root != str(args.profile_root):
            fail("installed Fcitx profile root does not match the gate")
        if before.home_root != str(args.profile_root):
            fail("installed Fcitx HOME does not match the fresh gate root")
        profile_evidence = verify_installed_candidate(
            candidate_verifier_snapshot,
            official_attestation_verifier_snapshot,
            root,
            root / "dist" / "linux" / RELEASE_LAYOUT / "build-attestation.json",
            before,
            protocol_root,
            args.profile_root,
            intended_scope,
            head,
            remaining(deadline, "fresh profile candidate preflight"),
            profile_only=True,
        )
        probe = build_probe(args.toolkit, source_snapshot, build_dir)
        probe_hash = sha256(probe)
        probe_metadata = probe.stat()
        environment = desktop_environment(args.toolkit, args.profile_root)
        environment.update(
            {
                "PATH": "/usr/bin",
                "MOZKEY_DOGFOOD_EXPECTED_VALUE": expected_value,
                "MOZKEY_DOGFOOD_KEY_DELAY_MS": "50",
                "MOZKEY_DOGFOOD_SETTLE_DELAY_SECONDS": "1",
                "MOZKEY_DOGFOOD_TIMEOUT_SECONDS": str(
                    max(10, min(300, args.timeout_seconds - 5))
                ),
                "MOZKEY_DOGFOOD_IM": "mozkey",
            }
        )
        environment.pop("MOZKEY_DOGFOOD_PASSWORD", None)
        if args.secure:
            environment["MOZKEY_DOGFOOD_PASSWORD"] = "1"
        if args.toolkit == "gtk":
            environment["MOZKEY_DOGFOOD_APP_ID"] = EXPECTED_PROGRAM_IDENTITIES["gtk"]

        process = subprocess.Popen(
            [str(probe)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
        capture: BoundedPipeCapture | None = None
        try:
            capture = BoundedPipeCapture(process)
            expected_ready = (
                "READY:active=true focused=true "
                f"password={'true' if args.secure else 'false'}"
            )
            ready_line = capture.next_stdout_line(deadline, "GUI probe readiness")
            if ready_line != expected_ready:
                fail("GUI probe readiness transcript was not exact")
            probe_identity = process_identity(process.pid, probe)
            if probe_identity.command != (str(probe),):
                fail("GUI probe command identity mismatch")
            during = installed_fcitx()
            if during != before:
                fail("installed Fcitx identity or scope changed before input")
            immediate_profile_evidence = verify_installed_candidate(
                candidate_verifier_snapshot,
                official_attestation_verifier_snapshot,
                root,
                root / "dist" / "linux" / RELEASE_LAYOUT / "build-attestation.json",
                before,
                protocol_root,
                args.profile_root,
                intended_scope,
                head,
                remaining(deadline, "immediate fresh profile preflight"),
                profile_only=True,
            )
            if stable_candidate_evidence(
                immediate_profile_evidence
            ) != stable_candidate_evidence(profile_evidence):
                fail("fresh profile evidence changed before GUI input")

            helper_status, helper_output, helper_error = run_sequence_helper(
                helper_snapshot,
                reading,
                "password" if args.secure else "conversion",
                {
                    key: value
                    for key, value in environment.items()
                    if key != "MOZKEY_DOGFOOD_EXPECTED_VALUE"
                },
                remaining(deadline, "canonical IME sequence"),
            )
            if helper_status != 0:
                fail(f"canonical IME sequence failed status={helper_status}")
            expected_helper_lines = 3 if args.secure else 5
            expected_helper_output = (
                HELPER_SUCCESS_LINE + "\n"
            ) * expected_helper_lines
            if helper_output != expected_helper_output:
                fail("canonical IME sequence verification transcript was not exact")
            if helper_error:
                fail("canonical IME sequence wrote to stderr")

            result_line = capture.next_stdout_line(deadline, "GUI result")
            if process.poll() is not None:
                fail("GUI probe exited before parent termination")
            stop_process(process)
            if process.returncode not in (-signal.SIGTERM, -signal.SIGKILL):
                fail("GUI probe was not terminated by its parent")
            capture.drain_to_eof(
                time.monotonic() + 3, "terminated GUI probe output"
            )
            output, error = capture.decoded()
            expected_probe_output = expected_ready + "\n" + result_line + "\n"
            if error or output != expected_probe_output:
                fail("GUI probe emitted an inexact transcript")
            match = re.fullmatch(
                r"RESULT:match=true length=([1-9][0-9]*) password="
                + ("true" if args.secure else "false"),
                result_line,
            )
            if match is None or int(match.group(1)) != len(expected_value):
                fail("GUI probe result did not exactly match the expected value metadata")
            after = installed_fcitx()
            if after != before:
                fail("installed Fcitx identity or scope changed during the gate")
            candidate_evidence = verify_installed_candidate(
                candidate_verifier_snapshot,
                official_attestation_verifier_snapshot,
                root,
                root / "dist" / "linux" / RELEASE_LAYOUT / "build-attestation.json",
                before,
                protocol_root,
                args.profile_root,
                intended_scope,
                head,
                remaining(deadline, "installed candidate provenance"),
                profile_only=False,
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
                if candidate_evidence.get(key) != profile_evidence.get(key):
                    fail("candidate identity changed after the GUI profile preflight")
            (
                final_protocol_root,
                final_protocol_identity,
                final_state_digest,
                final_project_digest,
            ) = verify_protocol_root(protocol_root, fixture_snapshot)
            if (
                final_protocol_root != protocol_root
                or final_protocol_identity != protocol_identity
                or final_state_digest != state_digest
                or final_project_digest != project_digest
            ):
                fail("Protocol fixture changed during the GUI gate")
            mutation_guard.assert_clean()
            if (
                probe.stat().st_dev != probe_metadata.st_dev
                or probe.stat().st_ino != probe_metadata.st_ino
                or sha256(probe) != probe_hash
            ):
                fail("compiled GUI probe changed during the gate")
        finally:
            try:
                stop_process(process)
            finally:
                try:
                    if capture is not None:
                        capture.close()
                finally:
                    try:
                        mutation_guard.assert_clean()
                    finally:
                        mutation_guard.close()

    if run_checked(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"], timeout=10
    ).stdout.strip() != head:
        fail("gate checkout HEAD changed during the gate")
    if run_checked(
        [
            "/usr/bin/git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        timeout=10,
    ).stdout:
        fail("gate checkout changed during the gate")
    final_blobs = {
        "runner": verify_tracked_file(root, script, "100755"),
        "source": verify_tracked_file(root, source, "100644"),
        "helper": verify_tracked_file(root, helper, "100755"),
        "socket_verifier": verify_tracked_file(root, socket_verifier, "100755"),
        "verified_ydotool": verify_tracked_file(root, verified_ydotool, "100755"),
        "candidate_verifier": verify_tracked_file(
            root, candidate_verifier, "100755"
        ),
        "official_attestation_verifier": verify_tracked_file(
            root, official_attestation_verifier, "100755"
        ),
        "zenz_normalizer": verify_tracked_file(root, zenz_normalizer, "100644"),
        "fixture": verify_tracked_file(root, fixture_path, "100644"),
    }
    if final_blobs != {
        "runner": runner_blob,
        "source": source_blob,
        "helper": helper_blob,
        "socket_verifier": socket_verifier_blob,
        "verified_ydotool": verified_ydotool_blob,
        "candidate_verifier": candidate_verifier_blob,
        "official_attestation_verifier": official_attestation_verifier_blob,
        "zenz_normalizer": zenz_normalizer_blob,
        "fixture": fixture_blob,
    }:
        fail("tracked dogfood inputs changed during the gate")

    return {
        "toolkit": args.toolkit,
        "scopeMode": args.scope_mode,
        "scopeEnvironment": intended_scope,
        "customApplied": custom_expected,
        "secureField": args.secure,
        "expectedCharacters": len(expected_value),
        "expectedSha256": hashlib.sha256(expected_value.encode("utf-8")).hexdigest(),
        "requestedProgramIdentity": EXPECTED_PROGRAM_IDENTITIES[args.toolkit],
        "inputMethod": "mozkey",
        "probeExecutable": EXPECTED_BINARY_NAMES[args.toolkit],
        "probeSha256": probe_hash,
        "probePid": probe_identity.pid,
        "fcitxPid": before.process.pid,
        "fcitxStartTime": before.process.start_time,
        "fcitxExecutable": before.process.executable,
        "runnerBlob": runner_blob,
        "sourceBlob": source_blob,
        "sequenceHelperBlob": helper_blob,
        "socketVerifierBlob": socket_verifier_blob,
        "verifiedYdotoolBlob": verified_ydotool_blob,
        "candidateVerifierBlob": candidate_verifier_blob,
        "officialAttestationVerifierBlob": official_attestation_verifier_blob,
        "zenzNormalizerBlob": zenz_normalizer_blob,
        "fixtureBlob": fixture_blob,
        "protocolStateSha256": state_digest,
        "protocolProjectSha256": project_digest,
        "candidateAttestationSha256": candidate_evidence["attestationSha256"],
        "consumerSha256": candidate_evidence["consumerSha256"],
        "fcitxProfileSha256": candidate_evidence["fcitxProfileSha256"],
        "installedAddonSha256": candidate_evidence["addonSha256"],
        "installedServerSha256": candidate_evidence["serverSha256"],
        "serverPid": candidate_evidence["serverPid"],
        "serverStartTime": candidate_evidence["serverStartTime"],
        "mozkeyHead": head,
        "identityStableBeforeAfter": True,
        "result": "pass",
    }


def main() -> int:
    try:
        result = run_gate(parse_args())
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError) as error:
        print(f"RESULT:fail reason={type(error).__name__}:{error}", file=os.sys.stderr)
        return 1
    print("RESULT:" + json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
