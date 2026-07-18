#!/usr/bin/python3

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.release.linux_dogfood import run_gui_scope_gate
from tools.release.linux_dogfood import run_server_restart_gate
from tools.release.linux_dogfood import verify_installed_candidate


class ProcVisibilityPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.proc = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def scanners(self):
        return (
            (run_server_restart_gate, b"fcitx5"),
            (run_gui_scope_gate, b"fcitx5"),
            (verify_installed_candidate, b"mozc_server"),
        )

    def test_protected_unrelated_process_is_a_scan_miss(self) -> None:
        (self.proc / "comm").write_bytes(b"systemd\n")
        for module, _expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                denied = PermissionError(
                    errno.EACCES, "protected same-UID process"
                )
                with mock.patch.object(module.os, "readlink", side_effect=denied):
                    self.assertIsNone(
                        module.scan_visible_executable(self.proc, os.getuid())
                    )

    def test_protected_process_stat_fails_closed(self) -> None:
        for module, _expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                denied = PermissionError(
                    errno.EPERM, "protected same-UID process metadata"
                )
                with mock.patch.object(Path, "stat", side_effect=denied):
                    with self.assertRaises(RuntimeError):
                        module.scan_visible_executable(self.proc, os.getuid())

    def test_protected_target_comm_fails_closed(self) -> None:
        for module, expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                (self.proc / "comm").write_bytes(expected_comm + b"\n")
                denied = PermissionError(errno.EACCES, "protected candidate")
                with mock.patch.object(module.os, "readlink", side_effect=denied):
                    with self.assertRaises(RuntimeError):
                        module.scan_visible_executable(self.proc, os.getuid())

    def test_unreadable_comm_fails_closed_while_process_exists(self) -> None:
        for module, _expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                (self.proc / "comm").unlink(missing_ok=True)
                denied = PermissionError(errno.EACCES, "protected candidate")
                with mock.patch.object(module.os, "readlink", side_effect=denied):
                    with self.assertRaises(RuntimeError):
                        module.scan_visible_executable(self.proc, os.getuid())

    def test_visible_executable_is_returned_exactly(self) -> None:
        expected = "/usr/bin/fcitx5"
        for module, _expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                with mock.patch.object(module.os, "readlink", return_value=expected):
                    self.assertEqual(
                        module.scan_visible_executable(self.proc, os.getuid()),
                        expected,
                    )

    def test_disappearing_process_is_a_scan_miss(self) -> None:
        for module, _expected_comm in self.scanners():
            with self.subTest(module=module.__name__):
                with mock.patch.object(
                    module.os, "readlink", side_effect=ProcessLookupError()
                ):
                    self.assertIsNone(
                        module.scan_visible_executable(self.proc, os.getuid())
                    )

    def test_exact_gui_identity_still_fails_closed(self) -> None:
        denied = PermissionError(errno.EPERM, "exact process is protected")
        with mock.patch.object(
            run_gui_scope_gate.os, "readlink", side_effect=denied
        ):
            with self.assertRaises(PermissionError):
                run_gui_scope_gate.process_identity(
                    os.getpid(), Path("/usr/bin/python3")
                )

    def test_exact_candidate_identity_still_fails_closed(self) -> None:
        denied = PermissionError(errno.EPERM, "exact process is protected")
        with mock.patch.object(
            verify_installed_candidate.os, "readlink", side_effect=denied
        ):
            with self.assertRaises(PermissionError):
                verify_installed_candidate.process_record(
                    os.getpid(), Path("/usr/bin/python3")
                )


if __name__ == "__main__":
    unittest.main()
