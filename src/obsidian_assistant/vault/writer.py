from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from obsidian_assistant.vault.policy import ExistingTargetError, VaultPolicy


@dataclass(frozen=True, slots=True)
class WriteResult:
    relative_path: PurePosixPath
    absolute_path: Path
    applied: bool
    bytes_count: int
    unchanged_existing: bool = False


class VaultWriter:
    def __init__(self, policy: VaultPolicy) -> None:
        self.policy = policy

    def create_markdown(
        self,
        relative_path: PurePosixPath,
        content: str,
        *,
        apply: bool,
        allow_identical_existing: bool = False,
    ) -> WriteResult:
        encoded = content.encode("utf-8")
        try:
            target = self.policy.resolve_new_markdown(relative_path)
        except ExistingTargetError:
            if not allow_identical_existing:
                raise
            return self._verify_identical(relative_path, encoded)

        if not apply:
            return WriteResult(relative_path, target, False, len(encoded))

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError as exc:
            if allow_identical_existing:
                return self._verify_identical(relative_path, encoded)
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

    def _verify_identical(self, relative_path: PurePosixPath, encoded: bytes) -> WriteResult:
        target = self.policy.resolve_markdown(relative_path)
        if target.is_symlink():
            raise ExistingTargetError(f"Target exists with different content: {relative_path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ExistingTargetError(
                    f"Target exists with different content: {relative_path}"
                ) from exc
            raise
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ExistingTargetError(
                    f"Target exists with different content: {relative_path}"
                )
            existing = handle.read(len(encoded) + 1)
        if existing != encoded:
            raise ExistingTargetError(f"Target exists with different content: {relative_path}")
        return WriteResult(relative_path, target, False, len(encoded), unchanged_existing=True)
