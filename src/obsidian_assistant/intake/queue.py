from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

from obsidian_assistant.intake.events import (
    CaptureEvent,
    EventValidationError,
    format_timestamp,
    parse_timestamp,
)


class QueueError(RuntimeError):
    """Base error for durable queue operations."""


class QueueDataError(QueueError):
    """Raised when a queue file is corrupt or has been tampered with."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


class IdempotencyConflict(QueueError):
    """Raised when a request ID is reused with different event content."""


class QueueState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    QUARANTINE = "quarantine"


_RECORD_KEYS = {
    "queue_schema_version",
    "status",
    "request_id",
    "event_sha256",
    "event",
    "attempts",
    "manual_retries",
    "enqueued_at",
    "claimed_at",
    "worker_id",
    "last_error",
}
_RECEIPT_KEYS = {
    "queue_schema_version",
    "status",
    "request_id",
    "event_sha256",
    "source",
    "attempts",
    "manual_retries",
    "completed_at",
    "note_path",
}
_QUEUE_SCHEMA_VERSION = 1
_MAX_QUEUE_FILE_BYTES = 3_000_000


@dataclass(frozen=True, slots=True)
class QueueRecord:
    status: QueueState
    event: CaptureEvent
    event_sha256: str
    attempts: int
    manual_retries: int
    enqueued_at: datetime
    claimed_at: datetime | None = None
    worker_id: str | None = None
    last_error: Mapping[str, object] | None = None

    @property
    def request_id(self) -> uuid.UUID:
        return self.event.request_id

    def to_dict(self) -> dict[str, object]:
        return {
            "queue_schema_version": _QUEUE_SCHEMA_VERSION,
            "status": self.status.value,
            "request_id": str(self.request_id),
            "event_sha256": self.event_sha256,
            "event": self.event.to_dict(),
            "attempts": self.attempts,
            "manual_retries": self.manual_retries,
            "enqueued_at": format_timestamp(self.enqueued_at),
            "claimed_at": (
                format_timestamp(self.claimed_at) if self.claimed_at is not None else None
            ),
            "worker_id": self.worker_id,
            "last_error": dict(self.last_error) if self.last_error is not None else None,
        }


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    request_id: uuid.UUID
    state: QueueState
    created: bool


@dataclass(frozen=True, slots=True)
class QueueSummary:
    pending: int
    processing: int
    completed: int
    quarantine: int

    def to_dict(self) -> dict[str, int]:
        return {
            "pending": self.pending,
            "processing": self.processing,
            "completed": self.completed,
            "quarantine": self.quarantine,
        }


@dataclass(frozen=True, slots=True)
class RequestStatus:
    request_id: uuid.UUID
    state: QueueState
    note_path: str | None = None


class FileQueue:
    """A single-host, crash-recoverable filesystem queue.

    Queue content stays outside the vault. Completed files are metadata-only receipts.
    """

    def __init__(
        self,
        runtime_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = runtime_path / "queue"
        self.pending_dir = self.root / QueueState.PENDING.value
        self.processing_dir = self.root / QueueState.PROCESSING.value
        self.completed_dir = self.root / QueueState.COMPLETED.value
        self.quarantine_dir = self.root / QueueState.QUARANTINE.value
        self.lock_path = self.root / ".lock"
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def enqueue(self, event: CaptureEvent) -> EnqueueResult:
        fingerprint = event.fingerprint()
        with self._lock():
            existing = self._find_request(event.request_id)
            if existing is not None:
                state, existing_fingerprint = existing
                if existing_fingerprint != fingerprint:
                    raise IdempotencyConflict(
                        "request_id is already associated with different event content"
                    )
                return EnqueueResult(event.request_id, state, created=False)

            record = QueueRecord(
                status=QueueState.PENDING,
                event=event,
                event_sha256=fingerprint,
                attempts=0,
                manual_retries=0,
                enqueued_at=self._now(),
            )
            self._write_json_atomic(
                self._record_path(self.pending_dir, event.request_id),
                record.to_dict(),
            )
            return EnqueueResult(event.request_id, QueueState.PENDING, created=True)

    def peek_pending(self) -> QueueRecord | None:
        with self._lock():
            return self._oldest_pending()

    def peek_request(self, request_id: uuid.UUID) -> QueueRecord | None:
        """Return one pending request without exposing other queue entries."""

        with self._lock():
            path = self._record_path(self.pending_dir, request_id)
            if not path.exists():
                return None
            record = self._load_record(path)
            if record.status is not QueueState.PENDING:
                raise QueueDataError(path, "Pending record has an invalid status")
            return record

    def claim_next(self, worker_id: str) -> QueueRecord | None:
        if not worker_id.strip():
            raise QueueError("worker_id cannot be empty")
        with self._lock():
            record = self._oldest_pending()
            if record is None:
                return None
            return self._claim_record(record, worker_id)

    def claim_request(self, request_id: uuid.UUID, worker_id: str) -> QueueRecord | None:
        """Claim a specific request so an interactive caller gets its own result."""

        if not worker_id.strip():
            raise QueueError("worker_id cannot be empty")
        with self._lock():
            path = self._record_path(self.pending_dir, request_id)
            if not path.exists():
                return None
            record = self._load_record(path)
            if record.status is not QueueState.PENDING:
                raise QueueDataError(path, "Pending record has an invalid status")
            return self._claim_record(record, worker_id)

    def request_status(self, request_id: uuid.UUID) -> RequestStatus | None:
        """Return metadata-only state for one request."""

        with self._lock():
            for state, directory in (
                (QueueState.COMPLETED, self.completed_dir),
                (QueueState.PROCESSING, self.processing_dir),
                (QueueState.PENDING, self.pending_dir),
                (QueueState.QUARANTINE, self.quarantine_dir),
            ):
                path = self._record_path(directory, request_id)
                if not path.exists():
                    continue
                if state is QueueState.COMPLETED:
                    receipt = self._load_receipt(path, request_id)
                    return RequestStatus(
                        request_id=request_id,
                        state=state,
                        note_path=str(receipt["note_path"]),
                    )
                record = self._load_record(path)
                if record.status is not state:
                    raise QueueDataError(path, "Queue record status does not match its directory")
                return RequestStatus(request_id=request_id, state=state)
        return None

    def complete(self, record: QueueRecord, note_path: str) -> None:
        if record.status is not QueueState.PROCESSING:
            raise QueueError("Only a processing record can be completed")
        parsed_note_path = PurePosixPath(note_path)
        if (
            not note_path
            or "\\" in note_path
            or parsed_note_path.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed_note_path.parts)
        ):
            raise QueueError("Completed note_path must be a safe relative path")
        with self._lock():
            processing = self._record_path(self.processing_dir, record.request_id)
            completed = self._record_path(self.completed_dir, record.request_id)
            receipt = {
                "queue_schema_version": _QUEUE_SCHEMA_VERSION,
                "status": QueueState.COMPLETED.value,
                "request_id": str(record.request_id),
                "event_sha256": record.event_sha256,
                "source": record.event.source,
                "attempts": record.attempts,
                "manual_retries": record.manual_retries,
                "completed_at": format_timestamp(self._now()),
                "note_path": note_path,
            }
            if completed.exists():
                existing = self._load_receipt(completed, record.request_id)
                if existing.get("event_sha256") != record.event_sha256:
                    raise IdempotencyConflict("Completed receipt fingerprint does not match")
            else:
                self._write_json_atomic(completed, receipt)
            processing.unlink(missing_ok=True)

    def fail(
        self,
        record: QueueRecord,
        *,
        error_type: str,
        error_message: str,
        permanent: bool,
        max_attempts: int,
    ) -> QueueState:
        if record.status is not QueueState.PROCESSING:
            raise QueueError("Only a processing record can fail")
        if max_attempts < 1:
            raise QueueError("max_attempts must be positive")
        destination_state = (
            QueueState.QUARANTINE
            if permanent or record.attempts >= max_attempts
            else QueueState.PENDING
        )
        failed = replace(
            record,
            status=destination_state,
            claimed_at=None,
            worker_id=None,
            last_error={
                "type": error_type,
                "message": error_message,
                "at": format_timestamp(self._now()),
            },
        )
        with self._lock():
            source = self._record_path(self.processing_dir, record.request_id)
            target_dir = (
                self.quarantine_dir
                if destination_state is QueueState.QUARANTINE
                else self.pending_dir
            )
            target = self._record_path(target_dir, record.request_id)
            if os.path.lexists(target):
                raise QueueError("Cannot move failed request: destination already exists")
            self._write_json_atomic(target, failed.to_dict())
            source.unlink(missing_ok=True)
        return destination_state

    def retry_quarantined(self, request_id: uuid.UUID) -> QueueRecord:
        with self._lock():
            source = self._record_path(self.quarantine_dir, request_id)
            if not source.exists():
                raise QueueError("Quarantined request was not found")
            record = self._load_record(source)
            if record.status is not QueueState.QUARANTINE:
                raise QueueDataError(source, "Quarantine record has an invalid status")
            if self._find_request_outside(request_id, QueueState.QUARANTINE) is not None:
                raise QueueError("Request already exists outside quarantine")

            retried = replace(
                record,
                status=QueueState.PENDING,
                attempts=0,
                manual_retries=record.manual_retries + 1,
                claimed_at=None,
                worker_id=None,
                last_error=None,
                enqueued_at=self._now(),
            )
            target = self._record_path(self.pending_dir, request_id)
            self._write_json_atomic(target, retried.to_dict())
            source.unlink()
            return retried

    def recover_stale(self, lease_seconds: int, *, force: bool = False) -> int:
        if lease_seconds < 1:
            raise QueueError("lease_seconds must be positive")
        recovered = 0
        now = self._now()
        with self._lock():
            for path in sorted(self.processing_dir.glob("*.json")):
                record = self._load_record(path)
                if record.status not in {QueueState.PENDING, QueueState.PROCESSING}:
                    raise QueueDataError(path, "Processing record has an invalid status")
                completed = self._record_path(self.completed_dir, record.request_id)
                if completed.exists():
                    receipt = self._load_receipt(completed, record.request_id)
                    if receipt.get("event_sha256") != record.event_sha256:
                        raise IdempotencyConflict("Completed receipt fingerprint does not match")
                    path.unlink()
                    recovered += 1
                    continue

                pending_target = self._record_path(self.pending_dir, record.request_id)
                if pending_target.exists():
                    pending_record = self._load_record(pending_target)
                    if pending_record.event_sha256 != record.event_sha256:
                        raise IdempotencyConflict("Pending and processing fingerprints differ")
                    path.unlink()
                    recovered += 1
                    continue

                stale = record.claimed_at is None or (
                    now - record.claimed_at >= timedelta(seconds=lease_seconds)
                )
                if not force and not stale:
                    continue
                target = pending_target
                if os.path.lexists(target):
                    raise QueueError("Cannot recover request: pending destination exists")
                pending = replace(
                    record,
                    status=QueueState.PENDING,
                    claimed_at=None,
                    worker_id=None,
                )
                self._write_json_atomic(target, pending.to_dict())
                path.unlink()
                recovered += 1
        return recovered

    def quarantine_corrupt(self, path: Path) -> Path:
        with self._lock():
            if path.parent not in {self.pending_dir, self.processing_dir} or not path.exists():
                raise QueueError("Corrupt queue file is no longer available")
            stamp = self._now().strftime("%Y%m%dT%H%M%S%fZ")
            target = self.quarantine_dir / f"corrupt-{stamp}-{path.name}"
            os.replace(path, target)
            return target

    def summary(self) -> QueueSummary:
        with self._lock():
            return QueueSummary(
                pending=self._count(self.pending_dir),
                processing=self._count(self.processing_dir),
                completed=self._count(self.completed_dir),
                quarantine=self._count(self.quarantine_dir),
            )

    def _oldest_pending(self) -> QueueRecord | None:
        records: list[QueueRecord] = []
        for path in self.pending_dir.glob("*.json"):
            record = self._load_record(path)
            if record.status is not QueueState.PENDING:
                raise QueueDataError(path, "Pending record has an invalid status")
            records.append(record)
        if not records:
            return None
        return min(records, key=lambda item: (item.enqueued_at, str(item.request_id)))

    def _claim_record(self, record: QueueRecord, worker_id: str) -> QueueRecord:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise QueueError("worker_id cannot be empty")
        source = self._record_path(self.pending_dir, record.request_id)
        target = self._record_path(self.processing_dir, record.request_id)
        if os.path.lexists(target):
            raise QueueError("Request is already present in processing")
        os.replace(source, target)
        claimed = replace(
            record,
            status=QueueState.PROCESSING,
            attempts=record.attempts + 1,
            claimed_at=self._now(),
            worker_id=normalized_worker_id,
        )
        self._write_json_atomic(target, claimed.to_dict())
        return claimed

    def _find_request(self, request_id: uuid.UUID) -> tuple[QueueState, str] | None:
        for state, directory in (
            (QueueState.COMPLETED, self.completed_dir),
            (QueueState.PROCESSING, self.processing_dir),
            (QueueState.PENDING, self.pending_dir),
            (QueueState.QUARANTINE, self.quarantine_dir),
        ):
            path = self._record_path(directory, request_id)
            if not path.exists():
                continue
            if state is QueueState.COMPLETED:
                receipt = self._load_receipt(path, request_id)
                fingerprint = receipt.get("event_sha256")
                if not isinstance(fingerprint, str):
                    raise QueueDataError(path, "Completed receipt is missing event_sha256")
                return state, fingerprint
            record = self._load_record(path)
            return state, record.event_sha256
        return None

    def _find_request_outside(
        self,
        request_id: uuid.UUID,
        excluded: QueueState,
    ) -> QueueState | None:
        directories = {
            QueueState.PENDING: self.pending_dir,
            QueueState.PROCESSING: self.processing_dir,
            QueueState.COMPLETED: self.completed_dir,
            QueueState.QUARANTINE: self.quarantine_dir,
        }
        for state, directory in directories.items():
            if state is not excluded and self._record_path(directory, request_id).exists():
                return state
        return None

    def _load_record(self, path: Path) -> QueueRecord:
        raw = self._load_json(path)
        if set(raw) != _RECORD_KEYS or raw.get("queue_schema_version") != _QUEUE_SCHEMA_VERSION:
            raise QueueDataError(path, "Queue record does not match schema version 1")
        try:
            status = QueueState(raw["status"])
        except (ValueError, TypeError) as exc:
            raise QueueDataError(path, "Queue record has an invalid status") from exc
        if status is QueueState.COMPLETED:
            raise QueueDataError(path, "Completed queue entries must use receipt format")
        try:
            event_raw = raw["event"]
            if not isinstance(event_raw, Mapping):
                raise EventValidationError("event must be an object")
            event = CaptureEvent.from_dict(event_raw)
        except EventValidationError as exc:
            raise QueueDataError(path, f"Invalid event: {exc}") from exc
        if raw.get("request_id") != str(event.request_id):
            raise QueueDataError(path, "Queue request_id does not match event")
        fingerprint = raw.get("event_sha256")
        if not isinstance(fingerprint, str) or fingerprint != event.fingerprint():
            raise QueueDataError(path, "Queue event fingerprint does not match")
        attempts = raw.get("attempts")
        manual_retries = raw.get("manual_retries")
        if type(attempts) is not int or attempts < 0:
            raise QueueDataError(path, "attempts must be a non-negative integer")
        if type(manual_retries) is not int or manual_retries < 0:
            raise QueueDataError(path, "manual_retries must be a non-negative integer")
        claimed_raw = raw.get("claimed_at")
        claimed_at = (
            None
            if claimed_raw is None
            else self._parse_record_time(path, claimed_raw, "claimed_at")
        )
        worker_id = raw.get("worker_id")
        if worker_id is not None and not isinstance(worker_id, str):
            raise QueueDataError(path, "worker_id must be a string or null")
        last_error = raw.get("last_error")
        if last_error is not None and not isinstance(last_error, Mapping):
            raise QueueDataError(path, "last_error must be an object or null")
        return QueueRecord(
            status=status,
            event=event,
            event_sha256=fingerprint,
            attempts=attempts,
            manual_retries=manual_retries,
            enqueued_at=self._parse_record_time(path, raw.get("enqueued_at"), "enqueued_at"),
            claimed_at=claimed_at,
            worker_id=worker_id,
            last_error=last_error,
        )

    def _load_receipt(self, path: Path, request_id: uuid.UUID) -> dict[str, Any]:
        raw = self._load_json(path)
        if set(raw) != _RECEIPT_KEYS or raw.get("queue_schema_version") != _QUEUE_SCHEMA_VERSION:
            raise QueueDataError(path, "Completed receipt does not match schema version 1")
        if raw.get("status") != QueueState.COMPLETED.value:
            raise QueueDataError(path, "Completed receipt has an invalid status")
        if raw.get("request_id") != str(request_id):
            raise QueueDataError(path, "Completed receipt request_id does not match its filename")
        fingerprint = raw.get("event_sha256")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise QueueDataError(path, "Completed receipt has an invalid event_sha256")
        source = raw.get("source")
        if not isinstance(source, str) or not source:
            raise QueueDataError(path, "Completed receipt has an invalid source")
        attempts = raw.get("attempts")
        manual_retries = raw.get("manual_retries")
        if type(attempts) is not int or attempts < 0:
            raise QueueDataError(path, "Completed receipt has invalid attempts")
        if type(manual_retries) is not int or manual_retries < 0:
            raise QueueDataError(path, "Completed receipt has invalid manual_retries")
        self._parse_record_time(path, raw.get("completed_at"), "completed_at")
        note_path = raw.get("note_path")
        if not isinstance(note_path, str):
            raise QueueDataError(path, "Completed receipt has an invalid note_path")
        parsed_note_path = PurePosixPath(note_path)
        if (
            not note_path
            or "\\" in note_path
            or parsed_note_path.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed_note_path.parts)
        ):
            raise QueueDataError(path, "Completed receipt has an unsafe note_path")
        return raw

    @staticmethod
    def _parse_record_time(path: Path, raw: object, field: str) -> datetime:
        try:
            return parse_timestamp(raw, field)
        except EventValidationError as exc:
            raise QueueDataError(path, str(exc)) from exc

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise QueueDataError(path, "Queue files cannot be symbolic links") from exc
            raise
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise QueueDataError(path, "Queue path is not a regular file")
            if file_stat.st_size > _MAX_QUEUE_FILE_BYTES:
                raise QueueDataError(path, "Queue file exceeds the safe size limit")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                encoded = stream.read(_MAX_QUEUE_FILE_BYTES + 1)
            if len(encoded) > _MAX_QUEUE_FILE_BYTES:
                raise QueueDataError(path, "Queue file exceeds the safe size limit")
            text = encoded.decode("utf-8")
            raw = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QueueDataError(path, "Queue file is not valid UTF-8 JSON") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(raw, dict):
            raise QueueDataError(path, "Queue file must contain a JSON object")
        return raw

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._ensure_layout()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _ensure_layout(self) -> None:
        for path in (
            self.root,
            self.pending_dir,
            self.processing_dir,
            self.completed_dir,
            self.quarantine_dir,
        ):
            if os.path.lexists(path) and path.is_symlink():
                raise QueueError("Queue directories cannot be symbolic links")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not path.is_dir():
                raise QueueError("Queue path is not a directory")
            os.chmod(path, 0o700)

    @staticmethod
    def _record_path(directory: Path, request_id: uuid.UUID) -> Path:
        return directory / f"{request_id}.json"

    @staticmethod
    def _count(directory: Path) -> int:
        return sum(1 for path in directory.glob("*.json") if path.is_file())

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise QueueError("Queue clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)
