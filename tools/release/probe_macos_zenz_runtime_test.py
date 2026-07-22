import io
import json
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
from tools.release import probe_zenz_runtime as portable_probe


def _create_probe_layout(root: Path) -> target.RuntimeLayout:
    resources = root / "MozkeyIbGConverter.app/Contents/Resources"
    model_directory = resources / "models"
    license_directory = resources / "licenses"
    model_directory.mkdir(parents=True)
    license_directory.mkdir()

    scorer = resources / target.SCORER_NAME
    server = resources / "llama-server"
    model = model_directory / target.MODEL_NAME
    manifest = resources / "zenz-runtime-manifest.json"
    for executable in (scorer, server):
        executable.write_bytes(b"Mach-O fixture")
        executable.chmod(0o755)
    model.write_bytes(b"GGUF fixture")
    model.chmod(0o644)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "mozkey.macos_zenz_runtime.v1",
                "llama_cpp": {
                    "tag": "b9637",
                    "archive_sha256": target.LLAMA_ARCHIVE_SHA256,
                    "built_targets": ["llama-server"],
                },
                "model": {"sha256": target.MODEL_SHA256},
                "architectures": target.ARCHITECTURE_MANIFEST,
                "scorer": {
                    "architectures": target.ARCHITECTURE_MANIFEST,
                    "filename": target.SCORER_NAME,
                },
                "backend": {
                    "accelerate": True,
                    "cpu": True,
                    "curl": False,
                    "llama_build_app": False,
                    "llama_build_tools": True,
                    "metal": False,
                    "multimodal_video": False,
                    "rpc": False,
                    "shared_libraries": False,
                    "web_ui": False,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest.chmod(0o644)

    license_source = (
        Path(__file__).resolve().parents[2]
        / "src/win32/installer/zenz_runtime/licenses"
    )
    for name in target.REQUIRED_LICENSES:
        shutil.copyfile(license_source / name, license_directory / name)
        (license_directory / name).chmod(0o644)

    return target.RuntimeLayout(
        converter_app=resources.parents[1],
        resources=resources,
        scorer=scorer,
        server=server,
        model=model,
    )


def _send_fake_wire_response(
    *,
    status: int,
    value: bytes = b"",
    debug: bytes = b"",
):
    response = bytearray(
        target.WIRE_RESPONSE_HEADER.pack(
            target.WIRE_MAGIC,
            target.WIRE_VERSION,
            target.WIRE_RESPONSE,
            7,
            status,
            1,
            len(value),
            len(debug),
        )
        + value
        + debug
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
        result = target._send_wire_request(
            Path("/private/runtime.sock"), 7, 2.0
        )
    return result, connection


class ProbeMacosZenzRuntimeTest(unittest.TestCase):
    def test_runtime_architecture_contract_is_arm64_only(self):
        self.assertEqual(target.ARCHITECTURES, (("arm64", "12.0"),))
        self.assertEqual(
            target.ARCHITECTURE_MANIFEST,
            {"arm64": {"deployment_target": "12.0"}},
        )

    def test_wire_contract_matches_portable_runtime_probe(self):
        for name in (
            "WIRE_MAGIC",
            "WIRE_VERSION",
            "WIRE_REQUEST",
            "WIRE_RESPONSE",
            "WIRE_STATUS_OK",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(target, name), getattr(portable_probe, name)
                )
        self.assertEqual(
            target.WIRE_REQUEST_HEADER.format,
            portable_probe.WIRE_REQUEST_HEADER.format,
        )
        self.assertEqual(
            target.WIRE_RESPONSE_HEADER.format,
            portable_probe.WIRE_RESPONSE_HEADER.format,
        )

    def test_scorer_ready_probe_uses_full_client_timeout(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "src/zenz_scorer/main.cc"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "constexpr uint32_t kLlamaReadyProbeTimeoutMsec = "
            "kMaxRequestTimeoutMsec;",
            source,
        )
        self.assertEqual(
            source.count("kLlamaReadyProbeTimeoutMsec,\n              8,"),
            2,
        )

    def test_bazel_packaging_stages_canonical_runtime_resource_paths(self):
        repository = Path(__file__).resolve().parents[2]
        runtime_build = (repository / "src/mac/zenz_runtime/BUILD.bazel").read_text(
            encoding="utf-8"
        )
        server_build = (repository / "src/server/BUILD.bazel").read_text(
            encoding="utf-8"
        )

        def copy_file_block(name):
            marker = f'copy_file(\n    name = "{name}",'
            start = runtime_build.index(marker)
            end = runtime_build.index("\n)\n", start) + len("\n)\n")
            return runtime_build[start:end]

        staging_contracts = {
            "packaged_llama_server": (
                "generated/llama-server",
                "llama-server",
                True,
                "Resources",
            ),
            "packaged_mozc_zenz_scorer": (
                "generated/mozc_zenz_scorer",
                "mozc_zenz_scorer",
                True,
                "Resources",
            ),
            "packaged_normalized_model": (
                "generated/models/zenz-v3.2-small-Q5_K_M.gguf",
                "zenz-v3.2-small-Q5_K_M.gguf",
                False,
                "Resources/models",
            ),
            "packaged_runtime_manifest": (
                "generated/zenz-runtime-manifest.json",
                "zenz-runtime-manifest.json",
                False,
                "Resources",
            ),
            "packaged_license_apache_2_0": (
                "//win32/installer/zenz_runtime:licenses/Apache-2.0.txt",
                "Apache-2.0.txt",
                False,
                "Resources/licenses",
            ),
            "packaged_license_third_party_notices": (
                "//win32/installer/zenz_runtime:licenses/THIRD_PARTY_NOTICES.md",
                "THIRD_PARTY_NOTICES.md",
                False,
                "Resources/licenses",
            ),
            "packaged_license_cpp_httplib_mit": (
                "//win32/installer/zenz_runtime:licenses/cpp-httplib-MIT.txt",
                "cpp-httplib-MIT.txt",
                False,
                "Resources/licenses",
            ),
            "packaged_license_llama_cpp_mit": (
                "//win32/installer/zenz_runtime:licenses/llama.cpp-MIT.txt",
                "llama.cpp-MIT.txt",
                False,
                "Resources/licenses",
            ),
            "packaged_license_nlohmann_json_mit": (
                "//win32/installer/zenz_runtime:licenses/nlohmann-json-MIT.txt",
                "nlohmann-json-MIT.txt",
                False,
                "Resources/licenses",
            ),
            "packaged_license_zenz_model": (
                "//win32/installer/zenz_runtime:licenses/zenz-v3.2-small-gguf.txt",
                "zenz-v3.2-small-gguf.txt",
                False,
                "Resources/licenses",
            ),
        }
        for name, (source, output, executable, destination) in staging_contracts.items():
            with self.subTest(name=name):
                block = copy_file_block(name)
                self.assertIn(f'src = "{source}"', block)
                self.assertIn(f'out = "{output}"', block)
                self.assertIn("allow_symlink = False", block)
                self.assertIn(f"is_executable = {executable}", block)
                self.assertIn(
                    f'"//mac/zenz_runtime:{name}": "{destination}"',
                    server_build,
                )

        self.assertNotIn(
            '"//win32/installer/zenz_runtime:licenses/', server_build
        )

    def test_owner_executable_package_is_accepted_without_relaxing_runtime_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "MozkeyIbG.pkg"
            package.write_bytes(b"package")
            package.chmod(0o744)
            layout = target.RuntimeLayout(
                converter_app=Path("/runtime/MozkeyIbGConverter.app"),
                resources=Path("/runtime/Resources"),
                scorer=Path("/runtime/mozc_zenz_scorer"),
                server=Path("/runtime/llama-server"),
                model=Path("/runtime/model.gguf"),
            )

            with mock.patch.object(target.sys, "platform", "darwin"), mock.patch.object(
                target, "_run"
            ) as run, mock.patch.object(
                target, "_find_layout", return_value=layout
            ), mock.patch.object(
                target, "_verify_macho_contract"
            ), mock.patch.object(
                target, "_verify_codesign"
            ):
                target.run_probe(package, live=False, timeout_seconds=120.0)

            run.assert_called_once()
            target._require_regular(
                package,
                executable=True,
                failure_code="package_layout_invalid",
            )
            with self.assertRaisesRegex(
                target.ProbeFailure, "package_layout_invalid"
            ):
                target._require_regular(
                    package,
                    executable=False,
                    failure_code="package_layout_invalid",
                )

            package.chmod(0o764)
            with mock.patch.object(target.sys, "platform", "darwin"):
                with self.assertRaisesRegex(
                    target.ProbeFailure, "package_layout_invalid"
                ):
                    target.run_probe(package, live=False, timeout_seconds=120.0)

    def test_layout_failures_are_field_specific_and_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            expanded = Path(temporary) / "expanded"
            expanded.mkdir()
            with self.assertRaises(target.ProbeFailure) as raised:
                target._find_layout(expanded)
            self.assertEqual(raised.exception.code, "runtime_scorer_count_invalid")
            self.assertNotIn(str(expanded), str(raised.exception))

        cases = (
            ("scorer", "runtime_scorer_layout_invalid"),
            ("server", "runtime_server_layout_invalid"),
            ("model", "runtime_model_layout_invalid"),
            ("manifest", "runtime_manifest_layout_invalid"),
            ("license", "runtime_license_layout_invalid"),
        )
        for field, expected_code in cases:
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory() as temporary,
            ):
                expanded = Path(temporary) / "expanded"
                layout = _create_probe_layout(expanded)
                paths = {
                    "scorer": layout.scorer,
                    "server": layout.server,
                    "model": layout.model,
                    "manifest": layout.resources
                    / "zenz-runtime-manifest.json",
                    "license": layout.resources
                    / "licenses"
                    / target.REQUIRED_LICENSES[0],
                }
                invalid_mode = (
                    0o755 if field not in ("scorer", "server") else 0o644
                )
                paths[field].chmod(invalid_mode)

                original_sha256_file = target.sha256_file

                def sha256_file(path):
                    if path == layout.model:
                        return target.MODEL_SHA256
                    return original_sha256_file(path)

                with mock.patch.object(
                    target, "sha256_file", side_effect=sha256_file
                ):
                    with self.assertRaises(target.ProbeFailure) as raised:
                        target._find_layout(expanded)

                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn(str(expanded), str(raised.exception))

    def test_empty_package_has_package_specific_layout_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "MozkeyIbG.pkg"
            package.touch(mode=0o644)
            with mock.patch.object(target.sys, "platform", "darwin"):
                with self.assertRaises(target.ProbeFailure) as raised:
                    target.run_probe(
                        package, live=False, timeout_seconds=120.0
                    )
            self.assertEqual(raised.exception.code, "package_layout_invalid")
            self.assertNotIn(str(package), str(raised.exception))

    def test_regular_file_failure_code_is_allowlisted(self):
        with self.assertRaisesRegex(
            ValueError, "unsafe regular-file failure code"
        ) as raised:
            target._require_regular(
                Path("/private/runtime"),
                executable=True,
                failure_code="/private/runtime",
            )
        self.assertNotIn("/private/runtime", str(raised.exception))

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
                target.ProbeFailure, "runtime_license_layout_invalid"
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
        result, connection = _send_fake_wire_response(
            status=target.WIRE_STATUS_OK,
            value=value,
        )
        self.assertIsNone(result)
        prompt = target.PROBE_PROMPT.encode("utf-8")
        self.assertEqual(
            connection.payload,
            target.WIRE_REQUEST_HEADER.pack(
                target.WIRE_MAGIC,
                target.WIRE_VERSION,
                target.WIRE_REQUEST,
                7,
                5000,
                16,
                len(prompt),
                0,
            )
            + prompt,
        )

    def test_wire_failure_debug_is_mapped_to_fixed_allowlisted_classes(self):
        cases = (
            (
                target.WIRE_STATUS_TIMEOUT,
                b"server_loading",
                target.WireFailureClass.SERVER_LOADING,
            ),
            (
                target.WIRE_STATUS_ERROR,
                b"timeout",
                target.WireFailureClass.TIMEOUT,
            ),
            (
                target.WIRE_STATUS_ERROR,
                b"empty_content",
                target.WireFailureClass.EMPTY_CONTENT,
            ),
            (
                target.WIRE_STATUS_ERROR,
                b"connect_failed",
                target.WireFailureClass.IO_FAILED,
            ),
            (
                target.WIRE_STATUS_ERROR,
                b"content_field_not_found",
                target.WireFailureClass.NON_OK,
            ),
            (
                target.WIRE_STATUS_ERROR,
                b"/private/runner/model-and-prompt",
                target.WireFailureClass.NON_OK,
            ),
        )
        for status, debug, expected in cases:
            with self.subTest(status=status, debug=debug):
                result, _ = _send_fake_wire_response(
                    status=status,
                    debug=debug,
                )
                self.assertEqual(result, expected)
                self.assertNotIn("private", result.value)

        result, _ = _send_fake_wire_response(
            status=target.WIRE_STATUS_OK,
            value=b" \n",
            debug=b"arbitrary-success-debug",
        )
        self.assertEqual(result, target.WireFailureClass.EMPTY_CONTENT)

        with mock.patch.object(
            target.socket,
            "socket",
            side_effect=OSError("/private/socket/details"),
        ):
            self.assertEqual(
                target._send_wire_request(
                    Path("/private/runtime.sock"), 7, 2.0
                ),
                target.WireFailureClass.IO_FAILED,
            )

    def test_wire_completion_timeout_uses_dominant_fixed_class(self):
        observations = {failure_class: 0 for failure_class in target.WireFailureClass}
        self.assertEqual(
            target._wire_completion_timeout_code(observations),
            "wire_completion_timeout_no_response",
        )
        observations[target.WireFailureClass.SERVER_LOADING] = 4
        observations[target.WireFailureClass.IO_FAILED] = 1
        code = target._wire_completion_timeout_code(observations)
        self.assertEqual(code, "wire_completion_timeout_server_loading")
        self.assertNotIn("/private", code)

    def test_live_probe_reports_only_dominant_fixed_failure_class(self):
        layout = target.RuntimeLayout(
            converter_app=Path("/runtime/Converter.app"),
            resources=Path("/runtime/Resources"),
            scorer=Path("/runtime/mozc_zenz_scorer"),
            server=Path("/runtime/llama-server"),
            model=Path("/runtime/model.gguf"),
        )

        class FakeScorer:
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

        scorer = FakeScorer()

        def stop_scorer(process):
            process.stopped = True

        with mock.patch.object(
            target.subprocess, "Popen", return_value=scorer
        ) as popen, mock.patch.object(
            Path, "exists", return_value=True
        ), mock.patch.object(
            target, "_validate_private_path"
        ), mock.patch.object(
            target,
            "_send_wire_request",
            side_effect=(
                target.WireFailureClass.SERVER_LOADING,
                target.WireFailureClass.SERVER_LOADING,
                target.WireFailureClass.IO_FAILED,
            ),
        ), mock.patch.object(
            target.time,
            "monotonic",
            side_effect=(0.0, 1.0, 2.0, 3.0, 4.0, 31.0),
        ), mock.patch.object(
            target.time, "sleep"
        ), mock.patch.object(
            target, "_stop_scorer", side_effect=stop_scorer
        ):
            with self.assertRaises(target.ProbeFailure) as raised:
                target._probe_live_runtime(layout, 30.0)

        self.assertEqual(
            raised.exception.code,
            "wire_completion_timeout_server_loading",
        )
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(
            popen.call_args.kwargs["stdout"], target.subprocess.DEVNULL
        )
        self.assertEqual(
            popen.call_args.kwargs["stderr"], target.subprocess.DEVNULL
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
            target, "_verify_arm64_macho_contract"
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

    def test_macho_contract_rejects_extra_scorer_architecture(self):
        with mock.patch.object(
            target, "_run", return_value="arm64 x86_64\n"
        ):
            with self.assertRaisesRegex(
                target.ProbeFailure, "scorer_architectures_invalid"
            ):
                target._verify_arm64_macho_contract(
                    Path("/runtime/mozc_zenz_scorer"), "scorer"
                )

    def test_macho_contract_accepts_arm64_and_12_deployment_target(self):
        with mock.patch.object(
            target,
            "_run",
            side_effect=["arm64\n", "    minos 12.0\n"],
        ):
            target._verify_arm64_macho_contract(
                Path("/runtime/mozc_zenz_scorer"), "scorer"
            )

    def test_macho_contract_rejects_wrong_scorer_deployment_target(self):
        with mock.patch.object(
            target,
            "_run",
            side_effect=[
                "arm64\n",
                "    minos 11.0\n",
            ],
        ):
            with self.assertRaisesRegex(
                target.ProbeFailure,
                "scorer_deployment_target_invalid",
            ):
                target._verify_arm64_macho_contract(
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

    def test_main_prints_only_fixed_wire_failure_code(self):
        stderr = io.StringIO()
        with mock.patch.object(
            target,
            "run_probe",
            side_effect=target.ProbeFailure(
                "wire_completion_timeout_server_loading"
            ),
        ):
            with redirect_stderr(stderr):
                self.assertEqual(
                    target.main(["--package", "/private/pkg"]),
                    1,
                )
        self.assertEqual(
            stderr.getvalue(),
            "macOS Zenz runtime probe failed: "
            "wire_completion_timeout_server_loading\n",
        )
        self.assertNotIn("/private", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
