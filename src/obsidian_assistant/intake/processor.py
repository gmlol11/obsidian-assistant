from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass
from enum import StrEnum

from obsidian_assistant.config import Settings
from obsidian_assistant.intake.queue import FileQueue, QueueDataError, QueueRecord, QueueState
from obsidian_assistant.services.capture import CaptureCommand, CaptureError, CaptureService
from obsidian_assistant.vault.policy import VaultPolicyError
from obsidian_assistant.vault.writer import WriteResult


class ProcessingState(StrEnum):
    EMPTY = "empty"
    PREVIEWED = "previewed"
    COMPLETED = "completed"
    RETRY = "retry"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    state: ProcessingState
    request_id: uuid.UUID | None = None
    note_path: str | None = None
    attempts: int = 0
    reused_existing: bool = False
    error_type: str | None = None


class QueueProcessor:
    def __init__(
        self,
        settings: Settings,
        queue: FileQueue,
        capture_service: CaptureService,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.capture_service = capture_service
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"

    def process_next(self, *, force_apply: bool = False) -> ProcessResult:
        return self._process(force_apply=force_apply)

    def process_request(
        self,
        request_id: uuid.UUID,
        *,
        force_apply: bool = False,
    ) -> ProcessResult:
        """Process only the requested pending capture."""

        return self._process(request_id=request_id, force_apply=force_apply)

    def _process(
        self,
        *,
        request_id: uuid.UUID | None = None,
        force_apply: bool = False,
    ) -> ProcessResult:
        preview_only = self.settings.dry_run and not force_apply
        try:
            if request_id is None:
                record = (
                    self.queue.peek_pending()
                    if preview_only
                    else self.queue.claim_next(self.worker_id)
                )
            else:
                record = (
                    self.queue.peek_request(request_id)
                    if preview_only
                    else self.queue.claim_request(request_id, self.worker_id)
                )
        except QueueDataError as exc:
            self.queue.quarantine_corrupt(exc.path)
            return ProcessResult(
                state=ProcessingState.QUARANTINED,
                error_type=type(exc).__name__,
            )

        if record is None:
            return ProcessResult(state=ProcessingState.EMPTY)
        if preview_only:
            result = self._capture(record, force_apply=False)
            return ProcessResult(
                state=ProcessingState.PREVIEWED,
                request_id=record.request_id,
                note_path=result.relative_path.as_posix(),
                attempts=record.attempts,
                reused_existing=result.unchanged_existing,
            )

        try:
            result = self._capture(record, force_apply=True)
        except (CaptureError, VaultPolicyError) as exc:
            self.queue.fail(
                record,
                error_type=type(exc).__name__,
                error_message=self._safe_error(exc),
                permanent=True,
                max_attempts=self.settings.queue_max_attempts,
            )
            return ProcessResult(
                state=ProcessingState.QUARANTINED,
                request_id=record.request_id,
                attempts=record.attempts,
                error_type=type(exc).__name__,
            )
        except OSError as exc:
            destination = self.queue.fail(
                record,
                error_type=type(exc).__name__,
                error_message=self._safe_error(exc),
                permanent=False,
                max_attempts=self.settings.queue_max_attempts,
            )
            state = (
                ProcessingState.QUARANTINED
                if destination is QueueState.QUARANTINE
                else ProcessingState.RETRY
            )
            return ProcessResult(
                state=state,
                request_id=record.request_id,
                attempts=record.attempts,
                error_type=type(exc).__name__,
            )

        note_path = result.relative_path.as_posix()
        self.queue.complete(record, note_path)
        return ProcessResult(
            state=ProcessingState.COMPLETED,
            request_id=record.request_id,
            note_path=note_path,
            attempts=record.attempts,
            reused_existing=result.unchanged_existing,
        )

    def _capture(self, record: QueueRecord, *, force_apply: bool) -> WriteResult:
        event = record.event
        return self.capture_service.capture(
            CaptureCommand(
                title=event.payload.title,
                text=event.payload.text,
                source=event.source,
            ),
            force_apply=force_apply,
            capture_id=event.request_id,
            created_at=event.created_at,
            allow_identical_existing=True,
        )

    def _safe_error(self, error: BaseException) -> str:
        message = " ".join(str(error).split())
        replacements = (
            (str(self.settings.vault_path), "<vault>"),
            (str(self.settings.runtime_path), "<runtime>"),
        )
        for sensitive_path, label in replacements:
            message = message.replace(sensitive_path, label)
        return message[:500] or type(error).__name__
