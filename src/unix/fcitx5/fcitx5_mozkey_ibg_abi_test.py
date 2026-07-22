from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REQUIRED_SYMBOLS = {
    "fcitx_addon_factory_instance",
    "fcitx_addon_factory_instance_mozkey_ibg",
}


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} /path/to/fcitx5-mozkey-ibg.so", file=sys.stderr)
        return 2

    addon = Path(sys.argv[1])
    if not addon.is_file():
        print(f"Mozkey IbG Fcitx5 addon is missing: {addon}", file=sys.stderr)
        return 1

    try:
        result = subprocess.run(
            ["nm", "-D", "--defined-only", str(addon)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(
            "nm is required to inspect the Mozkey IbG Fcitx5 addon ABI",
            file=sys.stderr,
        )
        return 1
    if result.returncode != 0:
        print(
            f"failed to inspect Mozkey IbG Fcitx5 addon ABI: {addon}",
            file=sys.stderr,
        )
        print(result.stderr, file=sys.stderr, end="")
        return 1

    exported_symbols = {
        fields[-1]
        for line in result.stdout.splitlines()
        if (fields := line.split())
    }
    missing_symbols = sorted(REQUIRED_SYMBOLS - exported_symbols)
    if missing_symbols:
        print(
            "Mozkey IbG Fcitx5 addon is missing ABI symbols: "
            + ", ".join(missing_symbols),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
