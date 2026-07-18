#!/usr/bin/python3 -I
"""Locate one exact dogfood text surface through AT-SPI.

The helper never asks AT-SPI to focus a widget.  It only observes the unique
screen-space text surface owned by the pinned probe lifetime.  The parent gate
uses those coordinates with its separately verified ydotool runner so the
compositor receives a normal user click.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NoReturn


MAX_PROC_BYTES = 1 << 20
MAX_APPLICATIONS = 256
MAX_CHILDREN = 256
MAX_NODES = 4096
MAX_DEPTH = 64
MAX_OUTPUT_BYTES = 4096
MAX_COORDINATE = 131072
MAX_DIMENSION = 32768
REQUIRED_STATE_NAMES = (
    "EDITABLE",
    "ENABLED",
    "FOCUSABLE",
    "SENSITIVE",
    "SHOWING",
    "VISIBLE",
)
EXPECTED_TITLES = {
    ("gtk", False): "Mozkey GTK Probe",
    ("gtk", True): "Mozkey GTK Password Probe",
    ("qt", False): "Mozkey Qt Probe",
    ("qt", True): "Mozkey Qt Password Probe",
    ("electron", False): "Mozkey Electron Probe",
    ("electron", True): "Mozkey Electron Password Probe",
}
EXPECTED_TOOLKIT_NAMES = {
    "gtk": frozenset(("gtk", "gtk4")),
    "qt": frozenset(("qt", "qt6")),
    "electron": frozenset(("chromium", "electron")),
}


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: str
    executable: str
    owner_uid: int


@dataclass(frozen=True)
class SurfaceObservation:
    schemaVersion: int
    toolkit: str
    toolkitName: str
    targetPid: int
    targetStartTime: str
    accessiblePid: int
    accessibleStartTime: str
    ownerUid: int
    role: str
    title: str
    x: int
    y: int
    width: int
    height: int
    clickX: int
    clickY: int


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Locate one exact Mozkey dogfood AT-SPI input surface."
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--toolkit", choices=("gtk", "qt", "electron"), required=True)
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--require-focused", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=10)
    return parser.parse_args()


def read_limited(path: Path) -> bytes:
    with path.open("rb") as stream:
        payload = stream.read(MAX_PROC_BYTES + 1)
    if len(payload) > MAX_PROC_BYTES:
        fail(f"bounded proc read exceeded for {path.name}")
    return payload


def proc_start_time(proc: Path) -> str:
    raw = read_limited(proc / "stat")
    close = raw.rfind(b")")
    if close < 0:
        fail("process stat is invalid")
    fields = raw[close + 2 :].decode("ascii", "strict").split()
    if len(fields) < 20 or not fields[19].isdigit():
        fail("process start time is invalid")
    return fields[19]


def proc_uids(proc: Path) -> tuple[int, ...]:
    status = read_limited(proc / "status").decode("ascii", "strict")
    line = next((item for item in status.splitlines() if item.startswith("Uid:")), None)
    if line is None:
        fail("process status has no uid identity")
    values = tuple(int(value) for value in line.split()[1:])
    if len(values) != 4:
        fail("process uid identity is invalid")
    return values


def proc_parent_pid(proc: Path) -> int:
    status = read_limited(proc / "status").decode("ascii", "strict")
    line = next(
        (item for item in status.splitlines() if item.startswith("PPid:")), None
    )
    if line is None:
        fail("process status has no parent identity")
    fields = line.split()
    if len(fields) != 2 or not fields[1].isdigit():
        fail("process parent identity is invalid")
    return int(fields[1])


def capture_process(
    pid: int, expected_executable: Path | None = None
) -> ProcessIdentity:
    if pid <= 1:
        fail("process pid is invalid")
    proc = Path("/proc") / str(pid)
    owner_uid = os.getuid()
    metadata = proc.stat()
    uids = proc_uids(proc)
    if metadata.st_uid != owner_uid or any(value != owner_uid for value in uids):
        fail("process owner identity mismatch")
    executable = os.readlink(proc / "exe")
    if expected_executable is not None and executable != str(expected_executable):
        fail("process executable identity mismatch")
    return ProcessIdentity(
        pid=pid,
        start_time=proc_start_time(proc),
        executable=executable,
        owner_uid=owner_uid,
    )


def is_descendant(pid: int, root_pid: int, owner_uid: int) -> bool:
    """Return true only for a fully readable same-owner lineage to root_pid."""

    current = pid
    visited: set[int] = set()
    for _ in range(MAX_DEPTH):
        if current == root_pid:
            return True
        if current <= 1 or current in visited:
            return False
        visited.add(current)
        proc = Path("/proc") / str(current)
        try:
            metadata = proc.stat()
            if metadata.st_uid != owner_uid or any(
                value != owner_uid for value in proc_uids(proc)
            ):
                return False
            current = proc_parent_pid(proc)
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            return False
    return False


def poll_pidfd(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poller.poll(0))


def validate_executable(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        fail("target executable must be an absolute non-symlink")
    canonical = path.resolve(strict=True)
    metadata = canonical.lstat()
    if (
        canonical != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in (0, os.getuid())
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or stat.S_IMODE(metadata.st_mode) & 0o111 == 0
    ):
        fail("target executable identity is invalid")
    return canonical


def validate_extents(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    values = (x, y, width, height)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        fail("AT-SPI screen extents are not integers")
    if (
        not -MAX_COORDINATE <= x <= MAX_COORDINATE
        or not -MAX_COORDINATE <= y <= MAX_COORDINATE
        or not 1 <= width <= MAX_DIMENSION
        or not 1 <= height <= MAX_DIMENSION
        or x + width > MAX_COORDINATE
        or y + height > MAX_COORDINATE
    ):
        fail("AT-SPI screen extents are outside the gate bounds")
    click_x = x + width // 2
    click_y = y + height // 2
    if (
        not -MAX_COORDINATE <= click_x <= MAX_COORDINATE
        or not -MAX_COORDINATE <= click_y <= MAX_COORDINATE
    ):
        fail("AT-SPI click point is outside the gate bounds")
    return click_x, click_y


def expected_title(toolkit: str, secure: bool) -> str:
    try:
        return EXPECTED_TITLES[(toolkit, secure)]
    except KeyError as error:
        raise RuntimeError("unsupported dogfood surface identity") from error


def expected_roles(atspi: Any, toolkit: str, secure: bool) -> tuple[Any, ...]:
    # GtkEntry keeps ATSPI_ROLE_TEXT when visibility is disabled; only the
    # distinct GtkPasswordEntry widget is translated to PASSWORD_TEXT.
    if toolkit == "gtk":
        return (atspi.Role.TEXT,)
    if secure:
        return (atspi.Role.PASSWORD_TEXT,)
    if toolkit == "electron":
        return (atspi.Role.ENTRY, atspi.Role.TEXT)
    return (atspi.Role.TEXT,)


def canonical_role_name(atspi: Any, role: Any) -> str:
    if role == atspi.Role.PASSWORD_TEXT:
        return "password text"
    if role == atspi.Role.ENTRY:
        return "entry"
    if role == atspi.Role.TEXT:
        return "text"
    fail("AT-SPI text surface role is outside the exact allowlist")


def normalize_toolkit_name(toolkit: str, value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(ord(character) < 0x20 for character in value)
    ):
        fail("AT-SPI toolkit identity is invalid")
    normalized = value.strip().casefold()
    if normalized not in EXPECTED_TOOLKIT_NAMES[toolkit]:
        fail("AT-SPI toolkit identity mismatch")
    return normalized


def matching_process(
    toolkit: str, accessible_pid: int, target: ProcessIdentity
) -> bool:
    if toolkit != "electron":
        return accessible_pid == target.pid
    return is_descendant(accessible_pid, target.pid, target.owner_uid)


def load_atspi() -> Any:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError) as error:
        raise RuntimeError("the system AT-SPI binding is unavailable") from error
    return Atspi


def ancestor_has_exact_title(node: Any, atspi: Any, title: str) -> bool:
    matches = 0
    current = node
    for _ in range(MAX_DEPTH):
        if current is None:
            break
        role = current.get_role()
        if role in (
            atspi.Role.DIALOG,
            atspi.Role.FRAME,
            atspi.Role.WINDOW,
        ) and current.get_name() == title:
            matches += 1
        if role == atspi.Role.APPLICATION:
            break
        current = current.get_parent()
    if matches > 1:
        fail("AT-SPI surface has an ambiguous title ancestry")
    return matches == 1


def required_states_present(
    node: Any, atspi: Any, require_focused: bool = False
) -> bool:
    state_set = node.get_state_set()
    if not all(
        state_set.contains(getattr(atspi.StateType, name))
        for name in REQUIRED_STATE_NAMES
    ):
        return False
    return not require_focused or state_set.contains(atspi.StateType.FOCUSED)


def observe_surface(
    node: Any,
    atspi: Any,
    toolkit: str,
    secure: bool,
    target: ProcessIdentity,
    require_focused: bool = False,
) -> SurfaceObservation:
    accessible_pid = int(node.get_process_id())
    if not matching_process(toolkit, accessible_pid, target):
        fail("AT-SPI node process identity mismatch")
    accessible = capture_process(
        accessible_pid,
        Path(target.executable) if toolkit != "electron" else None,
    )
    application = node.get_application()
    if application is None or int(application.get_process_id()) != accessible_pid:
        fail("AT-SPI application owner identity mismatch")
    toolkit_name = normalize_toolkit_name(toolkit, application.get_toolkit_name())
    title = expected_title(toolkit, secure)
    if not ancestor_has_exact_title(node, atspi, title):
        fail("AT-SPI text surface title mismatch")
    role = node.get_role()
    if role not in expected_roles(atspi, toolkit, secure):
        fail("AT-SPI text surface role mismatch")
    if not required_states_present(node, atspi, require_focused):
        fail("AT-SPI text surface state contract mismatch")
    component = node.get_component()
    if component is None:
        fail("AT-SPI text surface has no component interface")
    extents = component.get_extents(atspi.CoordType.SCREEN)
    click_x, click_y = validate_extents(
        int(extents.x), int(extents.y), int(extents.width), int(extents.height)
    )
    return SurfaceObservation(
        schemaVersion=1,
        toolkit=toolkit,
        toolkitName=toolkit_name,
        targetPid=target.pid,
        targetStartTime=target.start_time,
        accessiblePid=accessible.pid,
        accessibleStartTime=accessible.start_time,
        ownerUid=accessible.owner_uid,
        role=canonical_role_name(atspi, role),
        title=title,
        x=int(extents.x),
        y=int(extents.y),
        width=int(extents.width),
        height=int(extents.height),
        clickX=click_x,
        clickY=click_y,
    )


def scan_once(
    atspi: Any,
    toolkit: str,
    secure: bool,
    target: ProcessIdentity,
    require_focused: bool = False,
) -> list[SurfaceObservation]:
    desktops = list(atspi.get_desktop_list())
    if len(desktops) != 1:
        fail("expected exactly one AT-SPI desktop")
    desktop = desktops[0]
    application_count = int(desktop.get_child_count())
    if not 0 <= application_count <= MAX_APPLICATIONS:
        fail("AT-SPI application count exceeded the gate cap")
    allowed_roles = expected_roles(atspi, toolkit, secure)
    observations: list[SurfaceObservation] = []
    target_applications = 0
    nodes_seen = 0
    for application_index in range(application_count):
        application = desktop.get_child_at_index(application_index)
        if application is None:
            fail("AT-SPI desktop returned a missing application")
        accessible_pid = int(application.get_process_id())
        if not matching_process(toolkit, accessible_pid, target):
            continue
        target_applications += 1
        if target_applications > 1:
            fail("AT-SPI exposed multiple applications for the probe lifetime")
        stack: list[tuple[Any, int]] = [(application, 0)]
        while stack:
            node, depth = stack.pop()
            nodes_seen += 1
            if nodes_seen > MAX_NODES or depth > MAX_DEPTH:
                fail("AT-SPI probe subtree exceeded the traversal cap")
            role = node.get_role()
            if role in allowed_roles and ancestor_has_exact_title(
                node, atspi, expected_title(toolkit, secure)
            ):
                if not required_states_present(node, atspi):
                    fail("AT-SPI text surface state contract mismatch")
                if require_focused and not required_states_present(
                    node, atspi, True
                ):
                    continue
                observations.append(
                    observe_surface(
                        node,
                        atspi,
                        toolkit,
                        secure,
                        target,
                        require_focused,
                    )
                )
                if len(observations) > 1:
                    fail("AT-SPI probe surface is not unique")
            child_count = int(node.get_child_count())
            if not 0 <= child_count <= MAX_CHILDREN:
                fail("AT-SPI node child count exceeded the gate cap")
            for child_index in range(child_count - 1, -1, -1):
                child = node.get_child_at_index(child_index)
                if child is None:
                    fail("AT-SPI node returned a missing child")
                stack.append((child, depth + 1))
    return observations


def locate_surface(
    atspi: Any,
    toolkit: str,
    secure: bool,
    target: ProcessIdentity,
    deadline: float,
    require_focused: bool = False,
) -> SurfaceObservation:
    previous: SurfaceObservation | None = None
    while time.monotonic() < deadline:
        observations = scan_once(
            atspi, toolkit, secure, target, require_focused
        )
        if len(observations) == 1:
            current = observations[0]
            if current == previous:
                return current
            previous = current
        elif observations:
            fail("AT-SPI probe surface is not unique")
        else:
            previous = None
        time.sleep(0.05)
    fail("timed out locating the exact AT-SPI probe surface")


def encode_observation(observation: SurfaceObservation) -> str:
    line = "SURFACE:" + json.dumps(
        asdict(observation), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    encoded = (line + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        fail("AT-SPI surface evidence exceeded the output cap")
    return line


def main() -> int:
    args = parse_args()
    try:
        if os.getuid() == 0:
            fail("AT-SPI surface locator must run as the desktop user")
        if args.pid <= 1 or re.fullmatch(r"[1-9][0-9]*", args.start_time) is None:
            fail("target process identity arguments are invalid")
        if not 1 <= args.timeout_seconds <= 15:
            fail("AT-SPI locator timeout is invalid")
        executable = validate_executable(args.executable)
        target = capture_process(args.pid, executable)
        if target.start_time != args.start_time:
            fail("target process lifetime changed before AT-SPI discovery")
        try:
            target_pidfd = os.pidfd_open(target.pid)
        except (AttributeError, OSError) as error:
            raise RuntimeError("could not pin the target process lifetime") from error
        try:
            if poll_pidfd(target_pidfd):
                fail("target process exited before AT-SPI discovery")
            atspi = load_atspi()
            atspi.set_timeout(1000, 1000)
            deadline = time.monotonic() + args.timeout_seconds
            observation = locate_surface(
                atspi,
                args.toolkit,
                args.secure,
                target,
                deadline,
                args.require_focused,
            )
            if poll_pidfd(target_pidfd) or capture_process(
                target.pid, executable
            ) != target:
                fail("target process lifetime changed during AT-SPI discovery")
            accessible_pidfd = os.pidfd_open(observation.accessiblePid)
            try:
                if poll_pidfd(accessible_pidfd):
                    fail("AT-SPI surface owner exited before evidence emission")
                accessible = capture_process(
                    observation.accessiblePid,
                    executable if args.toolkit != "electron" else None,
                )
                if (
                    accessible.start_time != observation.accessibleStartTime
                    or accessible.owner_uid != observation.ownerUid
                    or not matching_process(args.toolkit, accessible.pid, target)
                ):
                    fail("AT-SPI surface owner identity changed")
            finally:
                os.close(accessible_pidfd)
        finally:
            os.close(target_pidfd)
        print(encode_observation(observation), flush=True)
        return 0
    except Exception as error:
        reason = re.sub(
            r"[\x00-\x1f\x7f]+",
            " ",
            f"{type(error).__name__}:{error}",
        )[:512]
        print(
            f"SURFACE:fail reason={reason}",
            file=os.sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
