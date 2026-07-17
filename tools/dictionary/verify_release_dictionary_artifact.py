#!/usr/bin/env python3
"""Verify the allowlisted Linux release dictionary transfer artifact."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

try:
    from tools.dictionary import daily_source_lock
    from tools.dictionary import prepare_daily_dictionary_linux as prepare
except ModuleNotFoundError:  # Direct script execution from the repository root.
    import daily_source_lock  # type: ignore[no-redef]
    import prepare_daily_dictionary_linux as prepare  # type: ignore[no-redef]


class ArtifactVerificationError(ValueError):
    pass


def verify(root: pathlib.Path, manifest_path: pathlib.Path) -> None:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactVerificationError(f"cannot read release manifest: {error}") from error

    if manifest.get("schema_version") != prepare.RELEASE_OUTPUT_MANIFEST_SCHEMA:
        raise ArtifactVerificationError("unexpected release output manifest schema")
    if manifest.get("profile") != daily_source_lock.RELEASE_PROFILE:
        raise ArtifactVerificationError("release output manifest has the wrong profile")
    if manifest.get("excluded_source_ids") != ["dic-nico-intersection-pixiv"]:
        raise ArtifactVerificationError("release output manifest exclusion list changed")
    if manifest.get("source_lock_sha256") != daily_source_lock.sha256_file(
        daily_source_lock.LOCK_PATH
    ):
        raise ArtifactVerificationError("release output manifest source-lock mismatch")

    records = manifest.get("files")
    if not isinstance(records, list):
        raise ArtifactVerificationError("release output manifest files must be a list")
    expected = {path.as_posix() for path in prepare.RELEASE_OUTPUT_PATHS}
    actual = {record.get("path") for record in records if isinstance(record, dict)}
    if len(actual) != len(records) or actual != expected:
        raise ArtifactVerificationError("release dictionary artifact is not the allowlist")

    for record in records:
        relative = pathlib.PurePosixPath(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactVerificationError(f"unsafe release output path: {relative}")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise ArtifactVerificationError(f"release output is missing or unsafe: {path}")
        if path.stat().st_size != record.get("size_bytes"):
            raise ArtifactVerificationError(f"release output size mismatch: {path}")
        if prepare.sha256_file(path) != record.get("sha256"):
            raise ArtifactVerificationError(f"release output SHA-256 mismatch: {path}")

    generated = root / "src" / "data" / "dictionary_koyasi" / "generated"
    unexpected = sorted(
        path.relative_to(root).as_posix()
        for path in generated.rglob("*")
        if path.is_file()
        and path.relative_to(root).as_posix() not in expected
        and path.name != ".gitignore"
    )
    if unexpected:
        raise ArtifactVerificationError(
            "release transfer contains non-allowlisted generated files: "
            + ", ".join(unexpected)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=prepare.RELEASE_OUTPUT_MANIFEST,
    )
    args = parser.parse_args()
    try:
        verify(args.root, args.manifest)
    except ArtifactVerificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Release-approved dictionary artifact verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
