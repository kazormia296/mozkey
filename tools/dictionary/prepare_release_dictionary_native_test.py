#!/usr/bin/env python3

from __future__ import annotations

import bz2
import hashlib
import io
import pathlib
import tempfile
import types
import unittest
from unittest import mock

from tools.dictionary import prepare_release_dictionary_native as target


class PrepareReleaseDictionaryNativeTest(unittest.TestCase):
    def test_jawiki_index_is_consumed_without_latest_network_lookup(self) -> None:
        module = types.SimpleNamespace(
            remove_short_or_long_hyouki=lambda value: (
                value if 2 <= len(value) <= 25 else None
            ),
            normalize_entry=lambda value: value,
        )
        with tempfile.TemporaryDirectory() as temporary:
            index = pathlib.Path(temporary) / "jawiki-index.txt.bz2"
            with bz2.open(index, "wt", encoding="utf-8") as stream:
                stream.write("1:1:東京\n")
                stream.write("2:2:東京駅\n")
                stream.write("3:3:東京駅\n")
                stream.write("4:4:Category:除外\n")
                stream.write("5:5:A\n")
                stream.write("6:6:AT&amp;T\n")

            hits = target._jawiki_hit_dictionary(module, index)

        self.assertEqual(hits["東京"], 2)
        self.assertEqual(hits["東京駅"], 1)
        self.assertEqual(hits["AT&T"], 1)
        self.assertNotIn("Category:除外", hits)

    def test_locked_download_rejects_digest_mismatch_without_publication(self) -> None:
        expected = hashlib.sha256(b"expected\n").hexdigest()
        lock = {
            "sources": {
                "fixture": {
                    "payloads": {
                        "data": {
                            "path": "fixture.txt",
                            "sha256": expected,
                            "url": "https://example.invalid/v1/fixture.txt",
                        }
                    }
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            destination = pathlib.Path(temporary) / "fixture.txt"
            with mock.patch.object(
                target.urllib.request,
                "urlopen",
                return_value=io.BytesIO(b"tampered\n"),
            ):
                with self.assertRaisesRegex(
                    target.NativePreparationError, "SHA-256 mismatch"
                ):
                    target.download_locked_payload(
                        lock, "fixture", "data", destination
                    )
            self.assertFalse(destination.exists())

    def test_locked_merge_injects_local_base_and_pinned_jawiki(self) -> None:
        merge_module = '''\
import csv
import unicodedata

def remove_short_or_long_hyouki(value):
    return value if 2 <= len(value) <= 25 else None

def normalize_entry(value):
    return unicodedata.normalize("NFKC", value)

def get_ut_entry(path):
    with open(path, encoding="utf-8") as stream:
        return list(csv.reader(stream, delimiter="\\t"))

def remove_duplicate(base_entries, ut_entries):
    base = {(entry[0], entry[4]) for entry in base_entries}
    seen = set()
    result = []
    for entry in ut_entries:
        key = (entry[0], entry[4])
        if key not in base and key not in seen:
            result.append(entry)
            seen.add(key)
    return result

def apply_jawiki_hit(entries, hits):
    for entry in entries:
        entry[3] = str(7000 if hits.get(entry[4]) else 9000)
    return sorted(entries)
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script = root / "merge.py"
            script.write_text(merge_module, encoding="utf-8")
            place = root / "place.txt.bz2"
            sudachi = root / "sudachi.txt.bz2"
            with bz2.open(place, "wt", encoding="utf-8") as stream:
                stream.write("きそ\t2\t2\t100\t既存\n")
                stream.write("とうきょう\t2\t2\t100\t東京\n")
            with bz2.open(sudachi, "wt", encoding="utf-8") as stream:
                stream.write("おおさか\t2\t2\t100\t大阪\n")
            jawiki = root / "jawiki.txt.bz2"
            with bz2.open(jawiki, "wt", encoding="utf-8") as stream:
                stream.write("1:1:東京\n")
            base = root / "dictionary00.txt"
            base.write_text("きそ\t1\t1\t100\t既存\n", encoding="utf-8")
            id_def = root / "id.def"
            id_def.write_text("7 名詞,一般,*\n", encoding="utf-8")
            output = root / "output.txt"

            target.generate_locked_merge_dictionary(
                merge_script=script,
                place_bz2=place,
                sudachi_bz2=sudachi,
                jawiki_index=jawiki,
                dictionary_paths=(base,),
                id_def=id_def,
                output=output,
            )

            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                [
                    "おおさか\t7\t7\t9000\t大阪",
                    "とうきょう\t7\t7\t7000\t東京",
                ],
            )


if __name__ == "__main__":
    unittest.main()
