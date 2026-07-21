#!/usr/bin/env python3
"""Generate a deterministic SPDX 2.3 file inventory for a staged Linux tree."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re


_NAMESPACE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validated_namespace_component(value: str, name: str) -> str:
    if not _NAMESPACE_COMPONENT.fullmatch(value):
        raise ValueError(f"{name} is not a safe SPDX namespace component")
    return value


def checksums(path: Path) -> tuple[str, str]:
    # SPDX package verification code requires SHA-1; this is not used as a
    # security boundary. The artifact itself has a SHA-256 sidecar.
    sha1 = hashlib.sha1(usedforsecurity=False)
    sha256 = hashlib.sha256()
    if path.is_symlink():
        block = os.readlink(path).encode("utf-8")
        sha1.update(block)
        sha256.update(block)
    else:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                sha1.update(block)
                sha256.update(block)
    return sha1.hexdigest(), sha256.hexdigest()


def file_types(path: Path) -> list[str] | None:
    """Returns only types that can be established from content.

    SPDX `fileTypes` is optional.  In particular, an arbitrary non-text file
    is not necessarily an executable BINARY, so unknown formats are omitted
    rather than mislabeled.
    """
    if path.is_symlink():
        return ["OTHER"]
    with path.open("rb") as source:
        prefix = source.read(64 * 1024)
    if prefix.startswith(b"\x7fELF"):
        return ["BINARY"]
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return ["IMAGE"]
    if prefix.startswith(b"GGUF"):
        return ["BINARY"]
    if prefix.startswith((b"\xde\x12\x04\x95", b"\x95\x04\x12\xde")):
        return ["BINARY"]
    if b"\x00" not in prefix:
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return ["TEXT"]
    return None


def creation_time() -> str:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if epoch <= 0:
        epoch = 1
    return (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def generate(
    root: Path,
    output: Path,
    version: str,
    revision: str,
    distro: str,
    architecture: str,
) -> dict:
    root = root.resolve()
    output_absolute = output.resolve()
    try:
        output_relative = output_absolute.relative_to(root)
    except ValueError as error:
        raise ValueError("SPDX output must be inside the staged root") from error
    files = []
    verification_hashes = []
    paths = sorted(
        (
            candidate
            for candidate in root.rglob("*")
            if (candidate.is_file() or candidate.is_symlink())
            and candidate.relative_to(root) != output_relative
        ),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    )
    for index, path in enumerate(paths, start=1):
        relative = path.relative_to(root).as_posix()
        sha1, sha256 = checksums(path)
        verification_hashes.append(sha1)
        entry = {
            "SPDXID": f"SPDXRef-File-{index}",
            "checksums": [
                {"algorithm": "SHA1", "checksumValue": sha1},
                {"algorithm": "SHA256", "checksumValue": sha256},
            ],
            "copyrightText": "NOASSERTION",
            "fileName": f"./{relative}",
            "licenseConcluded": "NOASSERTION",
            "licenseInfoInFiles": ["NOASSERTION"],
        }
        established_types = file_types(path)
        if established_types is not None:
            entry["fileTypes"] = established_types
        if path.is_symlink():
            entry["comment"] = f"Symbolic link to {os.readlink(path)}"
        files.append(entry)

    verification_code = hashlib.sha1(
        "".join(sorted(verification_hashes)).encode("ascii"),
        usedforsecurity=False,
    ).hexdigest()
    excluded_file = f"./{output_relative.as_posix()}"
    namespace_version = _validated_namespace_component(version, "version")
    namespace_revision = _validated_namespace_component(revision, "revision")
    namespace_distro = _validated_namespace_component(distro, "distro")
    namespace_architecture = _validated_namespace_component(
        architecture, "architecture"
    )
    target = f"{namespace_distro}-{namespace_architecture}"
    document = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": creation_time(),
            "creators": ["Tool: Mozkey generate_linux_spdx_sbom.py"],
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": (
            "https://github.com/kazormia296/mozkey-ibg/spdx/"
            f"linux/{target}/{namespace_version}/{namespace_revision}/"
            f"{verification_code}"
        ),
        "files": files,
        "name": f"mozkey-{target}-{namespace_version}",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-Mozkey-Linux",
                "copyrightText": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "name": f"mozkey-{target}",
                "packageVerificationCode": {
                    "packageVerificationCodeExcludedFiles": [excluded_file],
                    "packageVerificationCodeValue": verification_code,
                },
                "supplier": "Organization: Mozkey contributors",
                "versionInfo": version,
            }
        ],
        "relationships": [
            {
                "relatedSpdxElement": "SPDXRef-Package-Mozkey-Linux",
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            },
            *[
                {
                    "relatedSpdxElement": entry["SPDXID"],
                    "relationshipType": "CONTAINS",
                    "spdxElementId": "SPDXRef-Package-Mozkey-Linux",
                }
                for entry in files
            ],
        ],
        "spdxVersion": "SPDX-2.3",
    }
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--distro", required=True)
    parser.add_argument("--architecture", required=True)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("--root must be a staged directory")
    try:
        document = generate(
            args.root,
            args.output,
            args.version,
            args.revision,
            args.distro,
            args.architecture,
        )
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
