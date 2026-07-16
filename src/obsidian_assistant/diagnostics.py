from __future__ import annotations

import os
from dataclasses import dataclass

from obsidian_assistant.config import Settings


@dataclass(frozen=True, slots=True)
class Check:
    level: str
    name: str
    message: str


def inspect_settings(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    vault = settings.vault_path

    if not vault.exists():
        return [Check("ERROR", "vault", f"Vault does not exist: {vault}")]
    if not vault.is_dir():
        return [Check("ERROR", "vault", f"Vault path is not a directory: {vault}")]

    checks.append(Check("OK", "vault", f"Vault directory is available: {vault}"))

    for relative_dir in settings.allowed_write_dirs:
        target = vault.joinpath(*relative_dir.parts)
        if not target.exists():
            checks.append(
                Check("ERROR", "allowed-directory", f"Allowed directory does not exist: {relative_dir}")
            )
        elif not target.is_dir():
            checks.append(Check("ERROR", "allowed-directory", f"Allowed path is not a directory: {relative_dir}"))
        elif not os.access(target, os.W_OK):
            checks.append(Check("ERROR", "allowed-directory", f"Allowed directory is not writable: {relative_dir}"))
        else:
            checks.append(Check("OK", "allowed-directory", f"Writable directory: {relative_dir}"))

    if settings.dry_run:
        checks.append(Check("OK", "write-mode", "Dry-run is enabled"))
    else:
        checks.append(Check("WARNING", "write-mode", "Dry-run is disabled; capture writes by default"))

    if settings.telegram_configured:
        checks.append(Check("WARNING", "telegram", "A Telegram token is present but the adapter is not implemented"))
    else:
        checks.append(Check("OK", "telegram", "Telegram adapter is disabled"))

    if settings.openai_configured:
        checks.append(Check("WARNING", "openai", "An OpenAI key is present but the adapter is not implemented"))
    else:
        checks.append(Check("OK", "openai", "OpenAI adapter is disabled"))

    return checks
