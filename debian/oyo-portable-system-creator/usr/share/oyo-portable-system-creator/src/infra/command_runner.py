from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from src.core.errors import AppError
from src.infra.logger import AppLogger


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def __init__(self, logger: AppLogger) -> None:
        self.logger = logger

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        mask_values: list[str] | None = None,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        masked = self._masked(command, mask_values or [])
        self.logger.debug(f"$ {' '.join(masked)}")
        completed = subprocess.run(command, capture_output=True, text=True, cwd=cwd, input=input_text)
        result = CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        sanitized_stdout = self._sanitize_output_for_log(result.stdout)
        sanitized_stderr = self._sanitize_output_for_log(result.stderr)
        if sanitized_stdout:
            self.logger.debug(sanitized_stdout)
        if sanitized_stderr:
            self.logger.debug(sanitized_stderr)
        if check and result.returncode != 0:
            raise AppError.translated("E999", "error.command_failed", command=" ".join(masked))
        return result

    @staticmethod
    def _masked(command: list[str], masks: list[str]) -> list[str]:
        out = []
        for token in command:
            replaced = token
            for m in masks:
                replaced = replaced.replace(m, "***")
            out.append(replaced)
        return out

    @staticmethod
    def _sanitize_output_for_log(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[^\S\n\t]+", " ", normalized)
        normalized = "".join(ch for ch in normalized if ch in "\n\t" or ch.isprintable())
        return normalized.strip()
