from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from obsidian_assistant.config import Settings
from obsidian_assistant.intake.bridge import (
    BridgeRequest,
    BridgeValidationError,
    handle_bridge_capture,
    parse_bridge_input,
)
from obsidian_assistant.intake.events import CaptureEvent
from obsidian_assistant.intake.queue import FileQueue, QueueState


FIXED_ID = uuid.UUID("12345678-1234-5678-9234-567812345678")
OLDER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
FIXED_TIME = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)
SECRET_FIXTURE = "fixture capture content must not appear in bridge output"


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.vault = self.base / "vault"
        self.runtime = self.base / "runtime"
        (self.vault / "00 Inbox").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def settings(self, *, dry_run: bool) -> Settings:
        return Settings.from_mapping(
            {
                "OBSIDIAN_VAULT_PATH": str(self.vault),
                "OBSIDIAN_RUNTIME_PATH": str(self.runtime),
                "OBSIDIAN_DRY_RUN": str(dry_run).lower(),
            }
        )

    def event(self, *, request_id: uuid.UUID = FIXED_ID) -> CaptureEvent:
        return CaptureEvent.create(
            title="Telegram fixture",
            text=SECRET_FIXTURE,
            source="telegram",
            actor_id="telegram:123456789",
            request_id=request_id,
            created_at=FIXED_TIME,
        )

    def request(self, *, process_now: bool = True) -> BridgeRequest:
        return BridgeRequest(event=self.event(), process_now=process_now)

    def test_bridge_input_is_strict_and_telegram_only(self) -> None:
        raw = {
            "bridge_schema_version": 1,
            "process_now": True,
            "event": self.event().to_dict(),
            "unexpected": True,
        }
        with self.assertRaisesRegex(BridgeValidationError, "fields"):
            parse_bridge_input(json.dumps(raw).encode())

        local_event = CaptureEvent.create(
            title="Local",
            text="Fixture",
            source="local",
            actor_id="local-owner",
            request_id=FIXED_ID,
            created_at=FIXED_TIME,
        )
        raw.pop("unexpected")
        raw["event"] = local_event.to_dict()
        with self.assertRaisesRegex(BridgeValidationError, "telegram"):
            parse_bridge_input(json.dumps(raw).encode())

    def test_dry_run_confirms_preview_without_writing_or_losing_pending(self) -> None:
        response = handle_bridge_capture(self.settings(dry_run=True), self.request())

        self.assertEqual(response.accepted, "created")
        self.assertEqual(response.queue_state, "pending")
        self.assertEqual(response.processing_state, "previewed")
        self.assertIsNotNone(response.note_path)
        self.assertFalse(any((self.vault / "00 Inbox").iterdir()))
        self.assertNotIn(SECRET_FIXTURE, json.dumps(response.to_dict()))

    def test_apply_returns_completed_metadata_and_is_idempotent(self) -> None:
        settings = self.settings(dry_run=False)

        first = handle_bridge_capture(settings, self.request())
        duplicate = handle_bridge_capture(settings, self.request())

        self.assertEqual(first.queue_state, "completed")
        self.assertEqual(first.processing_state, "completed")
        self.assertEqual(duplicate.accepted, "duplicate")
        self.assertEqual(duplicate.queue_state, "completed")
        self.assertEqual(first.note_path, duplicate.note_path)
        self.assertEqual(len(list((self.vault / "00 Inbox").glob("*.md"))), 1)

    def test_immediate_processing_targets_only_the_interactive_request(self) -> None:
        settings = self.settings(dry_run=False)
        queue = FileQueue(self.runtime)
        queue.enqueue(self.event(request_id=OLDER_ID))

        response = handle_bridge_capture(settings, self.request())

        older_status = queue.request_status(OLDER_ID)
        target_status = queue.request_status(FIXED_ID)
        assert older_status is not None
        assert target_status is not None
        self.assertEqual(older_status.state, QueueState.PENDING)
        self.assertEqual(target_status.state, QueueState.COMPLETED)
        self.assertEqual(response.request_id, str(FIXED_ID))


if __name__ == "__main__":
    unittest.main()
