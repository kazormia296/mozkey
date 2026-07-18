import io
import os
import shutil
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.release import probe_macos_zenz_runtime as target


class ProbeMacosZenzRuntimeTest(unittest.TestCase):
    def test_dependency_license_allowlist_and_checksums_are_exact(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "src/win32/installer/zenz_runtime/licenses"
        )
        with tempfile.TemporaryDirectory() as temporary:
            licenses = Path(temporary) / "licenses"
            licenses.mkdir()
            for name in target.REQUIRED_LICENSES:
                shutil.copyfile(source / name, licenses / name)

            target._verify_license_allowlist(licenses)

            (licenses / "unexpected.txt").write_text(
                "unexpected\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                target.ProbeFailure, "runtime_licenses_invalid"
            ):
                target._verify_license_allowlist(licenses)
            (licenses / "unexpected.txt").unlink()

            (licenses / "cpp-httplib-MIT.txt").write_text(
                "not the pinned license\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                target.ProbeFailure, "runtime_license_checksum_mismatch"
            ):
                target._verify_license_allowlist(licenses)

    def test_wire_probe_requires_success_and_nonempty_value(self):
        value = "候補".encode("utf-8")
        response = bytearray(
            target.WIRE_RESPONSE_HEADER.pack(
                target.WIRE_MAGIC,
                target.WIRE_VERSION,
                target.WIRE_RESPONSE,
                7,
                target.WIRE_STATUS_OK,
                1,
                len(value),
                0,
            )
            + value
        )

        class FakeConnection:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect(self, path):
                self.path = path

            def sendall(self, payload):
                self.payload = payload

            def recv(self, size):
                chunk = bytes(response[:size])
                del response[:size]
                return chunk

            def close(self):
                pass

        connection = FakeConnection()
        with mock.patch.object(target.socket, "socket", return_value=connection):
            self.assertTrue(
                target._send_wire_request(Path("/private/runtime.sock"), 7, 2.0)
            )

    def test_macho_contract_rejects_dynamic_llama_dependency(self):
        layout = target.RuntimeLayout(
            converter_app=Path("/runtime/Converter.app"),
            resources=Path("/runtime/Resources"),
            scorer=Path("/runtime/mozc_zenz_scorer"),
            server=Path("/runtime/llama-server"),
            model=Path("/runtime/model.gguf"),
        )
        with mock.patch.object(
            target, "_verify_universal_macho_contract"
        ) as verify_contract, mock.patch.object(
            target,
            "_run",
            return_value=(
                "/runtime/llama-server:\n\t@rpath/libllama.dylib\n"
                "\t/System/Library/Frameworks/Accelerate.framework/"
                "Versions/A/Accelerate\n"
            ),
        ):
            with self.assertRaisesRegex(
                target.ProbeFailure, "llama_dependencies_invalid"
            ):
                target._verify_macho_contract(layout)
        verify_contract.assert_has_calls(
            [
                mock.call(layout.server, "llama"),
                mock.call(layout.scorer, "scorer"),
            ]
        )

    def test_macho_contract_rejects_single_architecture_scorer(self):
        with mock.patch.object(target, "_run", return_value="arm64\n"):
            with self.assertRaisesRegex(
                target.ProbeFailure, "scorer_architectures_invalid"
            ):
                target._verify_universal_macho_contract(
                    Path("/runtime/mozc_zenz_scorer"), "scorer"
                )

    def test_macho_contract_rejects_wrong_scorer_deployment_target(self):
        with mock.patch.object(
            target,
            "_run",
            side_effect=[
                "arm64 x86_64\n",
                "    minos 11.0\n",
                "    minos 11.0\n",
            ],
        ):
            with self.assertRaisesRegex(
                target.ProbeFailure,
                "scorer_deployment_target_invalid",
            ):
                target._verify_universal_macho_contract(
                    Path("/runtime/mozc_zenz_scorer"), "scorer"
                )

    def test_private_endpoint_requires_owner_only_mode(self):
        socket_path = Path("/private/runtime.sock")
        private_info = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid()
        )
        with mock.patch.object(Path, "lstat", return_value=private_info):
            target._validate_private_path(socket_path, socket_path=True)

        exposed_info = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o660, st_uid=os.geteuid()
        )
        with mock.patch.object(Path, "lstat", return_value=exposed_info):
            with self.assertRaisesRegex(
                target.ProbeFailure, "private_endpoint_invalid"
            ):
                target._validate_private_path(socket_path, socket_path=True)

    def test_main_redacts_unexpected_error(self):
        stderr = io.StringIO()
        with mock.patch.object(
            target,
            "run_probe",
            side_effect=RuntimeError("private prompt and package path"),
        ):
            with redirect_stderr(stderr):
                self.assertEqual(target.main(["--package", "/private/pkg"]), 1)
        self.assertEqual(
            stderr.getvalue(),
            "macOS Zenz runtime probe failed: unexpected_runtime_error\n",
        )


if __name__ == "__main__":
    unittest.main()
