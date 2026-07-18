#!/usr/bin/env python3
"""Normalize the pinned Zenz GGUF for an unmodified upstream llama.cpp.

The published Zenz v3.2 GGUF names a private pre-tokenizer
(`gpt2-small-japanese-char`) and carries incorrect special-token IDs.  Vanilla
llama.cpp consequently rejects it.  This tool rewrites only GGUF metadata:

* tokenizer.ggml.pre -> gpt-2
* BOS/EOS/UNK IDs -> the exact <s>, </s>, and [UNK] vocabulary positions

Tensor descriptors and the entire tensor-data region are copied byte-for-byte.
The source and normalized files are both pinned by the release lock.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "mozkey.zenz_gguf_normalization.v1"
MAGIC = b"GGUF"
GGUF_VERSION = 3
DEFAULT_ALIGNMENT = 32
CUSTOM_PRETOKENIZER = b"gpt2-small-japanese-char"
UPSTREAM_PRETOKENIZER = b"gpt-2"

KEY_PRE = b"tokenizer.ggml.pre"
KEY_TOKENS = b"tokenizer.ggml.tokens"
KEY_BOS = b"tokenizer.ggml.bos_token_id"
KEY_EOS = b"tokenizer.ggml.eos_token_id"
KEY_UNK = b"tokenizer.ggml.unknown_token_id"
KEY_ALIGNMENT = b"general.alignment"

TYPE_UINT8 = 0
TYPE_INT8 = 1
TYPE_UINT16 = 2
TYPE_INT16 = 3
TYPE_UINT32 = 4
TYPE_INT32 = 5
TYPE_FLOAT32 = 6
TYPE_BOOL = 7
TYPE_STRING = 8
TYPE_ARRAY = 9
TYPE_UINT64 = 10
TYPE_INT64 = 11
TYPE_FLOAT64 = 12

SCALAR_SIZES = {
    TYPE_UINT8: 1,
    TYPE_INT8: 1,
    TYPE_UINT16: 2,
    TYPE_INT16: 2,
    TYPE_UINT32: 4,
    TYPE_INT32: 4,
    TYPE_FLOAT32: 4,
    TYPE_BOOL: 1,
    TYPE_UINT64: 8,
    TYPE_INT64: 8,
    TYPE_FLOAT64: 8,
}

MAX_MODEL_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_STRING_BYTES = 4 * 1024 * 1024
MAX_KV_COUNT = 4096
MAX_TENSOR_COUNT = 1_000_000
MAX_ARRAY_COUNT = 2_000_000
MAX_DIMENSIONS = 16

LOCK_PATH = PurePosixPath("tools/release/zenz_gguf_normalization.lock.json")
EXPECTED_LOCK_KEYS = {"normalized", "schema_version", "source"}
EXPECTED_FILE_KEYS = {"path", "sha256", "size_bytes"}
EXPECTED_SOURCE_KEYS = {
    *EXPECTED_FILE_KEYS,
    "repository",
    "repository_commit",
    "source_filename",
}
EXPECTED_NORMALIZED_KEYS = {
    *EXPECTED_FILE_KEYS,
    "metadata_changes",
    "tensor_payload_sha256",
}


class NormalizationError(ValueError):
    """A deterministic, redaction-safe normalization failure."""


@dataclasses.dataclass(frozen=True)
class Entry:
    key: bytes
    type_tag: int
    start: int
    end: int
    value: Any = None


@dataclasses.dataclass(frozen=True)
class ParsedGguf:
    version: int
    tensor_count: int
    entries: tuple[Entry, ...]
    tensor_info_start: int
    tensor_info_end: int
    data_start: int
    alignment: int

    def by_key(self) -> dict[bytes, Entry]:
        output: dict[bytes, Entry] = {}
        for entry in self.entries:
            if entry.key in output:
                raise NormalizationError("duplicate GGUF metadata key")
            output[entry.key] = entry
        return output


class Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.data):
            raise NormalizationError("truncated GGUF")
        value = self.data[self.position : self.position + size]
        self.position += size
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> bytes:
        size = self.u64()
        if size > MAX_STRING_BYTES:
            raise NormalizationError("GGUF string exceeds the release limit")
        return self.take(size)

    def value(self, type_tag: int, *, capture_strings: bool = False) -> Any:
        if type_tag in SCALAR_SIZES:
            raw = self.take(SCALAR_SIZES[type_tag])
            if type_tag == TYPE_UINT32:
                return struct.unpack("<I", raw)[0]
            if type_tag == TYPE_BOOL:
                if raw not in {b"\x00", b"\x01"}:
                    raise NormalizationError("invalid GGUF boolean")
                return raw == b"\x01"
            return None
        if type_tag == TYPE_STRING:
            return self.string()
        if type_tag != TYPE_ARRAY:
            raise NormalizationError("unsupported GGUF metadata type")

        element_type = self.u32()
        if element_type == TYPE_ARRAY or element_type not in {
            *SCALAR_SIZES,
            TYPE_STRING,
        }:
            raise NormalizationError("unsupported GGUF array element type")
        count = self.u64()
        if count > MAX_ARRAY_COUNT:
            raise NormalizationError("GGUF array exceeds the release limit")
        strings: list[bytes] | None = [] if capture_strings else None
        for _ in range(count):
            item = self.value(element_type)
            if strings is not None:
                if element_type != TYPE_STRING or not isinstance(item, bytes):
                    raise NormalizationError("token vocabulary is not a string array")
                strings.append(item)
        return strings


def _round_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment > 1 << 20 or alignment & (alignment - 1):
        raise NormalizationError("invalid GGUF alignment")
    return (value + alignment - 1) // alignment * alignment


def parse_gguf(data: bytes) -> ParsedGguf:
    if not data or len(data) > MAX_MODEL_BYTES:
        raise NormalizationError("GGUF size is outside the release limit")
    cursor = Cursor(data)
    if cursor.take(4) != MAGIC:
        raise NormalizationError("invalid GGUF magic")
    version = cursor.u32()
    if version != GGUF_VERSION:
        raise NormalizationError("unsupported GGUF version")
    tensor_count = cursor.u64()
    kv_count = cursor.u64()
    if tensor_count <= 0 or tensor_count > MAX_TENSOR_COUNT:
        raise NormalizationError("invalid GGUF tensor count")
    if kv_count <= 0 or kv_count > MAX_KV_COUNT:
        raise NormalizationError("invalid GGUF metadata count")

    entries: list[Entry] = []
    for _ in range(kv_count):
        start = cursor.position
        key = cursor.string()
        if not key or len(key) > 1024:
            raise NormalizationError("invalid GGUF metadata key")
        type_tag = cursor.u32()
        value = cursor.value(type_tag, capture_strings=key == KEY_TOKENS)
        entries.append(Entry(key, type_tag, start, cursor.position, value))
        if cursor.position > MAX_METADATA_BYTES:
            raise NormalizationError("GGUF metadata exceeds the release limit")

    tensor_info_start = cursor.position
    for _ in range(tensor_count):
        cursor.string()
        dimensions = cursor.u32()
        if dimensions <= 0 or dimensions > MAX_DIMENSIONS:
            raise NormalizationError("invalid GGUF tensor dimensions")
        cursor.take(dimensions * 8)
        cursor.take(4)  # ggml type
        cursor.take(8)  # tensor offset relative to the data region
        if cursor.position > MAX_METADATA_BYTES:
            raise NormalizationError("GGUF tensor metadata exceeds the release limit")
    tensor_info_end = cursor.position

    provisional = ParsedGguf(
        version=version,
        tensor_count=tensor_count,
        entries=tuple(entries),
        tensor_info_start=tensor_info_start,
        tensor_info_end=tensor_info_end,
        data_start=0,
        alignment=DEFAULT_ALIGNMENT,
    )
    by_key = provisional.by_key()
    alignment_entry = by_key.get(KEY_ALIGNMENT)
    alignment = DEFAULT_ALIGNMENT
    if alignment_entry is not None:
        if alignment_entry.type_tag != TYPE_UINT32 or not isinstance(
            alignment_entry.value, int
        ):
            raise NormalizationError("invalid GGUF alignment metadata")
        alignment = alignment_entry.value
    data_start = _round_up(tensor_info_end, alignment)
    if data_start >= len(data):
        raise NormalizationError("GGUF has no tensor data")
    return dataclasses.replace(
        provisional, data_start=data_start, alignment=alignment
    )


def _encode_string(value: bytes) -> bytes:
    return struct.pack("<Q", len(value)) + value


def _encode_entry(key: bytes, type_tag: int, value: bytes | int) -> bytes:
    output = bytearray(_encode_string(key))
    output.extend(struct.pack("<I", type_tag))
    if type_tag == TYPE_STRING and isinstance(value, bytes):
        output.extend(_encode_string(value))
    elif type_tag == TYPE_UINT32 and isinstance(value, int):
        output.extend(struct.pack("<I", value))
    else:
        raise NormalizationError("invalid normalized metadata value")
    return bytes(output)


def _required_token_ids(parsed: ParsedGguf) -> dict[bytes, int]:
    tokens_entry = parsed.by_key().get(KEY_TOKENS)
    if (
        tokens_entry is None
        or tokens_entry.type_tag != TYPE_ARRAY
        or not isinstance(tokens_entry.value, list)
    ):
        raise NormalizationError("GGUF lacks a string token vocabulary")
    output: dict[bytes, int] = {}
    for token in (b"<s>", b"</s>", b"[UNK]"):
        matches = [
            index for index, candidate in enumerate(tokens_entry.value) if candidate == token
        ]
        if len(matches) != 1:
            raise NormalizationError("GGUF special token is missing or duplicated")
        output[token] = matches[0]
    return output


def inspect_metadata(data: bytes) -> dict[str, Any]:
    parsed = parse_gguf(data)
    by_key = parsed.by_key()
    required = _required_token_ids(parsed)

    def require(type_key: bytes, type_tag: int) -> Any:
        entry = by_key.get(type_key)
        if entry is None or entry.type_tag != type_tag:
            raise NormalizationError("GGUF normalization metadata is missing")
        return entry.value

    return {
        "pre_tokenizer": require(KEY_PRE, TYPE_STRING),
        "bos_token_id": require(KEY_BOS, TYPE_UINT32),
        "eos_token_id": require(KEY_EOS, TYPE_UINT32),
        "unknown_token_id": (
            by_key[KEY_UNK].value
            if KEY_UNK in by_key and by_key[KEY_UNK].type_tag == TYPE_UINT32
            else None
        ),
        "required_token_ids": required,
        "tensor_payload_sha256": hashlib.sha256(data[parsed.data_start :]).hexdigest(),
        "parsed": parsed,
    }


def normalized_bytes(source: bytes) -> bytes:
    metadata = inspect_metadata(source)
    parsed = metadata["parsed"]
    assert isinstance(parsed, ParsedGguf)
    if metadata["pre_tokenizer"] != CUSTOM_PRETOKENIZER:
        raise NormalizationError("source GGUF has an unexpected pre-tokenizer")
    required = metadata["required_token_ids"]
    assert isinstance(required, dict)
    replacements: dict[bytes, tuple[int, bytes | int]] = {
        KEY_PRE: (TYPE_STRING, UPSTREAM_PRETOKENIZER),
        KEY_BOS: (TYPE_UINT32, required[b"<s>"]),
        KEY_EOS: (TYPE_UINT32, required[b"</s>"]),
        KEY_UNK: (TYPE_UINT32, required[b"[UNK]"]),
    }

    by_key = parsed.by_key()
    output = bytearray(MAGIC)
    output.extend(struct.pack("<IQQ", parsed.version, parsed.tensor_count, len(parsed.entries) + (0 if KEY_UNK in by_key else 1)))
    for entry in parsed.entries:
        replacement = replacements.get(entry.key)
        if replacement is None:
            output.extend(source[entry.start : entry.end])
        else:
            output.extend(_encode_entry(entry.key, *replacement))
    if KEY_UNK not in by_key:
        output.extend(_encode_entry(KEY_UNK, *replacements[KEY_UNK]))
    output.extend(source[parsed.tensor_info_start : parsed.tensor_info_end])
    output.extend(b"\0" * (_round_up(len(output), parsed.alignment) - len(output)))
    output.extend(source[parsed.data_start :])
    return bytes(output)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str):
        raise NormalizationError(f"{label} path is invalid")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise NormalizationError(f"{label} path is unsafe")
    path = root.joinpath(*relative.parts)
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError as error:
        raise NormalizationError(f"{label} path escapes the repository") from error
    return path


def _load_lock(root: Path) -> tuple[Mapping[str, Any], Path, Path]:
    lock_path = root.joinpath(*LOCK_PATH.parts)
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NormalizationError("cannot read the Zenz normalization lock") from error
    if not isinstance(lock, Mapping) or set(lock) != EXPECTED_LOCK_KEYS:
        raise NormalizationError("invalid Zenz normalization lock")
    if lock.get("schema_version") != SCHEMA:
        raise NormalizationError("unexpected Zenz normalization lock schema")
    source = lock.get("source")
    normalized = lock.get("normalized")
    if (
        not isinstance(source, Mapping)
        or set(source) != EXPECTED_SOURCE_KEYS
        or not isinstance(normalized, Mapping)
        or set(normalized) != EXPECTED_NORMALIZED_KEYS
    ):
        raise NormalizationError("invalid Zenz normalization file records")
    return (
        lock,
        _safe_relative_path(root, source.get("path"), "source model"),
        _safe_relative_path(root, normalized.get("path"), "normalized model"),
    )


def _require_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise NormalizationError(f"{label} is unavailable") from error
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise NormalizationError(f"{label} is not a safe regular file")
    return info


def _require_hex_digest(value: object, label: str, *, allow_unset: bool = False) -> str | None:
    if value is None and allow_unset:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise NormalizationError(f"{label} digest is invalid")
    return value


def _verify_file_record(path: Path, record: Mapping[str, Any], label: str) -> bytes:
    info = _require_regular(path, label)
    size = record.get("size_bytes")
    digest = _require_hex_digest(record.get("sha256"), label)
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise NormalizationError(f"{label} size is invalid")
    if info.st_size != size or sha256_file(path) != digest:
        raise NormalizationError(f"{label} does not match the release lock")
    try:
        return path.read_bytes()
    except OSError as error:
        raise NormalizationError(f"cannot read {label}") from error


def _verify_normalized_metadata(data: bytes, record: Mapping[str, Any]) -> None:
    metadata = inspect_metadata(data)
    required = metadata["required_token_ids"]
    assert isinstance(required, dict)
    expected_changes = record.get("metadata_changes")
    if expected_changes != {
        "tokenizer.ggml.bos_token_id": required[b"<s>"],
        "tokenizer.ggml.eos_token_id": required[b"</s>"],
        "tokenizer.ggml.pre": "gpt-2",
        "tokenizer.ggml.unknown_token_id": required[b"[UNK]"],
    }:
        raise NormalizationError("Zenz normalization metadata lock changed")
    if (
        metadata["pre_tokenizer"] != UPSTREAM_PRETOKENIZER
        or metadata["bos_token_id"] != required[b"<s>"]
        or metadata["eos_token_id"] != required[b"</s>"]
        or metadata["unknown_token_id"] != required[b"[UNK]"]
    ):
        raise NormalizationError("normalized Zenz metadata is incorrect")
    payload_digest = _require_hex_digest(
        record.get("tensor_payload_sha256"), "tensor payload"
    )
    if metadata["tensor_payload_sha256"] != payload_digest:
        raise NormalizationError("normalized Zenz tensor payload changed")


def create(root: Path) -> tuple[Path, str, int]:
    root = root.resolve()
    lock, source_path, output_path = _load_lock(root)
    source_record = lock["source"]
    normalized_record = lock["normalized"]
    assert isinstance(source_record, Mapping)
    assert isinstance(normalized_record, Mapping)
    source = _verify_file_record(source_path, source_record, "source Zenz model")
    normalized = normalized_bytes(source)
    _verify_normalized_metadata(normalized, normalized_record)

    expected_digest = _require_hex_digest(
        normalized_record.get("sha256"), "normalized Zenz model", allow_unset=True
    )
    expected_size = normalized_record.get("size_bytes")
    digest = sha256_bytes(normalized)
    size = len(normalized)
    if expected_digest is not None and digest != expected_digest:
        raise NormalizationError("normalized Zenz model digest changed")
    if expected_size is not None and expected_size != size:
        raise NormalizationError("normalized Zenz model size changed")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() or output_path.is_symlink():
        _require_regular(output_path, "normalized Zenz output")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(normalized)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path, digest, size


def verify(root: Path) -> Path:
    root = root.resolve()
    lock, source_path, output_path = _load_lock(root)
    source_record = lock["source"]
    normalized_record = lock["normalized"]
    assert isinstance(source_record, Mapping)
    assert isinstance(normalized_record, Mapping)
    source = _verify_file_record(source_path, source_record, "source Zenz model")
    output = _verify_file_record(output_path, normalized_record, "normalized Zenz model")
    _verify_normalized_metadata(output, normalized_record)

    source_metadata = inspect_metadata(source)
    output_metadata = inspect_metadata(output)
    if source_metadata["tensor_payload_sha256"] != output_metadata["tensor_payload_sha256"]:
        raise NormalizationError("Zenz tensor payload differs from the source model")
    if normalized_bytes(source) != output:
        raise NormalizationError("normalized Zenz model is not reproducible")
    return output_path


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize the pinned Zenz GGUF for upstream llama.cpp"
    )
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "create":
            path, digest, size = create(arguments.root)
            print(f"normalized Zenz GGUF: {path} sha256={digest} size={size}")
        else:
            path = verify(arguments.root)
            print(f"normalized Zenz GGUF verified: {path}")
        return 0
    except NormalizationError as error:
        print(f"Zenz GGUF normalization failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
