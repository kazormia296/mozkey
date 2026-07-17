#!/usr/bin/python3

from __future__ import annotations

import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from tools.release.linux_dogfood import verify_installed_candidate


FIXED_NOW = datetime.datetime(
    2026, 7, 18, 12, 0, 0, tzinfo=datetime.timezone.utc
)


def consumer_payload(timestamp: str = "2026-07-18T12:00:00.000Z") -> bytes:
    document = {
        "capabilities": {
            "application_scoping": True,
            "dynamic_dictionary": True,
            "profile": True,
            "zenzai_v3_conditions": True,
        },
        "consumer_id": "fcitx5-mozkey",
        "format_version": 1,
        "last_seen": timestamp,
        "name": "Mozkey for Grimodex on Linux",
        "platform": "linux",
        "version": "v0.8.0",
    }
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


class ConsumerFixturePolicyTest(unittest.TestCase):
    def test_exact_fresh_consumer_payload_is_accepted(self) -> None:
        digest = verify_installed_candidate.validate_consumer_payload(
            consumer_payload(), "v0.8.0", now=FIXED_NOW
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_stale_consumer_payload_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_installed_candidate.validate_consumer_payload(
                consumer_payload("2026-07-18T11:39:59.000Z"),
                "v0.8.0",
                now=FIXED_NOW,
            )

    def test_fresh_heartbeat_from_prior_lifetime_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_installed_candidate.validate_consumer_payload(
                consumer_payload("2026-07-18T11:58:00.000Z"),
                "v0.8.0",
                now=FIXED_NOW,
                not_before=FIXED_NOW - datetime.timedelta(seconds=30),
            )

    def test_incomplete_zenz_capability_is_rejected(self) -> None:
        document = json.loads(consumer_payload().decode("ascii"))
        document["capabilities"]["zenzai_v3_conditions"] = False
        payload = (
            json.dumps(
                document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        with self.assertRaises(RuntimeError):
            verify_installed_candidate.validate_consumer_payload(
                payload, "v0.8.0", now=FIXED_NOW
            )

    def test_integer_capability_is_rejected(self) -> None:
        document = json.loads(consumer_payload().decode("ascii"))
        document["capabilities"]["profile"] = 1
        payload = (
            json.dumps(
                document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        with self.assertRaises(RuntimeError):
            verify_installed_candidate.validate_consumer_payload(
                payload, "v0.8.0", now=FIXED_NOW
            )

    def test_noncanonical_consumer_encoding_is_rejected(self) -> None:
        document = json.loads(consumer_payload().decode("ascii"))
        payload = (json.dumps(document, indent=2) + "\n").encode("ascii")
        with self.assertRaises(RuntimeError):
            verify_installed_candidate.validate_consumer_payload(
                payload, "v0.8.0", now=FIXED_NOW
            )

    def test_private_consumer_file_is_bound_to_head_version(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        now = datetime.datetime.now(datetime.timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{now.microsecond // 1000:03d}Z"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            consumers = root / "consumers"
            consumers.mkdir(mode=0o700)
            marker = consumers / "fcitx5-mozkey.json"
            marker.write_bytes(consumer_payload(timestamp))
            marker.chmod(0o600)
            ticks_per_second = os.sysconf("SC_CLK_TCK")
            start_time = str(
                int(time.clock_gettime(time.CLOCK_BOOTTIME) * ticks_per_second)
            )
            digest = verify_installed_candidate.validate_consumer_handshake(
                root, repository, os.getpid(), start_time
            )
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_atomic_heartbeat_temporary_file_is_retried(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        now = datetime.datetime.now(datetime.timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{now.microsecond // 1000:03d}Z"
        )
        pid = os.getpid()
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        start_time = str(
            int(time.clock_gettime(time.CLOCK_BOOTTIME) * ticks_per_second)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            consumers = root / "consumers"
            consumers.mkdir(mode=0o700)
            marker = consumers / "fcitx5-mozkey.json"
            marker.write_bytes(consumer_payload(timestamp))
            marker.chmod(0o600)
            transient = consumers / f".fcitx5-mozkey.{pid}.0.tmp"
            transient.write_bytes(b"pending")
            transient.chmod(0o600)

            def finish_refresh(_seconds: float) -> None:
                transient.unlink()

            with mock.patch.object(
                verify_installed_candidate.time,
                "sleep",
                side_effect=finish_refresh,
            ) as sleep:
                digest = verify_installed_candidate.validate_consumer_handshake(
                    root, repository, pid, start_time
                )
            sleep.assert_called_once_with(0.01)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_protocol_preparer_creates_private_empty_consumers_directory(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        script = Path(__file__).with_name("prepare_protocol_fixture.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            result = subprocess.run(
                [sys.executable, "-I", str(script), "--root", str(root)],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=15,
                cwd=repository,
                env={
                    "HOME": "/var/empty",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.startswith("RESULT:fixture_ready "))
            consumers = root / "consumers"
            self.assertEqual(list(consumers.iterdir()), [])
            self.assertEqual(stat.S_IMODE(consumers.stat().st_mode), 0o700)
            self.assertEqual(consumers.stat().st_uid, os.getuid())


if __name__ == "__main__":
    unittest.main()
