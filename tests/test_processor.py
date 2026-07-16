from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from obsidian_assistant.config import Settings
from obsidian_assistant.intake.events import CaptureEvent
from obsidian_assistant.intake.processor import ProcessingState, QueueProcessor
from obsidian_assistant.intake.queue import FileQueue
from obsidian_assistant.services.capture import CaptureCommand, CaptureService
from obsidian_assistant.vault.policy import VaultPolicy
from obsidian_assistant.vault.writer import VaultWriter


FIXED_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
FIXED_TIME = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class QueueProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.vault = self.base / "vault"
        self.runtime = self.base / "runtime"
        (self.vault / "00 Inbox").mkdir(parents=True)
        self.clock = MutableClock(FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def settings(self, *, dry_run: bool = True, max_attempts: int = 3) -> Settings:
        return Settings.from_mapping(
            {
                "OBSIDIAN_VAULT_PATH": str(self.vault),
                "OBSIDIAN_RUNTIME_PATH": str(self.runtime),
                "OBSIDIAN_DRY_RUN": str(dry_run).lower(),
                "OBSIDIAN_QUEUE_MAX_ATTEMPTS": str(max_attempts),
            }
        )

    def event(self) -> CaptureEvent:
        return CaptureEvent.create(
            title="Idea",
            text="Process safely",
            source="local",
            actor_id="test-owner",
            request_id=FIXED_ID,
            created_at=FIXED_TIME,
        )

    def components(self, *, dry_run: bool = True):
        settings = self.settings(dry_run=dry_run)
        queue = FileQueue(settings.runtime_path, clock=self.clock)
        service = CaptureService(settings, VaultWriter(VaultPolicy(settings)))
        processor = QueueProcessor(settings, queue, service, worker_id="test-worker")
        return settings, queue, service, processor

    def test_dry_run_previews_without_claiming_or_writing(self) -> None:
        _, queue, _, processor = self.components(dry_run=True)
        queue.enqueue(self.event())

        result = processor.process_next()

        self.assertEqual(result.state, ProcessingState.PREVIEWED)
        self.assertEqual(queue.summary().pending, 1)
        self.assertEqual(queue.summary().processing, 0)
        self.assertFalse(any((self.vault / "00 Inbox").iterdir()))

    def test_apply_creates_note_and_completed_receipt(self) -> None:
        _, queue, _, processor = self.components(dry_run=True)
        queue.enqueue(self.event())

        result = processor.process_next(force_apply=True)

        self.assertEqual(result.state, ProcessingState.COMPLETED)
        self.assertIsNotNone(result.note_path)
        self.assertTrue((self.vault / str(result.note_path)).is_file())
        self.assertEqual(queue.summary().completed, 1)
        self.assertEqual(queue.summary().pending, 0)

    def test_retry_after_note_write_reuses_identical_file(self) -> None:
        settings, queue, service, processor = self.components(dry_run=True)
        queue.enqueue(self.event())
        claimed = queue.claim_next("crashed-worker")
        assert claimed is not None
        service.capture(
            CaptureCommand("Idea", "Process safely", "local"),
            force_apply=True,
            capture_id=FIXED_ID,
            created_at=FIXED_TIME,
        )
        self.clock.value += timedelta(seconds=settings.queue_lease_seconds + 1)
        queue.recover_stale(settings.queue_lease_seconds)

        result = processor.process_next(force_apply=True)

        self.assertEqual(result.state, ProcessingState.COMPLETED)
        self.assertTrue(result.reused_existing)
        self.assertEqual(len(list((self.vault / "00 Inbox").glob("*.md"))), 1)

    def test_existing_different_file_is_quarantined_without_overwrite(self) -> None:
        _, queue, service, processor = self.components(dry_run=True)
        queue.enqueue(self.event())
        preview = service.capture(
            CaptureCommand("Idea", "Process safely", "local"),
            capture_id=FIXED_ID,
            created_at=FIXED_TIME,
        )
        preview.absolute_path.write_text("different fixture content", encoding="utf-8")

        result = processor.process_next(force_apply=True)

        self.assertEqual(result.state, ProcessingState.QUARANTINED)
        self.assertEqual(preview.absolute_path.read_text(encoding="utf-8"), "different fixture content")
        self.assertEqual(queue.summary().quarantine, 1)

    def test_corrupt_pending_file_moves_to_quarantine(self) -> None:
        _, queue, _, processor = self.components(dry_run=True)
        queue.summary()
        corrupt = queue.pending_dir / f"{FIXED_ID}.json"
        corrupt.write_text("not-json", encoding="utf-8")

        result = processor.process_next(force_apply=True)

        self.assertEqual(result.state, ProcessingState.QUARANTINED)
        self.assertFalse(corrupt.exists())
        self.assertEqual(queue.summary().quarantine, 1)


if __name__ == "__main__":
    unittest.main()
