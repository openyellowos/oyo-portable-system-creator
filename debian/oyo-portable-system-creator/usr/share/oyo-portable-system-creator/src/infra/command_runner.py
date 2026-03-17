from __future__ import annotations

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
    ) -> CommandResult:
        masked = self._masked(command, mask_values or [])
        self.logger.debug(f"$ {' '.join(masked)}")
        completed = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
        result = CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if result.stdout.strip():
            self.logger.debug(result.stdout.strip())
        if result.stderr.strip():
            self.logger.debug(result.stderr.strip())
        if check and result.returncode != 0:
            raise AppError("E999", f"Command failed: {' '.join(masked)}")
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
