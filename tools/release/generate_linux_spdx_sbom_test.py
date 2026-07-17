import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.release.generate_linux_spdx_sbom import generate


class GenerateLinuxSpdxSbomTest(unittest.TestCase):
    def test_inventory_is_sorted_and_hashes_symlink_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/z").write_bytes(b"z")
            (root / "usr/bin/a").write_bytes(b"a")
            (root / "usr/bin/link").symlink_to("/usr/bin/a")
            output = root / "usr/share/doc/mozkey/sbom.json"
            (root / "usr/bin/unknown").write_bytes(b"\x00\x01\x02")
            (root / "usr/bin/elf").write_bytes(b"\x7fELFpayload")
            document = generate(
                root, output, "0.7.7", "abc123", "archlinux", "x86_64"
            )

            names = [entry["fileName"] for entry in document["files"]]
            self.assertEqual(names, sorted(names))
            link = next(
                entry for entry in document["files"] if entry["fileName"].endswith("/link")
            )
            self.assertEqual(link["fileTypes"], ["OTHER"])
            self.assertIn("/usr/bin/a", link["comment"])
            text = next(
                entry for entry in document["files"] if entry["fileName"].endswith("/a")
            )
            self.assertEqual(text["fileTypes"], ["TEXT"])
            binary = next(
                entry
                for entry in document["files"]
                if entry["fileName"].endswith("/elf")
            )
            self.assertEqual(binary["fileTypes"], ["BINARY"])
            unknown = next(
                entry
                for entry in document["files"]
                if entry["fileName"].endswith("/unknown")
            )
            self.assertNotIn("fileTypes", unknown)
            self.assertEqual(document["spdxVersion"], "SPDX-2.3")
            verification = document["packages"][0]["packageVerificationCode"]
            self.assertEqual(
                verification["packageVerificationCodeExcludedFiles"],
                ["./usr/share/doc/mozkey/sbom.json"],
            )
            self.assertEqual(len(verification["packageVerificationCodeValue"]), 40)
            self.assertIn(
                "/linux/archlinux-x86_64/0.7.7/abc123/"
                + verification["packageVerificationCodeValue"],
                document["documentNamespace"],
            )

    def test_source_date_epoch_makes_serialization_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "file").write_text("same", encoding="utf-8")
            previous = os.environ.get("SOURCE_DATE_EPOCH")
            os.environ["SOURCE_DATE_EPOCH"] = "1"
            try:
                output = root / "sbom.json"
                first = generate(
                    root, output, "0.7.7", "rev", "archlinux", "x86_64"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(first, sort_keys=True) + "\n", encoding="utf-8"
                )
                second = generate(
                    root, output, "0.7.7", "rev", "archlinux", "x86_64"
                )
            finally:
                if previous is None:
                    os.environ.pop("SOURCE_DATE_EPOCH", None)
                else:
                    os.environ["SOURCE_DATE_EPOCH"] = previous
            self.assertEqual(
                json.dumps(first, sort_keys=True),
                json.dumps(second, sort_keys=True),
            )

    def test_rejects_unsafe_namespace_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "file").write_text("same", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "distro"):
                generate(root, root / "sbom.json", "0.7.7", "rev", "../bad", "x86_64")


if __name__ == "__main__":
    unittest.main()
