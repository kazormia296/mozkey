from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest

from tools import check_no_network_imports
from tools import check_no_network_strings


class NoNetworkChecksTest(unittest.TestCase):
    def test_missing_build_outputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for module in (check_no_network_imports, check_no_network_strings):
                with self.subTest(module=module.__name__), redirect_stderr(
                    io.StringIO()
                ):
                    self.assertEqual(
                        module.main(["check", "--root", str(root)]),
                        1,
                    )

    def test_llama_server_is_a_runtime_target(self) -> None:
        for module in (check_no_network_imports, check_no_network_strings):
            with self.subTest(module=module.__name__):
                self.assertIsNotNone(
                    module.RUNTIME_BINARY_PATTERN.fullmatch("llama-server.exe")
                )
        self.assertEqual(
            check_no_network_imports.ALLOWED_FORBIDDEN_DLLS_BY_BINARY[
                "llama-server.exe"
            ],
            {"ws2_32.dll"},
        )
        self.assertIn(
            "ws2_32.dll",
            check_no_network_imports.ALLOWED_FORBIDDEN_DLLS_BY_BINARY[
                "mozc_zenz_scorer.exe"
            ],
        )


if __name__ == "__main__":
    unittest.main()
