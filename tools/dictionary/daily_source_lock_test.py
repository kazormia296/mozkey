#!/usr/bin/env python3

from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest

from tools.dictionary import daily_source_lock as target


class DailySourceLockTest(unittest.TestCase):
    def test_repository_lock_is_valid_and_nico_is_local_only(self) -> None:
        lock = target.load_lock()
        release_ids = set(lock["profiles"][target.RELEASE_PROFILE]["source_ids"])
        local_ids = set(lock["profiles"][target.LOCAL_PROFILE]["source_ids"])
        self.assertEqual(
            release_ids,
            {
                "merge-ut-dictionaries",
                "mozcdic-ut-place-names",
                "mozcdic-ut-sudachidict",
                "mozcdic-ut-personal-names",
                "jawiki-dump-index",
            },
        )
        self.assertEqual(local_ids - release_ids, {"dic-nico-intersection-pixiv"})
        nico = lock["sources"]["dic-nico-intersection-pixiv"]
        self.assertFalse(nico["release_approved"])
        self.assertTrue(nico["local_evaluation_only"])
        jawiki = lock["sources"]["jawiki-dump-index"]
        self.assertEqual(jawiki["kind"], "url")
        self.assertIn(
            jawiki["version"],
            jawiki["payloads"]["multistream_index"]["url"],
        )

    def test_url_source_must_be_version_pinned(self) -> None:
        lock = target.load_lock()
        malformed = copy.deepcopy(lock)
        malformed["sources"]["jawiki-dump-index"]["payloads"][
            "multistream_index"
        ]["url"] = "https://dumps.wikimedia.org/jawiki/latest/index.txt.bz2"
        with self.assertRaisesRegex(target.SourceLockError, "not revision-pinned"):
            target.validate_lock(malformed)

    def test_unapproved_source_cannot_enter_release_profile(self) -> None:
        lock = target.load_lock()
        malformed = copy.deepcopy(lock)
        malformed["profiles"][target.RELEASE_PROFILE]["source_ids"].append(
            "dic-nico-intersection-pixiv"
        )
        with self.assertRaisesRegex(
            target.SourceLockError, "strict local subset|unapproved source"
        ):
            target.validate_lock(malformed)

    def test_payload_verification_is_fail_closed(self) -> None:
        lock = target.load_lock()
        expected = lock["sources"]["mozcdic-ut-personal-names"]["payloads"][
            "dictionary_txt"
        ]["sha256"]
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = pathlib.Path(temp_dir) / "payload.txt"
            payload.write_bytes(b"fixture\n")
            modified = copy.deepcopy(lock)
            modified["sources"]["mozcdic-ut-personal-names"]["payloads"][
                "dictionary_txt"
            ]["sha256"] = target.sha256_file(payload)
            target.verify_payload(
                modified,
                "mozcdic-ut-personal-names",
                "dictionary_txt",
                payload,
            )
            payload.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(target.SourceLockError, "SHA-256 mismatch"):
                target.verify_payload(
                    modified,
                    "mozcdic-ut-personal-names",
                    "dictionary_txt",
                    payload,
                )
        self.assertEqual(len(expected), 64)


if __name__ == "__main__":
    unittest.main()
