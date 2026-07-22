#!/usr/bin/python3 -I
"""Drive an exact real Fcitx IBus conversion without compositor focus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


IBUS_BUS_NAME = "org.freedesktop.IBus"
DBUS_BUS_NAME = "org.freedesktop.DBus"
DBUS_ROOT_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"
IBUS_ROOT_PATH = "/org/freedesktop/IBus"
IBUS_ROOT_INTERFACE = "org.freedesktop.IBus"
IBUS_CONTEXT_INTERFACE = "org.freedesktop.IBus.InputContext"
IBUS_SERVICE_INTERFACE = "org.freedesktop.IBus.Service"
IBUS_CAPABILITIES = 0x1F
RETURN_KEYSYM = 0xFF0D
TEXT_METADATA = frozenset({"IBusText", "IBusAttribute", "IBusAttrList"})
FCITX_REMOTE_TIMEOUT_SECONDS = 5.0
FCITX_REMOTE_PATH = "/usr/bin/fcitx5-remote"
FCITX_PATH = "/usr/bin/fcitx5"
IBUS_ENGINE = "mozkey-ibg"
MAX_READING_CHARACTERS = 128
CONTROL_TIMEOUT_SECONDS = 15.0
FIXTURE_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "release_fixture.json")


@dataclass(frozen=True)
class FcitxSnapshot:
    input_method: str
    state: str


@dataclass(frozen=True)
class ExpectedIBusOwner:
    owner: str
    pid: int
    start_time: str


def proc_start_time(pid: int) -> str:
    with open(f"/proc/{pid}/stat", "rb") as stream:
        raw = stream.read(1 << 20)
    close = raw.rfind(b")")
    fields = raw[close + 2 :].decode("ascii", "strict").split()
    if close < 0 or len(fields) < 20 or not fields[19].isdigit():
        raise RuntimeError("IBus owner process stat is invalid")
    return fields[19]


def expected_ibus_owner() -> ExpectedIBusOwner:
    owner = os.environ.get("MOZKEY_DOGFOOD_EXPECTED_IBUS_OWNER", "")
    pid_text = os.environ.get("MOZKEY_DOGFOOD_EXPECTED_FCITX_PID", "")
    start_time = os.environ.get("MOZKEY_DOGFOOD_EXPECTED_FCITX_START_TIME", "")
    if (
        re.fullmatch(r":[0-9]+\.[0-9]+", owner) is None
        or re.fullmatch(r"[1-9][0-9]*", pid_text) is None
        or re.fullmatch(r"[1-9][0-9]*", start_time) is None
    ):
        raise RuntimeError("exact expected IBus/Fcitx lifetime is required")
    return ExpectedIBusOwner(owner, int(pid_text), start_time)


def verify_ibus_owner(bus: object, expected: ExpectedIBusOwner) -> None:
    daemon = dbus.Interface(
        bus.get_object(DBUS_BUS_NAME, DBUS_ROOT_PATH),
        DBUS_INTERFACE,
    )
    first_owner = str(daemon.GetNameOwner(IBUS_BUS_NAME))
    first_pid = int(daemon.GetConnectionUnixProcessID(first_owner))
    first_uid = int(daemon.GetConnectionUnixUser(first_owner))
    second_owner = str(daemon.GetNameOwner(IBUS_BUS_NAME))
    second_pid = int(daemon.GetConnectionUnixProcessID(second_owner))
    second_uid = int(daemon.GetConnectionUnixUser(second_owner))
    if (
        first_owner != expected.owner
        or second_owner != first_owner
        or first_pid != expected.pid
        or second_pid != first_pid
        or first_uid != os.getuid()
        or second_uid != first_uid
    ):
        raise RuntimeError("IBus well-known name owner changed or is unexpected")
    proc = f"/proc/{first_pid}"
    try:
        if (
            os.stat(proc).st_uid != os.getuid()
            or os.readlink(f"{proc}/exe") != FCITX_PATH
        ):
            raise RuntimeError("IBus owner is not the exact installed Fcitx process")
        if proc_start_time(first_pid) != expected.start_time:
            raise RuntimeError("IBus owner Fcitx lifetime changed")
    except (FileNotFoundError, ProcessLookupError) as error:
        raise RuntimeError("IBus owner disappeared during input") from error


def release_fixture() -> tuple[str, str, str]:
    with open(FIXTURE_PATH, encoding="utf-8") as stream:
        fixture = json.load(stream)
    if set(fixture) != {
        "schema_version",
        "reading",
        "custom_value",
        "default_value",
        "state",
        "project",
    } or fixture.get("schema_version") != 1:
        raise RuntimeError("tracked release fixture is invalid")
    reading = fixture.get("reading")
    expected = fixture.get("custom_value")
    baseline = fixture.get("default_value")
    project = fixture.get("project")
    if (
        not isinstance(reading, str)
        or re.fullmatch(r"[a-z]+", reading) is None
        or len(reading) > MAX_READING_CHARACTERS
        or not isinstance(expected, str)
        or not expected
        or not isinstance(baseline, str)
        or not baseline
        or baseline == expected
        or baseline.strip() == reading
        or not isinstance(project, dict)
        or not isinstance(project.get("entries"), list)
        or len(project["entries"]) != 1
        or project["entries"][0].get("surface") != expected
    ):
        raise RuntimeError("tracked release fixture expectations are invalid")
    return reading, expected, baseline


def nested_strings(value: object) -> list[str]:
    if isinstance(value, (str, dbus.String, dbus.ObjectPath, dbus.Signature)):
        return [str(value)]
    if isinstance(value, Mapping):
        output: list[str] = []
        for key, item in value.items():
            output.extend(nested_strings(key))
            output.extend(nested_strings(item))
        return output
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        output = []
        for item in value:
            output.extend(nested_strings(item))
        return output
    return []


def commit_value(payload: object) -> str | None:
    values = [item for item in nested_strings(payload) if item not in TEXT_METADATA]
    return values[0] if len(values) == 1 else None


def pump_until(deadline: float, predicate) -> bool:
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    while context.pending():
        context.iteration(False)
    return bool(predicate())


def run_fcitx_remote(*arguments: str) -> str:
    try:
        result = subprocess.run(
            [FCITX_REMOTE_PATH, *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FCITX_REMOTE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise RuntimeError("fcitx5-remote is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("fcitx5-remote timed out") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError("fcitx5-remote failed") from error
    return result.stdout


def query_fcitx_snapshot() -> FcitxSnapshot:
    def query_once() -> FcitxSnapshot:
        input_method_lines = run_fcitx_remote("-n").splitlines()
        if len(input_method_lines) != 1 or not input_method_lines[0].strip():
            raise RuntimeError("could not identify the focused input method")
        input_method = input_method_lines[0].strip()
        if input_method != input_method_lines[0]:
            raise RuntimeError("focused input method contained surrounding whitespace")

        state_lines = run_fcitx_remote().splitlines()
        if len(state_lines) != 1 or state_lines[0] not in {"1", "2"}:
            raise RuntimeError("could not identify a restorable Fcitx state")
        return FcitxSnapshot(input_method=input_method, state=state_lines[0])

    first = query_once()
    second = query_once()
    if first != second:
        raise RuntimeError("Fcitx state changed while it was being captured")
    return first


def restore_fcitx_snapshot(expected: FcitxSnapshot) -> None:
    failures: list[str] = []
    try:
        run_fcitx_remote("-s", expected.input_method)
    except RuntimeError:
        failures.append("input_method")
    try:
        run_fcitx_remote("-o" if expected.state == "2" else "-c")
    except RuntimeError:
        failures.append("state")

    try:
        actual = query_fcitx_snapshot()
    except RuntimeError:
        failures.append("verification")
    else:
        if actual != expected:
            failures.append("verification_mismatch")

    if failures:
        raise RuntimeError(
            "failed to restore and verify Fcitx state: " + ",".join(failures)
        )


def install_termination_handlers() -> dict[signal.Signals, object]:
    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    previous: dict[signal.Signals, object] = {}
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, terminate)
    return previous


def restore_termination_handlers(previous: Mapping[signal.Signals, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def main() -> int:
    if os.getuid() == 0:
        raise RuntimeError("dogfood probe must run as the desktop user")
    expected_owner = expected_ibus_owner()
    identity_suffix = (
        f" ibus_owner={expected_owner.owner} fcitx_pid={expected_owner.pid} "
        f"fcitx_start_time={expected_owner.start_time}"
    )
    reading, custom, baseline = release_fixture()
    expectation = os.environ.get("MOZKEY_DOGFOOD_EXPECTATION", "custom")
    if expectation not in {"custom", "default"}:
        raise RuntimeError("MOZKEY_DOGFOOD_EXPECTATION must be custom or default")
    expected = custom if expectation == "custom" else baseline
    client_name = "grimodex" if expectation == "custom" else "mozkey-dogfood-ordinary"
    pause_after_text = os.environ.get("MOZKEY_DOGFOOD_PAUSE_AFTER", "")
    pause_after = int(pause_after_text) if pause_after_text else 0
    expected_commit_count = 1
    if pause_after < 0 or pause_after >= len(reading):
        if pause_after != 0:
            raise RuntimeError("MOZKEY_DOGFOOD_PAUSE_AFTER is out of range")
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    verify_ibus_owner(bus, expected_owner)
    root = dbus.Interface(
        bus.get_object(IBUS_BUS_NAME, IBUS_ROOT_PATH), IBUS_ROOT_INTERFACE
    )
    context_path = root.CreateInputContext(client_name)
    verify_ibus_owner(bus, expected_owner)
    context_object = bus.get_object(IBUS_BUS_NAME, context_path)
    context = dbus.Interface(context_object, IBUS_CONTEXT_INTERFACE)
    service = dbus.Interface(context_object, IBUS_SERVICE_INTERFACE)

    commit_payloads: list[object] = []
    forwarded_events: list[tuple[int, int, int]] = []
    consumed: list[bool] = []

    def on_commit(payload: object) -> None:
        commit_payloads.append(payload)

    def on_forward(keyval: int, keycode: int, state: int) -> None:
        forwarded_events.append((int(keyval), int(keycode), int(state)))

    commit_match = context_object.connect_to_signal(
        "CommitText", on_commit, dbus_interface=IBUS_CONTEXT_INTERFACE
    )
    forward_match = context_object.connect_to_signal(
        "ForwardKeyEvent", on_forward, dbus_interface=IBUS_CONTEXT_INTERFACE
    )
    try:
        context.SetCapabilities(dbus.UInt32(IBUS_CAPABILITIES))
        context.SetEngine(IBUS_ENGINE)
        context.Enable()
        context.FocusIn()
        pump_until(time.monotonic() + 0.25, lambda: False)
        # fcitx5-remote operates on the focused context. Capture the disposable
        # probe context before issuing the first command that mutates it.
        previous_fcitx = query_fcitx_snapshot()
        previous_handlers = install_termination_handlers()
        try:
            run_fcitx_remote("-s", "mozkey-ibg")
            run_fcitx_remote("-o")
            pump_until(time.monotonic() + 0.25, lambda: False)
            requested_fcitx = query_fcitx_snapshot()
            if requested_fcitx != FcitxSnapshot(input_method="mozkey-ibg", state="2"):
                raise RuntimeError("requested Mozkey Fcitx identity was not established")
            verify_ibus_owner(bus, expected_owner)

            for index, character in enumerate(reading, start=1):
                consumed.append(
                    bool(
                        context.ProcessKeyEvent(
                            dbus.UInt32(ord(character)),
                            dbus.UInt32(0),
                            dbus.UInt32(0),
                        )
                    )
                )
                if pause_after and index == pause_after:
                    print(
                        f"READY:partial_keys={pause_after} "
                        f"consumed={sum(consumed)}/{len(consumed)}"
                        + identity_suffix,
                        flush=True,
                    )
                    verify_ibus_owner(bus, expected_owner)
                    ready, _, _ = select.select(
                        [sys.stdin], [], [], CONTROL_TIMEOUT_SECONDS
                    )
                    if not ready:
                        raise RuntimeError("fault probe control input timed out")
                    if sys.stdin.readline() == "":
                        raise RuntimeError("fault probe control input closed")
                    verify_ibus_owner(bus, expected_owner)
            consumed.append(
                bool(context.ProcessKeyEvent(dbus.UInt32(ord(" ")), 0, 0))
            )
            consumed.append(
                bool(context.ProcessKeyEvent(dbus.UInt32(RETURN_KEYSYM), 0, 0))
            )

            pump_until(
                time.monotonic() + 5.0,
                lambda: len(commit_payloads) >= expected_commit_count,
            )
            pump_until(time.monotonic() + 0.25, lambda: False)
            verify_ibus_owner(bus, expected_owner)
            commit_values = [commit_value(payload) for payload in commit_payloads]
            exact_commit = (
                len(commit_payloads) == expected_commit_count
                and all(value is not None for value in commit_values)
                and "".join(value for value in commit_values if value is not None)
                == expected
            )
            passed = exact_commit and all(consumed) and not forwarded_events
            if not passed:
                fingerprints = [
                    None
                    if value is None
                    else {
                        "chars": len(value),
                        "sha256": hashlib.sha256(value.encode()).hexdigest(),
                    }
                    for value in commit_values
                ]
                result_line = (
                    "RESULT:match=false "
                    f"expectation={expectation} "
                    f"commit_count={len(commit_payloads)} "
                    f"consumed={sum(consumed)}/{len(consumed)} "
                    f"forwarded={len(forwarded_events)} "
                    f"commit_fingerprints={fingerprints}"
                    + identity_suffix
                )
                result = 1
            else:
                result = 0
                result_line = (
                    "RESULT:match=true "
                    f"expectation={expectation} "
                    f"commit_count={expected_commit_count} "
                    f"consumed={len(consumed)}/{len(consumed)} forwarded=0"
                    + identity_suffix
                )
        finally:
            try:
                restore_fcitx_snapshot(previous_fcitx)
            finally:
                restore_termination_handlers(previous_handlers)
        verify_ibus_owner(bus, expected_owner)
        print(result_line)
        return result
    finally:
        try:
            context.FocusOut()
        finally:
            commit_match.remove()
            forward_match.remove()
            service.Destroy()


if __name__ == "__main__":
    raise SystemExit(main())
