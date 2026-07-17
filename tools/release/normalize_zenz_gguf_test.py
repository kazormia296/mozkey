#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest

from tools.release import normalize_zenz_gguf as target


TOKENS = (b"[UNK]", b"ordinary", b"<s>", b"</s>", b"tail")
TENSOR_PAYLOAD = bytes(range(1, 65)) + b"synthetic-gguf-tensor-payload\x00\xff"
SOURCE_PATH = "fixtures/source-zenz.gguf"
NORMALIZED_PATH = "dist/zenz/linux/normalized-zenz.gguf"


def encode_string(value: bytes) -> bytes:
    return struct.pack("<Q", len(value)) + value


def encode_entry(key: bytes, type_tag: int, value: object) -> bytes:
    output = bytearray(encode_string(key))
    output.extend(struct.pack("<I", type_tag))
    if type_tag == target.TYPE_STRING and isinstance(value, bytes):
        output.extend(encode_string(value))
    elif type_tag == target.TYPE_UINT32 and isinstance(value, int):
        output.extend(struct.pack("<I", value))
    elif type_tag == target.TYPE_ARRAY and isinstance(value, tuple):
        output.extend(struct.pack("<IQ", target.TYPE_STRING, len(value)))
        for item in value:
            if not isinstance(item, bytes):
                raise TypeError("synthetic token entries must be bytes")
            output.extend(encode_string(item))
    else:
        raise TypeError("unsupported synthetic GGUF metadata")
    return bytes(output)


def fixture_entries(
    *,
    pre_tokenizer: bytes = target.CUSTOM_PRETOKENIZER,
    bos_token_id: int = 99,
    eos_token_id: int = 98,
    unknown_token_id: int | None = 97,
    tokens: tuple[bytes, ...] = TOKENS,
) -> list[tuple[bytes, int, object]]:
    entries: list[tuple[bytes, int, object]] = [
        (target.KEY_ALIGNMENT, target.TYPE_UINT32, target.DEFAULT_ALIGNMENT),
        (b"general.name", target.TYPE_STRING, b"synthetic-zenz"),
        (target.KEY_PRE, target.TYPE_STRING, pre_tokenizer),
        (target.KEY_TOKENS, target.TYPE_ARRAY, tokens),
        (target.KEY_BOS, target.TYPE_UINT32, bos_token_id),
        (target.KEY_EOS, target.TYPE_UINT32, eos_token_id),
    ]
    if unknown_token_id is not None:
        entries.append((target.KEY_UNK, target.TYPE_UINT32, unknown_token_id))
    return entries


def build_gguf(
    entries: list[tuple[bytes, int, object]],
    *,
    payload: bytes = TENSOR_PAYLOAD,
) -> bytes:
    output = bytearray(target.MAGIC)
    output.extend(struct.pack("<IQQ", target.GGUF_VERSION, 1, len(entries)))
    for key, type_tag, value in entries:
        output.extend(encode_entry(key, type_tag, value))

    # One small, syntactically valid GGUF v3 tensor descriptor. The parser does
    # not need a real quantized tensor for metadata-only normalization tests.
    output.extend(encode_string(b"synthetic.weight"))
    output.extend(struct.pack("<I", 1))
    output.extend(struct.pack("<Q", 16))
    output.extend(struct.pack("<I", 0))
    output.extend(struct.pack("<Q", 0))
    padding = (-len(output)) % target.DEFAULT_ALIGNMENT
    output.extend(b"\0" * padding)
    output.extend(payload)
    return bytes(output)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NormalizeZenzGgufTest(unittest.TestCase):
    def normalized_fixture(self, *, include_unknown: bool = True) -> bytes:
        return build_gguf(
            fixture_entries(
                pre_tokenizer=target.UPSTREAM_PRETOKENIZER,
                bos_token_id=TOKENS.index(b"<s>"),
                eos_token_id=TOKENS.index(b"</s>"),
                unknown_token_id=(TOKENS.index(b"[UNK]") if include_unknown else None),
            )
        )

    def write_lock(
        self,
        root: Path,
        source: bytes,
        normalized: bytes,
        *,
        source_sha256: str | None = None,
        normalized_sha256: str | None = None,
    ) -> tuple[Path, Path, Path]:
        source_path = root / SOURCE_PATH
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(source)
        normalized_path = root / NORMALIZED_PATH
        lock_path = root.joinpath(*target.LOCK_PATH.parts)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        parsed_normalized = target.inspect_metadata(normalized)
        lock = {
            "normalized": {
                "metadata_changes": {
                    "tokenizer.ggml.bos_token_id": TOKENS.index(b"<s>"),
                    "tokenizer.ggml.eos_token_id": TOKENS.index(b"</s>"),
                    "tokenizer.ggml.pre": "gpt-2",
                    "tokenizer.ggml.unknown_token_id": TOKENS.index(b"[UNK]"),
                },
                "path": NORMALIZED_PATH,
                "sha256": normalized_sha256 or sha256(normalized),
                "size_bytes": len(normalized),
                "tensor_payload_sha256": parsed_normalized[
                    "tensor_payload_sha256"
                ],
            },
            "schema_version": target.SCHEMA,
            "source": {
                "path": SOURCE_PATH,
                "repository": "https://example.invalid/synthetic-zenz",
                "repository_commit": "1" * 40,
                "sha256": source_sha256 or sha256(source),
                "size_bytes": len(source),
                "source_filename": "synthetic-zenz.gguf",
            },
        }
        lock_path.write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return source_path, normalized_path, lock_path

    def test_custom_metadata_normalizes_deterministically_and_preserves_tensor_data(
        self,
    ) -> None:
        source = build_gguf(fixture_entries())
        expected = self.normalized_fixture()

        first = target.normalized_bytes(source)
        second = target.normalized_bytes(source)
        self.assertEqual(first, expected)
        self.assertEqual(second, first)

        source_metadata = target.inspect_metadata(source)
        normalized_metadata = target.inspect_metadata(first)
        self.assertEqual(source_metadata["pre_tokenizer"], target.CUSTOM_PRETOKENIZER)
        self.assertEqual(source_metadata["bos_token_id"], 99)
        self.assertEqual(source_metadata["eos_token_id"], 98)
        self.assertEqual(source_metadata["unknown_token_id"], 97)
        self.assertEqual(
            normalized_metadata["pre_tokenizer"], target.UPSTREAM_PRETOKENIZER
        )
        self.assertEqual(normalized_metadata["bos_token_id"], TOKENS.index(b"<s>"))
        self.assertEqual(normalized_metadata["eos_token_id"], TOKENS.index(b"</s>"))
        self.assertEqual(
            normalized_metadata["unknown_token_id"], TOKENS.index(b"[UNK]")
        )

        source_parsed = source_metadata["parsed"]
        normalized_parsed = normalized_metadata["parsed"]
        self.assertEqual(source[source_parsed.data_start :], TENSOR_PAYLOAD)
        self.assertEqual(first[normalized_parsed.data_start :], TENSOR_PAYLOAD)
        self.assertEqual(
            source[
                source_parsed.tensor_info_start : source_parsed.tensor_info_end
            ],
            first[
                normalized_parsed.tensor_info_start : normalized_parsed.tensor_info_end
            ],
        )
        self.assertEqual(
            source_metadata["tensor_payload_sha256"],
            normalized_metadata["tensor_payload_sha256"],
        )

    def test_create_is_reproducible_and_verify_accepts_the_locked_output(self) -> None:
        source = build_gguf(fixture_entries())
        expected = self.normalized_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, output_path, _ = self.write_lock(root, source, expected)

            created_path, digest, size = target.create(root)
            self.assertEqual(created_path, output_path)
            self.assertEqual(output_path.read_bytes(), expected)
            self.assertEqual(digest, sha256(expected))
            self.assertEqual(size, len(expected))
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o644)
            self.assertEqual(target.verify(root), output_path)

            first = output_path.read_bytes()
            target.create(root)
            self.assertEqual(output_path.read_bytes(), first)

    def test_missing_unknown_id_is_inserted_at_the_required_vocabulary_position(
        self,
    ) -> None:
        source = build_gguf(fixture_entries(unknown_token_id=None))
        normalized = target.normalized_bytes(source)
        metadata = target.inspect_metadata(normalized)
        self.assertEqual(metadata["unknown_token_id"], TOKENS.index(b"[UNK]"))
        self.assertEqual(len(metadata["parsed"].entries), 7)

    def test_rejects_truncated_gguf(self) -> None:
        source = build_gguf(fixture_entries())
        with self.assertRaisesRegex(target.NormalizationError, "truncated GGUF"):
            target.parse_gguf(source[:20])

    def test_create_rejects_wrong_source_digest(self) -> None:
        source = build_gguf(fixture_entries())
        expected = self.normalized_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_lock(root, source, expected, source_sha256="0" * 64)
            with self.assertRaisesRegex(
                target.NormalizationError, "does not match the release lock"
            ):
                target.create(root)

    def test_verify_rejects_wrong_normalized_digest(self) -> None:
        source = build_gguf(fixture_entries())
        expected = self.normalized_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, output_path, _ = self.write_lock(root, source, expected)
            target.create(root)
            output_path.write_bytes(output_path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(
                target.NormalizationError, "does not match the release lock"
            ):
                target.verify(root)

    def test_create_rejects_source_and_output_symlinks(self) -> None:
        source = build_gguf(fixture_entries())
        expected = self.normalized_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path, output_path, _ = self.write_lock(root, source, expected)
            real_source = source_path.with_name("real-source.gguf")
            source_path.replace(real_source)
            source_path.symlink_to(real_source.name)
            with self.assertRaisesRegex(target.NormalizationError, "safe regular file"):
                target.create(root)

            source_path.unlink()
            real_source.replace(source_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.symlink_to(source_path)
            with self.assertRaisesRegex(target.NormalizationError, "safe regular file"):
                target.create(root)

    def test_rejects_duplicate_required_metadata(self) -> None:
        entries = fixture_entries()
        entries.append((target.KEY_BOS, target.TYPE_UINT32, 2))
        duplicate = build_gguf(entries)
        with self.assertRaisesRegex(
            target.NormalizationError, "duplicate GGUF metadata key"
        ):
            target.normalized_bytes(duplicate)


if __name__ == "__main__":
    unittest.main()
