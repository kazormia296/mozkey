import os
import unittest
from unittest import mock

from mac import build_package as target


class ResolveKeychainPathTest(unittest.TestCase):
    def test_preserves_absolute_temporary_keychain_path(self):
        path = "/private/tmp/mozkey-signing.keychain-db"
        self.assertEqual(target.ResolveKeychainPath(path), path)

    def test_resolves_relative_keychain_name_below_user_keychains(self):
        with mock.patch.dict(os.environ, {"HOME": "/Users/runner"}):
            self.assertEqual(
                target.ResolveKeychainPath("login.keychain-db"),
                "/Users/runner/Library/Keychains/login.keychain-db",
            )

    def test_rejects_empty_keychain(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            target.ResolveKeychainPath("")


if __name__ == "__main__":
    unittest.main()
