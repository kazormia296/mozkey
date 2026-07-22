#!/usr/bin/python3

from __future__ import annotations

import ast
import json
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tools.release.linux_dogfood import run_server_restart_gate
from tools.release.linux_dogfood import verify_installed_candidate


class FreshProfilePolicyTest(unittest.TestCase):
    @staticmethod
    def write_marker(root: Path) -> None:
        marker = root / run_server_restart_gate.PROFILE_MARKER_NAME
        marker.write_bytes(b"{}\n")
        marker.chmod(0o400)

    def test_generated_profile_is_exact_and_accepted_by_both_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = run_server_restart_gate.write_fcitx_profile(root)
            self.assertEqual(
                evidence[4],
                verify_installed_candidate.validate_fcitx_profile(root),
            )
            self.assertEqual(
                (root / "fcitx5" / "profile").read_bytes(),
                run_server_restart_gate.FCITX_PROFILE_PAYLOAD,
            )
            self.assertEqual(stat.S_IMODE((root / "fcitx5").stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((root / "fcitx5" / "profile").stat().st_mode),
                0o600,
            )
            self.write_marker(root)
            run_server_restart_gate.validate_prelaunch_profile_manifest(root)

    def test_modified_profile_fails_both_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_server_restart_gate.write_fcitx_profile(root)
            profile = root / "fcitx5" / "profile"
            profile.write_bytes(profile.read_bytes() + b"# modified\n")
            for verifier in (
                run_server_restart_gate.validate_fcitx_profile,
                verify_installed_candidate.validate_fcitx_profile,
            ):
                with self.subTest(verifier=verifier.__module__):
                    with self.assertRaises(RuntimeError):
                        verifier(root)

    def test_wrong_profile_mode_fails_both_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_server_restart_gate.write_fcitx_profile(root)
            profile = root / "fcitx5" / "profile"
            profile.chmod(0o644)
            for verifier in (
                run_server_restart_gate.validate_fcitx_profile,
                verify_installed_candidate.validate_fcitx_profile,
            ):
                with self.subTest(verifier=verifier.__module__):
                    with self.assertRaises(RuntimeError):
                        verifier(root)

    def test_mozkey_override_fails_runner_and_candidate_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_server_restart_gate.write_fcitx_profile(root)
            self.write_marker(root)
            override_directory = root / "fcitx5" / "conf"
            override_directory.mkdir(mode=0o700)
            override = override_directory / "mozkey-ibg.conf"
            override.write_bytes(b"[Zenz]\nEnabled=False\n")
            override.chmod(0o600)
            with self.assertRaises(RuntimeError):
                run_server_restart_gate.validate_prelaunch_profile_manifest(root)
            for verifier in (
                run_server_restart_gate.validate_no_mozkey_config_override,
                verify_installed_candidate.validate_no_mozkey_config_override,
            ):
                with self.subTest(verifier=verifier.__module__):
                    with self.assertRaises(RuntimeError):
                        verifier(root)

    def test_hardlinked_profile_fails_both_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_server_restart_gate.write_fcitx_profile(root)
            profile = root / "fcitx5" / "profile"
            (root / "profile-alias").hardlink_to(profile)
            for verifier in (
                run_server_restart_gate.validate_fcitx_profile,
                verify_installed_candidate.validate_fcitx_profile,
            ):
                with self.subTest(verifier=verifier.__module__):
                    with self.assertRaises(RuntimeError):
                        verifier(root)

    def test_atomic_profile_replacement_cannot_mix_inode_and_payload(self) -> None:
        for verifier in (
            run_server_restart_gate.validate_fcitx_profile,
            verify_installed_candidate.validate_fcitx_profile,
        ):
            with self.subTest(verifier=verifier.__module__):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    run_server_restart_gate.write_fcitx_profile(root)
                    profile = root / "fcitx5" / "profile"
                    original_read = run_server_restart_gate.os.read
                    replaced = False

                    def replacing_read(descriptor: int, count: int) -> bytes:
                        nonlocal replaced
                        if not replaced:
                            replacement = profile.with_name("profile.replacement")
                            replacement.write_bytes(
                                run_server_restart_gate.FCITX_PROFILE_PAYLOAD
                            )
                            replacement.chmod(0o600)
                            replacement.replace(profile)
                            replaced = True
                        return original_read(descriptor, count)

                    with mock.patch("os.read", side_effect=replacing_read):
                        with self.assertRaises(RuntimeError):
                            verifier(root)

    def test_prelaunch_protocol_consumers_must_be_private_and_empty(self) -> None:
        fixture_path = Path(__file__).with_name("release_fixture.json")
        fixture, _reading, _custom, _default = (
            run_server_restart_gate.load_fixture(fixture_path)
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            runtime.chmod(0o700)
            root = runtime / "protocol"
            root.mkdir(mode=0o700)
            consumers = root / "consumers"
            consumers.mkdir(mode=0o700)
            projects = root / "projects"
            projects.mkdir(mode=0o700)
            project = fixture["project"]
            assert isinstance(project, dict)
            project_id = project["project_id"]
            assert isinstance(project_id, str)
            project_path = projects / f"{project_id}.json"
            project_path.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            project_path.chmod(0o600)
            state = root / "state.json"
            state.write_text(
                json.dumps(fixture["state"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            state.chmod(0o600)
            run_server_restart_gate.validate_prelaunch_protocol_manifest(
                root, runtime, fixture
            )
            state_payload = state.read_bytes()
            state.write_bytes(b"{}\n")
            with self.assertRaises(RuntimeError):
                run_server_restart_gate.validate_prelaunch_protocol_manifest(
                    root, runtime, fixture
                )
            state.write_bytes(state_payload)
            state.chmod(0o600)
            marker = consumers / "fcitx5-mozkey-ibg.json"
            marker.write_bytes(b"{}\n")
            marker.chmod(0o600)
            with self.assertRaises(RuntimeError):
                run_server_restart_gate.validate_prelaunch_protocol_manifest(
                    root, runtime, fixture
                )

    def test_headless_probe_selects_mozkey_engine(self) -> None:
        source = Path(__file__).with_name("ibus_headless_probe.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assignments = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        self.assertEqual(assignments.get("IBUS_ENGINE"), "mozkey-ibg")


if __name__ == "__main__":
    unittest.main()
