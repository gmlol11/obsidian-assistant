from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from obsidian_assistant.services.capture import (
    CaptureCommand,
    CaptureError,
    normalize_capture_command,
)


class EventValidationError(ValueError):
    """Raised when an intake event does not match the supported contract."""


SCHEMA_VERSION = 1
EVENT_TYPE = "capture.text"
_ACTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_EVENT_KEYS = {
    "schema_version",
    "request_id",
    "event_type",
    "source",
    "actor_id",
    "created_at",
    "payload",
}


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise EventValidationError("Timestamp must include a timezone")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: object, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field} must be a non-empty ISO 8601 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise EventValidationError(f"{field} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EventValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TextCapturePayload:
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    request_id: uuid.UUID
    source: str
    actor_id: str
    created_at: datetime
    payload: TextCapturePayload
    schema_version: int = SCHEMA_VERSION
    event_type: str = EVENT_TYPE

    @classmethod
    def create(
        cls,
        *,
        title: str,
        text: str,
        source: str = "local",
        actor_id: str = "local-owner",
        request_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> CaptureEvent:
        try:
            normalized = normalize_capture_command(
                CaptureCommand(title=title, text=text, source=source)
            )
        except CaptureError as exc:
            raise EventValidationError(str(exc)) from exc

        normalized_actor = actor_id.strip()
        if not _ACTOR_ID.fullmatch(normalized_actor):
            raise EventValidationError(
                "actor_id must contain letters, digits, dots, colons, at signs, "
                "hyphens, or underscores"
            )

        event_time = created_at or datetime.now(timezone.utc)
        if event_time.tzinfo is None:
            raise EventValidationError("created_at must include a timezone")
        return cls(
            request_id=request_id or uuid.uuid4(),
            source=normalized.source,
            actor_id=normalized_actor,
            created_at=event_time.astimezone(timezone.utc),
            payload=TextCapturePayload(title=normalized.title, text=normalized.text),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CaptureEvent:
        if set(raw) != _EVENT_KEYS:
            raise EventValidationError("Event fields do not match schema version 1")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise EventValidationError("Unsupported event schema_version")
        if raw.get("event_type") != EVENT_TYPE:
            raise EventValidationError("Unsupported event_type")

        request_value = raw.get("request_id")
        if not isinstance(request_value, str):
            raise EventValidationError("request_id must be a UUID string")
        try:
            request_id = uuid.UUID(request_value)
        except ValueError as exc:
            raise EventValidationError("request_id must be a valid UUID") from exc
        if str(request_id) != request_value:
            raise EventValidationError("request_id must use canonical UUID form")

        payload = raw.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"title", "text"}:
            raise EventValidationError("payload must contain exactly title and text")
        title = payload.get("title")
        text = payload.get("text")
        source = raw.get("source")
        actor_id = raw.get("actor_id")
        if not all(isinstance(item, str) for item in (title, text, source, actor_id)):
            raise EventValidationError("Event text fields must be strings")

        return cls.create(
            title=title,
            text=text,
            source=source,
            actor_id=actor_id,
            request_id=request_id,
            created_at=parse_timestamp(raw.get("created_at"), "created_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_id": str(self.request_id),
            "event_type": self.event_type,
            "source": self.source,
            "actor_id": self.actor_id,
            "created_at": format_timestamp(self.created_at),
            "payload": {
                "title": self.payload.title,
                "text": self.payload.text,
            },
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
