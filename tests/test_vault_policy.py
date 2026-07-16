from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from obsidian_assistant.config import Settings
from obsidian_assistant.vault.policy import ExistingTargetError, VaultPolicy, VaultPolicyError
from obsidian_assistant.vault.writer import VaultWriter


def build_settings(vault: Path, *, dry_run: bool = True) -> Settings:
    return Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(vault),
            "OBSIDIAN_ALLOWED_WRITE_DIRS": "00 Inbox",
            "OBSIDIAN_DRY_RUN": str(dry_run).lower(),
        }
    )


class VaultPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        (self.root / "00 Inbox").mkdir(parents=True)
        (self.root / "10 Daily").mkdir()
        self.policy = VaultPolicy(build_settings(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_accepts_new_markdown_in_allowed_directory(self) -> None:
        target = self.policy.resolve_new_markdown("00 Inbox/new.md")
        self.assertEqual(target, self.root.resolve() / "00 Inbox" / "new.md")

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(VaultPolicyError):
            self.policy.resolve_new_markdown("00 Inbox/../../outside.md")

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(VaultPolicyError):
            self.policy.resolve_new_markdown("/tmp/outside.md")

    def test_rejects_disallowed_directory(self) -> None:
        with self.assertRaisesRegex(VaultPolicyError, "outside allowed"):
            self.policy.resolve_new_markdown("10 Daily/note.md")

    def test_rejects_non_markdown_file(self) -> None:
        with self.assertRaisesRegex(VaultPolicyError, "Markdown"):
            self.policy.resolve_new_markdown("00 Inbox/file.txt")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_rejects_symlink_escape(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "00 Inbox" / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(VaultPolicyError, "escapes"):
            self.policy.resolve_new_markdown("00 Inbox/escape/note.md")

    def test_existing_file_is_never_overwritten(self) -> None:
        existing = self.root / "00 Inbox" / "existing.md"
        existing.write_text("original", encoding="utf-8")
        writer = VaultWriter(self.policy)

        with self.assertRaises(ExistingTargetError):
            writer.create_markdown(PurePosixPath("00 Inbox/existing.md"), "replacement", apply=True)

        self.assertEqual(existing.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
