#!/usr/bin/python3 -I
"""Verify an explicitly named private ydotoold socket and owner process."""

from __future__ import annotations

import argparse
import os
import select
import stat
from dataclasses import dataclass
from pathlib import Path


EXPECTED_DAEMON = "/usr/bin/ydotoold"
MAX_PROC_BYTES = 1 << 20


@dataclass(frozen=True)
class YdotoolIdentity:
    proc_device: int
    proc_inode: int
    process_start_time: str
    socket_device: int
    socket_inode: int
    kernel_socket_inode: str
    owning_descriptors: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    return parser.parse_args()


def read_limited(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_PROC_BYTES + 1)
    if len(data) > MAX_PROC_BYTES:
        raise RuntimeError("proc file exceeds verification cap")
    return data


def process_start_time(proc: Path, pid: int) -> str:
    raw_stat = read_limited(proc / "stat").decode("ascii")
    prefix = f"{pid} ("
    closing_paren = raw_stat.rfind(") ")
    if not raw_stat.startswith(prefix) or closing_paren < len(prefix):
        raise RuntimeError("invalid process stat")
    fields_after_comm = raw_stat[closing_paren + 2 :].split()
    if len(fields_after_comm) < 20:
        raise RuntimeError("invalid process stat")
    return fields_after_comm[19]


def process_uids(proc: Path) -> tuple[int, int, int, int]:
    uid_line = next(
        (
            line
            for line in read_limited(proc / "status").decode().splitlines()
            if line.startswith("Uid:")
        ),
        None,
    )
    if uid_line is None:
        raise RuntimeError("ydotoold uid identity is unavailable")
    values = uid_line.split()[1:]
    if len(values) != 4:
        raise RuntimeError("ydotoold uid identity is invalid")
    return tuple(int(value) for value in values)  # type: ignore[return-value]


def socket_kernel_inode(socket_path: Path) -> str:
    rows = []
    socket_name = str(socket_path)
    for line in read_limited(Path("/proc/net/unix")).decode().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8 and fields[7] == socket_name:
            rows.append(fields)
    if len(rows) != 1 or not rows[0][6].isdigit():
        raise RuntimeError("ydotool socket has no unique kernel identity")
    return rows[0][6]


def owning_descriptors(proc: Path, kernel_inode: str) -> tuple[str, ...]:
    descriptors = []
    for descriptor in (proc / "fd").iterdir():
        try:
            if os.readlink(descriptor) == f"socket:[{kernel_inode}]":
                descriptors.append(descriptor.name)
        except FileNotFoundError:
            continue
    if not descriptors:
        raise RuntimeError("ydotoold does not own the named socket")
    return tuple(sorted(descriptors, key=int))


def capture_identity(
    socket_path: Path, proc: Path, pid: int, target_uid: int
) -> YdotoolIdentity:
    proc_stat = proc.stat()
    if proc_stat.st_uid != target_uid:
        raise RuntimeError("ydotoold process owner mismatch")
    start_time = process_start_time(proc, pid)
    if os.readlink(proc / "exe") != EXPECTED_DAEMON:
        raise RuntimeError("ydotoold executable mismatch")
    if any(uid != target_uid for uid in process_uids(proc)):
        raise RuntimeError("ydotoold uid identity mismatch")

    socket_stat = socket_path.lstat()
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("ydotool path is not a socket")
    if socket_stat.st_uid != target_uid:
        raise RuntimeError("ydotool socket owner mismatch")
    if stat.S_IMODE(socket_stat.st_mode) != 0o600:
        raise RuntimeError("ydotool socket mode must be 0600")

    kernel_inode = socket_kernel_inode(socket_path)
    descriptors = owning_descriptors(proc, kernel_inode)
    return YdotoolIdentity(
        proc_device=proc_stat.st_dev,
        proc_inode=proc_stat.st_ino,
        process_start_time=start_time,
        socket_device=socket_stat.st_dev,
        socket_inode=socket_stat.st_ino,
        kernel_socket_inode=kernel_inode,
        owning_descriptors=descriptors,
    )


def main() -> int:
    args = parse_args()
    if os.getuid() == 0:
        raise RuntimeError("ydotool verification must run as the desktop user")
    if args.pid <= 1:
        raise RuntimeError("ydotoold pid is invalid")
    socket_path = args.socket
    if not socket_path.is_absolute():
        raise RuntimeError("ydotool socket must be an absolute non-symlink")
    try:
        if socket_path.resolve(strict=True) != socket_path:
            raise RuntimeError("ydotool socket must be an absolute non-symlink")
    except FileNotFoundError as error:
        raise RuntimeError("ydotool socket is unavailable") from error

    target_uid = os.getuid()
    proc = Path("/proc") / str(args.pid)
    try:
        pidfd = os.pidfd_open(args.pid)
    except (AttributeError, OSError) as error:
        raise RuntimeError("could not pin the ydotoold process identity") from error
    try:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        if poller.poll(0):
            raise RuntimeError("ydotoold exited before verification")

        initial_identity = capture_identity(
            socket_path, proc, args.pid, target_uid
        )
        final_identity = capture_identity(socket_path, proc, args.pid, target_uid)
        if initial_identity != final_identity or poller.poll(0):
            raise RuntimeError("ydotool identity changed during verification")
    finally:
        os.close(pidfd)

    print("ydotool socket verified: exact_private_owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
