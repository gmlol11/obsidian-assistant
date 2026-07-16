from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from obsidian_assistant.config import ConfigError, Settings, load_env_file
from obsidian_assistant.diagnostics import inspect_settings
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
    return parser


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
    policy = VaultPolicy(settings)
    service = CaptureService(settings, VaultWriter(policy))
    result = service.capture(
        CaptureCommand(title=args.title, text=args.text, source=args.source),
        force_apply=args.apply,
    )
    mode = "CREATED" if result.applied else "DRY-RUN"
    print(f"[{mode}] {result.relative_path.as_posix()} ({result.bytes_count} bytes)")
    return 0


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
    except (ConfigError, CaptureError, VaultPolicyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    parser.error(f"Unknown command: {args.command}")
    return 2
