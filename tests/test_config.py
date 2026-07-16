from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from obsidian_assistant.config import ConfigError, Settings, load_env_file


class SettingsTests(unittest.TestCase):
    def test_vault_path_is_required(self) -> None:
        with self.assertRaisesRegex(ConfigError, "OBSIDIAN_VAULT_PATH"):
            Settings.from_mapping({})

    def test_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_mapping(
                {"OBSIDIAN_VAULT_PATH": "vault"},
                base_dir=Path(directory),
            )

        self.assertTrue(settings.dry_run)
        self.assertEqual(settings.inbox_dir.as_posix(), "00 Inbox")
        self.assertEqual([item.as_posix() for item in settings.allowed_write_dirs], ["00 Inbox"])
        self.assertEqual(settings.runtime_path, (Path(directory) / "runtime").resolve())
        self.assertEqual(settings.queue_max_attempts, 3)
        self.assertEqual(settings.queue_lease_seconds, 300)
        self.assertFalse(settings.telegram_configured)
        self.assertFalse(settings.openai_configured)

    def test_inbox_must_be_allowed(self) -> None:
        values = {
            "OBSIDIAN_VAULT_PATH": "vault",
            "OBSIDIAN_INBOX_DIR": "00 Inbox",
            "OBSIDIAN_ALLOWED_WRITE_DIRS": "10 Daily",
        }
        with self.assertRaisesRegex(ConfigError, "inside an allowed"):
            Settings.from_mapping(values)

    def test_unsafe_allowed_directory_is_rejected(self) -> None:
        values = {
            "OBSIDIAN_VAULT_PATH": "vault",
            "OBSIDIAN_ALLOWED_WRITE_DIRS": "../outside",
        }
        with self.assertRaisesRegex(ConfigError, "unsafe"):
            Settings.from_mapping(values)

    def test_runtime_path_cannot_contain_or_be_inside_vault(self) -> None:
        for runtime_path in ("vault/runtime", "."):
            with self.subTest(runtime_path=runtime_path):
                with self.assertRaisesRegex(ConfigError, "outside"):
                    Settings.from_mapping(
                        {
                            "OBSIDIAN_VAULT_PATH": "vault",
                            "OBSIDIAN_RUNTIME_PATH": runtime_path,
                        }
                    )

    def test_env_file_does_not_override_process_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OBSIDIAN_DRY_RUN=false\nLLM_PROVIDER='disabled'\n", encoding="utf-8")
            environ = {"OBSIDIAN_DRY_RUN": "true"}
            load_env_file(env_file, environ)

        self.assertEqual(environ["OBSIDIAN_DRY_RUN"], "true")
        self.assertEqual(environ["LLM_PROVIDER"], "disabled")

    def test_public_summary_has_no_secret_values(self) -> None:
        settings = Settings.from_mapping(
            {
                "OBSIDIAN_VAULT_PATH": "vault",
                "TELEGRAM_BOT_TOKEN": "telegram-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
        )
        summary = str(settings.public_summary())
        self.assertNotIn("telegram-secret", summary)
        self.assertNotIn("openai-secret", summary)
        self.assertTrue(settings.telegram_configured)
        self.assertTrue(settings.openai_configured)


if __name__ == "__main__":
    unittest.main()
