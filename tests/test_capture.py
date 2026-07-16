from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from obsidian_assistant.config import Settings
from obsidian_assistant.services.capture import CaptureCommand, CaptureError, CaptureService
from obsidian_assistant.vault.policy import ExistingTargetError, VaultPolicy
from obsidian_assistant.vault.writer import VaultWriter


FIXED_TIME = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)
FIXED_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


class CaptureServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        (self.root / "00 Inbox").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def service(self, *, dry_run: bool) -> CaptureService:
        settings = Settings.from_mapping(
            {
                "OBSIDIAN_VAULT_PATH": str(self.root),
                "OBSIDIAN_DRY_RUN": str(dry_run).lower(),
            }
        )
        return CaptureService(
            settings,
            VaultWriter(VaultPolicy(settings)),
            clock=lambda: FIXED_TIME,
            id_factory=lambda: FIXED_ID,
        )

    def test_dry_run_does_not_create_file(self) -> None:
        result = self.service(dry_run=True).capture(CaptureCommand("Idea", "Test the flow"))
        self.assertFalse(result.applied)
        self.assertFalse(result.absolute_path.exists())

    def test_apply_creates_expected_markdown(self) -> None:
        result = self.service(dry_run=True).capture(
            CaptureCommand("  Новая   идея  ", "Проверить обработку", "local"),
            force_apply=True,
        )
        content = result.absolute_path.read_text(encoding="utf-8")

        self.assertTrue(result.applied)
        self.assertEqual(
            result.relative_path.as_posix(),
            "00 Inbox/20260716T093000000000Z-12345678123456781234567812345678.md",
        )
        self.assertIn("created: 2026-07-16", content)
        self.assertIn('source: "local"', content)
        self.assertIn('capture_id: "12345678-1234-5678-1234-567812345678"', content)
        self.assertIn("# Новая идея", content)
        self.assertTrue(content.endswith("Проверить обработку\n"))

    def test_dry_run_false_writes_without_force_flag(self) -> None:
        result = self.service(dry_run=False).capture(CaptureCommand("Idea", "Apply by configuration"))
        self.assertTrue(result.applied)
        self.assertTrue(result.absolute_path.exists())

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaisesRegex(CaptureError, "Text"):
            self.service(dry_run=True).capture(CaptureCommand("Idea", "   "))

    def test_invalid_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(CaptureError, "Source"):
            self.service(dry_run=True).capture(CaptureCommand("Idea", "Text", "telegram/user"))

    def test_identical_existing_file_is_accepted_only_when_explicitly_allowed(self) -> None:
        service = self.service(dry_run=True)
        first = service.capture(CaptureCommand("Idea", "Crash-safe retry"), force_apply=True)
        second = service.capture(
            CaptureCommand("Idea", "Crash-safe retry"),
            force_apply=True,
            capture_id=FIXED_ID,
            created_at=FIXED_TIME,
            allow_identical_existing=True,
        )

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertTrue(second.unchanged_existing)

    def test_identical_retry_never_follows_target_symlink(self) -> None:
        service = self.service(dry_run=True)
        first = service.capture(CaptureCommand("Idea", "Crash-safe retry"), force_apply=True)
        outside = Path(self.temporary.name) / "outside.md"
        first.absolute_path.replace(outside)
        first.absolute_path.symlink_to(outside)

        with self.assertRaises(ExistingTargetError):
            service.capture(
                CaptureCommand("Idea", "Crash-safe retry"),
                force_apply=True,
                capture_id=FIXED_ID,
                created_at=FIXED_TIME,
                allow_identical_existing=True,
            )


if __name__ == "__main__":
    unittest.main()
