#!/usr/bin/env python3
"""Create and verify Mozkey Linux Bazel build attestations.

The attestation binds one successful, exact release build invocation to the
current Git HEAD, the approved dictionary inputs, and the five installable
Bazel outputs.  It intentionally contains no timestamp so the same state
produces the same document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping

try:
    from tools.release import normalize_zenz_gguf as zenz_normalizer
except ModuleNotFoundError:  # Direct execution sets sys.path to tools/release.
    import normalize_zenz_gguf as zenz_normalizer


SCHEMA = "mozkey.linux_build_attestation.v2"
RELEASE_DICTIONARY_MANIFEST_SCHEMA = "mozkey.release_dictionary_outputs.v1"
SOURCE_LOCK_SCHEMA = "mozkey.daily_dictionary_source_lock.v2"
RELEASE_PROFILE = "release-approved-only"
NICO_SOURCE_ID = "dic-nico-intersection-pixiv"

COMMON_TARGETS = (
    "//unix/fcitx5:fcitx5-mozkey.so",
    "//unix/fcitx5:grimodex_consumer_tool",
    "//server:mozc_server",
    "//gui/tool:mozc_tool",
    "//zenz_scorer:mozc_zenz_scorer",
)
COMMON_FLAGS = (
    "--config=oss_linux",
    "--config=release_build",
    "--define=mozkey_dictionary_profile=release-approved-only",
    "--//unix/fcitx5:use_server=true",
)
LAYOUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "ubuntu-layout": {
        "targets": ("package", *COMMON_TARGETS),
        "flags": COMMON_FLAGS,
    },
    "archlinux-x86_64": {
        "targets": COMMON_TARGETS,
        "flags": (
            "--config=oss_linux",
            "--config=release_build",
            "--config=no_sframe",
            "--define=mozkey_dictionary_profile=release-approved-only",
            "--//unix/fcitx5:use_server=true",
        ),
    },
}
BAZEL_DRIVERS = {
    "bazelisk": ("bazelisk",),
    "npx-bazelisk": ("npx", "--yes", "@bazel/bazelisk@1.28.1"),
}

RELEASE_MANIFEST_PATH = PurePosixPath(
    "dist/dictionary/linux-release-approved-output-manifest.json"
)
SOURCE_LOCK_PATH = PurePosixPath("tools/dictionary/daily_sources.lock.json")
DICTIONARY_OUTPUT_PATHS = (
    PurePosixPath(
        "src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-daily.txt"
    ),
    PurePosixPath(
        "src/data/dictionary_koyasi/generated/profiled/"
        "mozcdic-ut-personal-names-daily.txt"
    ),
    PurePosixPath(
        "src/data/dictionary_koyasi/generated/profiled/koyasi-syntax-guard.txt"
    ),
)
BINARY_PATHS = (
    PurePosixPath("src/bazel-bin/unix/fcitx5/fcitx5-mozkey.so"),
    PurePosixPath("src/bazel-bin/unix/fcitx5/grimodex_consumer_tool"),
    PurePosixPath("src/bazel-bin/server/mozc_server"),
    PurePosixPath("src/bazel-bin/gui/tool/mozc_tool"),
    PurePosixPath("src/bazel-bin/zenz_scorer/mozc_zenz_scorer"),
)
ZENZ_SOURCE_MODEL_PATH = PurePosixPath(
    "src/win32/installer/zenz_runtime/models/zenz-v3.2-small-Q5_K_M.gguf"
)
ZENZ_NORMALIZED_MODEL_PATH = PurePosixPath(
    "dist/zenz/linux/zenz-v3.2-small-Q5_K_M.gguf"
)
ZENZ_NORMALIZATION_LOCK_PATH = PurePosixPath(
    "tools/release/zenz_gguf_normalization.lock.json"
)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_KEYS = {
    "bazel",
    "binaries",
    "dictionary",
    "git_head",
    "layout",
    "schema_version",
    "zenz_runtime",
}
_FILE_RECORD_KEYS = {"path", "sha256", "size_bytes"}


class AttestationError(ValueError):
    """The build state or attestation is unsafe or inconsistent."""


def _run_git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise AttestationError(f"Git inspection failed: {error}") from error
    return result.stdout.strip()


def git_head(root: Path) -> str:
    head = _run_git(root, "rev-parse", "HEAD")
    if not _HEX40.fullmatch(head):
        raise AttestationError("Git HEAD is not a full 40-character object id")
    return head


def require_clean_worktree(root: Path) -> None:
    status = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules",
    )
    if status:
        raise AttestationError(
            "Git worktree or index contains tracked changes or non-ignored "
            "untracked files"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root_path(root: Path, relative: PurePosixPath) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise AttestationError(f"unsafe repository-relative path: {relative}")
    return root.joinpath(*relative.parts)


def file_record(root: Path, relative: PurePosixPath) -> dict[str, Any]:
    path = _root_path(root, relative)
    if not path.is_file() or path.is_symlink():
        raise AttestationError(f"missing or unsafe attested file: {relative}")
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def binary_record(root: Path, relative: PurePosixPath) -> dict[str, Any]:
    path = _root_path(root, relative)
    record = file_record(root, relative)
    if not os.access(path, os.X_OK):
        raise AttestationError(f"attested Bazel output is not executable: {relative}")
    return record


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AttestationError(f"cannot read {label}: {error}") from error
    if not isinstance(value, Mapping):
        raise AttestationError(f"{label} must be a JSON object")
    return value


def _verify_source_lock(root: Path) -> dict[str, Any]:
    record = file_record(root, SOURCE_LOCK_PATH)
    lock = _load_json(_root_path(root, SOURCE_LOCK_PATH), "dictionary source lock")
    if lock.get("schema_version") != SOURCE_LOCK_SCHEMA:
        raise AttestationError("unexpected dictionary source-lock schema")
    profiles = lock.get("profiles")
    sources = lock.get("sources")
    if not isinstance(profiles, Mapping) or not isinstance(sources, Mapping):
        raise AttestationError("dictionary source lock lacks profiles or sources")
    release = profiles.get(RELEASE_PROFILE)
    if not isinstance(release, Mapping) or not isinstance(
        release.get("source_ids"), list
    ):
        raise AttestationError("dictionary source lock lacks the release profile")
    source_ids = release["source_ids"]
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise AttestationError("release dictionary source ids are empty or duplicated")
    if NICO_SOURCE_ID in source_ids:
        raise AttestationError("Nico/Pixiv entered the release dictionary source lock")
    for source_id in source_ids:
        source = sources.get(source_id)
        if not isinstance(source, Mapping) or source.get("release_approved") is not True:
            raise AttestationError(f"unapproved release dictionary source: {source_id}")
    return record


def _verify_release_dictionary(root: Path) -> dict[str, Any]:
    lock_record = _verify_source_lock(root)
    manifest_record = file_record(root, RELEASE_MANIFEST_PATH)
    manifest = _load_json(
        _root_path(root, RELEASE_MANIFEST_PATH), "release dictionary manifest"
    )
    if manifest.get("schema_version") != RELEASE_DICTIONARY_MANIFEST_SCHEMA:
        raise AttestationError("unexpected release dictionary manifest schema")
    if manifest.get("profile") != RELEASE_PROFILE:
        raise AttestationError("release dictionary manifest has the wrong profile")
    if manifest.get("excluded_source_ids") != [NICO_SOURCE_ID]:
        raise AttestationError("release dictionary exclusion list changed")
    if manifest.get("source_lock_path") != SOURCE_LOCK_PATH.as_posix():
        raise AttestationError("release dictionary source-lock path changed")
    if manifest.get("source_lock_sha256") != lock_record["sha256"]:
        raise AttestationError("release dictionary source-lock digest mismatch")

    expected_paths = tuple(path.as_posix() for path in DICTIONARY_OUTPUT_PATHS)
    records = manifest.get("files")
    if not isinstance(records, list) or len(records) != len(expected_paths):
        raise AttestationError("release dictionary manifest is not the output allowlist")
    by_path: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _FILE_RECORD_KEYS:
            raise AttestationError("invalid release dictionary file record")
        path = record.get("path")
        if not isinstance(path, str) or path in by_path:
            raise AttestationError("invalid or duplicate release dictionary path")
        by_path[path] = record
    if set(by_path) != set(expected_paths):
        raise AttestationError("release dictionary manifest contains a wrong output")

    output_records = []
    for relative in DICTIONARY_OUTPUT_PATHS:
        actual = file_record(root, relative)
        if dict(by_path[relative.as_posix()]) != actual:
            raise AttestationError(
                f"release dictionary output does not match its manifest: {relative}"
            )
        output_records.append(actual)
    return {
        "outputs": output_records,
        "release_manifest": manifest_record,
        "source_lock": lock_record,
    }


def _verify_zenz_runtime(root: Path) -> dict[str, Any]:
    try:
        zenz_normalizer.verify(root)
    except zenz_normalizer.NormalizationError as error:
        raise AttestationError(f"Zenz normalization is invalid: {error}") from error

    lock = _load_json(
        _root_path(root, ZENZ_NORMALIZATION_LOCK_PATH),
        "Zenz normalization lock",
    )
    source = lock.get("source")
    normalized = lock.get("normalized")
    if (
        lock.get("schema_version") != zenz_normalizer.SCHEMA
        or not isinstance(source, Mapping)
        or not isinstance(normalized, Mapping)
        or source.get("path") != ZENZ_SOURCE_MODEL_PATH.as_posix()
        or normalized.get("path") != ZENZ_NORMALIZED_MODEL_PATH.as_posix()
    ):
        raise AttestationError("Zenz normalization lock paths or schema changed")

    source_record = file_record(root, ZENZ_SOURCE_MODEL_PATH)
    normalized_record = file_record(root, ZENZ_NORMALIZED_MODEL_PATH)
    for actual, locked, label in (
        (source_record, source, "source"),
        (normalized_record, normalized, "normalized"),
    ):
        if (
            locked.get("sha256") != actual["sha256"]
            or locked.get("size_bytes") != actual["size_bytes"]
        ):
            raise AttestationError(f"Zenz {label} model does not match its lock")

    tensor_payload_sha256 = normalized.get("tensor_payload_sha256")
    if not isinstance(tensor_payload_sha256, str) or not _HEX64.fullmatch(
        tensor_payload_sha256
    ):
        raise AttestationError("Zenz tensor payload digest is invalid")
    return {
        "normalization_lock": file_record(root, ZENZ_NORMALIZATION_LOCK_PATH),
        "normalized_model": normalized_record,
        "source_model": source_record,
        "tensor_payload_sha256": tensor_payload_sha256,
    }


def _layout_spec(layout: str) -> dict[str, list[str]]:
    try:
        spec = LAYOUTS[layout]
    except KeyError as error:
        raise AttestationError(f"unsupported Linux build layout: {layout}") from error
    return {
        "flags": list(spec["flags"]),
        "targets": list(spec["targets"]),
    }


def build_document(root: Path, layout: str, bazel_driver: str) -> dict[str, Any]:
    root = root.resolve()
    require_clean_worktree(root)
    if bazel_driver not in BAZEL_DRIVERS:
        raise AttestationError("unsupported Bazel driver")
    spec = _layout_spec(layout)
    dictionary = _verify_release_dictionary(root)
    zenz_runtime = _verify_zenz_runtime(root)
    binaries = [binary_record(root, path) for path in BINARY_PATHS]
    return {
        "bazel": {
            "driver": bazel_driver,
            "driver_argv": list(BAZEL_DRIVERS[bazel_driver]),
            **spec,
        },
        "binaries": binaries,
        "dictionary": dictionary,
        "git_head": git_head(root),
        "layout": layout,
        "schema_version": SCHEMA,
        "zenz_runtime": zenz_runtime,
    }


def _attestation_path(root: Path, output: Path) -> Path:
    root = root.resolve()
    output = output.resolve()
    allowed_root = (root / "dist" / "linux").resolve()
    try:
        output.relative_to(allowed_root)
    except ValueError as error:
        raise AttestationError("attestation output must be under dist/linux") from error
    return output


def write_document(root: Path, output: Path, document: Mapping[str, Any]) -> None:
    output = _attestation_path(root, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def create(root: Path, layout: str, output: Path, bazel_driver: str) -> dict[str, Any]:
    document = build_document(root, layout, bazel_driver)
    write_document(root, output, document)
    return document


def _validate_file_records(
    records: Any, expected: list[dict[str, Any]], label: str
) -> None:
    if not isinstance(records, list) or records != expected:
        raise AttestationError(f"{label} digest set does not match current files")
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _FILE_RECORD_KEYS:
            raise AttestationError(f"invalid {label} file record")
        if not _HEX64.fullmatch(str(record.get("sha256", ""))):
            raise AttestationError(f"invalid {label} SHA-256")


def verify(root: Path, layout: str, attestation: Path) -> Mapping[str, Any]:
    root = root.resolve()
    require_clean_worktree(root)
    attestation = _attestation_path(root, attestation)
    document = _load_json(attestation, "Linux build attestation")
    if set(document) != _TOP_LEVEL_KEYS:
        raise AttestationError("Linux build attestation top-level shape changed")
    if document.get("schema_version") != SCHEMA:
        raise AttestationError("unexpected Linux build attestation schema")
    if document.get("layout") != layout:
        raise AttestationError("Linux build attestation layout mismatch")
    if document.get("git_head") != git_head(root):
        raise AttestationError("Linux build attestation Git HEAD mismatch")

    bazel = document.get("bazel")
    expected_spec = _layout_spec(layout)
    if not isinstance(bazel, Mapping) or set(bazel) != {
        "driver",
        "driver_argv",
        "flags",
        "targets",
    }:
        raise AttestationError("invalid Bazel attestation shape")
    if bazel.get("driver") not in BAZEL_DRIVERS:
        raise AttestationError("invalid attested Bazel driver")
    if bazel.get("driver_argv") != list(BAZEL_DRIVERS[bazel["driver"]]):
        raise AttestationError("invalid attested Bazel driver invocation")
    if bazel.get("flags") != expected_spec["flags"]:
        raise AttestationError("attested Bazel flags do not match the release profile")
    if bazel.get("targets") != expected_spec["targets"]:
        raise AttestationError("attested Bazel targets do not match the layout")

    expected_dictionary = _verify_release_dictionary(root)
    if document.get("dictionary") != expected_dictionary:
        raise AttestationError("attested dictionary does not match current inputs")
    expected_binaries = [binary_record(root, path) for path in BINARY_PATHS]
    _validate_file_records(document.get("binaries"), expected_binaries, "binary")
    expected_zenz_runtime = _verify_zenz_runtime(root)
    if document.get("zenz_runtime") != expected_zenz_runtime:
        raise AttestationError("attested Zenz runtime does not match current inputs")
    return document


def default_attestation_path(root: Path, layout: str) -> Path:
    _layout_spec(layout)
    return root / "dist" / "linux" / layout / "build-attestation.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--root", required=True, type=Path)
    create_parser.add_argument("--layout", required=True, choices=tuple(LAYOUTS))
    create_parser.add_argument("--output", required=True, type=Path)
    create_parser.add_argument(
        "--bazel-driver", required=True, choices=("bazelisk", "npx-bazelisk")
    )

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", required=True, type=Path)
    verify_parser.add_argument("--layout", required=True, choices=tuple(LAYOUTS))
    verify_parser.add_argument("--attestation", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create(args.root, args.layout, args.output, args.bazel_driver)
            print(f"Linux build attestation: {args.output}")
        else:
            verify(args.root, args.layout, args.attestation)
            print("Mozkey Linux build attestation verified.")
    except AttestationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
