from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from obsidian_assistant.vault.policy import ExistingTargetError, VaultPolicy


@dataclass(frozen=True, slots=True)
class WriteResult:
    relative_path: PurePosixPath
    absolute_path: Path
    applied: bool
    bytes_count: int


class VaultWriter:
    def __init__(self, policy: VaultPolicy) -> None:
        self.policy = policy

    def create_markdown(
        self,
        relative_path: PurePosixPath,
        content: str,
        *,
        apply: bool,
    ) -> WriteResult:
        target = self.policy.resolve_new_markdown(relative_path)
        encoded = content.encode("utf-8")

        if not apply:
            return WriteResult(relative_path, target, False, len(encoded))

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError as exc:
            raise ExistingTargetError(f"Target already exists: {relative_path}") from exc

        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise

        return WriteResult(relative_path, target, True, len(encoded))
