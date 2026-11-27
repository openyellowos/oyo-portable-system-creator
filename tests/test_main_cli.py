from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from src.core.controller import Controller
from src.main import run_cli


class CliDryRunTests(unittest.TestCase):
    def test_cli_dry_run_validates_estimate_without_running_device_workflow(self) -> None:
        class Workflow:
            def __init__(self) -> None:
                self.validated = []

            def precheck(self, state) -> None:
                self.validated.append(state)
                state.metadata["source_path"] = "/temporary-source"
                state.metadata["copy_bytes"] = 1234
                state.used_bytes = 1234
                state.required_bytes = 2345

            def run_create(self, _state):
                raise AssertionError("CLI --dry-run must not run create workflow")

            def run_backup(self, _state):
                raise AssertionError("CLI --dry-run must not run backup workflow")

        for mode in ("create", "backup"):
            with self.subTest(mode=mode):
                workflow = Workflow()
                controller = Controller(workflow)  # type: ignore[arg-type]
                output = io.StringIO()
                with patch("src.main.build_controller", return_value=controller):
                    with contextlib.redirect_stdout(output):
                        result = run_cli([mode, "--target", "/dev/fake", "--dry-run"])

                self.assertEqual(result, 0)
                self.assertEqual(len(workflow.validated), 1)
                self.assertEqual(workflow.validated[0].mode, mode)
                self.assertIn("required_bytes=2345", output.getvalue())


if __name__ == "__main__":
    unittest.main()
