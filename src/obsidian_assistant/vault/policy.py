from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from obsidian_assistant.config import Settings


class VaultPolicyError(ValueError):
    """Raised when an operation violates the vault path policy."""


class ExistingTargetError(VaultPolicyError):
    """Raised when create-only policy encounters an existing path."""


def _is_within(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


class VaultPolicy:
    def __init__(self, settings: Settings) -> None:
        try:
            self.root = settings.vault_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise VaultPolicyError(f"Vault does not exist: {settings.vault_path}") from exc
        if not self.root.is_dir():
            raise VaultPolicyError(f"Vault is not a directory: {self.root}")

        allowed_roots: list[Path] = []
        for relative in settings.allowed_write_dirs:
            candidate = self.root.joinpath(*relative.parts)
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise VaultPolicyError(f"Allowed directory does not exist: {relative}") from exc
            if not resolved.is_dir() or not _is_within(self.root, resolved):
                raise VaultPolicyError(f"Unsafe allowed directory: {relative}")
            allowed_roots.append(resolved)
        self.allowed_roots = tuple(allowed_roots)

    def resolve_markdown(self, relative_path: PurePosixPath | str) -> Path:
        raw_path = str(relative_path)
        if "\\" in raw_path:
            raise VaultPolicyError("Path must use forward slashes")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or path == PurePosixPath("."):
            raise VaultPolicyError("Target path must be relative")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise VaultPolicyError("Target path contains an unsafe segment")
        if path.suffix.lower() != ".md":
            raise VaultPolicyError("Only Markdown files can be created")

        unresolved = self.root.joinpath(*path.parts)
        try:
            resolved_parent = unresolved.parent.resolve(strict=True)
        except FileNotFoundError as exc:
            raise VaultPolicyError(f"Target directory does not exist: {path.parent}") from exc

        target = resolved_parent / unresolved.name
        if not _is_within(self.root, target):
            raise VaultPolicyError("Target escapes the vault root")
        if not any(_is_within(allowed_root, target) for allowed_root in self.allowed_roots):
            raise VaultPolicyError("Target is outside allowed write directories")
        return target

    def resolve_new_markdown(self, relative_path: PurePosixPath | str) -> Path:
        target = self.resolve_markdown(relative_path)
        if os.path.lexists(target):
            raise ExistingTargetError(f"Target already exists: {relative_path}")
        return target
