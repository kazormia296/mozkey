#!/usr/bin/env python3
# Copyright 2026
#
# Checks whether Mozc Windows runtime binaries contain network, telemetry,
# updater, or crash-upload markers.
#
# HARD_DENY markers fail the check.
# REPORT_ONLY markers are printed for audit, but do not fail the check.

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


HARD_DENY_STRINGS = {
    # Concrete Google Update / updater / telemetry / crash-upload markers.
    "clients.google.com",
    "clients1.google.com",
    "clients2.google.com",
    "clients3.google.com",
    "clients4.google.com",
    "googleupdate",
    "omaha",
    "telemetry",
    "usagestats",
    "crash_report",
    "crashreport",
    "breakpad",
}

REPORT_ONLY_STRINGS = {
    # These are useful audit markers, but too generic for hard failure.
    #
    # http:// and https:// appear in XML namespaces, manifests, comments,
    # documentation-like resources, and support URLs.
    "http://",
    "https://",

    # Protobuf and related libraries may embed "type.googleapis.com" as a type
    # URL prefix. That is not evidence of network communication.
    "googleapis.com",

    # This can remain as a protobuf field name such as "upload_usage_stats".
    # Functional blocking is enforced by StatsConfigUtil and UI removal.
    "usage_stats",
}

RUNTIME_BINARY_PATTERN = re.compile(
    r"^(?:mozc.*|llama-server)\.(exe|dll)$", re.IGNORECASE
)


def collect_targets(root: Path, explicit_paths: list[Path]) -> list[Path]:
    if explicit_paths:
        return explicit_paths

    if not root.exists():
        return []

    targets: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip Bazel runfiles duplicates such as:
        #   mozc_tool.exe.runfiles\_main\gui\tool\mozc_tool.exe
        if any(part.endswith(".runfiles") for part in path.parts):
            continue

        if not RUNTIME_BINARY_PATTERN.match(path.name):
            continue

        targets.append(path)

    return sorted(targets)


def decode_binary(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    ascii_text = data.decode("ascii", errors="ignore")
    utf16le_text = data.decode("utf-16le", errors="ignore")
    return ascii_text, utf16le_text


def find_markers(haystacks: tuple[str, str], markers: set[str]) -> list[str]:
    return sorted(
        marker
        for marker in markers
        if any(marker.lower() in haystack for haystack in haystacks)
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Explicit .exe/.dll files to inspect.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("src") / "bazel-bin",
        help="Root directory to recursively scan when no explicit paths are given.",
    )
    args = parser.parse_args(argv[1:])

    targets = collect_targets(args.root, args.paths)

    if not targets:
        print(
            f"[NO_NETWORK_STRING_CHECK_FAILED] No targets found under {args.root}",
            file=sys.stderr,
        )
        return 1

    failed = False

    for path in targets:
        if not path.exists():
            print(f"[NO_NETWORK_STRING_MISSING] {path}", file=sys.stderr)
            failed = True
            continue

        ascii_text, utf16le_text = decode_binary(path)
        haystacks = (ascii_text.lower(), utf16le_text.lower())

        hard_found = find_markers(haystacks, HARD_DENY_STRINGS)
        report_found = find_markers(haystacks, REPORT_ONLY_STRINGS)

        if hard_found:
            failed = True
            print(f"[NO_NETWORK_STRING_VIOLATION] {path}", file=sys.stderr)
            for marker in hard_found:
                print(f"  - {marker}", file=sys.stderr)
        else:
            print(f"[NO_NETWORK_STRING_OK] {path}")

        if report_found:
            print(f"[NO_NETWORK_STRING_REPORT_ONLY] {path}")
            for marker in report_found:
                print(f"  - {marker}")

    if failed:
        print("[NO_NETWORK_STRING_CHECK_FAILED]")
        return 1

    print("[NO_NETWORK_STRING_CHECK_PASSED]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
