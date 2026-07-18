#!/usr/bin/python3 -I
"""Fail-closed mixed-literal product probe for one real Fcitx IBus context."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import dbus


IBUS_BUS_NAME = "org.freedesktop.IBus"
DBUS_BUS_NAME = "org.freedesktop.DBus"
DBUS_ROOT_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"
IBUS_ROOT_PATH = "/org/freedesktop/IBus"
IBUS_ROOT_INTERFACE = "org.freedesktop.IBus"
IBUS_CONTEXT_INTERFACE = "org.freedesktop.IBus.InputContext"
IBUS_SERVICE_INTERFACE = "org.freedesktop.IBus.Service"
IBUS_CAPABILITIES = 0x3F  # Includes IBUS_CAP_SURROUNDING_TEXT.
IBUS_ENGINE = "mozkey"
FCITX_REMOTE_PATH = "/usr/bin/fcitx5-remote"
FCITX_PATH = "/usr/bin/fcitx5"
FCITX_REMOTE_TIMEOUT_SECONDS = 5.0
SIGNAL_TIMEOUT_SECONDS = 5.0
SETTLE_SECONDS = 0.20

EISU_KEYSYM = 0xFF30
HIRAGANA_KEYSYM = 0xFF25
HENKAN_KEYSYM = 0xFF23
HOME_KEYSYM = 0xFF50
LEFT_KEYSYM = 0xFF51
RIGHT_KEYSYM = 0xFF53
END_KEYSYM = 0xFF57
RETURN_KEYSYM = 0xFF0D
SHIFT_MASK = 1 << 0

IBUS_ATTRIBUTE_TYPES = frozenset({1, 2, 3})
EXPECTED_LITERAL = "RUST_LOG=debug"
EXPECTED_ROMANIZED_SUFFIX = "dekuwashiiloguwodasu"
EXPECTED_KANA_SUFFIX = "でくわしいろぐをだす"
EXPECTED_RAW_PREEDIT = EXPECTED_LITERAL + EXPECTED_KANA_SUFFIX
EXPECTED_CONVERTED_VALUE = "RUST_LOG=debugで詳しいログを出す"
FIXTURE_PATH = Path(__file__).resolve().with_name("protected_surface_fixture.json")


@dataclass(frozen=True)
class FcitxSnapshot:
    input_method: str
    state: str


@dataclass(frozen=True)
class ExpectedIBusOwner:
    owner: str
    pid: int
    start_time: str


@dataclass(frozen=True)
class ProtectedSurfaceFixture:
    literal: str
    romanized_suffix: str
    kana_suffix: str
    raw_preedit: str
    converted_value: str


@dataclass(frozen=True)
class PreeditEvent:
    text: str
    cursor: int
    visible: bool
    payload_sha256: str
    segment_ranges: tuple[tuple[int, int], ...]


@dataclass
class HostBuffer:
    text: str
    cursor: int
    anchor: int

    def apply_delete(self, offset: int, length: int) -> None:
        if length <= 0 or self.cursor == self.anchor:
            raise RuntimeError(
                "reconversion deletion must replace the active selection"
            )
        start = self.cursor + offset
        end = start + length
        selection_start = min(self.cursor, self.anchor)
        selection_end = max(self.cursor, self.anchor)
        if (
            start < 0
            or end > len(self.text)
            or start > end
            or start != selection_start
            or end != selection_end
        ):
            raise RuntimeError("reconversion deletion is outside the host buffer")
        self.text = self.text[:start] + self.text[end:]
        self.cursor = start
        self.anchor = start

    def apply_commit(self, value: str) -> None:
        if self.cursor != self.anchor:
            raise RuntimeError("reconversion committed before deleting the selection")
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)
        self.anchor = self.cursor


def load_fixture(path: Path = FIXTURE_PATH) -> ProtectedSurfaceFixture:
    with path.open(encoding="utf-8") as stream:
        document = json.load(stream)
    expected = {
        "schema_version": 1,
        "literal": EXPECTED_LITERAL,
        "romanized_suffix": EXPECTED_ROMANIZED_SUFFIX,
        "kana_suffix": EXPECTED_KANA_SUFFIX,
        "raw_preedit": EXPECTED_RAW_PREEDIT,
        "converted_value": EXPECTED_CONVERTED_VALUE,
    }
    if not isinstance(document, dict) or document != expected:
        raise RuntimeError(
            "tracked protected-surface fixture is not the exact product case"
        )
    if (
        document["raw_preedit"]
        != document["literal"] + document["kana_suffix"]
        or not document["converted_value"].startswith(document["literal"])
        or document["converted_value"] == document["raw_preedit"]
        or not document["literal"].isascii()
        or len(document["literal"].encode("utf-8")) != len(document["literal"])
    ):
        raise RuntimeError("tracked protected-surface fixture invariants are invalid")
    return ProtectedSurfaceFixture(
        literal=document["literal"],
        romanized_suffix=document["romanized_suffix"],
        kana_suffix=document["kana_suffix"],
        raw_preedit=document["raw_preedit"],
        converted_value=document["converted_value"],
    )


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


def ibus_sequence(value: object, label: str) -> tuple[object, ...]:
    if (
        isinstance(
            value,
            (str, dbus.String, dbus.ObjectPath, dbus.Signature, bytes, bytearray),
        )
        or isinstance(value, Mapping)
        or not isinstance(value, Iterable)
    ):
        raise RuntimeError(f"{label} is not an exact IBus sequence")
    return tuple(value)


def unpack_ibus_text(payload: object) -> tuple[str, tuple[object, ...]]:
    text_parts = ibus_sequence(payload, "IBusText")
    if (
        len(text_parts) != 4
        or str(text_parts[0]) != "IBusText"
        or not isinstance(text_parts[1], Mapping)
        or not isinstance(text_parts[2], (str, dbus.String))
    ):
        raise RuntimeError("IBusText has an invalid exact structure")
    attr_list = ibus_sequence(text_parts[3], "IBusAttrList")
    if (
        len(attr_list) != 3
        or str(attr_list[0]) != "IBusAttrList"
        or not isinstance(attr_list[1], Mapping)
    ):
        raise RuntimeError("IBusAttrList has an invalid exact structure")
    return str(text_parts[2]), ibus_sequence(attr_list[2], "IBus attributes")


def ibus_text_value(payload: object) -> str | None:
    try:
        value, _attributes = unpack_ibus_text(payload)
    except RuntimeError:
        return None
    return value


def ibus_segment_ranges(
    payload: object, expected_text: str
) -> tuple[tuple[int, int], ...]:
    value, raw_attributes = unpack_ibus_text(payload)
    if value != expected_text:
        raise RuntimeError("IBusText value changed while parsing segment attributes")
    ranges: set[tuple[int, int]] = set()
    for raw_attribute in raw_attributes:
        attribute = ibus_sequence(raw_attribute, "IBusAttribute")
        if (
            len(attribute) != 6
            or str(attribute[0]) != "IBusAttribute"
            or not isinstance(attribute[1], Mapping)
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in attribute[2:]
            )
        ):
            raise RuntimeError("IBusAttribute has an invalid exact structure")
        attribute_type, attribute_value, start, end = (
            int(item) for item in attribute[2:]
        )
        if (
            attribute_type not in IBUS_ATTRIBUTE_TYPES
            or attribute_value < 0
            or attribute_value > 0xFFFFFFFF
            or start < 0
            or start >= end
            or end > len(value)
        ):
            raise RuntimeError("IBusAttribute range or value is invalid")
        ranges.add((start, end))
    return tuple(sorted(ranges))


def require_segment_partition(event: PreeditEvent, label: str) -> None:
    if not event.text or not event.segment_ranges:
        raise RuntimeError(f"{label} has no observable segment ranges")
    boundary = 0
    for start, end in event.segment_ranges:
        if start != boundary:
            raise RuntimeError(f"{label} segment ranges are not an exact partition")
        boundary = end
    if boundary != len(event.text):
        raise RuntimeError(f"{label} segment ranges do not cover the preedit")


def has_segment_boundary_change(
    baseline: PreeditEvent, event: PreeditEvent
) -> bool:
    return event.segment_ranges != baseline.segment_ranges


def canonical_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): canonical_payload(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (str, dbus.String, dbus.ObjectPath, dbus.Signature)):
        return str(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return [canonical_payload(item) for item in value]
    if value is None:
        return None
    raise RuntimeError("IBus payload contains an unsupported value")


def payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        canonical_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def serialized_ibus_text(value: str) -> dbus.Struct:
    attributes = dbus.Struct(
        (
            dbus.String("IBusAttrList"),
            dbus.Dictionary({}, signature="sv"),
            dbus.Array([], signature="v"),
        ),
        signature="sa{sv}av",
        variant_level=1,
    )
    return dbus.Struct(
        (
            dbus.String("IBusText"),
            dbus.Dictionary({}, signature="sv"),
            dbus.String(value),
            attributes,
        ),
        signature="sa{sv}sv",
        variant_level=1,
    )


def require_literal_prefix(event: PreeditEvent, literal: str) -> None:
    if not event.visible or not event.text:
        return
    literal_bytes = literal.encode("utf-8")
    if event.text.encode("utf-8")[: len(literal_bytes)] != literal_bytes:
        raise RuntimeError("protected literal bytes changed in visible preedit")


def pump_until(deadline: float, predicate) -> bool:
    from gi.repository import GLib

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


def pump_for(seconds: float) -> None:
    pump_until(time.monotonic() + seconds, lambda: False)


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
    from dbus.mainloop.glib import DBusGMainLoop

    if os.getuid() == 0:
        raise RuntimeError("protected-surface probe must run as the desktop user")
    fixture = load_fixture()
    expected_owner = expected_ibus_owner()
    identity_suffix = (
        f" ibus_owner={expected_owner.owner} fcitx_pid={expected_owner.pid} "
        f"fcitx_start_time={expected_owner.start_time}"
    )

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    verify_ibus_owner(bus, expected_owner)
    root = dbus.Interface(
        bus.get_object(IBUS_BUS_NAME, IBUS_ROOT_PATH), IBUS_ROOT_INTERFACE
    )
    context_path = root.CreateInputContext("mozkey-protected-surface-dogfood")
    verify_ibus_owner(bus, expected_owner)
    context_object = bus.get_object(IBUS_BUS_NAME, context_path)
    context = dbus.Interface(context_object, IBUS_CONTEXT_INTERFACE)
    service = dbus.Interface(context_object, IBUS_SERVICE_INTERFACE)

    preedit_events: list[PreeditEvent] = []
    commit_values: list[str] = []
    deletion_events: list[tuple[int, int]] = []
    forwarded_events: list[tuple[int, int, int]] = []
    consumed_events: list[bool] = []
    callback_failures: list[str] = []
    chronology: list[str] = []
    host: HostBuffer | None = None

    def on_preedit(payload: object, cursor: int, visible: bool) -> None:
        try:
            value = ibus_text_value(payload)
            cursor_value = int(cursor)
            if (
                value is None
                or cursor_value < 0
                or cursor_value > len(value)
            ):
                raise RuntimeError("preedit signal is not a valid exact IBusText")
            preedit_events.append(
                PreeditEvent(
                    text=value,
                    cursor=cursor_value,
                    visible=bool(visible),
                    payload_sha256=payload_sha256(payload),
                    segment_ranges=ibus_segment_ranges(payload, value),
                )
            )
        except BaseException as error:
            callback_failures.append(f"preedit:{type(error).__name__}")

    def on_commit(payload: object) -> None:
        nonlocal host
        try:
            value = ibus_text_value(payload)
            if value is None:
                raise RuntimeError("commit signal is not an exact IBusText")
            chronology.append("commit")
            commit_values.append(value)
            if host is not None:
                host.apply_commit(value)
        except BaseException as error:
            callback_failures.append(f"commit:{type(error).__name__}")

    def on_delete(offset: int, length: int) -> None:
        nonlocal host
        try:
            event = (int(offset), int(length))
            chronology.append("delete")
            deletion_events.append(event)
            if host is None:
                raise RuntimeError("unexpected deletion before reconversion")
            host.apply_delete(*event)
        except BaseException as error:
            callback_failures.append(f"delete:{type(error).__name__}")

    def on_forward(keyval: int, keycode: int, state: int) -> None:
        forwarded_events.append((int(keyval), int(keycode), int(state)))

    def require_callbacks_clean() -> None:
        if callback_failures:
            raise RuntimeError(
                "IBus callback validation failed: "
                + ",".join(callback_failures)
            )

    def wait_for_preedit(start: int, predicate, label: str) -> PreeditEvent:
        found = pump_until(
            time.monotonic() + SIGNAL_TIMEOUT_SECONDS,
            lambda: bool(callback_failures)
            or any(predicate(event) for event in preedit_events[start:]),
        )
        require_callbacks_clean()
        if not found:
            raise RuntimeError(f"timed out waiting for {label}")
        pump_for(SETTLE_SECONDS)
        require_callbacks_clean()
        visible = [event for event in preedit_events[start:] if event.visible]
        if not visible or not predicate(visible[-1]):
            raise RuntimeError(f"{label} did not remain the latest visible preedit")
        return visible[-1]

    def observe_preedit_change(
        start: int, baseline: PreeditEvent, label: str
    ) -> PreeditEvent | None:
        pump_until(
            time.monotonic() + 1.0,
            lambda: bool(callback_failures)
            or any(event.visible for event in preedit_events[start:]),
        )
        require_callbacks_clean()
        pump_for(SETTLE_SECONDS)
        require_callbacks_clean()
        visible = [event for event in preedit_events[start:] if event.visible]
        if not visible:
            return None
        event = visible[-1]
        require_literal_prefix(event, fixture.literal)
        require_segment_partition(event, label)
        if not has_segment_boundary_change(baseline, event):
            if event.text != baseline.text:
                raise RuntimeError(
                    f"{label} changed text without changing a segment boundary"
                )
            return None
        if not event.text.startswith(fixture.literal):
            raise RuntimeError(f"{label} changed protected literal bytes")
        return event

    def wait_for_count(values: list[object], count: int, label: str) -> None:
        found = pump_until(
            time.monotonic() + SIGNAL_TIMEOUT_SECONDS,
            lambda: bool(callback_failures) or len(values) >= count,
        )
        require_callbacks_clean()
        if not found:
            raise RuntimeError(f"timed out waiting for {label}")
        pump_for(SETTLE_SECONDS)
        require_callbacks_clean()

    def send_key(keyval: int, state: int = 0) -> None:
        consumed = bool(
            context.ProcessKeyEvent(
                dbus.UInt32(keyval), dbus.UInt32(0), dbus.UInt32(state)
            )
        )
        consumed_events.append(consumed)
        if not consumed:
            raise RuntimeError("required protected-surface key was not consumed")
        pump_for(0.01)
        require_callbacks_clean()

    def require_protected_events(start: int) -> None:
        require_callbacks_clean()
        visible = [
            event for event in preedit_events[start:] if event.visible and event.text
        ]
        if not visible:
            raise RuntimeError("protected-surface stage emitted no visible preedit")
        for event in visible:
            require_literal_prefix(event, fixture.literal)

    signal_matches = [
        context_object.connect_to_signal(
            "UpdatePreeditText", on_preedit, dbus_interface=IBUS_CONTEXT_INTERFACE
        ),
        context_object.connect_to_signal(
            "CommitText", on_commit, dbus_interface=IBUS_CONTEXT_INTERFACE
        ),
        context_object.connect_to_signal(
            "DeleteSurroundingText", on_delete, dbus_interface=IBUS_CONTEXT_INTERFACE
        ),
        context_object.connect_to_signal(
            "ForwardKeyEvent", on_forward, dbus_interface=IBUS_CONTEXT_INTERFACE
        ),
    ]

    try:
        context.SetCapabilities(dbus.UInt32(IBUS_CAPABILITIES))
        context.SetEngine(IBUS_ENGINE)
        context.Enable()
        context.FocusIn()
        pump_for(0.25)
        previous_fcitx = query_fcitx_snapshot()
        previous_handlers = install_termination_handlers()
        try:
            run_fcitx_remote("-s", IBUS_ENGINE)
            run_fcitx_remote("-o")
            pump_for(0.25)
            if query_fcitx_snapshot() != FcitxSnapshot(IBUS_ENGINE, "2"):
                raise RuntimeError(
                    "requested Mozkey Fcitx identity was not established"
                )
            verify_ibus_owner(bus, expected_owner)

            # Keep the ASCII literal and kana reading in one Mozc composition.
            send_key(HIRAGANA_KEYSYM)
            send_key(EISU_KEYSYM)
            for character in fixture.literal:
                send_key(ord(character))
            literal_checkpoint = wait_for_preedit(
                0,
                lambda event: event.visible
                and event.text == fixture.literal
                and event.cursor == len(fixture.literal),
                "exact protected literal",
            )
            require_literal_prefix(literal_checkpoint, fixture.literal)
            protected_event_start = len(preedit_events)

            send_key(EISU_KEYSYM)
            for character in fixture.romanized_suffix:
                send_key(ord(character))
            mixed = wait_for_preedit(
                protected_event_start,
                lambda event: event.visible
                and event.text == fixture.raw_preedit
                and event.cursor == len(fixture.raw_preedit),
                "exact tracked mixed raw preedit",
            )
            require_protected_events(protected_event_start)
            verify_ibus_owner(bus, expected_owner)

            # Prove that cursor traversal through the protected prefix cannot
            # rewrite it.  The boundary is ASCII, so byte and character index
            # are both exactly len(literal).
            cursor_event_start = len(preedit_events)
            send_key(HOME_KEYSYM)
            wait_for_preedit(
                cursor_event_start,
                lambda event: event.visible
                and event.text == fixture.raw_preedit
                and event.cursor == 0,
                "composition cursor at beginning",
            )
            for _ in range(len(fixture.literal)):
                send_key(RIGHT_KEYSYM)
            boundary_event = wait_for_preedit(
                cursor_event_start,
                lambda event: event.visible
                and event.text == fixture.raw_preedit
                and event.cursor == len(fixture.literal),
                "composition cursor at protected boundary",
            )
            require_literal_prefix(boundary_event, fixture.literal)
            end_event_start = len(preedit_events)
            send_key(END_KEYSYM)
            wait_for_preedit(
                end_event_start,
                lambda event: event.visible
                and event.text == fixture.raw_preedit
                and event.cursor == len(fixture.raw_preedit)
                and event.payload_sha256 == mixed.payload_sha256,
                "composition cursor exact round-trip",
            )
            require_protected_events(cursor_event_start)
            verify_ibus_owner(bus, expected_owner)

            # Enter explicit conversion, resize the focused segment in both
            # directions, and require an exact round-trip before commit.
            conversion_event_start = len(preedit_events)
            send_key(ord(" "))
            conversion = wait_for_preedit(
                conversion_event_start,
                lambda event: event.visible
                and event.text == fixture.converted_value,
                "exact adjacent-kana conversion",
            )
            require_protected_events(conversion_event_start)

            focus_event_start = len(preedit_events)
            send_key(HOME_KEYSYM)
            conversion = wait_for_preedit(
                focus_event_start,
                lambda event: event.visible
                and event.text == fixture.converted_value,
                "first conversion segment focus",
            )
            require_segment_partition(conversion, "baseline conversion")

            resize_event_start = len(preedit_events)
            first_resize_start = len(preedit_events)
            send_key(LEFT_KEYSYM, SHIFT_MASK)
            resized = observe_preedit_change(
                first_resize_start, conversion, "segment shrink"
            )
            if resized is not None:
                inverse_key = RIGHT_KEYSYM
            else:
                second_resize_start = len(preedit_events)
                send_key(RIGHT_KEYSYM, SHIFT_MASK)
                resized = observe_preedit_change(
                    second_resize_start, conversion, "segment expand"
                )
                if resized is None:
                    raise RuntimeError(
                        "focused segment could not be resized in either direction"
                    )
                inverse_key = LEFT_KEYSYM

            inverse_event_start = len(preedit_events)
            send_key(inverse_key, SHIFT_MASK)
            expanded = wait_for_preedit(
                inverse_event_start,
                lambda event: event.visible
                and event.text == fixture.converted_value
                and event.cursor == conversion.cursor
                and event.payload_sha256 == conversion.payload_sha256
                and event.segment_ranges == conversion.segment_ranges,
                "segment resize exact round-trip",
            )
            require_segment_partition(expanded, "round-trip conversion")
            require_literal_prefix(expanded, fixture.literal)
            require_protected_events(resize_event_start)
            require_protected_events(protected_event_start)
            verify_ibus_owner(bus, expected_owner)

            send_key(RETURN_KEYSYM)
            wait_for_count(commit_values, 1, "first exact commit")
            if (
                commit_values != [fixture.converted_value]
                or deletion_events
                or chronology != ["commit"]
            ):
                raise RuntimeError("initial stitched commit was not exact")
            verify_ibus_owner(bus, expected_owner)

            # Reconvert the exact committed selection in the same context.
            host = HostBuffer(
                text=fixture.converted_value,
                cursor=len(fixture.converted_value),
                anchor=0,
            )
            context.SetSurroundingText(
                serialized_ibus_text(fixture.converted_value),
                dbus.UInt32(len(fixture.converted_value)),
                dbus.UInt32(0),
            )
            pump_for(SETTLE_SECONDS)
            reconversion_event_start = len(preedit_events)
            send_key(HENKAN_KEYSYM)
            reconversion = wait_for_preedit(
                reconversion_event_start,
                lambda event: event.visible
                and event.text == fixture.converted_value,
                "exact protected reconversion preedit",
            )
            wait_for_count(deletion_events, 1, "reconversion deletion")
            require_literal_prefix(reconversion, fixture.literal)
            require_protected_events(reconversion_event_start)
            expected_deletion = (
                -len(fixture.converted_value),
                len(fixture.converted_value),
            )
            if (
                deletion_events != [expected_deletion]
                or host.text
                or host.cursor != 0
                or host.anchor != 0
                or chronology != ["commit", "delete"]
            ):
                raise RuntimeError(
                    "reconversion did not delete the exact selected surface"
                )

            send_key(RETURN_KEYSYM)
            wait_for_count(commit_values, 2, "reconversion exact commit")
            if (
                commit_values != [fixture.converted_value, fixture.converted_value]
                or deletion_events != [expected_deletion]
                or chronology != ["commit", "delete", "commit"]
                or host.text != fixture.converted_value
                or host.cursor != len(fixture.converted_value)
                or host.anchor != host.cursor
            ):
                raise RuntimeError("reconversion host-buffer round-trip was not exact")

            pump_for(SETTLE_SECONDS)
            require_callbacks_clean()
            verify_ibus_owner(bus, expected_owner)
            if not consumed_events or not all(consumed_events) or forwarded_events:
                raise RuntimeError(
                    "protected-surface input was forwarded or unconsumed"
                )
            if len(commit_values) != 2 or len(deletion_events) != 1:
                raise RuntimeError("protected-surface signal cardinality changed")

            literal_sha256 = hashlib.sha256(fixture.literal.encode("utf-8")).hexdigest()
            raw_sha256 = hashlib.sha256(
                fixture.raw_preedit.encode("utf-8")
            ).hexdigest()
            converted_sha256 = hashlib.sha256(
                fixture.converted_value.encode("utf-8")
            ).hexdigest()
            result_line = (
                "RESULT:protected_surface_exact_pass contexts=1 "
                "literal_bytes=preserved adjacent_kana=converted "
                f"cursor_boundary={len(fixture.literal)} "
                "resize_roundtrip=exact commit_count=2 deletion_count=1 "
                "reconversion=exact "
                f"literal_sha256={literal_sha256} "
                f"raw_sha256={raw_sha256} "
                f"converted_sha256={converted_sha256}"
                + identity_suffix
            )
        finally:
            try:
                restore_fcitx_snapshot(previous_fcitx)
            finally:
                restore_termination_handlers(previous_handlers)
        verify_ibus_owner(bus, expected_owner)
        print(result_line)
        return 0
    finally:
        try:
            context.FocusOut()
        finally:
            for match in signal_matches:
                match.remove()
            service.Destroy()


if __name__ == "__main__":
    raise SystemExit(main())
