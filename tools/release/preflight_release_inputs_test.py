from __future__ import annotations

import hashlib
import io
from pathlib import Path
import unittest

from tools.release.preflight_release_inputs import (
    InputPreflightError,
    PinnedInput,
    load_pinned_inputs,
    verify_pinned_input,
)


class _Response(io.BytesIO):
    def __init__(
        self,
        payload: bytes,
        *,
        url: str = "https://cdn.example.invalid/input",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(payload)
        self._url = url
        self.status = status
        self.headers = headers or {"Content-Length": str(len(payload))}

    def geturl(self) -> str:
        return self._url


class ReleaseInputPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[2]

    def test_repository_runtime_locks_are_consistent(self) -> None:
        inputs, model_path, model_digest, model_size = load_pinned_inputs(
            self.repository
        )

        self.assertTrue(model_path.is_file())
        self.assertEqual(model_path.stat().st_size, model_size)
        self.assertEqual(len(model_digest), 64)
        self.assertEqual(
            {item.name for item in inputs},
            {
                "llama.cpp source",
                "Zenz source model",
                "QT6",
                "NDK_LINUX",
                "NDK_MAC",
                "NINJA_MAC",
                "NINJA_WIN",
                "NINJA_WIN_ARM64",
                "LLVM_WIN",
                "LLVM_WIN_ARM64",
                "MSYS2",
            },
        )

    def test_full_download_checks_size_and_digest(self) -> None:
        payload = b"pinned-input"
        pinned = PinnedInput(
            name="fixture",
            url="https://example.invalid/input",
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            download_and_hash=True,
        )

        verify_pinned_input(pinned, lambda *_args, **_kwargs: _Response(payload))

        mismatched = PinnedInput(
            name=pinned.name,
            url=pinned.url,
            sha256="0" * 64,
            size=pinned.size,
            download_and_hash=True,
        )
        with self.assertRaisesRegex(InputPreflightError, "checksum mismatch"):
            verify_pinned_input(
                mismatched,
                lambda *_args, **_kwargs: _Response(payload),
            )

    def test_head_probe_rejects_size_or_transport_drift(self) -> None:
        pinned = PinnedInput(
            name="fixture",
            url="https://example.invalid/input",
            sha256="1" * 64,
            size=10,
            download_and_hash=False,
        )
        with self.assertRaisesRegex(InputPreflightError, "size changed"):
            verify_pinned_input(
                pinned,
                lambda *_args, **_kwargs: _Response(
                    b"", headers={"Content-Length": "9"}
                ),
            )
        with self.assertRaisesRegex(InputPreflightError, "outside HTTPS"):
            verify_pinned_input(
                pinned,
                lambda *_args, **_kwargs: _Response(
                    b"",
                    url="http://example.invalid/input",
                    headers={"Content-Length": "10"},
                ),
            )


if __name__ == "__main__":
    unittest.main()
