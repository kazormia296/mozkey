#!/usr/bin/env python3
"""Validate release identity before a PR merge, tag, or release build."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Sequence

try:
    from tools.release.validate_mozkey_release import (
        ReleaseValidationError,
        parse_release_tag,
        parse_version_file,
        parse_version_source,
        validate_release,
    )
except ModuleNotFoundError:  # Direct execution sets sys.path to tools/release.
    from validate_mozkey_release import (  # type: ignore[no-redef]
        ReleaseValidationError,
        parse_release_tag,
        parse_version_file,
        parse_version_source,
        validate_release,
    )


@dataclass(frozen=True)
class PreflightIdentity:
    phase: str
    candidate_tag: str
    commit: str
    main_commit: str


def _git(
    repository: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ReleaseValidationError(
            f"git {' '.join(arguments)} failed in {repository}: {detail}"
        )
    return result


def _commit(repository: Path, ref: str) -> str:
    return _git(repository, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _require_ancestor(repository: Path, ancestor: str, descendant: str) -> None:
    result = _git(
        repository,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if result.returncode == 1:
        raise ReleaseValidationError(
            f"{ancestor} is not an ancestor of {descendant}"
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ReleaseValidationError(f"failed to verify Git ancestry: {detail}")


def _tag_exists(repository: Path, tag: str) -> bool:
    result = _git(
        repository,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/tags/{tag}",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ReleaseValidationError(f"failed to inspect candidate tag {tag}")
    return result.returncode == 0


def _version_at_ref(
    repository: Path, ref: str, version_file: Path
) -> tuple[int, int, int]:
    repository = repository.resolve()
    try:
        relative = version_file.resolve().relative_to(repository)
    except ValueError as error:
        raise ReleaseValidationError(
            f"version file must be inside the repository: {version_file}"
        ) from error
    source = _git(repository, "show", f"{ref}:{relative.as_posix()}").stdout
    return parse_version_source(source, f"{ref}:{relative.as_posix()}")


def _candidate_tag(version: tuple[int, int, int]) -> str:
    return "v" + ".".join(str(value) for value in version)


def _require_clean(repository: Path) -> None:
    status = _git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules",
    ).stdout
    if status:
        raise ReleaseValidationError("pre-tag checkout must be clean")


def validate_preflight_identity(
    *,
    phase: str,
    candidate_tag: str | None,
    version_file: Path,
    repository: Path,
    main_ref: str,
) -> PreflightIdentity:
    repository = repository.resolve()
    version_file = version_file.resolve()
    head_commit = _commit(repository, "HEAD")
    main_commit = _commit(repository, main_ref)

    if phase == "tag":
        if not candidate_tag:
            raise ReleaseValidationError("tag phase requires --tag")
        identity = validate_release(
            tag=candidate_tag,
            ref_type="tag",
            version_file=version_file,
            repository=repository,
            main_ref=main_ref,
        )
        return PreflightIdentity(
            phase=phase,
            candidate_tag=identity.tag,
            commit=identity.commit,
            main_commit=main_commit,
        )

    working_version = parse_version_file(version_file)
    inferred_tag = _candidate_tag(working_version)
    if candidate_tag is not None:
        if parse_release_tag(candidate_tag) != working_version:
            raise ReleaseValidationError(
                f"candidate tag {candidate_tag} does not match {inferred_tag}"
            )
    else:
        candidate_tag = inferred_tag

    if phase == "pull-request":
        _require_ancestor(repository, main_commit, head_commit)
        main_version = _version_at_ref(repository, main_ref, version_file)
        if working_version < main_version:
            raise ReleaseValidationError(
                f"release version {inferred_tag} is older than {main_ref}"
            )
        if working_version > main_version and _tag_exists(repository, inferred_tag):
            raise ReleaseValidationError(
                f"version-bump PR reuses existing tag {inferred_tag}"
            )
    elif phase == "branch":
        if head_commit != main_commit:
            raise ReleaseValidationError(
                f"branch preflight HEAD {head_commit} is not {main_ref} {main_commit}"
            )
    elif phase == "pre-tag":
        if head_commit != main_commit:
            raise ReleaseValidationError(
                f"pre-tag HEAD {head_commit} is not {main_ref} {main_commit}"
            )
        _require_clean(repository)
        if _tag_exists(repository, inferred_tag):
            raise ReleaseValidationError(
                f"candidate tag already exists: {inferred_tag}"
            )
    else:
        raise ReleaseValidationError(f"unsupported preflight phase: {phase}")

    return PreflightIdentity(
        phase=phase,
        candidate_tag=candidate_tag,
        commit=head_commit,
        main_commit=main_commit,
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("pull-request", "branch", "pre-tag", "tag"),
        required=True,
    )
    parser.add_argument("--tag")
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--main-ref", default="origin/main")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        identity = validate_preflight_identity(
            phase=arguments.phase,
            candidate_tag=arguments.tag,
            version_file=arguments.version_file,
            repository=arguments.repository,
            main_ref=arguments.main_ref,
        )
    except ReleaseValidationError as error:
        print(f"release identity preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        f"validated {identity.phase} identity for {identity.candidate_tag}: "
        f"HEAD={identity.commit} {arguments.main_ref}={identity.main_commit}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
