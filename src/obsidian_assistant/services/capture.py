from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Callable

from obsidian_assistant.config import Settings
from obsidian_assistant.vault.writer import VaultWriter, WriteResult


class CaptureError(ValueError):
    """Raised when incoming capture data is invalid."""


_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MAX_TEXT_LENGTH = 500_000
_MAX_TITLE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class CaptureCommand:
    title: str
    text: str
    source: str = "local"


def normalize_capture_command(command: CaptureCommand) -> CaptureCommand:
    title = " ".join(command.title.split())
    text = command.text.strip()
    source = command.source.strip().lower()

    if not title:
        raise CaptureError("Title cannot be empty")
    if len(title) > _MAX_TITLE_LENGTH:
        raise CaptureError(f"Title cannot exceed {_MAX_TITLE_LENGTH} characters")
    if not text:
        raise CaptureError("Text cannot be empty")
    if len(text) > _MAX_TEXT_LENGTH:
        raise CaptureError(f"Text cannot exceed {_MAX_TEXT_LENGTH} characters")
    if not _SOURCE.fullmatch(source):
        raise CaptureError("Source must contain lowercase letters, digits, hyphens, or underscores")
    return CaptureCommand(title=title, text=text, source=source)


class CaptureService:
    def __init__(
        self,
        settings: Settings,
        writer: VaultWriter,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], uuid.UUID] | None = None,
    ) -> None:
        self.settings = settings
        self.writer = writer
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or uuid.uuid4

    def capture(
        self,
        command: CaptureCommand,
        *,
        force_apply: bool = False,
        capture_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
        allow_identical_existing: bool = False,
    ) -> WriteResult:
        normalized = normalize_capture_command(command)
        raw_now = created_at or self.clock()
        if raw_now.tzinfo is None:
            raise CaptureError("created_at must include a timezone")
        now = raw_now.astimezone(timezone.utc)
        effective_capture_id = capture_id or self.id_factory()
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{timestamp}-{effective_capture_id.hex}.md"
        relative_path = self.settings.inbox_dir / filename
        content = self._render_markdown(
            normalized.title,
            normalized.text,
            normalized.source,
            effective_capture_id,
            now,
        )
        should_apply = force_apply or not self.settings.dry_run
        return self.writer.create_markdown(
            relative_path,
            content,
            apply=should_apply,
            allow_identical_existing=allow_identical_existing,
        )

    @staticmethod
    def _render_markdown(
        title: str,
        text: str,
        source: str,
        capture_id: uuid.UUID,
        created_at: datetime,
    ) -> str:
        day = created_at.date().isoformat()
        yaml_source = json.dumps(source, ensure_ascii=False)
        yaml_capture_id = json.dumps(str(capture_id))
        return (
            "---\n"
            "type: note\n"
            f"created: {day}\n"
            f"updated: {day}\n"
            f"source: {yaml_source}\n"
            "status: inbox\n"
            "tags: []\n"
            f"capture_id: {yaml_capture_id}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"{text}\n"
        )
