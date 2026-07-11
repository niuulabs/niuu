"""Security regressions for Skuld filesystem and log boundaries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skuld.path_security import (
    UnsafePathError,
    resolve_contained_path,
    resolve_path_in_roots,
)


class PathSecurityTests(unittest.TestCase):
    def test_relative_parent_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnsafePathError):
                resolve_contained_path(directory, "nested/../../secret.txt")

    def test_absolute_path_is_rejected_by_relative_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnsafePathError):
                resolve_contained_path(directory, "/etc/passwd")

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            (root / "escape").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(UnsafePathError):
                resolve_contained_path(root, "escape/secret.txt", strict=True)

    def test_symlink_within_root_resolves_to_canonical_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("safe", encoding="utf-8")
            (root / "alias.txt").symlink_to(target)

            resolved = resolve_contained_path(root, "alias.txt", strict=True)

            self.assertEqual(resolved, target.resolve())

    def test_absolute_path_must_belong_to_an_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            secret = outside / "secret.txt"
            secret.write_text("secret", encoding="utf-8")

            with self.assertRaises(UnsafePathError):
                resolve_path_in_roots(secret, (root,))


if __name__ == "__main__":
    unittest.main()
