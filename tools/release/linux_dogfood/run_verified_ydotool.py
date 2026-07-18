#!/usr/bin/python3 -I
"""Run one bounded ydotool command while pinning its private daemon."""

from __future__ import annotations

import argparse
import os
import select
import signal
import stat
import subprocess
from pathlib import Path

import importlib.util
import sys


YDOTOOL = Path("/usr/bin/ydotool")
ALLOWED_COMMANDS = frozenset(("click", "key", "mousemove", "type"))
MAX_OUTPUT_BYTES = 1 << 16


def load_verifier():
    path = Path(__file__).resolve().with_name("verify_ydotool_socket.py")
    specification = importlib.util.spec_from_file_location(
        "mozkey_dogfood_ydotool_verifier", path
    )
    if specification is None or specification.loader is None:
        fail("could not load the adjacent ydotool verifier")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def fail(message: str) -> None:
    raise RuntimeError(message)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=1)


def validate_command(arguments: list[str]) -> list[str]:
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    if not arguments or arguments[0] not in ALLOWED_COMMANDS:
        fail("ydotool command is not allowed")
    if len(arguments) > 512:
        fail("ydotool argument count exceeds the gate cap")
    if any("\x00" in argument or len(argument) > 4096 for argument in arguments):
        fail("ydotool argument is invalid")
    return arguments


def main() -> int:
    args = parse_args()
    if os.getuid() == 0:
        fail("verified ydotool must run as the desktop user")
    if args.pid <= 1:
        fail("ydotoold pid is invalid")
    if args.timeout_seconds < 1 or args.timeout_seconds > 15:
        fail("ydotool timeout is invalid")
    command = validate_command(args.command)
    verifier = load_verifier()
    if not args.socket.is_absolute() or args.socket.resolve(strict=True) != args.socket:
        fail("ydotool socket must be canonical and non-symlink")
    ydotool_metadata = YDOTOOL.lstat()
    if (
        YDOTOOL.is_symlink()
        or not stat.S_ISREG(ydotool_metadata.st_mode)
        or ydotool_metadata.st_uid != 0
        or stat.S_IMODE(ydotool_metadata.st_mode) & 0o022 != 0
        or YDOTOOL.resolve(strict=True) != YDOTOOL
        or stat.S_IMODE(ydotool_metadata.st_mode) & 0o111 == 0
    ):
        fail("exact ydotool executable is unavailable")

    proc = Path("/proc") / str(args.pid)
    try:
        pidfd = os.pidfd_open(args.pid)
    except (AttributeError, OSError) as error:
        raise RuntimeError("could not pin the ydotoold process identity") from error
    try:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        if poller.poll(0):
            fail("ydotoold exited before command verification")
        before = verifier.capture_identity(args.socket, proc, args.pid, os.getuid())

        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "YDOTOOL_SOCKET": str(args.socket),
        }
        process = subprocess.Popen(
            [str(YDOTOOL), *command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
        try:
            try:
                output, error = process.communicate(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                stop_process(process)
                fail("ydotool command timed out")
        finally:
            stop_process(process)

        after = verifier.capture_identity(args.socket, proc, args.pid, os.getuid())
        if before != after or poller.poll(0):
            fail("ydotool identity changed while sending input")
        if len(output) + len(error) > MAX_OUTPUT_BYTES:
            fail("ydotool output exceeded the gate cap")
        if process.returncode != 0:
            fail(f"ydotool command failed with status {process.returncode}")
    finally:
        os.close(pidfd)

    print("ydotool socket verified: exact_private_owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
