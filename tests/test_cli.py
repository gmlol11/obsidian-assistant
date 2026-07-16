from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from obsidian_assistant.cli import main


class QueueCliTests(unittest.TestCase):
    def test_queue_flow_previews_then_applies_without_printing_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            vault = base / "vault"
            runtime = base / "runtime"
            (vault / "00 Inbox").mkdir(parents=True)
            env_file = base / ".env"
            env_file.write_text(
                "\n".join(
                    (
                        f"OBSIDIAN_VAULT_PATH={vault}",
                        f"OBSIDIAN_RUNTIME_PATH={runtime}",
                        "OBSIDIAN_DRY_RUN=true",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            secret_fixture = "fixture text must not appear in operational output"

            with (
                patch.dict(os.environ, {}, clear=True),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                self.assertEqual(
                    main(
                        [
                            "--env-file",
                            str(env_file),
                            "queue",
                            "enqueue",
                            "--title",
                            "Fixture",
                            "--text",
                            secret_fixture,
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(["--env-file", str(env_file), "queue", "status", "--json"]),
                    0,
                )
                self.assertEqual(
                    main(["--env-file", str(env_file), "queue", "process"]),
                    0,
                )
                self.assertFalse(any((vault / "00 Inbox").iterdir()))
                self.assertEqual(
                    main(["--env-file", str(env_file), "queue", "process", "--apply"]),
                    0,
                )

            self.assertEqual(stderr.getvalue(), "")
            self.assertNotIn(secret_fixture, stdout.getvalue())
            self.assertIn("[PREVIEWED]", stdout.getvalue())
            self.assertIn("[COMPLETED]", stdout.getvalue())
            self.assertEqual(len(list((vault / "00 Inbox").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
