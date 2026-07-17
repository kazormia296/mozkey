#!/usr/bin/python3 -I
"""Materialize the tracked, non-secret Protocol v1 dogfood fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    return parser.parse_args()


def fail(message: str) -> None:
    raise RuntimeError(message)


def load_fixture(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        fail("release fixture must be a canonical regular file")
    with path.open("r", encoding="utf-8") as stream:
        fixture = json.load(stream)
    if set(fixture) != {
        "schema_version",
        "reading",
        "custom_value",
        "default_value",
        "state",
        "project",
    }:
        fail("release fixture fields changed")
    if fixture["schema_version"] != 1:
        fail("release fixture schema changed")
    reading = fixture["reading"]
    if not isinstance(reading, str) or not reading.isascii() or not reading.isalpha():
        fail("release fixture reading is invalid")
    if reading.lower() != reading:
        fail("release fixture reading must be lowercase")
    custom = fixture["custom_value"]
    default = fixture["default_value"]
    if (
        not isinstance(custom, str)
        or not isinstance(default, str)
        or not custom
        or not default
        or custom == default
        or custom.strip() == reading
        or default.strip() == reading
    ):
        fail("release fixture expectations are invalid")
    state = fixture["state"]
    project = fixture["project"]
    if not isinstance(state, dict) or not isinstance(project, dict):
        fail("release fixture Protocol documents are invalid")
    project_id = project.get("project_id")
    if (
        state.get("active_project_id") != project_id
        or not isinstance(project_id, str)
        or re.fullmatch(r"[a-z0-9-]+", project_id) is None
    ):
        fail("release fixture project identity is inconsistent")
    entries = project.get("entries")
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or entries[0].get("surface") != custom
    ):
        fail("release fixture custom expectation is not backed by its dictionary")
    return fixture


def verify_empty_root(root: Path) -> Path:
    if not root.is_absolute():
        fail("fixture root must be absolute")
    if root.is_symlink() or root.resolve(strict=True) != root:
        fail("fixture root must be canonical and non-symlink")
    metadata = root.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        fail("fixture root owner or type is invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        fail("fixture root must have mode 0700")
    if any(root.iterdir()):
        fail("fixture root must start empty")
    return root


def write_private(path: Path, payload: object) -> str:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                fail("short write while materializing release fixture")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(data).hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    args = parse_args()
    if os.getuid() == 0:
        fail("fixture preparation must run as the desktop user")
    fixture_path = Path(__file__).resolve().with_name("release_fixture.json")
    fixture = load_fixture(fixture_path)
    root = verify_empty_root(args.root)
    projects = root / "projects"
    projects.mkdir(mode=0o700)
    project_id = fixture["project"]["project_id"]
    project_digest = write_private(projects / f"{project_id}.json", fixture["project"])
    fsync_directory(projects)
    state_digest = write_private(root / "state.json", fixture["state"])
    fsync_directory(root)
    print(
        "RESULT:fixture_ready "
        f"state_sha256={state_digest} project_sha256={project_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
