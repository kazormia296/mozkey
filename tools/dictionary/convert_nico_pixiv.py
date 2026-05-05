#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
import unicodedata
from collections import Counter


@dataclasses.dataclass(frozen=True)
class UserEntry:
    key: str
    value: str
    pos: str
    comment: str


@dataclasses.dataclass(frozen=True)
class SystemEntry:
    key: str
    lid: int
    rid: int
    cost: int
    value: str


URL_RE = re.compile(r"^(https?://|www\.)", re.IGNORECASE)


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def normalize_text(s: str) -> str:
    return unicodedata.normalize("NFKC", s.strip())


def has_control_char(s: str) -> bool:
    return any(unicodedata.category(ch).startswith("C") for ch in s)


def is_symbol_only(s: str) -> bool:
    visible = False
    for ch in s:
        if ch.isspace():
            continue
        visible = True
        cat = unicodedata.category(ch)
        if not (cat.startswith("P") or cat.startswith("S")):
            return False
    return visible


def is_ascii(s: str) -> bool:
    return bool(s) and all(ord(ch) < 128 for ch in s)


def find_noun_general_id(id_def: pathlib.Path) -> int:
    with id_def.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Typical format starts with numeric id and then POS name.
            if "名詞,一般" not in line:
                continue

            parts = line.split()
            if not parts:
                continue

            try:
                return int(parts[0])
            except ValueError:
                continue

    raise RuntimeError(f"failed to find 名詞,一般 id in {id_def}")


def parse_system_line(line: str) -> SystemEntry | None:
    cols = line.rstrip("\n\r").split("\t")
    if len(cols) != 5:
        return None

    key, lid, rid, cost, value = cols
    try:
        return SystemEntry(
            key=normalize_text(key),
            lid=int(lid),
            rid=int(rid),
            cost=int(cost),
            value=normalize_text(value),
        )
    except ValueError:
        return None


def load_existing_signatures(paths: list[pathlib.Path]) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()

    for path in paths:
        if not path.exists():
            print(f"warning: existing dictionary not found: {path}", file=sys.stderr)
            continue

        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            for line in f:
                entry = parse_system_line(line)
                if entry is None:
                    continue
                signatures.add((entry.key, entry.value))

    return signatures


def load_base_dictionary_paths(root: pathlib.Path) -> list[pathlib.Path]:
    dictionary_oss = root / "src" / "data" / "dictionary_oss"
    return sorted(dictionary_oss.glob("dictionary[0-9][0-9].txt"))


def parse_user_dict_line(line: str) -> UserEntry | None:
    line = line.rstrip("\n\r")

    if not line:
        return None

    if line.startswith("!") or line.startswith("#"):
        return None

    cols = line.split("\t")
    if len(cols) < 2:
        return None

    key = normalize_text(cols[0])
    value = normalize_text(cols[1])
    pos = normalize_text(cols[2]) if len(cols) >= 3 else ""
    comment = normalize_text(cols[3]) if len(cols) >= 4 else ""

    if not key or not value:
        return None

    return UserEntry(key=key, value=value, pos=pos, comment=comment)


def reject_reason(entry: UserEntry) -> str | None:
    if "\ufffd" in entry.key or "\ufffd" in entry.value:
        return "replacement_char"

    if has_control_char(entry.key) or has_control_char(entry.value):
        return "control_char"

    if URL_RE.search(entry.value):
        return "url"

    if is_symbol_only(entry.value):
        return "symbol_only"

    return None


def cost_for(entry: UserEntry, base_cost: int) -> int:
    cost = base_cost
    key_len = len(entry.key)

    # nico/pixiv entries are useful proper nouns, but they should not displace
    # common daily conversions. Keep them available but weak by default.
    if key_len <= 2:
        cost += 4000
    elif key_len == 3:
        cost += 2500
    elif key_len == 4:
        cost += 1200

    if is_ascii(entry.value):
        cost += 1000

    # If the source says "アルファベット", keep it even weaker.
    if entry.pos == "アルファベット":
        cost += 800

    return min(cost, 20000)


def convert(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    existing_paths: list[pathlib.Path],
    noun_general_id: int,
    base_cost: int,
) -> int:
    existing = load_existing_signatures(existing_paths)

    stats = Counter()
    seen_new: set[tuple[str, str]] = set()
    output: list[SystemEntry] = []

    watch_keys = {
        "こう",
        "せい",
        "かん",
        "とう",
        "しょう",
        "きょう",
        "しよう",
        "かんじ",
        "にほんご",
        "とうきょう",
    }
    watch_total = Counter()
    watch_strong = Counter()
    watch_examples: dict[str, list[SystemEntry]] = {key: [] for key in watch_keys}
    strong_cost_threshold = 12500

    with input_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for line in f:
            stats["input"] += 1

            user_entry = parse_user_dict_line(line)
            if user_entry is None:
                stats["skipped_non_entry"] += 1
                continue

            reason = reject_reason(user_entry)
            if reason is not None:
                stats[f"rejected_{reason}"] += 1
                continue

            signature = (user_entry.key, user_entry.value)

            if signature in existing:
                stats["duplicate_existing"] += 1
                continue

            if signature in seen_new:
                stats["duplicate_new"] += 1
                continue

            seen_new.add(signature)

            system_entry = SystemEntry(
                key=user_entry.key,
                lid=noun_general_id,
                rid=noun_general_id,
                cost=cost_for(user_entry, base_cost),
                value=user_entry.value,
            )

            output.append(system_entry)

            if system_entry.key in watch_keys:
                watch_total[system_entry.key] += 1

                if system_entry.cost <= strong_cost_threshold:
                    watch_strong[system_entry.key] += 1

                    if len(watch_examples[system_entry.key]) < 8:
                        watch_examples[system_entry.key].append(system_entry)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for entry in output:
            f.write(f"{entry.key}\t{entry.lid}\t{entry.rid}\t{entry.cost}\t{entry.value}\n")

    stats["output"] = len(output)

    print("Input:")
    print(f"  {input_path}")
    print("Output:")
    print(f"  {output_path}")
    print("")
    print("Stats:")
    for key in sorted(stats.keys()):
        print(f"  {key}: {stats[key]}")

    print("")
    print("Cost:")
    print(f"  noun_general_id: {noun_general_id}")
    print(f"  base_cost:       {base_cost}")

    print("")
    print(f"Watch keys, strong cost <= {strong_cost_threshold}:")
    for key in sorted(watch_keys):
        print(f"  {key}: total={watch_total[key]}, strong={watch_strong[key]}")

        for entry in watch_examples[key]:
            print(f"    {entry.key}\t{entry.lid}\t{entry.rid}\t{entry.cost}\t{entry.value}")

    return 0


def main() -> int:
    root = repo_root()

    parser = argparse.ArgumentParser(
        description="Convert dic-nico-intersection-pixiv Google/Mozc user dictionary to Mozc system dictionary delta."
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=root / "src" / "data" / "dictionary_koyasi" / "generated" / "nico_pixiv" / "dic-nico-intersection-pixiv-google.txt",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=root / "src" / "data" / "dictionary_koyasi" / "generated" / "profiled" / "dic-nico-pixiv-delta.txt",
    )
    parser.add_argument(
        "--daily",
        type=pathlib.Path,
        default=root / "src" / "data" / "dictionary_koyasi" / "generated" / "profiled" / "mozcdic-ut-daily.txt",
    )
    parser.add_argument(
        "--id-def",
        type=pathlib.Path,
        default=root / "src" / "data" / "dictionary_oss" / "id.def",
    )
    parser.add_argument(
        "--base-cost",
        type=int,
        default=10800,
    )
    parser.add_argument(
        "--no-base-dictionary",
        action="store_true",
        help="Do not compare against src/data/dictionary_oss/dictionary00.txt ... dictionary09.txt.",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input does not exist: {args.input}", file=sys.stderr)
        return 1

    existing_paths = [args.daily]

    if not args.no_base_dictionary:
        existing_paths.extend(load_base_dictionary_paths(root))

    noun_general_id = find_noun_general_id(args.id_def)

    return convert(
        input_path=args.input,
        output_path=args.output,
        existing_paths=existing_paths,
        noun_general_id=noun_general_id,
        base_cost=args.base_cost,
    )


if __name__ == "__main__":
    raise SystemExit(main())
