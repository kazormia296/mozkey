#!/usr/bin/env python3
"""Prepare the public Mozkey dictionary without PowerShell.

The historical merge-ut entry point downloads moving ``master`` and ``latest``
inputs while it runs.  This module keeps the reviewed merge implementation but
injects Mozkey's tracked base dictionaries and a digest-pinned jawiki index, so
the Linux release path has no ambient network inputs.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import html
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any

try:
    from tools.dictionary import daily_source_lock
except ModuleNotFoundError:  # Direct execution from the repository root.
    import daily_source_lock  # type: ignore[no-redef]


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE_CACHE_RELATIVE = pathlib.Path("dist/dictionary/native-sources")
RAW_DICTIONARY_RELATIVE = pathlib.Path(
    "src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt"
)
RAW_SAMPLE_RELATIVE = pathlib.Path(
    "src/data/dictionary_koyasi/generated/mozcdic-ut-sample.txt"
)
PERSONAL_NAMES_BZ2_RELATIVE = pathlib.Path(
    "src/data/dictionary_koyasi/generated/personal_names/"
    "mozcdic-ut-personal-names.txt.bz2"
)
PERSONAL_NAMES_TXT_RELATIVE = pathlib.Path(
    "src/data/dictionary_koyasi/generated/personal_names/"
    "mozcdic-ut-personal-names.txt"
)
PROFILED_OUTPUT_RELATIVES = (
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

MERGE_SOURCE_ID = "merge-ut-dictionaries"
PLACE_SOURCE_ID = "mozcdic-ut-place-names"
SUDACHI_SOURCE_ID = "mozcdic-ut-sudachidict"
PERSONAL_NAMES_SOURCE_ID = "mozcdic-ut-personal-names"
JAWIKI_SOURCE_ID = "jawiki-dump-index"


class NativePreparationError(RuntimeError):
    """The native release dictionary could not be prepared safely."""


def _relative_payload_path(value: object, label: str) -> pathlib.PurePosixPath:
    if not isinstance(value, str):
        raise NativePreparationError(f"{label} path must be a string")
    relative = pathlib.PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise NativePreparationError(f"unsafe {label} path: {relative}")
    return relative


def _payload(
    lock: Mapping[str, Any], source_id: str, payload_id: str
) -> Mapping[str, Any]:
    source = daily_source_lock.source(lock, source_id)
    payloads = source["payloads"]
    payload = payloads.get(payload_id)
    if not isinstance(payload, Mapping):
        raise NativePreparationError(f"missing payload: {source_id}.{payload_id}")
    return payload


def _git_output(repository: pathlib.Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise NativePreparationError(
            f"git {' '.join(arguments)} failed for {repository}: {error}"
        ) from error
    return result.stdout.strip()


def verify_locked_checkout(
    lock: Mapping[str, Any],
    source_id: str,
    repository: pathlib.Path,
    payload_ids: Sequence[str],
) -> None:
    source = daily_source_lock.source(lock, source_id)
    if source.get("kind") != "git":
        raise NativePreparationError(f"source is not Git-backed: {source_id}")
    if not (repository / ".git").is_dir():
        raise NativePreparationError(f"locked checkout is incomplete: {repository}")

    expected_origin = source["repository"]
    actual_origin = _git_output(repository, "remote", "get-url", "origin")
    if actual_origin != expected_origin:
        raise NativePreparationError(
            f"origin mismatch for {source_id}: expected {expected_origin}, "
            f"got {actual_origin}"
        )
    expected_revision = source["revision"]
    actual_revision = _git_output(repository, "rev-parse", "HEAD")
    if actual_revision != expected_revision:
        raise NativePreparationError(
            f"revision mismatch for {source_id}: expected {expected_revision}, "
            f"got {actual_revision}"
        )
    expected_tree = source.get("tree")
    if expected_tree is not None:
        actual_tree = _git_output(repository, "rev-parse", "HEAD^{tree}")
        if actual_tree != expected_tree:
            raise NativePreparationError(
                f"tree mismatch for {source_id}: expected {expected_tree}, "
                f"got {actual_tree}"
            )

    for payload_id in payload_ids:
        payload = _payload(lock, source_id, payload_id)
        relative = _relative_payload_path(
            payload.get("path"), f"{source_id}.{payload_id}"
        )
        path = repository.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise NativePreparationError(
                f"locked payload is missing or unsafe: {path}"
            )
        daily_source_lock.verify_payload(lock, source_id, payload_id, path)


def ensure_locked_checkout(
    lock: Mapping[str, Any],
    source_id: str,
    cache_root: pathlib.Path,
    payload_ids: Sequence[str],
) -> pathlib.Path:
    source = daily_source_lock.source(lock, source_id)
    if source.get("kind") != "git":
        raise NativePreparationError(f"source is not Git-backed: {source_id}")
    revision = source["revision"]
    destination = cache_root / f"{source_id}-{revision[:12]}"
    if destination.exists():
        verify_locked_checkout(lock, source_id, destination, payload_ids)
        print(f"locked checkout verified: {source_id} {revision}")
        return destination

    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{source_id}-", dir=cache_root)
    )
    try:
        subprocess.run(["git", "init", "-q", str(temporary)], check=True)
        subprocess.run(
            ["git", "-C", str(temporary), "config", "core.autocrlf", "false"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(temporary),
                "remote",
                "add",
                "origin",
                source["repository"],
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(temporary),
                "fetch",
                "--depth",
                "1",
                "origin",
                revision,
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(temporary), "checkout", "-q", "--detach", "FETCH_HEAD"],
            check=True,
        )
        verify_locked_checkout(lock, source_id, temporary, payload_ids)
        os.replace(temporary, destination)
    except (OSError, subprocess.CalledProcessError) as error:
        raise NativePreparationError(
            f"failed to acquire locked checkout {source_id}@{revision}: {error}"
        ) from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    print(f"locked checkout acquired: {source_id} {revision}")
    return destination


def download_locked_payload(
    lock: Mapping[str, Any],
    source_id: str,
    payload_id: str,
    destination: pathlib.Path,
) -> pathlib.Path:
    payload = _payload(lock, source_id, payload_id)
    url = payload.get("url")
    if not isinstance(url, str):
        raise NativePreparationError(
            f"locked payload has no download URL: {source_id}.{payload_id}"
        )
    if destination.is_file() and not destination.is_symlink():
        try:
            daily_source_lock.verify_payload(lock, source_id, payload_id, destination)
            print(f"locked download verified: {destination}")
            return destination
        except daily_source_lock.SourceLockError:
            print(f"cached payload changed; refreshing from lock: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozkey/locked-build"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        daily_source_lock.verify_payload(lock, source_id, payload_id, temporary)
        os.replace(temporary, destination)
    except (OSError, daily_source_lock.SourceLockError) as error:
        raise NativePreparationError(
            f"failed to download locked payload {source_id}.{payload_id}: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)
    print(f"locked payload downloaded: {destination}")
    return destination


def _checkout_payload_path(
    lock: Mapping[str, Any],
    source_id: str,
    payload_id: str,
    checkout: pathlib.Path,
) -> pathlib.Path:
    payload = _payload(lock, source_id, payload_id)
    relative = _relative_payload_path(payload.get("path"), f"{source_id}.{payload_id}")
    return checkout.joinpath(*relative.parts)


def _load_upstream_merge_module(script: pathlib.Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "mozkey_locked_merge_ut", script
    )
    if specification is None or specification.loader is None:
        raise NativePreparationError(f"cannot load pinned merge script: {script}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _base_entries(
    merge_module: ModuleType,
    dictionary_paths: Sequence[pathlib.Path],
    id_def: pathlib.Path,
) -> tuple[list[list[str]], str]:
    general_noun_id: str | None = None
    with id_def.open(encoding="utf-8") as stream:
        for line in stream:
            if " 名詞,一般," in line:
                general_noun_id = line.split(" 名詞,一般,", 1)[0]
                break
    if general_noun_id is None:
        raise NativePreparationError(f"general noun ID was not found in {id_def}")

    entries: list[list[str]] = []
    for dictionary in dictionary_paths:
        with dictionary.open(encoding="utf-8", newline="") as stream:
            for row in csv.reader(stream, delimiter="\t"):
                reading, lid, rid, cost, value = row[:5]
                value = merge_module.remove_short_or_long_hyouki(value)
                if not value:
                    continue
                value = merge_module.normalize_entry(value)
                entries.append([reading, lid, rid, cost, value])
    return entries, general_noun_id


def _jawiki_hit_dictionary(
    merge_module: ModuleType, index_path: pathlib.Path
) -> dict[str, int]:
    titles: list[str] = []
    skipped_prefixes = (
        "ファイル:",
        "Wikipedia:",
        "Template:",
        "Portal:",
        "Help:",
        "Category:",
        "プロジェクト:",
        "曖昧さ回避",
    )
    with bz2.open(index_path, "rt", encoding="utf-8") as stream:
        for raw_entry in stream:
            fields = raw_entry.rstrip().split(":", 2)
            if len(fields) != 3:
                raise NativePreparationError(
                    f"malformed jawiki index row in {index_path}: {raw_entry[:80]!r}"
                )
            entry = html.unescape(fields[2]).split(" (")[-1]
            entry = merge_module.remove_short_or_long_hyouki(entry)
            if not entry or entry.startswith(skipped_prefixes):
                continue
            titles.append(merge_module.normalize_entry(entry))

    unique_titles = sorted(set(titles))
    hits: dict[str, int] = {}
    index = 0
    while index < len(unique_titles):
        title = unique_titles[index]
        count = 1
        while (
            index + count < len(unique_titles)
            and unique_titles[index + count].startswith(title)
        ):
            count += 1
        hits[title] = count
        index += 1
    return hits


def generate_locked_merge_dictionary(
    *,
    merge_script: pathlib.Path,
    place_bz2: pathlib.Path,
    sudachi_bz2: pathlib.Path,
    jawiki_index: pathlib.Path,
    dictionary_paths: Sequence[pathlib.Path],
    id_def: pathlib.Path,
    output: pathlib.Path,
) -> None:
    merge_module = _load_upstream_merge_module(merge_script)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".mozkey-locked-merge-", dir=output.parent
    ) as temporary:
        raw_input = pathlib.Path(temporary) / "mozcdic-ut.txt"
        with raw_input.open("wb") as combined:
            for compressed in (place_bz2, sudachi_bz2):
                with bz2.open(compressed, "rb") as source:
                    shutil.copyfileobj(source, combined, length=1024 * 1024)

        ut_entries = merge_module.get_ut_entry(str(raw_input))
        base_entries, general_noun_id = _base_entries(
            merge_module, dictionary_paths, id_def
        )
        ut_entries = merge_module.remove_duplicate(base_entries, ut_entries)
        jawiki_hits = _jawiki_hit_dictionary(merge_module, jawiki_index)
        ut_entries = merge_module.apply_jawiki_hit(ut_entries, jawiki_hits)

        staged = pathlib.Path(temporary) / "mozcdic-ut-safe.txt"
        with staged.open("w", encoding="utf-8", newline="\n") as stream:
            for entry in ut_entries:
                entry[1] = entry[2] = general_noun_id
                stream.write("\t".join(entry) + "\n")
        if staged.stat().st_size == 0:
            raise NativePreparationError("locked merge produced an empty dictionary")
        os.replace(staged, output)


def _write_sample(source: pathlib.Path, output: pathlib.Path, lines: int) -> None:
    if lines <= 0:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    try:
        with source.open(encoding="utf-8") as input_stream, temporary.open(
            "w", encoding="utf-8", newline="\n"
        ) as output_stream:
            for index, line in enumerate(input_stream):
                if index >= lines:
                    break
                output_stream.write(line)
        if temporary.stat().st_size == 0:
            raise NativePreparationError("generated dictionary sample is empty")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_personal_names(
    lock: Mapping[str, Any], repo_root: pathlib.Path, download: bool
) -> pathlib.Path:
    compressed = repo_root / PERSONAL_NAMES_BZ2_RELATIVE
    output = repo_root / PERSONAL_NAMES_TXT_RELATIVE
    if download:
        download_locked_payload(
            lock,
            PERSONAL_NAMES_SOURCE_ID,
            "dictionary_bz2",
            compressed,
        )
    elif not compressed.is_file() and not output.is_file():
        raise NativePreparationError(
            f"cached personal-names input is missing: {output}"
        )

    if output.is_file() and not output.is_symlink():
        try:
            daily_source_lock.verify_payload(
                lock, PERSONAL_NAMES_SOURCE_ID, "dictionary_txt", output
            )
            return output
        except daily_source_lock.SourceLockError:
            pass
    if not compressed.is_file() or compressed.is_symlink():
        raise NativePreparationError(
            f"locked personal-names archive is missing or unsafe: {compressed}"
        )
    daily_source_lock.verify_payload(
        lock, PERSONAL_NAMES_SOURCE_ID, "dictionary_bz2", compressed
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    try:
        with bz2.open(compressed, "rb") as source, temporary.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
        daily_source_lock.verify_payload(
            lock, PERSONAL_NAMES_SOURCE_ID, "dictionary_txt", temporary
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _run(command: Sequence[str], repo_root: pathlib.Path) -> None:
    try:
        subprocess.run(command, cwd=repo_root, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise NativePreparationError(
            f"dictionary command failed: {' '.join(command)}: {error}"
        ) from error


def _profile_outputs(
    repo_root: pathlib.Path,
    raw_dictionary: pathlib.Path,
    personal_names: pathlib.Path,
) -> None:
    staging_root = repo_root / "dist" / "dictionary"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="native-profile-", dir=staging_root
    ) as temporary:
        stage = pathlib.Path(temporary)
        daily = stage / "mozcdic-ut-daily.txt"
        personal = stage / "mozcdic-ut-personal-names-daily.txt"
        guard = stage / "koyasi-syntax-guard.txt"
        guard_debug = stage / "koyasi-syntax-guard-debug.tsv"
        excluded_nico = stage / "nico-pixiv-excluded.txt"

        _run(
            [
                sys.executable,
                str(repo_root / "tools/dictionary/profile_merge_ut.py"),
                "--profile",
                "daily",
                "--input",
                str(raw_dictionary),
                "--output",
                str(daily),
            ],
            repo_root,
        )
        _run(
            [
                sys.executable,
                str(repo_root / "tools/dictionary/profile_personal_names.py"),
                "--input",
                str(personal_names),
                "--output",
                str(personal),
                "--daily",
                str(daily),
                "--nico-pixiv-delta",
                str(excluded_nico),
                "--id-def",
                str(repo_root / "src/data/dictionary_oss/id.def"),
            ],
            repo_root,
        )
        _run(
            [
                sys.executable,
                str(repo_root / "tools/dictionary/check_merge_ut_profile.py"),
                "--profile",
                "daily",
                "--dictionary",
                str(daily),
            ],
            repo_root,
        )
        _run(
            [
                sys.executable,
                str(
                    repo_root
                    / "tools/dictionary/generate_syntax_guard_dictionary.py"
                ),
                "--output",
                str(guard),
                "--debug-tsv",
                str(guard_debug),
            ],
            repo_root,
        )

        staged_outputs = (daily, personal, guard)
        for staged in staged_outputs:
            if not staged.is_file() or staged.stat().st_size == 0:
                raise NativePreparationError(
                    f"native profile output is missing or empty: {staged}"
                )
        for staged, relative in zip(
            staged_outputs, PROFILED_OUTPUT_RELATIVES, strict=True
        ):
            destination = repo_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)


def prepare_release_dictionary(
    *,
    repo_root: pathlib.Path = REPO_ROOT,
    source_mode: str,
    sample_lines: int = 5000,
) -> None:
    repo_root = repo_root.resolve()
    lock_path = repo_root / "tools/dictionary/daily_sources.lock.json"
    lock = daily_source_lock.load_lock(lock_path)
    raw_dictionary = repo_root / RAW_DICTIONARY_RELATIVE
    personal_names: pathlib.Path

    if source_mode == "download":
        cache_root = repo_root / SOURCE_CACHE_RELATIVE
        merge_checkout = ensure_locked_checkout(
            lock,
            MERGE_SOURCE_ID,
            cache_root,
            ("merge_script",),
        )
        place_checkout = ensure_locked_checkout(
            lock,
            PLACE_SOURCE_ID,
            cache_root,
            ("dictionary_bz2",),
        )
        sudachi_checkout = ensure_locked_checkout(
            lock,
            SUDACHI_SOURCE_ID,
            cache_root,
            ("dictionary_bz2",),
        )
        jawiki_payload = _payload(lock, JAWIKI_SOURCE_ID, "multistream_index")
        jawiki_relative = _relative_payload_path(
            jawiki_payload.get("path"),
            f"{JAWIKI_SOURCE_ID}.multistream_index",
        )
        jawiki_index = download_locked_payload(
            lock,
            JAWIKI_SOURCE_ID,
            "multistream_index",
            cache_root.joinpath(*jawiki_relative.parts),
        )
        dictionary_paths = tuple(
            repo_root / f"src/data/dictionary_oss/dictionary{index:02d}.txt"
            for index in range(10)
        )
        generate_locked_merge_dictionary(
            merge_script=_checkout_payload_path(
                lock, MERGE_SOURCE_ID, "merge_script", merge_checkout
            ),
            place_bz2=_checkout_payload_path(
                lock, PLACE_SOURCE_ID, "dictionary_bz2", place_checkout
            ),
            sudachi_bz2=_checkout_payload_path(
                lock, SUDACHI_SOURCE_ID, "dictionary_bz2", sudachi_checkout
            ),
            jawiki_index=jawiki_index,
            dictionary_paths=dictionary_paths,
            id_def=repo_root / "src/data/dictionary_oss/id.def",
            output=raw_dictionary,
        )
        _write_sample(raw_dictionary, repo_root / RAW_SAMPLE_RELATIVE, sample_lines)
        personal_names = _prepare_personal_names(lock, repo_root, download=True)
    elif source_mode == "cached":
        if not raw_dictionary.is_file() or raw_dictionary.is_symlink():
            raise NativePreparationError(
                f"cached merge-ut input is missing or unsafe: {raw_dictionary}"
            )
        personal_names = _prepare_personal_names(lock, repo_root, download=False)
    else:
        raise NativePreparationError(f"unsupported native source mode: {source_mode}")

    derived_payloads = daily_source_lock.source(lock, MERGE_SOURCE_ID)["payloads"]
    if "release_safe_output" in derived_payloads:
        daily_source_lock.verify_payload(
            lock, MERGE_SOURCE_ID, "release_safe_output", raw_dictionary
        )
    daily_source_lock.verify_payload(
        lock, PERSONAL_NAMES_SOURCE_ID, "dictionary_txt", personal_names
    )
    _profile_outputs(repo_root, raw_dictionary, personal_names)
    print("Native release-approved-only dictionary is ready.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Mozkey's pinned public dictionary on Linux."
    )
    parser.add_argument(
        "--source-mode", choices=("download", "cached"), required=True
    )
    parser.add_argument("--root", type=pathlib.Path, default=REPO_ROOT)
    parser.add_argument("--sample-lines", type=int, default=5000)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sample_lines < 1:
        print("error: --sample-lines must be positive", file=sys.stderr)
        return 2
    try:
        prepare_release_dictionary(
            repo_root=args.root,
            source_mode=args.source_mode,
            sample_lines=args.sample_lines,
        )
    except (
        NativePreparationError,
        daily_source_lock.SourceLockError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
