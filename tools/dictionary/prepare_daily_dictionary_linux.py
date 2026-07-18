#!/usr/bin/env python3
"""Linux entry point for Mozkey's existing daily dictionary pipeline.

The production modes intentionally delegate to prepare_daily_dictionary.ps1.
The sample mode is an offline smoke test for the tracked merge-ut sample and
the existing Python profiler/checker; it never selects the generated daily
Bazel target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence

try:
    from tools.dictionary import daily_source_lock
except ModuleNotFoundError:  # Direct script execution from the repository root.
    import daily_source_lock  # type: ignore[no-redef]


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PREPARE_SCRIPT = REPO_ROOT / "tools" / "dictionary" / "prepare_daily_dictionary.ps1"
SOURCE_MANIFEST = (
    REPO_ROOT / "dist" / "dictionary" / "linux-daily-source-manifest.json"
)
SOURCE_MANIFEST_SCHEMA = "mozkey.daily_dictionary_sources.v2"
RELEASE_OUTPUT_MANIFEST = (
    REPO_ROOT / "dist" / "dictionary" / "linux-release-approved-output-manifest.json"
)
RELEASE_OUTPUT_MANIFEST_SCHEMA = "mozkey.release_dictionary_outputs.v1"
TRACKED_SAMPLE_PATH = (
    REPO_ROOT
    / "src"
    / "data"
    / "dictionary_koyasi"
    / "sample"
    / "mozcdic-ut-sample.txt"
)

MERGE_OUTPUT_PATH = pathlib.Path(
    "src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt"
)
NICO_INPUT_PATH = pathlib.Path(
    "src/data/dictionary_koyasi/generated/nico_pixiv/"
    "dic-nico-intersection-pixiv-google.txt"
)
PERSONAL_NAMES_INPUT_PATH = pathlib.Path(
    "src/data/dictionary_koyasi/generated/personal_names/"
    "mozcdic-ut-personal-names.txt"
)
CACHED_SOURCE_PATHS_BY_PROFILE = {
    daily_source_lock.LOCAL_PROFILE: (
        MERGE_OUTPUT_PATH,
        NICO_INPUT_PATH,
        PERSONAL_NAMES_INPUT_PATH,
    ),
    daily_source_lock.RELEASE_PROFILE: (
        MERGE_OUTPUT_PATH,
        PERSONAL_NAMES_INPUT_PATH,
    ),
}
RELEASE_OUTPUT_PATHS = (
    pathlib.Path(
        "src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-daily.txt"
    ),
    pathlib.Path(
        "src/data/dictionary_koyasi/generated/profiled/"
        "mozcdic-ut-personal-names-daily.txt"
    ),
    pathlib.Path(
        "src/data/dictionary_koyasi/generated/profiled/koyasi-syntax-guard.txt"
    ),
)


def resolve_pwsh(requested: str | None) -> str | None:
    """Returns a usable pwsh path, without executing it."""
    if requested:
        candidate = pathlib.Path(requested).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        resolved = shutil.which(requested)
        return resolved
    return shutil.which("pwsh")


def cached_source_paths(profile: str) -> tuple[pathlib.Path, ...]:
    try:
        return CACHED_SOURCE_PATHS_BY_PROFILE[profile]
    except KeyError as error:
        raise ValueError(f"unknown dictionary profile: {profile}") from error


def cached_sources(
    repo_root: pathlib.Path = REPO_ROOT,
    profile: str = daily_source_lock.LOCAL_PROFILE,
) -> list[pathlib.Path]:
    return [repo_root / relative for relative in cached_source_paths(profile)]


def missing_cached_sources(
    repo_root: pathlib.Path = REPO_ROOT,
    profile: str = daily_source_lock.LOCAL_PROFILE,
) -> list[pathlib.Path]:
    return [path for path in cached_sources(repo_root, profile) if not path.is_file()]


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_records(
    repo_root: pathlib.Path = REPO_ROOT,
    profile: str = daily_source_lock.LOCAL_PROFILE,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative, path in zip(
        cached_source_paths(profile), cached_sources(repo_root, profile), strict=True
    ):
        record: dict[str, object] = {
            "path": relative.as_posix(),
            "exists": path.is_file(),
        }
        if path.is_file():
            record["size_bytes"] = path.stat().st_size
            record["sha256"] = sha256_file(path)
        records.append(record)
    return records


def write_source_manifest(
    source_mode: str,
    status: str,
    records: list[dict[str, object]],
    output: pathlib.Path = SOURCE_MANIFEST,
    profile: str = daily_source_lock.LOCAL_PROFILE,
) -> None:
    manifest = {
        "profile": profile,
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "source_lock_path": daily_source_lock.LOCK_PATH.relative_to(REPO_ROOT).as_posix(),
        "source_lock_sha256": sha256_file(daily_source_lock.LOCK_PATH),
        "source_mode": source_mode,
        "status": status,
        "files": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def release_output_records(
    repo_root: pathlib.Path = REPO_ROOT,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in RELEASE_OUTPUT_PATHS:
        path = repo_root / relative
        if not path.is_file():
            raise RuntimeError(f"release dictionary output is missing: {path}")
        records.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def write_release_output_manifest(
    records: list[dict[str, object]],
    output: pathlib.Path = RELEASE_OUTPUT_MANIFEST,
) -> None:
    manifest = {
        "excluded_source_ids": ["dic-nico-intersection-pixiv"],
        "files": records,
        "profile": daily_source_lock.RELEASE_PROFILE,
        "schema_version": RELEASE_OUTPUT_MANIFEST_SCHEMA,
        "source_lock_path": daily_source_lock.LOCK_PATH.relative_to(REPO_ROOT).as_posix(),
        "source_lock_sha256": sha256_file(daily_source_lock.LOCK_PATH),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_prepare_command(
    pwsh: str,
    source_mode: str,
    sample_lines: int,
    bash_path: str | None,
    prepare_script: pathlib.Path = PREPARE_SCRIPT,
    profile: str = daily_source_lock.LOCAL_PROFILE,
) -> list[str]:
    command = [
        pwsh,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(prepare_script),
        "-SampleLines",
        str(sample_lines),
    ]
    if source_mode == "cached":
        command.append("-SkipDownload")
    if profile == daily_source_lock.RELEASE_PROFILE:
        command.append("-ReleaseApprovedOnly")
    if bash_path:
        command.extend(["-BashPath", bash_path])
    return command


def run_sample_smoke(repo_root: pathlib.Path = REPO_ROOT) -> None:
    """Profiles and checks the committed sample without repository writes."""
    tracked_sample = (
        repo_root
        / "src"
        / "data"
        / "dictionary_koyasi"
        / "sample"
        / "mozcdic-ut-sample.txt"
    )
    if not tracked_sample.is_file():
        raise RuntimeError(f"tracked sample is missing: {tracked_sample}")

    with tempfile.TemporaryDirectory(prefix="mozkey-daily-sample-") as temp_dir:
        profiled = pathlib.Path(temp_dir) / "mozcdic-ut-daily.txt"
        subprocess.run(
            [
                sys.executable,
                str(repo_root / "tools" / "dictionary" / "profile_merge_ut.py"),
                "--profile",
                "daily",
                "--input",
                str(tracked_sample),
                "--output",
                str(profiled),
            ],
            cwd=repo_root,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(repo_root / "tools" / "dictionary" / "check_merge_ut_profile.py"),
                "--profile",
                "daily",
                "--dictionary",
                str(profiled),
            ],
            cwd=repo_root,
            check=True,
        )

    print("Offline sample smoke passed.")
    print("The selected Bazel profile was not modified; no daily artifact was kept.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or smoke-test Mozkey's daily dictionary on Linux."
    )
    parser.add_argument(
        "--profile",
        choices=(daily_source_lock.LOCAL_PROFILE, daily_source_lock.RELEASE_PROFILE),
        default=daily_source_lock.LOCAL_PROFILE,
        help=(
            "local-evaluation includes pinned nico/pixiv data; "
            "release-approved-only excludes it from public outputs"
        ),
    )
    parser.add_argument(
        "--source-mode",
        choices=("download", "cached", "sample"),
        required=True,
        help=(
            "download: refresh external inputs; cached: pass -SkipDownload and "
            "require all repository-local cached inputs; sample: offline smoke "
            "using only the tracked sample"
        ),
    )
    parser.add_argument(
        "--pwsh",
        help="PowerShell executable or path. Required by download/cached modes.",
    )
    parser.add_argument(
        "--bash-path",
        help="Optional bash path forwarded to prepare_daily_dictionary.ps1.",
    )
    parser.add_argument(
        "--sample-lines",
        type=int,
        default=5000,
        help="SampleLines forwarded to prepare_daily_dictionary.ps1 (default: 5000).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sample_lines < 1:
        print("error: --sample-lines must be positive", file=sys.stderr)
        return 2

    pwsh = resolve_pwsh(args.pwsh)
    print(f"source mode: {args.source_mode}")
    print(f"profile: {args.profile}")
    print(f"pwsh: {pwsh or 'not found'}")

    if args.source_mode == "sample":
        print(f"tracked sample sha256: {sha256_file(TRACKED_SAMPLE_PATH)}")
        run_sample_smoke()
        return 0

    lock = daily_source_lock.load_lock()

    if pwsh is None:
        print(
            "error: pwsh is required for download/cached modes. Install PowerShell "
            "or pass --pwsh /absolute/path/to/pwsh.",
            file=sys.stderr,
        )
        return 2

    before_records: list[dict[str, object]] | None = None
    if args.source_mode == "cached":
        missing = missing_cached_sources(profile=args.profile)
        if missing:
            print("error: cached mode requires these existing inputs:", file=sys.stderr)
            for path in missing:
                print(f"  {path}", file=sys.stderr)
            print(
                "Run --source-mode download once, or restore the documented cache "
                "paths before retrying. No network fallback is attempted.",
                file=sys.stderr,
            )
            return 2
        print("cached mode: forwarding -SkipDownload to the PowerShell pipeline")
        before_records = source_records(profile=args.profile)
        daily_source_lock.verify_payload(
            lock,
            "mozcdic-ut-personal-names",
            "dictionary_txt",
            REPO_ROOT / PERSONAL_NAMES_INPUT_PATH,
        )
        if args.profile == daily_source_lock.LOCAL_PROFILE:
            daily_source_lock.verify_payload(
                lock,
                "dic-nico-intersection-pixiv",
                "dictionary",
                REPO_ROOT / NICO_INPUT_PATH,
            )
        for record in before_records:
            print(f"cached sha256: {record['sha256']}  {record['path']}")

    write_source_manifest(
        source_mode=args.source_mode,
        status="running",
        records=source_records(profile=args.profile),
        profile=args.profile,
    )

    command = build_prepare_command(
        pwsh=pwsh,
        source_mode=args.source_mode,
        sample_lines=args.sample_lines,
        bash_path=args.bash_path,
        profile=args.profile,
    )
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    except subprocess.CalledProcessError:
        write_source_manifest(
            source_mode=args.source_mode,
            status="failed",
            records=source_records(profile=args.profile),
            profile=args.profile,
        )
        raise

    after_records = source_records(profile=args.profile)
    if any(not record["exists"] for record in after_records):
        write_source_manifest(
            source_mode=args.source_mode,
            status="incomplete",
            records=after_records,
            profile=args.profile,
        )
        raise RuntimeError("daily dictionary pipeline left a raw source input missing")
    if before_records is not None and after_records != before_records:
        write_source_manifest(
            source_mode=args.source_mode,
            status="cache_changed_during_run",
            records=after_records,
            profile=args.profile,
        )
        raise RuntimeError("cached dictionary inputs changed while the pipeline ran")

    outputs: list[dict[str, object]] | None = None
    if args.profile == daily_source_lock.RELEASE_PROFILE:
        try:
            # The PowerShell pipeline creates the profiled release outputs, so
            # their immutable manifest must be captured only after that process
            # has completed and the cached inputs have been revalidated.
            outputs = release_output_records()
        except RuntimeError:
            write_source_manifest(
                source_mode=args.source_mode,
                status="release_output_incomplete",
                records=after_records,
                profile=args.profile,
            )
            raise

    write_source_manifest(
        source_mode=args.source_mode,
        status="complete",
        records=after_records,
        profile=args.profile,
    )
    if outputs is not None:
        write_release_output_manifest(outputs)
        print(f"release output manifest: {RELEASE_OUTPUT_MANIFEST}")
    print(f"source manifest: {SOURCE_MANIFEST}")
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
