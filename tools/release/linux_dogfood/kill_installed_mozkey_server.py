#!/usr/bin/python3 -I
"""SIGKILL one exact installed Mozkey server through a pidfd."""

from __future__ import annotations

import importlib.util
import os
import signal
import stat
import sys
import time
from pathlib import Path


def main() -> int:
    if os.getuid() == 0:
        raise RuntimeError("fault injection must run as the desktop user")
    release_root_text = os.environ.get("MOZKEY_DOGFOOD_RELEASE_ROOT")
    if not release_root_text:
        raise RuntimeError("MOZKEY_DOGFOOD_RELEASE_ROOT is required")
    supplied_root = Path(release_root_text)
    if (
        not supplied_root.is_absolute()
        or supplied_root.is_symlink()
        or supplied_root.resolve(strict=True) != supplied_root
    ):
        raise RuntimeError("release root must be canonical and absolute")
    root_metadata = supplied_root.stat()
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.getuid()
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        raise RuntimeError("release root must be a private snapshot")

    stopper_path = supplied_root / "tools/release/stop_mozkey_linux_runtime.py"
    stopper_metadata = stopper_path.lstat()
    if (
        not stat.S_ISREG(stopper_metadata.st_mode)
        or stopper_metadata.st_uid != os.getuid()
        or stat.S_IMODE(stopper_metadata.st_mode) != 0o400
        or stopper_path.resolve(strict=True) != stopper_path
    ):
        raise RuntimeError("runtime identity verifier snapshot is invalid")
    specification = importlib.util.spec_from_file_location(
        "mozkey_dogfood_fault_stopper", stopper_path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load the runtime identity verifier snapshot")
    stopper = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = stopper
    specification.loader.exec_module(stopper)

    target_uid = os.getuid()
    procfs = stopper.Procfs()
    servers = [
        identity
        for identity in procfs.scan(target_uid)
        if identity.exe == stopper.MOZKEY_SERVER
    ]
    if len(servers) != 1:
        raise RuntimeError(f"expected exactly one installed server, found {len(servers)}")

    server = servers[0]
    backend = stopper.PidfdSignalBackend()
    if not stopper._signal_identity(
        procfs, backend, server, target_uid, signal.SIGKILL
    ):
        raise RuntimeError("installed server disappeared before SIGKILL")
    remaining = stopper._wait_for_exit(
        procfs,
        [server],
        target_uid,
        2.0,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    if remaining:
        raise RuntimeError("installed server did not exit after SIGKILL")
    print(
        "RESULT:exact_pidfd_sigkill "
        f"pid={server.pid} start_time={server.start_time}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
