#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import dataclasses
import pathlib
import sys
from typing import Iterable


@dataclasses.dataclass(frozen=True)
class Entry:
    key: str
    lid: int
    rid: int
    cost: int
    value: str


@dataclasses.dataclass
class WatchStats:
    total: int = 0
    strong: int = 0
    examples: list[Entry] = dataclasses.field(default_factory=list)


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def default_dictionary_path(profile: str) -> pathlib.Path:
    root = repo_root()
    return (
        root
        / "src"
        / "data"
        / "dictionary_koyasi"
        / "generated"
        / "profiled"
        / f"mozcdic-ut-{profile}.txt"
    )


def default_positive_path(profile: str) -> pathlib.Path:
    root = repo_root()
    return (
        root
        / "src"
        / "data"
        / "dictionary_koyasi"
        / "evaluation"
        / f"positive_{profile}.tsv"
    )


def default_watch_path(profile: str) -> pathlib.Path:
    root = repo_root()
    return (
        root
        / "src"
        / "data"
        / "dictionary_koyasi"
        / "evaluation"
        / f"watch_{profile}.tsv"
    )


def parse_entry(line: str, line_number: int) -> Entry:
    cols = line.rstrip("\n\r").split("\t")

    if len(cols) != 5:
        raise ValueError(f"line {line_number}: expected 5 columns, got {len(cols)}")

    key, lid_text, rid_text, cost_text, value = cols

    return Entry(
        key=key,
        lid=int(lid_text),
        rid=int(rid_text),
        cost=int(cost_text),
        value=value,
    )


def load_positive(path: pathlib.Path) -> dict[tuple[str, str], str]:
    positives: dict[tuple[str, str], str] = {}

    if not path.exists():
        return positives

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.rstrip("\n\r")

            if not line or line.startswith("#"):
                continue

            cols = line.split("\t")
            if len(cols) < 2:
                raise ValueError(f"{path}: line {line_number}: expected at least 2 columns")

            key = cols[0]
            value = cols[1]
            note = cols[2] if len(cols) >= 3 else ""

            positives[(key, value)] = note

    return positives


def load_watch_keys(path: pathlib.Path) -> dict[str, str]:
    watch: dict[str, str] = {}

    if not path.exists():
        return watch

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.rstrip("\n\r")

            if not line or line.startswith("#"):
                continue

            cols = line.split("\t")
            if len(cols) < 1:
                raise ValueError(f"{path}: line {line_number}: expected at least 1 column")

            key = cols[0]
            note = cols[1] if len(cols) >= 2 else ""

            watch[key] = note

    return watch


def check_dictionary(
    dictionary_path: pathlib.Path,
    positives: dict[tuple[str, str], str],
    watch: dict[str, str],
    strong_cost_threshold: int,
    max_examples_per_key: int,
) -> int:
    positive_remaining = set(positives.keys())
    watch_stats: dict[str, WatchStats] = {
        key: WatchStats() for key in watch.keys()
    }

    total_lines = 0
    min_cost: int | None = None
    max_cost: int | None = None
    cost_buckets = collections.Counter()

    with dictionary_path.open("r", encoding="utf-8-sig", newline="") as f:
        for line_number, line in enumerate(f, start=1):
            total_lines += 1
            entry = parse_entry(line, line_number)

            min_cost = entry.cost if min_cost is None else min(min_cost, entry.cost)
            max_cost = entry.cost if max_cost is None else max(max_cost, entry.cost)

            if entry.cost < 7000:
                cost_buckets["<7000"] += 1
            elif entry.cost < 8000:
                cost_buckets["7000-7999"] += 1
            elif entry.cost < 9000:
                cost_buckets["8000-8999"] += 1
            elif entry.cost < 10000:
                cost_buckets["9000-9999"] += 1
            else:
                cost_buckets[">=10000"] += 1

            signature = (entry.key, entry.value)
            positive_remaining.discard(signature)

            if entry.key in watch_stats:
                stats = watch_stats[entry.key]
                stats.total += 1

                if entry.cost <= strong_cost_threshold:
                    stats.strong += 1

                    if len(stats.examples) < max_examples_per_key:
                        stats.examples.append(entry)

    print("Dictionary:")
    print(f"  path:        {dictionary_path}")
    print(f"  total lines: {total_lines}")
    print(f"  min cost:    {min_cost}")
    print(f"  max cost:    {max_cost}")
    print("")
    print("Cost buckets:")
    for key in ["<7000", "7000-7999", "8000-8999", "9000-9999", ">=10000"]:
        print(f"  {key:10s}: {cost_buckets[key]}")

    print("")
    print("Positive checks:")
    if not positives:
        print("  no positive checks")
    elif not positive_remaining:
        print(f"  OK: {len(positives)} / {len(positives)} found")
    else:
        found = len(positives) - len(positive_remaining)
        print(f"  NG: {found} / {len(positives)} found")
        for key, value in sorted(positive_remaining):
            note = positives[(key, value)]
            print(f"    missing: {key}\t{value}\t{note}")

    print("")
    print(f"Watch keys, strong cost <= {strong_cost_threshold}:")
    if not watch_stats:
        print("  no watch keys")
    else:
        for key in sorted(watch_stats.keys()):
            stats = watch_stats[key]
            note = watch[key]

            print(f"  {key}: total={stats.total}, strong={stats.strong}, note={note}")

            for entry in stats.examples:
                print(f"    {entry.key}\t{entry.lid}\t{entry.rid}\t{entry.cost}\t{entry.value}")

    if positive_remaining:
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check generated merge-ut profile dictionary."
    )
    parser.add_argument(
        "--profile",
        choices=["daily", "rich", "max"],
        required=True,
    )
    parser.add_argument(
        "--dictionary",
        type=pathlib.Path,
        default=None,
    )
    parser.add_argument(
        "--positive",
        type=pathlib.Path,
        default=None,
    )
    parser.add_argument(
        "--watch",
        type=pathlib.Path,
        default=None,
    )
    parser.add_argument(
        "--strong-cost-threshold",
        type=int,
        default=8500,
    )
    parser.add_argument(
        "--max-examples-per-key",
        type=int,
        default=8,
    )

    args = parser.parse_args()

    dictionary_path = args.dictionary or default_dictionary_path(args.profile)
    positive_path = args.positive or default_positive_path(args.profile)
    watch_path = args.watch or default_watch_path(args.profile)

    if not dictionary_path.exists():
        print(f"error: dictionary does not exist: {dictionary_path}", file=sys.stderr)
        return 1

    positives = load_positive(positive_path)
    watch = load_watch_keys(watch_path)

    return check_dictionary(
        dictionary_path=dictionary_path,
        positives=positives,
        watch=watch,
        strong_cost_threshold=args.strong_cost_threshold,
        max_examples_per_key=args.max_examples_per_key,
    )


if __name__ == "__main__":
    raise SystemExit(main())
