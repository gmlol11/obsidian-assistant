from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


class ConfigError(ValueError):
    """Raised when runtime configuration violates the safe baseline."""


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def load_env_file(path: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Load a small, strict KEY=VALUE file without overriding process variables."""

    target = environ if environ is not None else os.environ
    if not path.is_file():
        raise ConfigError(f"Environment file does not exist: {path}")

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ConfigError(f"Invalid environment entry at {path}:{line_number}")

        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        target.setdefault(key, value)

    return target


def _parse_bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _parse_positive_int(value: str, key: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ConfigError(f"{key} must be a positive integer")
    return parsed


def _relative_directory(value: str, key: str) -> PurePosixPath:
    if "\\" in value:
        raise ConfigError(f"{key} must use forward slashes")
    path = PurePosixPath(value.strip())
    if not value.strip() or path.is_absolute() or path == PurePosixPath("."):
        raise ConfigError(f"{key} must be a non-empty relative directory")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigError(f"{key} contains an unsafe path segment")
    return path


def _contains_path(parent: PurePosixPath, child: PurePosixPath) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    vault_path: Path
    runtime_path: Path
    inbox_dir: PurePosixPath
    allowed_write_dirs: tuple[PurePosixPath, ...]
    dry_run: bool
    log_level: str
    queue_max_attempts: int
    queue_lease_seconds: int
    telegram_configured: bool
    openai_configured: bool
    llm_provider: str

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        base_dir: Path | None = None,
    ) -> Settings:
        raw_vault_path = values.get("OBSIDIAN_VAULT_PATH", "").strip()
        if not raw_vault_path:
            raise ConfigError("OBSIDIAN_VAULT_PATH is required")

        working_dir = (base_dir or Path.cwd()).resolve()
        vault_path = Path(raw_vault_path).expanduser()
        if not vault_path.is_absolute():
            vault_path = working_dir / vault_path
        vault_path = vault_path.resolve(strict=False)

        raw_runtime_path = values.get("OBSIDIAN_RUNTIME_PATH", "./runtime").strip()
        if not raw_runtime_path:
            raise ConfigError("OBSIDIAN_RUNTIME_PATH cannot be empty")
        runtime_path = Path(raw_runtime_path).expanduser()
        if not runtime_path.is_absolute():
            runtime_path = working_dir / runtime_path
        runtime_path = runtime_path.resolve(strict=False)
        if (
            runtime_path == vault_path
            or vault_path in runtime_path.parents
            or runtime_path in vault_path.parents
        ):
            raise ConfigError("OBSIDIAN_RUNTIME_PATH must be outside the Obsidian vault")

        inbox_dir = _relative_directory(
            values.get("OBSIDIAN_INBOX_DIR", "00 Inbox"),
            "OBSIDIAN_INBOX_DIR",
        )

        raw_allowed = values.get("OBSIDIAN_ALLOWED_WRITE_DIRS", "00 Inbox")
        allowed_write_dirs = tuple(
            _relative_directory(item, "OBSIDIAN_ALLOWED_WRITE_DIRS")
            for item in raw_allowed.split(",")
            if item.strip()
        )
        if not allowed_write_dirs:
            raise ConfigError("OBSIDIAN_ALLOWED_WRITE_DIRS must contain at least one directory")
        if not any(_contains_path(allowed, inbox_dir) for allowed in allowed_write_dirs):
            raise ConfigError("OBSIDIAN_INBOX_DIR must be inside an allowed write directory")

        log_level = values.get("OBSIDIAN_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError(f"OBSIDIAN_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}")

        environment = values.get("OBSIDIAN_ASSISTANT_ENV", "development").strip()
        if not environment:
            raise ConfigError("OBSIDIAN_ASSISTANT_ENV cannot be empty")

        return cls(
            environment=environment,
            vault_path=vault_path,
            runtime_path=runtime_path,
            inbox_dir=inbox_dir,
            allowed_write_dirs=allowed_write_dirs,
            dry_run=_parse_bool(values.get("OBSIDIAN_DRY_RUN", "true"), "OBSIDIAN_DRY_RUN"),
            log_level=log_level,
            queue_max_attempts=_parse_positive_int(
                values.get("OBSIDIAN_QUEUE_MAX_ATTEMPTS", "3"),
                "OBSIDIAN_QUEUE_MAX_ATTEMPTS",
            ),
            queue_lease_seconds=_parse_positive_int(
                values.get("OBSIDIAN_QUEUE_LEASE_SECONDS", "300"),
                "OBSIDIAN_QUEUE_LEASE_SECONDS",
            ),
            telegram_configured=bool(values.get("TELEGRAM_BOT_TOKEN", "").strip()),
            openai_configured=bool(values.get("OPENAI_API_KEY", "").strip()),
            llm_provider=values.get("LLM_PROVIDER", "disabled").strip() or "disabled",
        )

    def public_summary(self) -> dict[str, object]:
        """Return diagnostic metadata without tokens or note content."""

        return {
            "environment": self.environment,
            "vault_path": str(self.vault_path),
            "runtime_path": str(self.runtime_path),
            "inbox_dir": self.inbox_dir.as_posix(),
            "allowed_write_dirs": [item.as_posix() for item in self.allowed_write_dirs],
            "dry_run": self.dry_run,
            "log_level": self.log_level,
            "queue_max_attempts": self.queue_max_attempts,
            "queue_lease_seconds": self.queue_lease_seconds,
            "telegram_configured": self.telegram_configured,
            "openai_configured": self.openai_configured,
            "llm_provider": self.llm_provider,
        }
