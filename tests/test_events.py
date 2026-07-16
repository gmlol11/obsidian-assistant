from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from obsidian_assistant.intake.events import CaptureEvent, EventValidationError


FIXED_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
FIXED_TIME = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)


class CaptureEventTests(unittest.TestCase):
    def test_round_trip_normalizes_and_preserves_versioned_contract(self) -> None:
        event = CaptureEvent.create(
            title="  Новая   идея ",
            text="  Проверить очередь  ",
            source="LOCAL",
            actor_id="owner.android",
            request_id=FIXED_ID,
            created_at=FIXED_TIME,
        )

        restored = CaptureEvent.from_dict(event.to_dict())

        self.assertEqual(restored, event)
        self.assertEqual(event.payload.title, "Новая идея")
        self.assertEqual(event.payload.text, "Проверить очередь")
        self.assertEqual(event.to_dict()["schema_version"], 1)
        self.assertEqual(event.to_dict()["event_type"], "capture.text")
        self.assertEqual(len(event.fingerprint()), 64)

    def test_unknown_fields_are_rejected(self) -> None:
        raw = CaptureEvent.create(
            title="Idea",
            text="Text",
            request_id=FIXED_ID,
            created_at=FIXED_TIME,
        ).to_dict()
        raw["unexpected"] = True

        with self.assertRaisesRegex(EventValidationError, "fields"):
            CaptureEvent.from_dict(raw)

    def test_actor_id_and_naive_time_are_rejected(self) -> None:
        with self.assertRaisesRegex(EventValidationError, "actor_id"):
            CaptureEvent.create(title="Idea", text="Text", actor_id="owner with spaces")
        with self.assertRaisesRegex(EventValidationError, "timezone"):
            CaptureEvent.create(
                title="Idea",
                text="Text",
                created_at=datetime(2026, 7, 16, 9, 30),
            )


if __name__ == "__main__":
    unittest.main()
