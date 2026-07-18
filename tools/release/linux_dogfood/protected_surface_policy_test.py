#!/usr/bin/python3
"""Policy tests for the tracked protected-surface product gate."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dbus

from tools.release.linux_dogfood import protected_surface_headless_probe as probe
from tools.release.linux_dogfood import run_protected_surface_gate as runner


def attributed_ibus_text(
    value: str,
    attribute_values: tuple[tuple[int, int, int, int], ...],
) -> dbus.Struct:
    attributes = [
        dbus.Struct(
            (
                dbus.String("IBusAttribute"),
                dbus.Dictionary({}, signature="sv"),
                dbus.UInt32(attribute_type),
                dbus.UInt32(attribute_value),
                dbus.UInt32(start),
                dbus.UInt32(end),
            ),
            signature="sa{sv}uuuu",
            variant_level=1,
        )
        for attribute_type, attribute_value, start, end in attribute_values
    ]
    attribute_list = dbus.Struct(
        (
            dbus.String("IBusAttrList"),
            dbus.Dictionary({}, signature="sv"),
            dbus.Array(attributes, signature="v"),
        ),
        signature="sa{sv}av",
        variant_level=1,
    )
    return dbus.Struct(
        (
            dbus.String("IBusText"),
            dbus.Dictionary({}, signature="sv"),
            dbus.String(value),
            attribute_list,
        ),
        signature="sa{sv}sv",
        variant_level=1,
    )


def segmented_ibus_text(
    value: str,
    ranges: tuple[tuple[int, int], ...],
    *,
    attribute_type: int = 1,
) -> dbus.Struct:
    return attributed_ibus_text(
        value,
        tuple((attribute_type, 1, start, end) for start, end in ranges),
    )


class ProtectedSurfacePolicyTest(unittest.TestCase):
    def test_fixture_is_the_exact_mixed_literal_product_case(self) -> None:
        fixture = probe.load_fixture()
        self.assertEqual(fixture.literal, "RUST_LOG=debug")
        self.assertEqual(
            fixture.raw_preedit, "RUST_LOG=debugでくわしいろぐをだす"
        )
        self.assertEqual(
            fixture.converted_value, "RUST_LOG=debugで詳しいログを出す"
        )
        self.assertEqual(
            fixture.literal.encode("utf-8"), fixture.raw_preedit.encode("utf-8")[:14]
        )

    def test_fixture_drift_fails_closed(self) -> None:
        source = Path(__file__).with_name("protected_surface_fixture.json")
        document = json.loads(source.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.json"
            for key, value in (
                ("literal", "RUST_LOG=Debug"),
                ("raw_preedit", "RUST_LOG=debugでくわしいログをだす"),
                ("converted_value", "RUST_LOG=debugで詳しいログをだす"),
            ):
                mutated = dict(document)
                mutated[key] = value
                path.write_text(
                    json.dumps(mutated, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                with self.assertRaises(RuntimeError):
                    probe.load_fixture(path)
                with self.assertRaises(RuntimeError):
                    runner.load_fixture(path)

    def test_ibus_text_serialization_preserves_exact_unicode(self) -> None:
        value = "RUST_LOG=debugで詳しいログを出す"
        payload = probe.serialized_ibus_text(value)
        self.assertEqual(probe.ibus_text_value(payload), value)
        self.assertRegex(probe.payload_sha256(payload), r"^[0-9a-f]{64}$")
        message = dbus.lowlevel.MethodCallMessage(
            "org.example.Test",
            "/org/example/Test",
            "org.example.Test",
            "SetSurroundingText",
        )
        message.append(
            payload,
            dbus.UInt32(len(value)),
            dbus.UInt32(0),
            signature="vuu",
        )
        marshalled = message.get_args_list()
        self.assertEqual(message.get_signature(), "vuu")
        self.assertEqual(probe.ibus_text_value(marshalled[0]), value)
        self.assertEqual((int(marshalled[1]), int(marshalled[2])), (len(value), 0))

    def test_literal_prefix_check_is_byte_exact(self) -> None:
        fixture = probe.load_fixture()
        exact = probe.PreeditEvent(
            fixture.converted_value,
            len(fixture.literal),
            True,
            "0" * 64,
            ((0, len(fixture.converted_value)),),
        )
        probe.require_literal_prefix(exact, fixture.literal)
        for mutated in (
            "Rust_LOG=debugで詳しいログを出す",
            "ＲＵＳＴ＿ＬＯＧ=debugで詳しいログを出す",
            "RUST_LOG=Debugで詳しいログを出す",
        ):
            with self.assertRaises(RuntimeError):
                probe.require_literal_prefix(
                    probe.PreeditEvent(
                        mutated,
                        14,
                        True,
                        "0" * 64,
                        ((0, len(mutated)),),
                    ),
                    fixture.literal,
                )

    def test_segment_boundary_change_requires_exact_ibus_attribute_ranges(
        self,
    ) -> None:
        value = "RUST_LOG=debugで詳しいログを出す"
        baseline_ranges = ((0, 14), (14, len(value)))
        resized_ranges = ((0, 15), (15, len(value)))
        baseline_payload = attributed_ibus_text(
            value,
            (
                (2, 0xFFFFFF, 0, 14),
                (3, 0, 0, 14),
                (1, 1, 14, len(value)),
            ),
        )
        resized_payload = segmented_ibus_text(value, resized_ranges)
        self.assertEqual(
            probe.ibus_segment_ranges(baseline_payload, value), baseline_ranges
        )
        self.assertEqual(
            probe.ibus_segment_ranges(resized_payload, value), resized_ranges
        )
        baseline = probe.PreeditEvent(
            value,
            14,
            True,
            probe.payload_sha256(baseline_payload),
            baseline_ranges,
        )
        cursor_and_payload_only = probe.PreeditEvent(
            value,
            15,
            True,
            "f" * 64,
            baseline_ranges,
        )
        resized = probe.PreeditEvent(
            value,
            15,
            True,
            probe.payload_sha256(resized_payload),
            resized_ranges,
        )
        probe.require_segment_partition(baseline, "baseline")
        probe.require_segment_partition(resized, "resized")
        self.assertFalse(
            probe.has_segment_boundary_change(baseline, cursor_and_payload_only)
        )
        self.assertTrue(probe.has_segment_boundary_change(baseline, resized))

    def test_segment_attribute_parser_rejects_malformed_ranges(self) -> None:
        value = "RUST_LOG=debugで詳しいログを出す"
        for ranges in (
            ((0, 13), (14, len(value))),
            ((0, 15), (14, len(value))),
        ):
            payload = segmented_ibus_text(value, ranges)
            event = probe.PreeditEvent(
                value,
                14,
                True,
                probe.payload_sha256(payload),
                probe.ibus_segment_ranges(payload, value),
            )
            with self.assertRaises(RuntimeError):
                probe.require_segment_partition(event, "malformed")
        with self.assertRaises(RuntimeError):
            probe.ibus_segment_ranges(
                segmented_ibus_text(value, ((0, len(value) + 1),)), value
            )
        with self.assertRaises(RuntimeError):
            probe.ibus_segment_ranges(
                segmented_ibus_text(value, ((0, len(value)),), attribute_type=4),
                value,
            )

    def test_host_buffer_requires_exact_delete_then_commit(self) -> None:
        value = "RUST_LOG=debugで詳しいログを出す"
        host = probe.HostBuffer(value, len(value), 0)
        host.apply_delete(-len(value), len(value))
        self.assertEqual((host.text, host.cursor, host.anchor), ("", 0, 0))
        host.apply_commit(value)
        self.assertEqual(
            (host.text, host.cursor, host.anchor),
            (value, len(value), len(value)),
        )

        for offset, length in (
            (0, len(value)),
            (-len(value) + 1, len(value)),
            (-len(value), len(value) - 1),
        ):
            invalid = probe.HostBuffer(value, len(value), 0)
            with self.assertRaises(RuntimeError):
                invalid.apply_delete(offset, length)
        with self.assertRaises(RuntimeError):
            probe.HostBuffer(value, len(value), 0).apply_commit(value)

    def test_restart_support_bootstrap_verifies_and_snapshots_exact_blob(
        self,
    ) -> None:
        payload = b"#!/usr/bin/python3\nvalue = 1\n"
        blob = runner.bootstrap_git_blob_id(payload)
        expected_head = "1" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            support = root / "tools/release/linux_dogfood/run_server_restart_gate.py"
            support.parent.mkdir(parents=True)
            support.write_bytes(payload)
            support.chmod(0o755)
            relative = support.relative_to(root)

            def fake_git(_root: Path, *arguments: str) -> str:
                self.assertEqual(_root, root)
                responses = {
                    ("rev-parse", "--show-toplevel"): str(root),
                    ("rev-parse", "HEAD"): expected_head,
                    (
                        "ls-files",
                        "--stage",
                        "--",
                        str(relative),
                    ): f"100755 {blob} 0\t{relative}",
                    (
                        "ls-tree",
                        "HEAD",
                        "--",
                        str(relative),
                    ): f"100755 blob {blob}\t{relative}",
                }
                return responses[arguments]

            with mock.patch.object(runner, "bootstrap_git", side_effect=fake_git):
                self.assertEqual(
                    runner.bootstrap_verify_restart_runner(
                        root, support, expected_head
                    ),
                    blob,
                )
                snapshot = runner.bootstrap_snapshot_restart_runner(
                    support, root / "verified_restart_support.py", blob
                )
                runner.bootstrap_verify_snapshot(snapshot)
                support.write_bytes(payload + b"# drift\n")
                with self.assertRaises(RuntimeError):
                    runner.bootstrap_verify_restart_runner(
                        root, support, expected_head
                    )

    def test_restart_support_is_imported_only_after_private_snapshot(self) -> None:
        source = Path(__file__).with_name("run_protected_surface_gate.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)]
        verify_lines = [
            node.lineno
            for node in calls
            if isinstance(node.func, ast.Name)
            and node.func.id == "bootstrap_verify_restart_runner"
        ]
        snapshot_line = next(
            node.lineno
            for node in calls
            if isinstance(node.func, ast.Name)
            and node.func.id == "bootstrap_snapshot_restart_runner"
        )
        import_call = next(
            node
            for node in calls
            if isinstance(node.func, ast.Name) and node.func.id == "load_module"
        )
        self.assertGreaterEqual(len(verify_lines), 3)
        self.assertLess(min(verify_lines), snapshot_line)
        self.assertLess(snapshot_line, import_call.lineno)
        self.assertGreater(max(verify_lines), import_call.lineno)
        self.assertIsInstance(import_call.args[1], ast.Attribute)
        self.assertEqual(import_call.args[1].attr, "path")
        self.assertIsInstance(import_call.args[1].value, ast.Name)
        self.assertEqual(import_call.args[1].value.id, "restart_snapshot")

    def test_snapshot_guard_detects_transient_mutation_then_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()

            def assert_detected(name: str, mutate) -> None:
                directory = root / name
                directory.mkdir()
                path = directory / "probe.py"
                original = b"trusted\n"
                path.write_bytes(original)
                path.chmod(0o600)
                guard = runner.SnapshotMutationGuard(
                    (directory, path), lambda: None
                )
                try:
                    mutate(directory, path, original)
                    with self.assertRaises(RuntimeError):
                        guard.assert_clean()
                finally:
                    guard.close()

            assert_detected(
                "chmod",
                lambda _directory, path, _original: (
                    path.chmod(0o400),
                    path.chmod(0o600),
                ),
            )
            assert_detected(
                "modify",
                lambda _directory, path, original: (
                    path.write_bytes(b"untrusted\n"),
                    path.write_bytes(original),
                ),
            )

            def replace_restore(
                directory: Path, path: Path, _original: bytes
            ) -> None:
                held = directory / "held"
                path.rename(held)
                path.write_bytes(b"untrusted\n")
                path.unlink()
                held.rename(path)

            assert_detected("replace", replace_restore)

    def test_snapshot_directory_identity_fixes_exact_entry_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "probe.py").write_bytes(b"trusted\n")
            root.chmod(0o500)
            evidence = runner.capture_snapshot_directory(
                root, ("probe.py",), 0o500
            )
            runner.verify_snapshot_directories((evidence,))
            root.chmod(0o700)
            (root / "extra.py").write_bytes(b"extra\n")
            root.chmod(0o500)
            with self.assertRaises(RuntimeError):
                runner.verify_snapshot_directories((evidence,))
            root.chmod(0o700)

    def test_snapshot_executions_have_pre_post_and_finally_guards(self) -> None:
        source = Path(__file__).with_name("run_protected_surface_gate.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

        def guarded_calls(function: ast.FunctionDef, guard_name: str) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(function):
                if not isinstance(node, ast.With):
                    continue
                guarded = any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr == "checked_execution"
                    and isinstance(item.context_expr.func.value, ast.Name)
                    and item.context_expr.func.value.id == guard_name
                    for item in node.items
                )
                if not guarded:
                    continue
                for child in node.body:
                    for call in ast.walk(child):
                        if isinstance(call, ast.Call):
                            if isinstance(call.func, ast.Attribute):
                                names.add(call.func.attr)
                            elif isinstance(call.func, ast.Name):
                                names.add(call.func.id)
            return names

        candidate_calls = guarded_calls(
            functions["run_verified_gate"], "snapshot_guard"
        )
        self.assertTrue(
            {
                "load_stopper",
                "run_bounded_command",
                "verify_installed_candidate",
                "unique_installed_server",
            }.issubset(candidate_calls)
        )
        bootstrap_calls = guarded_calls(functions["main"], "bootstrap_guard")
        self.assertTrue(
            {"load_module", "run_verified_gate"}.issubset(bootstrap_calls)
        )

        for function_name, guard_name in (
            ("run_verified_gate", "snapshot_guard"),
            ("main", "bootstrap_guard"),
        ):
            final_calls = [
                call
                for node in ast.walk(functions[function_name])
                if isinstance(node, ast.Try)
                for statement in node.finalbody
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "assert_clean"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == guard_name
            ]
            self.assertTrue(final_calls)

    def test_probe_has_one_context_and_all_required_observations(self) -> None:
        source = Path(__file__).with_name("protected_surface_headless_probe.py")
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(calls.count("CreateInputContext"), 1)
        self.assertEqual(calls.count("SetSurroundingText"), 1)
        for signal_name in (
            "UpdatePreeditText",
            "CommitText",
            "DeleteSurroundingText",
            "ForwardKeyEvent",
        ):
            self.assertIn(f'"{signal_name}"', text)
        for token in (
            "event.text == fixture.raw_preedit",
            "event.cursor == len(fixture.raw_preedit)",
            "cursor_value > len(value)",
            "event.payload_sha256 == mixed.payload_sha256",
            "event.payload_sha256 == conversion.payload_sha256",
            "event.segment_ranges == conversion.segment_ranges",
            "has_segment_boundary_change(baseline, event)",
            "require_segment_partition(conversion, \"baseline conversion\")",
            "require_protected_events(protected_event_start)",
            "literal_bytes=preserved",
            "adjacent_kana=converted",
            "resize_roundtrip=exact",
            "reconversion=exact",
        ):
            self.assertIn(token, text)
        self.assertNotIn('len(value.encode("utf-8"))', text)
        self.assertNotIn("ydotool", text.lower())
        self.assertNotIn("gtk", text.lower())
        self.assertNotIn("qt", text.lower())

    def test_runner_reuses_release_grade_bounds_and_identity(self) -> None:
        source = Path(__file__).with_name("run_protected_surface_gate.py")
        text = source.read_text(encoding="utf-8")
        for call in (
            "verify_tracked_file",
            "snapshot_tracked_file",
            "run_bounded_command",
            "load_fresh_profile_evidence",
            "verify_fresh_profile_launch",
            "installed_fcitx",
            "ibus_owner_identity",
            "verify_installed_candidate",
            "bootstrap_verify_restart_runner",
            "bootstrap_snapshot_restart_runner",
            "bootstrap_verify_snapshot",
            "SnapshotMutationGuard",
            "snapshot_watch_paths",
            "capture_snapshot_directory",
            "checked_execution",
        ):
            self.assertIn(call, text)
        self.assertLessEqual(runner.PROBE_TIMEOUT_SECONDS, 45.0)
        self.assertNotIn("ydotool", text.lower())

    def test_runner_requires_exact_probe_transcript(self) -> None:
        fixture = runner.load_fixture(
            Path(__file__).with_name("protected_surface_fixture.json")
        )
        ibus = SimpleNamespace(owner=":1.42")
        fcitx = SimpleNamespace(pid=123, start_time="456")
        result = runner.expected_probe_result(fixture, ibus, fcitx)
        self.assertIn("contexts=1", result)
        self.assertIn("cursor_boundary=14", result)
        self.assertIn("commit_count=2 deletion_count=1", result)
        self.assertTrue(
            result.endswith(
                "ibus_owner=:1.42 fcitx_pid=123 fcitx_start_time=456"
            )
        )


if __name__ == "__main__":
    unittest.main()
