#!/usr/bin/python3
"""Policy tests for fail-closed AT-SPI dogfood surface focusing."""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.release.linux_dogfood import atspi_surface_locator as locator
from tools.release.linux_dogfood import run_gui_scope_gate as gate


class AtspiSurfaceLocatorPolicyTest(unittest.TestCase):
    def target(self) -> gate.ProcessIdentity:
        return gate.ProcessIdentity(
            pid=321,
            start_time="987654",
            executable="/tmp/mozkey-probe",
            command=("/tmp/mozkey-probe",),
        )

    def payload(self, toolkit: str = "gtk", secure: bool = False) -> dict[str, object]:
        titles = {
            ("gtk", False): "Mozkey GTK Probe",
            ("gtk", True): "Mozkey GTK Password Probe",
            ("qt", False): "Mozkey Qt Probe",
            ("qt", True): "Mozkey Qt Password Probe",
        }
        return {
            "schemaVersion": 1,
            "toolkit": toolkit,
            "toolkitName": toolkit,
            "targetPid": 321,
            "targetStartTime": "987654",
            "accessiblePid": 321,
            "accessibleStartTime": "987654",
            "ownerUid": os.getuid(),
            "role": "password text" if secure and toolkit == "qt" else "text",
            "title": titles[(toolkit, secure)],
            "x": 100,
            "y": 200,
            "width": 720,
            "height": 120,
            "clickX": 460,
            "clickY": 260,
        }

    def transcript(self, payload: dict[str, object]) -> str:
        return "SURFACE:" + json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ) + "\n"

    def test_accepts_exact_gtk_and_qt_text_and_password_surfaces(self) -> None:
        for toolkit in ("gtk", "qt"):
            for secure in (False, True):
                with self.subTest(toolkit=toolkit, secure=secure):
                    evidence = gate.parse_surface_evidence(
                        0,
                        self.transcript(self.payload(toolkit, secure)),
                        "",
                        toolkit=toolkit,
                        secure=secure,
                        target=self.target(),
                    )
                    self.assertEqual(evidence.accessible_pid, 321)
                    self.assertEqual(evidence.click_x, 460)
                    self.assertEqual(evidence.click_y, 260)

    def test_rejects_owner_role_title_extent_and_schema_drift(self) -> None:
        mutations = (
            ("accessiblePid", 322),
            ("ownerUid", os.getuid() + 1),
            ("role", "password text"),
            ("title", "Mozkey GTK Probe (stale)"),
            ("width", 0),
            ("clickX", 461),
            ("schemaVersion", 2),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value
                with self.assertRaises(RuntimeError):
                    gate.parse_surface_evidence(
                        0,
                        self.transcript(payload),
                        "",
                        toolkit="gtk",
                        secure=False,
                        target=self.target(),
                    )

    def test_rejects_inexact_or_oversized_locator_transcript(self) -> None:
        valid = self.transcript(self.payload())
        for output, error, status in (
            (valid + "SURFACE:{}\n", "", 0),
            (valid, "unexpected", 0),
            (valid, "", 1),
            ("SURFACE:" + "x" * 5000 + "\n", "", 0),
        ):
            with self.subTest(status=status, error=error, size=len(output)):
                with self.assertRaises(RuntimeError):
                    gate.parse_surface_evidence(
                        status,
                        output,
                        error,
                        toolkit="gtk",
                        secure=False,
                        target=self.target(),
                    )

    def test_extent_policy_is_bounded_and_uses_the_center(self) -> None:
        self.assertEqual(locator.validate_extents(100, 200, 720, 120), (460, 260))
        for extents in (
            (0, 0, 0, 1),
            (0, 0, 1, 0),
            (locator.MAX_COORDINATE, 0, 2, 1),
            (0, locator.MAX_COORDINATE, 1, 2),
            (0, 0, locator.MAX_DIMENSION + 1, 1),
        ):
            with self.subTest(extents=extents):
                with self.assertRaises(RuntimeError):
                    locator.validate_extents(*extents)

    def test_exact_process_policy_and_electron_descendant_policy_are_separate(
        self,
    ) -> None:
        target = locator.ProcessIdentity(321, "9", "/tmp/probe", os.getuid())
        self.assertTrue(locator.matching_process("gtk", 321, target))
        self.assertFalse(locator.matching_process("qt", 322, target))
        with mock.patch.object(
            locator, "is_descendant", return_value=True
        ) as descendant:
            self.assertTrue(locator.matching_process("electron", 654, target))
        descendant.assert_called_once_with(654, 321, os.getuid())

    def test_toolkit_specific_role_contract_matches_the_probe_widgets(self) -> None:
        roles = SimpleNamespace(
            TEXT=object(), PASSWORD_TEXT=object(), ENTRY=object()
        )
        atspi = SimpleNamespace(Role=roles)
        self.assertEqual(locator.expected_roles(atspi, "gtk", False), (roles.TEXT,))
        self.assertEqual(locator.expected_roles(atspi, "gtk", True), (roles.TEXT,))
        self.assertEqual(locator.expected_roles(atspi, "qt", False), (roles.TEXT,))
        self.assertEqual(
            locator.expected_roles(atspi, "qt", True), (roles.PASSWORD_TEXT,)
        )
        self.assertEqual(
            locator.expected_roles(atspi, "electron", False),
            (roles.ENTRY, roles.TEXT),
        )

    def test_focus_path_is_locate_move_relocate_click(self) -> None:
        evidence = gate.parse_surface_evidence(
            0,
            self.transcript(self.payload()),
            "",
            toolkit="gtk",
            secure=False,
            target=self.target(),
        )
        events: list[tuple[str, tuple[str, ...] | None]] = []

        def locate(*_args, **_kwargs):
            events.append(("locate", None))
            return evidence

        def pointer(_runner, arguments, _environment, _deadline, **_kwargs):
            events.append(("pointer", tuple(arguments)))

        with (
            mock.patch.object(
                gate, "locate_probe_surface", side_effect=locate
            ),
            mock.patch.object(
                gate, "run_verified_pointer_command", side_effect=pointer
            ),
        ):
            actual = gate.focus_probe_surface(
                Path("/snapshot/locator"),
                Path("/snapshot/runner"),
                "gtk",
                False,
                self.target(),
                {},
                100.0,
            )
        self.assertEqual(actual, evidence)
        self.assertEqual(
            events,
            [
                ("locate", None),
                ("pointer", ("mousemove", "--absolute", "460", "260")),
                ("locate", None),
                ("pointer", ("click", "0xC0")),
                ("locate", None),
            ],
        )

    def test_locator_source_never_calls_atspi_grab_focus(self) -> None:
        source = Path(locator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("grab_focus(", source)
        self.assertIn("CoordType.SCREEN", source)
        self.assertIn("pidfd_open", source)
        self.assertIn("MAX_NODES", source)
        self.assertIn("MAX_OUTPUT_BYTES", source)
        self.assertIn('parser.add_argument("--require-focused"', source)
        self.assertIn("atspi.StateType.FOCUSED", source)

    def test_gui_gate_focuses_before_ready_and_rechecks_after_input(self) -> None:
        source = Path(gate.__file__).read_text(encoding="utf-8")
        focus = source.index("surface_evidence = focus_probe_surface(")
        ready = source.index(
            'ready_line = capture.next_stdout_line(deadline, "GUI probe readiness")'
        )
        sequence = source.index("helper_status, helper_output, helper_error")
        final_surface = source.index("final_surface = locate_probe_surface(")
        self.assertLess(focus, ready)
        self.assertLess(ready, sequence)
        self.assertLess(sequence, final_surface)
        self.assertIn('"surfaceLocatorBlob": surface_locator_blob', source)
        self.assertIn('"focusMethod": "atspi-screen-extents+verified-ydotool"', source)

    def test_electron_uses_descendant_surface_before_ready(self) -> None:
        source = Path(__file__).with_name("electron_scope_probe.mjs").read_text(
            encoding="utf-8"
        )
        focus = source.index("surfaceEvidence = await focusElectronSurface();")
        ready = source.index(
            "`READY:${JSON.stringify({ kind, scopeMode, active: true, "
            "focused: true })}\\n`"
        )
        sequence = source.index("sequence = await runSequenceHelper(", ready)
        final_surface = source.index(
            "const finalSurface = await locateElectronSurface(true);"
        )
        self.assertLess(focus, ready)
        self.assertLess(ready, sequence)
        self.assertLess(sequence, final_surface)
        self.assertIn(
            'const launchArguments = ["--force-renderer-accessibility"]', source
        )
        self.assertIn("surfaceProcess.executable !== executablePath", source)
        self.assertIn("evidence.x + evidence.width > 131072", source)
        self.assertIn("evidence.clickX > 131072", source)
        self.assertIn("surfaceLocator: verifyTrackedFile(", source)
        self.assertIn("let helperEnvironment;", source)
        self.assertIn("helperEnvironment = {", source)
        self.assertNotIn("const helperEnvironment = {", source)


class SnapshotMutationGuardPolicyTest(unittest.TestCase):
    def make_guard(
        self,
    ) -> tuple[
        gate.SnapshotMutationGuard,
        Path,
        tuple[tuple[Path, bytes, int], ...],
    ]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve() / "tracked-inputs"
        directory.mkdir(mode=0o700)
        names_and_modes = (
            ("gtk_probe.c", 0o400),
            ("send_ime_sequence.sh", 0o500),
            ("verify_ydotool_socket.py", 0o500),
            ("run_verified_ydotool.py", 0o500),
            ("atspi_surface_locator.py", 0o500),
            ("verify_installed_candidate.py", 0o500),
            ("linux_build_attestation.py", 0o500),
            ("normalize_zenz_gguf.py", 0o400),
            ("release_fixture.json", 0o400),
        )
        files: list[tuple[Path, bytes, int]] = []
        specifications: list[tuple[Path, str, int]] = []
        for index, (name, mode) in enumerate(names_and_modes):
            path = directory / name
            payload = f"snapshot-{index}\n".encode("ascii")
            path.write_bytes(payload)
            path.chmod(mode)
            files.append((path, payload, mode))
            specifications.append((path, gate.git_blob_id(payload), mode))
        guard = gate.SnapshotMutationGuard(directory, specifications)
        self.addCleanup(guard.close)
        return guard, directory, tuple(files)

    def test_tracks_exact_nine_file_and_directory_identities(self) -> None:
        guard, directory, files = self.make_guard()
        guard.assert_clean()
        self.assertEqual(len(guard.snapshots), 9)
        self.assertEqual(guard.directory_identity.path, directory)
        self.assertEqual(guard.directory_identity.mode, 0o500)
        self.assertEqual(stat_mode(directory), 0o500)
        self.assertEqual(
            guard.directory_identity.entries,
            tuple(sorted(path.name for path, _, _ in files)),
        )
        for record, (path, payload, mode) in zip(guard.snapshots, files):
            self.assertEqual(record.path, path)
            self.assertEqual(record.blob, gate.git_blob_id(payload))
            self.assertEqual(record.mode, mode)
            self.assertGreater(record.device, 0)
            self.assertGreater(record.inode, 0)

    def test_temporary_chmod_restore_is_retained_by_inotify(self) -> None:
        guard, _directory, files = self.make_guard()
        path, _payload, mode = files[0]
        path.chmod(0o600)
        path.chmod(mode)
        with self.assertRaisesRegex(RuntimeError, "snapshot was mutated"):
            guard.assert_clean()

    def test_temporary_modify_restore_is_retained_by_inotify(self) -> None:
        guard, _directory, files = self.make_guard()
        path, payload, mode = files[0]
        path.chmod(0o600)
        path.write_bytes(b"temporary mutation\n")
        path.write_bytes(payload)
        path.chmod(mode)
        with self.assertRaisesRegex(RuntimeError, "snapshot was mutated"):
            guard.assert_clean()

    def test_temporary_replace_restore_is_retained_by_inotify(self) -> None:
        guard, directory, files = self.make_guard()
        path, payload, mode = files[0]
        backup = directory / "original"
        directory.chmod(0o700)
        path.rename(backup)
        path.write_bytes(payload)
        path.chmod(mode)
        backup.replace(path)
        directory.chmod(0o500)
        with self.assertRaisesRegex(RuntimeError, "snapshot was mutated"):
            guard.assert_clean()

    def test_run_checked_verifies_before_and_after_success_or_failure(self) -> None:
        events: list[str] = []

        class RecordingGuard:
            def assert_clean(self) -> None:
                events.append("verify")

        def successful_run(*_args, **_kwargs):
            events.append("child")
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch.object(gate.subprocess, "run", side_effect=successful_run):
            gate.run_checked(
                ["/usr/bin/true"],
                timeout=1,
                snapshot_guard=RecordingGuard(),  # type: ignore[arg-type]
            )
        self.assertEqual(events, ["verify", "child", "verify"])

        events.clear()

        def failing_run(*_args, **_kwargs):
            events.append("child")
            raise OSError("synthetic failure")

        with mock.patch.object(gate.subprocess, "run", side_effect=failing_run):
            with self.assertRaises(OSError):
                gate.run_checked(
                    ["/usr/bin/false"],
                    timeout=1,
                    snapshot_guard=RecordingGuard(),  # type: ignore[arg-type]
                )
        self.assertEqual(events, ["verify", "child", "verify"])

    def test_every_external_snapshot_path_has_pre_post_guard_wiring(self) -> None:
        for function in (gate.run_sequence_helper, gate.run_bounded_command):
            source = inspect.getsource(function)
            self.assertGreaterEqual(source.count("snapshot_guard.assert_clean()"), 3)
            self.assertIn("finally:", source)
        tree = ast.parse(textwrap.dedent(inspect.getsource(gate.run_gate)))
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)

        def dotted_name(node: ast.AST) -> str:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                prefix = dotted_name(node.value)
                return f"{prefix}.{node.attr}" if prefix else node.attr
            return ""

        def call_name(call: ast.Call) -> str:
            return dotted_name(call.func)

        def is_guard_assert(statement: ast.stmt) -> bool:
            return (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and call_name(statement.value) == "snapshot_guard.assert_clean"
            )

        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        guard_assignment = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and call_name(node.value) == "SnapshotMutationGuard"
        )
        expected_guarded_calls = {
            "build_probe": 1,
            "focus_probe_surface": 1,
            "locate_probe_surface": 2,
            "run_checked": 2,
            "run_sequence_helper": 1,
            "verify_installed_candidate": 3,
            "verify_tracked_file": 10,
        }
        for name, expected_count in expected_guarded_calls.items():
            matching = [
                call
                for call in calls
                if call.lineno > guard_assignment.lineno and call_name(call) == name
            ]
            self.assertEqual(len(matching), expected_count, name)
            for call in matching:
                keyword = next(
                    (item for item in call.keywords if item.arg == "snapshot_guard"),
                    None,
                )
                self.assertIsNotNone(keyword, name)
                self.assertIsInstance(keyword.value, ast.Name)
                self.assertEqual(keyword.value.id, "snapshot_guard")

        probe_spawn = next(
            call
            for call in calls
            if call.lineno > guard_assignment.lineno
            and call_name(call) == "subprocess.Popen"
        )
        spawn_statement = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(candidate is probe_spawn for candidate in ast.walk(node))
        )

        statement_lists = []
        for node in ast.walk(function):
            for _field, value in ast.iter_fields(node):
                if isinstance(value, list) and spawn_statement in value:
                    statement_lists.append(value)
        self.assertEqual(len(statement_lists), 1)
        statements = statement_lists[0]
        spawn_index = statements.index(spawn_statement)
        self.assertGreater(spawn_index, 0)
        self.assertTrue(is_guard_assert(statements[spawn_index - 1]))
        lifetime_try = next(
            item for item in statements[spawn_index + 1 :] if isinstance(item, ast.Try)
        )
        self.assertTrue(is_guard_assert(lifetime_try.body[0]))
        self.assertTrue(
            any(
                isinstance(candidate, ast.Call)
                and call_name(candidate) == "stop_process"
                for item in lifetime_try.finalbody
                for candidate in ast.walk(item)
            )
        )
        self.assertTrue(
            any(
                is_guard_assert(candidate)
                for item in lifetime_try.finalbody
                for candidate in ast.walk(item)
                if isinstance(candidate, ast.stmt)
            )
        )


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
