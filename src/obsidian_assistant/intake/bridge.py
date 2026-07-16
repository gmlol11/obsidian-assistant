from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from obsidian_assistant.config import Settings
from obsidian_assistant.intake.events import CaptureEvent, EventValidationError
from obsidian_assistant.intake.processor import ProcessResult, QueueProcessor
from obsidian_assistant.intake.queue import FileQueue, QueueError, QueueState
from obsidian_assistant.services.capture import CaptureService
from obsidian_assistant.vault.policy import VaultPolicy
from obsidian_assistant.vault.writer import VaultWriter


class BridgeValidationError(ValueError):
    """Raised when the local OpenClaw bridge receives an invalid envelope."""


BRIDGE_SCHEMA_VERSION = 1
MAX_BRIDGE_INPUT_BYTES = 64_000
_BRIDGE_KEYS = {"bridge_schema_version", "process_now", "event"}


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    event: CaptureEvent
    process_now: bool

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BridgeRequest:
        if set(raw) != _BRIDGE_KEYS:
            raise BridgeValidationError("Bridge fields do not match schema version 1")
        if raw.get("bridge_schema_version") != BRIDGE_SCHEMA_VERSION:
            raise BridgeValidationError("Unsupported bridge_schema_version")
        process_now = raw.get("process_now")
        if type(process_now) is not bool:
            raise BridgeValidationError("process_now must be a boolean")
        event_raw = raw.get("event")
        if not isinstance(event_raw, Mapping):
            raise BridgeValidationError("event must be an object")
        try:
            event = CaptureEvent.from_dict(event_raw)
        except EventValidationError as exc:
            raise BridgeValidationError(f"Invalid event: {exc}") from exc
        if event.source != "telegram":
            raise BridgeValidationError("Bridge accepts only telegram capture events")
        expected_actor = event.actor_id.removeprefix("telegram:")
        if (
            not event.actor_id.startswith("telegram:")
            or not expected_actor.isascii()
            or not expected_actor.isdecimal()
        ):
            raise BridgeValidationError("Telegram actor_id must contain a numeric sender ID")
        return cls(event=event, process_now=process_now)


@dataclass(frozen=True, slots=True)
class BridgeResponse:
    request_id: str
    accepted: str
    queue_state: str
    processing_state: str | None
    note_path: str | None
    bridge_schema_version: int = BRIDGE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "bridge_schema_version": self.bridge_schema_version,
            "request_id": self.request_id,
            "accepted": self.accepted,
            "queue_state": self.queue_state,
            "processing_state": self.processing_state,
            "note_path": self.note_path,
        }


def parse_bridge_input(encoded: bytes) -> BridgeRequest:
    if len(encoded) > MAX_BRIDGE_INPUT_BYTES:
        raise BridgeValidationError("Bridge input exceeds the safe size limit")
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeValidationError("Bridge input must be valid UTF-8 JSON") from exc
    if not isinstance(raw, Mapping):
        raise BridgeValidationError("Bridge input must be a JSON object")
    return BridgeRequest.from_dict(raw)


def handle_bridge_capture(settings: Settings, request: BridgeRequest) -> BridgeResponse:
    queue = FileQueue(settings.runtime_path)
    enqueue_result = queue.enqueue(request.event)
    process_result: ProcessResult | None = None

    if request.process_now and enqueue_result.state is QueueState.PENDING:
        service = CaptureService(settings, VaultWriter(VaultPolicy(settings)))
        processor = QueueProcessor(settings, queue, service)
        process_result = processor.process_request(request.event.request_id)

    status = queue.request_status(request.event.request_id)
    if status is None:
        raise QueueError("Queued request disappeared before status confirmation")
    return BridgeResponse(
        request_id=str(request.event.request_id),
        accepted="created" if enqueue_result.created else "duplicate",
        queue_state=status.state.value,
        processing_state=(process_result.state.value if process_result is not None else None),
        note_path=status.note_path or (process_result.note_path if process_result else None),
    )
