#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True)
class EvalRow:
    status: str
    key: str
    output: str
    mode: str
    expected: str
    version: str
    raw: str


def parse_row(line: str) -> EvalRow | None:
    line = line.rstrip("\n\r")
    if not line:
        return None

    cols = line.split("\t")
    if len(cols) < 6:
        return None

    status = cols[0].strip()
    if status not in ("OK:", "FAILED:"):
        return None

    return EvalRow(
        status=status[:-1],
        key=cols[1],
        output=cols[2],
        mode=cols[3],
        expected=cols[4],
        version=cols[5],
        raw=line,
    )


def load(path: pathlib.Path) -> dict[tuple[str, str, str], EvalRow]:
    rows: dict[tuple[str, str, str], EvalRow] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for line in f:
            row = parse_row(line)
            if row is None:
                continue
            # Use the input key, evaluation mode, and expected value as the
            # stable identity.  The output and version are what we compare.
            rows[(row.key, row.mode, row.expected)] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Mozc evaluation TSV files and fail on OK->FAILED regressions."
    )
    parser.add_argument("--before", type=pathlib.Path, required=True)
    parser.add_argument("--after", type=pathlib.Path, required=True)
    parser.add_argument(
        "--allow-output-change",
        action="store_true",
        help="Allow OK->OK output changes. OK->FAILED still fails.",
    )
    args = parser.parse_args()

    before = load(args.before)
    after = load(args.after)

    regressions: list[tuple[EvalRow, EvalRow]] = []
    improvements: list[tuple[EvalRow, EvalRow]] = []
    ok_output_changes: list[tuple[EvalRow, EvalRow]] = []
    failed_output_changes: list[tuple[EvalRow, EvalRow]] = []
    status_changed_output_same: list[tuple[EvalRow, EvalRow]] = []
    removed_rows: list[EvalRow] = []
    added_rows: list[EvalRow] = []

    for identity, old in before.items():
        new = after.get(identity)

        if new is None:
            removed_rows.append(old)
            continue

        if old.status == "OK" and new.status == "FAILED":
            regressions.append((old, new))
        elif old.status == "FAILED" and new.status == "OK":
            improvements.append((old, new))
        elif old.status == "OK" and new.status == "OK" and old.output != new.output:
            ok_output_changes.append((old, new))
        elif old.status == "FAILED" and new.status == "FAILED" and old.output != new.output:
            failed_output_changes.append((old, new))

        if old.status != new.status and old.output == new.output:
            status_changed_output_same.append((old, new))

    for identity, new in after.items():
        if identity not in before:
            added_rows.append(new)

    print(f"before rows: {len(before)}")
    print(f"after rows:  {len(after)}")
    print("")
    print(f"FAILED -> OK improvements:      {len(improvements)}")
    print(f"OK -> FAILED regressions:       {len(regressions)}")
    print(f"OK -> OK output changes:        {len(ok_output_changes)}")
    print(f"FAILED -> FAILED output changes:{len(failed_output_changes)}")
    print(f"status changed, output same:    {len(status_changed_output_same)}")
    print(f"removed rows:                   {len(removed_rows)}")
    print(f"added rows:                     {len(added_rows)}")
    print("")

    if improvements:
        print("Improvements:")
        for old, new in improvements[:50]:
            print(f"  {old.key}: {old.output} -> {new.output}  expected={new.expected}")
        if len(improvements) > 50:
            print(f"  ... {len(improvements) - 50} more")
        print("")

    if ok_output_changes:
        print("OK output changes:")
        for old, new in ok_output_changes[:50]:
            print(
                f"  {old.key}: {old.output} -> {new.output}  "
                f"mode={new.mode} expected={new.expected}"
            )
        if len(ok_output_changes) > 50:
            print(f"  ... {len(ok_output_changes) - 50} more")
        print("")

    if failed_output_changes:
        print("FAILED output changes:")
        for old, new in failed_output_changes[:100]:
            print(
                f"  {old.key}: {old.output} -> {new.output}  "
                f"mode={new.mode} expected={new.expected}"
            )
        if len(failed_output_changes) > 100:
            print(f"  ... {len(failed_output_changes) - 100} more")
        print("")

    if status_changed_output_same:
        print("Status changed but output is same:")
        for old, new in status_changed_output_same[:100]:
            print(
                f"  {old.key}: {old.status} -> {new.status}, "
                f"output={new.output} mode={new.mode} expected={new.expected}"
            )
        if len(status_changed_output_same) > 100:
            print(f"  ... {len(status_changed_output_same) - 100} more")
        print("")

    if removed_rows:
        print("Removed rows:")
        for row in removed_rows[:100]:
            print(f"  {row.key}: {row.raw}")
        if len(removed_rows) > 100:
            print(f"  ... {len(removed_rows) - 100} more")
        print("")

    if added_rows:
        print("Added rows:")
        for row in added_rows[:100]:
            print(f"  {row.key}: {row.raw}")
        if len(added_rows) > 100:
            print(f"  ... {len(added_rows) - 100} more")
        print("")

    if regressions:
        print("Regressions:")
        for old, new in regressions:
            print(
                f"  {old.key}: {old.output} -> {new.output}  "
                f"mode={new.mode} expected={new.expected}"
            )
        print("")
        print("ERROR: OK->FAILED regressions exist.")
        return 1

    if ok_output_changes and not args.allow_output_change:
        print("OK->OK output changes exist. Inspect them or pass --allow-output-change.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
