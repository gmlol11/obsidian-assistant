from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Sequence

from obsidian_assistant.config import ConfigError, Settings, load_env_file
from obsidian_assistant.diagnostics import inspect_settings
from obsidian_assistant.intake.events import CaptureEvent, EventValidationError
from obsidian_assistant.intake.processor import ProcessingState, QueueProcessor
from obsidian_assistant.intake.queue import FileQueue, QueueError
from obsidian_assistant.services.capture import CaptureCommand, CaptureError, CaptureService
from obsidian_assistant.vault.policy import VaultPolicy, VaultPolicyError
from obsidian_assistant.vault.writer import VaultWriter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsidian-assistant",
        description="Safe local automation foundation for an Obsidian vault.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional KEY=VALUE environment file. Process variables take precedence.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Validate configuration and vault access.")
    commands.add_parser("show-config", help="Print non-secret effective configuration.")

    capture = commands.add_parser("capture", help="Create or preview a new Inbox note.")
    capture.add_argument("--title", default="Входящая заметка")
    capture.add_argument("--text", required=True)
    capture.add_argument("--source", default="local")
    capture.add_argument(
        "--apply",
        action="store_true",
        help="Write even when OBSIDIAN_DRY_RUN=true. Use only with an intentional vault path.",
    )

    queue = commands.add_parser("queue", help="Manage the durable local capture queue.")
    queue_commands = queue.add_subparsers(dest="queue_command", required=True)

    enqueue = queue_commands.add_parser("enqueue", help="Validate and queue a text capture.")
    enqueue.add_argument("--title", default="Входящая заметка")
    enqueue.add_argument("--text", required=True)
    enqueue.add_argument("--source", default="local")
    enqueue.add_argument("--actor-id", default="local-owner")
    enqueue.add_argument("--request-id", type=_uuid_argument)

    status = queue_commands.add_parser("status", help="Show queue counters without note text.")
    status.add_argument("--json", action="store_true", dest="as_json")

    process = queue_commands.add_parser("process", help="Preview or process pending captures.")
    process.add_argument("--limit", type=_positive_integer, default=1)
    process.add_argument(
        "--apply",
        action="store_true",
        help="Process and write even when OBSIDIAN_DRY_RUN=true.",
    )

    retry = queue_commands.add_parser("retry", help="Return one quarantined request to pending.")
    retry.add_argument("request_id", type=_uuid_argument)

    recover = queue_commands.add_parser(
        "recover",
        help="Return stale processing leases to pending after an interrupted worker.",
    )
    recover.add_argument("--force", action="store_true")
    return parser


def _uuid_argument(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a valid UUID") from exc


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _load_settings(env_file: Path | None) -> Settings:
    base_dir = Path.cwd()
    if env_file is not None:
        resolved_env_file = env_file.expanduser().resolve(strict=False)
        load_env_file(resolved_env_file)
        base_dir = resolved_env_file.parent
    return Settings.from_mapping(os.environ, base_dir=base_dir)


def _doctor(settings: Settings) -> int:
    checks = inspect_settings(settings)
    for check in checks:
        print(f"[{check.level}] {check.name}: {check.message}")
    return 1 if any(check.level == "ERROR" for check in checks) else 0


def _capture(settings: Settings, args: argparse.Namespace) -> int:
    service = _capture_service(settings)
    result = service.capture(
        CaptureCommand(title=args.title, text=args.text, source=args.source),
        force_apply=args.apply,
    )
    mode = "CREATED" if result.applied else "DRY-RUN"
    print(f"[{mode}] {result.relative_path.as_posix()} ({result.bytes_count} bytes)")
    return 0


def _capture_service(settings: Settings) -> CaptureService:
    return CaptureService(settings, VaultWriter(VaultPolicy(settings)))


def _queue_command(settings: Settings, args: argparse.Namespace) -> int:
    queue = FileQueue(settings.runtime_path)
    if args.queue_command == "enqueue":
        event = CaptureEvent.create(
            title=args.title,
            text=args.text,
            source=args.source,
            actor_id=args.actor_id,
            request_id=args.request_id,
        )
        result = queue.enqueue(event)
        label = "ENQUEUED" if result.created else "DUPLICATE"
        print(f"[{label}] {result.request_id} state={result.state.value}")
        return 0

    if args.queue_command == "status":
        summary = queue.summary().to_dict()
        if args.as_json:
            print(json.dumps(summary, sort_keys=True))
        else:
            print(" ".join(f"{name}={count}" for name, count in summary.items()))
        return 0

    if args.queue_command == "process":
        processor = QueueProcessor(settings, queue, _capture_service(settings))
        exit_code = 0
        for _ in range(args.limit):
            result = processor.process_next(force_apply=args.apply)
            if result.state is ProcessingState.EMPTY:
                print("[EMPTY] no pending captures")
                break
            request_id = str(result.request_id) if result.request_id is not None else "unknown"
            details = [f"request_id={request_id}", f"attempts={result.attempts}"]
            if result.note_path is not None:
                details.append(f"note={result.note_path}")
            if result.reused_existing:
                details.append("reused_existing=true")
            if result.error_type is not None:
                details.append(f"error={result.error_type}")
            print(f"[{result.state.value.upper()}] {' '.join(details)}")
            if result.state in {ProcessingState.RETRY, ProcessingState.QUARANTINED}:
                exit_code = 1
            if result.state is ProcessingState.PREVIEWED:
                break
        return exit_code

    if args.queue_command == "retry":
        record = queue.retry_quarantined(args.request_id)
        print(f"[RETRIED] {record.request_id} state={record.status.value}")
        return 0

    if args.queue_command == "recover":
        count = queue.recover_stale(settings.queue_lease_seconds, force=args.force)
        print(f"[RECOVERED] count={count}")
        return 0

    raise QueueError(f"Unknown queue command: {args.queue_command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _load_settings(args.env_file)
        if args.command == "doctor":
            return _doctor(settings)
        if args.command == "show-config":
            print(json.dumps(settings.public_summary(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "capture":
            return _capture(settings, args)
        if args.command == "queue":
            return _queue_command(settings, args)
    except (
        ConfigError,
        CaptureError,
        EventValidationError,
        QueueError,
        VaultPolicyError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    parser.error(f"Unknown command: {args.command}")
    return 2
