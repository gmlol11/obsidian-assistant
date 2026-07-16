from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from obsidian_assistant.intake.events import CaptureEvent
from obsidian_assistant.intake.queue import FileQueue, IdempotencyConflict, QueueState


FIXED_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
FIXED_TIME = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FileQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / "runtime"
        self.clock = MutableClock(FIXED_TIME)
        self.queue = FileQueue(self.runtime, clock=self.clock)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def event(self, *, text: str = "Queue this") -> CaptureEvent:
        return CaptureEvent.create(
            title="Idea",
            text=text,
            source="local",
            actor_id="test-owner",
            request_id=FIXED_ID,
            created_at=FIXED_TIME,
        )

    def test_enqueue_is_idempotent_and_conflicts_on_changed_content(self) -> None:
        first = self.queue.enqueue(self.event())
        duplicate = self.queue.enqueue(self.event())

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(self.queue.summary().pending, 1)
        with self.assertRaisesRegex(IdempotencyConflict, "different"):
            self.queue.enqueue(self.event(text="Different"))

    def test_complete_replaces_event_with_metadata_only_receipt(self) -> None:
        self.queue.enqueue(self.event(text="private fixture text"))
        claimed = self.queue.claim_next("test-worker")
        assert claimed is not None
        self.queue.complete(claimed, "00 Inbox/example.md")

        receipt_path = self.queue.completed_dir / f"{FIXED_ID}.json"
        receipt_text = receipt_path.read_text(encoding="utf-8")
        receipt = json.loads(receipt_text)

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["note_path"], "00 Inbox/example.md")
        self.assertNotIn("event", receipt)
        self.assertNotIn("actor_id", receipt)
        self.assertNotIn("private fixture text", receipt_text)
        self.assertEqual(self.queue.summary().processing, 0)

    def test_failure_retries_then_quarantines_and_can_be_retried_manually(self) -> None:
        self.queue.enqueue(self.event())
        first = self.queue.claim_next("test-worker")
        assert first is not None
        state = self.queue.fail(
            first,
            error_type="OSError",
            error_message="temporary",
            permanent=False,
            max_attempts=2,
        )
        self.assertEqual(state, QueueState.PENDING)

        second = self.queue.claim_next("test-worker")
        assert second is not None
        state = self.queue.fail(
            second,
            error_type="OSError",
            error_message="temporary",
            permanent=False,
            max_attempts=2,
        )
        self.assertEqual(state, QueueState.QUARANTINE)

        retried = self.queue.retry_quarantined(FIXED_ID)
        self.assertEqual(retried.status, QueueState.PENDING)
        self.assertEqual(retried.attempts, 0)
        self.assertEqual(retried.manual_retries, 1)

    def test_stale_processing_lease_is_recovered(self) -> None:
        self.queue.enqueue(self.event())
        claimed = self.queue.claim_next("test-worker")
        assert claimed is not None
        self.clock.value += timedelta(seconds=301)

        recovered = self.queue.recover_stale(300)

        self.assertEqual(recovered, 1)
        self.assertEqual(self.queue.summary().pending, 1)
        self.assertEqual(self.queue.summary().processing, 0)


if __name__ == "__main__":
    unittest.main()
