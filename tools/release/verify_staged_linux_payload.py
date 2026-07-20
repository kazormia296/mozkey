#!/usr/bin/env python3
"""Verify that a native package preserved an attested staged Linux tree."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Iterable


class PayloadError(RuntimeError):
    """A staged or extracted package payload is unsafe or differs."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_tree(root: Path) -> dict[str, tuple[object, ...]]:
    try:
        info = root.lstat()
    except OSError as error:
        raise PayloadError("payload_root_missing") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise PayloadError("payload_root_invalid")

    records: dict[str, tuple[object, ...]] = {}
    try:
        for directory, directory_names, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_path = Path(directory)
            for name in sorted((*directory_names, *filenames)):
                path = directory_path / name
                relative = path.relative_to(root).as_posix()
                item = path.lstat()
                mode = stat.S_IMODE(item.st_mode)
                if stat.S_ISDIR(item.st_mode):
                    records[relative] = ("directory", mode)
                elif stat.S_ISREG(item.st_mode):
                    records[relative] = (
                        "file",
                        mode,
                        item.st_size,
                        _sha256(path),
                    )
                elif stat.S_ISLNK(item.st_mode):
                    records[relative] = ("symlink", mode, os.readlink(path))
                else:
                    raise PayloadError(f"unsupported_payload_entry:{relative}")
    except (OSError, ValueError) as error:
        raise PayloadError("payload_tree_unreadable") from error
    return records


def verify_payload(expected: Path, actual: Path) -> None:
    expected_records = snapshot_tree(expected)
    actual_records = snapshot_tree(actual)
    if expected_records == actual_records:
        return
    all_paths = sorted(expected_records.keys() | actual_records.keys())
    for path in all_paths:
        if expected_records.get(path) != actual_records.get(path):
            raise PayloadError(f"package_payload_mismatch:{path}")
    raise PayloadError("package_payload_mismatch")


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare staged and package-extracted Linux payload trees"
    )
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        verify_payload(arguments.expected, arguments.actual)
    except PayloadError as error:
        print(f"Linux package payload verification failed: {error}", file=sys.stderr)
        return 1
    print("Linux package payload matches the attested stage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
